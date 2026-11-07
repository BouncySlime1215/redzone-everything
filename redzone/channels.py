"""Where can you actually watch a game? Map each game to a tuner target.

Targets look like  "youtubetv:espn"  or  "prime:<gti>"  or  "youtubetv:st"  (Sunday Ticket slot TBD).

Rules (Raleigh-Durham market, YouTube TV + Sunday Ticket + Prime):
- National cable (ESPN, ESPN2, FS1, NFL Network, TNT, truTV, NBA TV, NHL Network, MLB Network, USA)
  -> that YouTube TV channel.
- Broadcast networks: national windows (MNF on ABC, SNF on NBC, national CBS/FOX) -> local affiliate.
- Sunday-afternoon NFL on CBS/FOX: Panthers games and anything shown locally -> local CBS/FOX;
  everything else -> Sunday Ticket. Which games air locally isn't in any free feed, so those are
  marked "likely" and you can correct them on the phone. Corrections are remembered.
- Prime Video, NBA League Pass (via Prime), Monumental (Wizards/Caps local) -> Prime.
- Anything else -> unknown (shown on the phone so you can set it by hand).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from redzone.models import GameState, League, Phase

DATA = Path(__file__).resolve().parent.parent / "data"

# ESPN broadcast name (lowercased) -> our channel key
NETWORKS = {
    "espn": "espn", "espn2": "espn2", "espnu": "espnu", "espnews": "espnews", "abc": "abc",
    "espn+": "espnplus", "espn unlimited": "espnplus",
    "fox": "fox", "fs1": "fs1", "fs2": "fs2", "cbs": "cbs", "nbc": "nbc", "cbssn": "cbssn",
    "nfl net": "nflnetwork", "nfl network": "nflnetwork", "nfln": "nflnetwork",
    "tnt": "tnt", "trutv": "trutv", "tbs": "tbs", "nba tv": "nbatv", "nbatv": "nbatv",
    "nhl net": "nhlnetwork", "nhl network": "nhlnetwork", "mlb net": "mlbnetwork", "mlb network": "mlbnetwork",
    "usa net": "usa", "usa": "usa", "nbcsn": "nbcsn", "nbc sports network": "nbcsn",
    "prime video": "prime", "amazon prime video": "prime", "prime": "prime",
    "nba league pass": "leaguepass", "league pass": "leaguepass",
    "monumental": "monumental", "monumental sports network": "monumental", "msn": "monumental",
    "peacock": "peacock", "netflix": "netflix", "apple tv": "appletv", "max": "max", "hbo max": "max",
    # College: conference networks are YouTube TV channels; ACC Network Extra / SEC Network+ are
    # ESPN+ streams (ESPN app, lowest tier); FloSports and B1G+ aren't set up.
    "acc network": "accn", "accn": "accn", "sec network": "secn", "secn": "secn",
    "big ten network": "btn", "btn": "btn", "b1g network": "btn", "cbs sports network": "cbssn",
    "accnx": "espnplus", "acc network extra": "espnplus", "secn+": "espnplus", "sec network+": "espnplus",
    "cw": "cw", "the cw": "cw", "flosports": "flosports", "b1g+": "bigtenplus",
}
# Where each channel key is watchable for you.
APP_FOR = {
    "prime": "prime", "leaguepass": "prime", "monumental": "prime",
    "peacock": None, "netflix": None, "appletv": None, "max": None, "espnplus": "youtubetv_event",
    "flosports": None, "bigtenplus": None,
}
# Regional sports networks: blacked out here, so out-of-market games go to League Pass (NBA) / ESPN+ (NHL).
RSN_HINTS = ("sn", "sports net", "sportsnet", "fanduel", "nesn", "msg", "scripps", "dsn", "marquee", "yes",
             "spectrum", "altitude", "root", "bally", "chsn", "kunpd", "victory", "kcop", "kmcc", "gulf coast")
BROADCAST = {"cbs", "fox", "nbc", "abc", "cw"}
LOCAL_TEAMS = {League.NFL: {"CAR"}, League.NBA: {"CHA"}, League.NHL: {"CAR"}, League.MLB: set()}
# Wizards/Caps local games are on Monumental, which you watch through Prime Video Channels.
MONUMENTAL_TEAMS = {League.NBA: {"WSH", "WAS"}, League.NHL: {"WSH"}}


def _names_monumental(feed: dict, g: GameState) -> bool:
    """A Prime feed with no home/away tag that says it's the Monumental (Wizards/Caps) feed."""
    if not (MONUMENTAL_TEAMS.get(g.league, set()) & {g.home.abbr, g.away.abbr}):
        return False
    text = f"{feed.get('feed_name') or ''} {feed.get('title') or ''} {feed.get('network') or ''}".lower()
    return "monumental" in text


@dataclass
class Where:
    target: str | None      # None = unknown
    label: str              # "ESPN on YouTube TV", "Sunday Ticket", ...
    confidence: str         # "sure" | "likely" | "unknown" | "you set it"
    tunable: bool           # can the TV actually be sent there right now?


class ChannelMap:
    def __init__(self, path: Path = DATA / "channels.json"):
        self.path = path
        self.codes: dict[str, str] = {}        # channel key -> YouTube TV watch code
        self.overrides: dict[str, str] = {}    # game_id -> target
        self.team_rules: dict[str, str] = {}   # "nfl:WSH" -> target (sticky preferences)
        self.prime_gti: dict[str, str] = {}    # game_id -> Prime GTI
        self.st_slots: dict[str, str] = {}     # game_id -> Sunday Ticket slot code
        self.espn_ids: dict[str, str] = {}     # game_id -> ESPN watch id (ESPN+ events)
        # tuners.espnplus_yttv (set by the engine): ESPN+ events through YouTube TV (ESPN Unlimited).
        # A proven per-event watch code wins; else the event is tuned by in-app search. A search that
        # fails marks the event missed for EVENT_MISS_S, and it keeps its old route (fail closed).
        self.espnplus_yttv = False
        self.yttv_events: dict[str, str] = {}  # game_id -> proven YouTube TV watch code of the ESPN+ event
        self.yttv_event_miss: dict[str, float] = {}   # game_id -> when its search last failed (not saved)
        self._event_specs: dict = {}           # game_id -> yttv_event.EventSpec (not saved)
        self.st_codes: list[str] = []          # YouTube TV codes of "NFL Sunday Ticket 1..16" (fixed)
        self.link_notes: dict[str, str] = {}   # game_id -> "blacked out here" etc.
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                d = json.loads(self.path.read_text())
            except ValueError:
                d = {}
            self.codes = d.get("codes", {})
            self.overrides = d.get("overrides", {})
            self.team_rules = d.get("team_rules", {})
            self.prime_gti = d.get("prime_gti", {})
            self.st_slots = d.get("st_slots", {})
            self.espn_ids = d.get("espn_ids", {})
            self.yttv_events = d.get("yttv_events", {})
            self.st_codes = d.get("st_codes", [])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"codes": self.codes, "overrides": self.overrides,
                                   "team_rules": self.team_rules, "prime_gti": self.prime_gti,
                                   "st_slots": self.st_slots, "espn_ids": self.espn_ids,
                                   "st_codes": self.st_codes, "yttv_events": self.yttv_events,
                                   "saved": time.time()}, indent=1))
        tmp.replace(self.path)

    # --- user input -----------------------------------------------------------

    def set_code_from_link(self, channel: str, link: str) -> str:
        """Paste a YouTube TV link (tv.youtube.com/watch/XXXXXXXXXXX?...) for a channel."""
        code = parse_yttv_link(link)
        if not code:
            raise ValueError("That doesn't look like a YouTube TV watch link")
        self.codes[channel] = code
        self.save()
        return code

    def set_st_codes(self, text: str) -> list[str]:
        """Paste the Sunday Ticket channel links (1..16, any order/format); codes kept in paste order."""
        codes = []
        for m in YTTV_LINK.finditer(text or ""):
            if m.group(1) not in codes:
                codes.append(m.group(1))
        if not codes:
            raise ValueError("No YouTube TV links found — copy them from tv.youtube.com's Live guide")
        self.st_codes = codes[:20]
        self.save()
        return self.st_codes

    def set_override(self, game_id: str, target: str | None) -> None:
        if target:
            self.overrides[game_id] = target
        else:
            self.overrides.pop(game_id, None)
        self.save()

    # --- lookup -----------------------------------------------------------------

    def where(self, g: GameState, sunday_ticket: bool = True) -> Where:
        if g.game_id in self.overrides:
            t = self.overrides[g.game_id]
            return Where(t, describe(t), "you set it", self._tunable(t))
        nets = [NETWORKS.get(b.lower().strip()) for b in g.broadcasts]
        nets = [n for n in nets if n]
        lg = g.league
        teams = {g.home.abbr, g.away.abbr}

        if lg in MONUMENTAL_TEAMS and teams & MONUMENTAL_TEAMS[lg] and not (set(nets) - {"leaguepass", "monumental"}):
            return self._prime(g, "monumental", "Monumental (Prime Video)", "likely")

        # National cable first: it's never blacked out.
        for n in nets:
            if n not in BROADCAST and n in NETWORKS.values() and APP_FOR.get(n, "youtubetv") == "youtubetv":
                w = self._yttv(n)
                if not w.tunable and lg == League.NHL and n == "nhlnetwork":
                    # No YouTube TV code for NHL Network: the out-of-market ESPN+ feed if we have it.
                    e = self._espnplus(g)
                    if e.tunable:
                        return e
                return w
        for n in nets:
            if APP_FOR.get(n) == "prime":
                return self._prime(g, n, describe(f"prime:{n}"), "sure" if n == "prime" else "likely")
        if "espnplus" in nets or (lg == League.NHL and not nets):
            return self._espnplus(g)

        if lg == League.NFL and set(nets) & {"cbs", "fox"} and _is_sunday_afternoon(g):
            if teams & LOCAL_TEAMS[League.NFL]:
                return self._yttv(next(n for n in nets if n in ("cbs", "fox")), "likely")
            if sunday_ticket:
                slot = self.st_slots.get(g.game_id)
                if slot:
                    return Where(f"youtubetv:st:{slot}", "Sunday Ticket", "sure", True)
                return Where("youtubetv:st", "Sunday Ticket (channel not matched yet)", "likely", False)
            return Where(None, "not on in your area", "likely", False)

        for n in nets:
            if n in BROADCAST:
                return self._yttv(n)
        if lg == League.NBA:
            # Out-of-market NBA with no national TV: League Pass through Prime.
            return self._prime(g, "leaguepass", describe("prime:leaguepass"), "likely")
        if lg == League.NHL:
            # Out-of-market NHL with only regional TV: ESPN+ (linked into YouTube TV).
            return self._espnplus(g)
        if any(APP_FOR.get(n, "x") is None for n in nets):
            return Where(None, f"only on {', '.join(g.broadcasts)} (not set up)", "sure", False)
        return Where(None, "unknown — set it on the phone", "unknown", False)

    def _espnplus(self, g: GameState) -> Where:
        yt = self._espnplus_yttv(g)
        if yt is not None:
            return yt

        if g.game_id in self.st_slots:
            return Where(f"youtubetv:ev:{self.st_slots[g.game_id]}", "ESPN+ · YouTube TV", "sure", True)
        if g.game_id in self.espn_ids:
            return Where(f"espn:{self.espn_ids[g.game_id]}", "ESPN+ · ESPN app", "likely", True)
        return Where("youtubetv:espnplus", "ESPN+ (event link not found yet)", "likely", False)

    def _espnplus_yttv(self, g: GameState) -> Where | None:
        """ESPN+ through YouTube TV (tuners.espnplus_yttv), or None = the old route (flag off, the
        teams can't be told apart by name, or this event's search failed recently)."""
        if not self.espnplus_yttv:
            return None
        code = self.yttv_events.get(g.game_id)
        if code:
            return Where(f"youtubetv:ev:{code}", "ESPN+ · YouTube TV", "sure", True)
        if time.time() - self.yttv_event_miss.get(g.game_id, 0.0) < EVENT_MISS_S:
            return None
        from redzone.tuners.yttv_event import event_spec
        spec = event_spec(g.game_id, g.home.name or "", g.away.name or "")
        if spec is None:
            return None
        self._event_specs[g.game_id] = spec
        return Where(f"youtubetv:evq:{g.game_id}", "ESPN+ · YouTube TV (by search)", "likely", True)

    def event_missed(self, target: str) -> None:
        """A search tune of this ESPN+ event failed: back to its old route for EVENT_MISS_S."""
        app, _, rest = target.partition(":")
        if app == "youtubetv" and rest.startswith("evq:"):
            self.yttv_event_miss[rest[4:]] = time.time()

    def yttv_event_ref(self, target: str) -> str | None:
        """Tuner ref ('event:...') of an ESPN+ event tuned by search, else None."""
        app, _, rest = target.partition(":")
        if app != "youtubetv" or not rest.startswith("evq:"):
            return None
        spec = self._event_specs.get(rest[4:])
        return spec.ref() if spec is not None else None

    def apply_links(self, g: GameState, found: dict, my_teams: set[str]) -> None:
        """Event links found online (Prime GTIs, ESPN+ watch ids) for one game."""
        prime = found.get("prime")
        if prime and prime.get("gti"):
            feeds = [f for f in [prime] + list(prime.get("alts") or []) if f.get("gti")]
            # Apple's per-IP "playable here" flag is only a hint (it judges wherever the Mac is right
            # now, and it's been wrong both ways), so it ranks feeds but never blocks one on its own.
            # Real blackouts for your market are certain, though: local teams' games.
            local = (g.home.abbr in LOCAL_TEAMS.get(g.league, set())
                     or g.away.abbr in LOCAL_TEAMS.get(g.league, set()))
            if local and g.league == League.NBA:
                self.link_notes[g.game_id] = "blacked out here (local team) — on your regional channel"
                return
            ok = [f for f in feeds if f.get("geo_ok") is not False] or feeds
            side = "home" if g.home.abbr in my_teams else "away" if g.away.abbr in my_teams else None
            # Your team's game: prefer your team's own broadcast (e.g. the Wizards' Monumental feed).
            pick = next((f for f in ok if side and f.get("feed") == side), None) or next(
                (f for f in ok if side and _names_monumental(f, g)), ok[0])
            self.prime_gti[g.game_id] = pick["gti"]
            if all(f.get("geo_ok") is False for f in feeds):
                self.link_notes[g.game_id] = "may be blacked out — check on the TV"
            else:
                self.link_notes.pop(g.game_id, None)
        for prov in ("espnplus", "espn"):
            it = found.get(prov)
            if it and it.get("play_id") and g.game_id not in self.espn_ids:
                self.espn_ids[g.game_id] = str(it["play_id"])

    def _yttv(self, ch: str, conf: str = "sure") -> Where:
        t = f"youtubetv:{ch}"
        return Where(t, describe(t), conf, self._tunable(t))

    def _prime(self, g: GameState, ch: str, label: str, conf: str) -> Where:
        gti = self.prime_gti.get(g.game_id)
        note = self.link_notes.get(g.game_id)
        if gti:
            return Where(f"prime:{gti}", f"{label} — {note}" if note else label, "likely" if note else "sure", True)
        if note:
            return Where(None, f"{label} — {note}", "likely", False)
        return Where(f"prime:{ch}", label + " (event link not found yet)", conf, False)

    def _tunable(self, target: str) -> bool:
        app, _, rest = target.partition(":")
        if app == "youtubetv":
            if rest.startswith(("st:", "ev:")):
                return True
            if rest.startswith("evq:"):
                return self.espnplus_yttv and rest[4:] in self._event_specs
            return rest in self.codes or rest in SEARCH_CHANNELS
        if app == "prime":
            return rest.startswith("amzn1.")
        if app == "espn":
            return bool(rest)
        return False

    def yttv_search_key(self, target: str) -> str | None:
        """A YouTube TV channel we have no watch code for but can reach by in-app search
        (SEARCH_CHANNELS): its channel key, else None."""
        app, _, rest = target.partition(":")
        if app != "youtubetv" or rest.startswith(("st:", "ev:")) or rest in self.codes:
            return None
        return rest if rest in SEARCH_CHANNELS else None

    def yttv_code(self, target: str) -> str | None:
        app, _, rest = target.partition(":")
        if app != "youtubetv":
            return None
        if rest.startswith(("st:", "ev:")):
            return rest[3:]
        return self.codes.get(rest)


# YouTube TV channels with no known watch code that tune by in-app search instead (~15-20 s):
# search -> the channel (or its network page -> Live -> the live tile), confirmed by the media
# session naming the channel. NBCSN proved by hand on the 4K Plus, Nov 3.
# What to type for each (the channel table's own search name where it works; "WRAL" finds WRAL
# news, while "NBC" opens the NBC network page whose Live row is the local station, Nov 3).
SEARCH_QUERY = {"nbc": "NBC"}
EVENT_MISS_S = 600.0   # an ESPN+ event whose YouTube TV search failed keeps its old route this long
SEARCH_CHANNELS = frozenset({"nbcsn", "nbc"})   # both proven on the stick (Nov 3). Not yet: NHL Network (not in this lineup: Sports Plus add-on)

YTTV_LINK = re.compile(r"tv\.youtube\.com/watch/([A-Za-z0-9_-]{11})")


def parse_yttv_link(link: str) -> str | None:
    m = YTTV_LINK.search(link or "")
    if m:
        return m.group(1)
    link = (link or "").strip()
    return link if re.fullmatch(r"[A-Za-z0-9_-]{11}", link) else None


PRETTY = {"espn": "ESPN", "espn2": "ESPN2", "espnu": "ESPNU", "abc": "ABC", "fox": "FOX", "fs1": "FS1",
          "cbs": "CBS", "nbc": "NBC", "nflnetwork": "NFL Network", "tnt": "TNT", "trutv": "truTV",
          "tbs": "TBS", "nbatv": "NBA TV", "nhlnetwork": "NHL Network", "mlbnetwork": "MLB Network",
          "usa": "USA", "redzone": "NFL RedZone", "st": "Sunday Ticket", "cbssn": "CBS Sports Net",
          "fs2": "FS2", "espnews": "ESPNews", "nbcsn": "NBCSN", "accn": "ACC Network",
          "secn": "SEC Network", "btn": "Big Ten Network", "cw": "The CW"}

# Channels to ask for in the setup helper, most useful first.
SETUP_CHANNELS = ["espn", "espn2", "abc", "cbs", "fox", "nbc", "nflnetwork", "redzone", "nbcsn", "tnt", "trutv",
                  "tbs", "nbatv", "nhlnetwork", "fs1", "mlbnetwork", "usa", "espnu", "cbssn",
                  "accn", "secn", "btn"]


def describe(target: str | None) -> str:
    if not target:
        return "unknown"
    app, _, rest = target.partition(":")
    if app == "youtubetv":
        if rest.startswith("st"):
            return "Sunday Ticket"
        if rest.startswith("evq:"):
            return "ESPN+ · YouTube TV (by search)"
        return f"{PRETTY.get(rest, rest.upper())} · YouTube TV"
    if app == "prime":
        return {"leaguepass": "NBA League Pass · Prime", "monumental": "Monumental · Prime"}.get(rest, "Prime Video")
    if app == "espn":
        return "ESPN+ · ESPN app"
    return target


def _is_sunday_afternoon(g: GameState) -> bool:
    if not g.start_time:
        return False
    kickoff = datetime.fromtimestamp(g.start_time, tz=ZoneInfo("America/New_York"))
    return kickoff.weekday() == 6 and 12 <= kickoff.hour < 19


ET = ZoneInfo("America/New_York")
REDZONE_TARGET = "youtubetv:redzone"


def in_redzone_window(g: GameState) -> bool:
    """RedZone covers the Sunday 1pm and 4pm ET NFL windows, not night games or London mornings."""
    if g.league != League.NFL or not g.start_time:
        return False
    kick = datetime.fromtimestamp(g.start_time, tz=ET)
    return kick.weekday() == 6 and 12 <= kick.hour < 17


def redzone_on(games) -> bool:
    """NFL RedZone is worth tuning only while it whips around: two or more afternoon games live."""
    return sum(1 for g in games if g.phase == Phase.LIVE and in_redzone_window(g)) >= 2


def _team_words(t) -> set[str]:
    words = {t.abbr.lower()}
    for part in (t.name or "").lower().replace(".", "").split():
        if len(part) > 2:
            words.add(part)
    return words


def match_title(text: str, games: list[GameState]) -> GameState | None:
    """Which game is this on-screen title about? Both teams must be mentioned; best score wins."""
    import re as _re
    words = set(_re.findall(r"[a-z0-9]+", (text or "").lower()))
    best, best_score = None, 0
    for g in games:
        h, a = _team_words(g.home) & words, _team_words(g.away) & words
        if h and a:
            score = len(h) + len(a)
            if score > best_score:
                best, best_score = g, score
    return best
