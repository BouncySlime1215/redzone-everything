"""Provider candidates: installed apps, learned access failures, and per-game routes.

Discovery after the owner selects and signs into their devices:
1. Installed?   One `pm list packages` per stick (every 30 min) says which provider apps
                exist (roles_from_packages).  Package names are looked up, never assumed.
2. Subscribed?  Assumed yes when the app is installed.  When a tune lands on a sign-in or
                sign-up screen (classify_screen), ProviderBook.learn() marks it: that game's
                link is skipped for a day, and the app counts as "signed out" / "not
                subscribed" for a while; it is re-tried on its own later.
3. Which one?   best_source() ranks every way to watch a game: something we can tune now,
                never a blacked-out feed, your team's own broadcast for your teams (the
                national feed otherwise), then YouTube TV (fast, and it can rewind for
                catch-up replays) > Prime Video > ESPN > Peacock > Fubo > the rest.

The guardrail that matters most lives here too: is_purchase_screen().  Tuners must never
press OK on a screen offering a purchase, trial or plan -- on Fire TV a blind OK there has
started a paid subscription for other people automating these apps.

Everything is pure except ProviderBook's file and scan_apps (one adb call, never raises).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Mapping

from redzone.channels import APP_FOR, LOCAL_TEAMS, MONUMENTAL_TEAMS, NETWORKS, PRETTY, ChannelMap, Where, describe
from redzone.models import GameState, League

log = logging.getLogger(__name__)

DATA = Path(__file__).resolve().parent.parent.parent / "data"


# --- the providers ------------------------------------------------------------------

@dataclass(frozen=True)
class Provider:
    key: str                      # target prefix and tuner name: "peacock" -> "peacock:<pvid>"
    name: str                     # what the phone says
    packages: tuple[str, ...]     # Fire TV build first, then Android TV builds (checked in order)
    hints: tuple[str, ...] = ()   # package-name fragments, for builds nobody has listed yet
    rewind: bool = False          # can rewind live TV (catch-up replays need it)


# Preference order = list order.  Package names: see the report table (Fire TV builds were
# confirmed by people running ADBTuner/FruitDeepLinks, APKMirror, Home Assistant users).
PROVIDERS: tuple[Provider, ...] = (
    Provider("youtubetv", "YouTube TV", ("com.amazon.firetv.youtube.tv",
                                         "com.google.android.youtube.tvunplugged"),
             ("tvunplugged",), rewind=True),
    Provider("prime", "Prime Video", ("com.amazon.firebat", "com.amazon.avod.thirdpartyclient",
                                      "com.amazon.amazonvideo.livingroom", "com.amazon.avod")),
    Provider("espn", "ESPN", ("com.espn.gtv", "com.espn.score_center", "com.espn.androidtv",
                              "com.espn.espnplus"), ("espn",)),
    Provider("peacock", "Peacock", ("com.peacock.peacockfiretv", "com.peacocktv.peacockandroid"),
             ("peacock",)),
    Provider("fubo", "Fubo", ("com.fubo.firetv.screen",), ("fubo",)),
    Provider("paramount", "Paramount+", ("com.cbs.ott",), ("paramountplus",)),
    Provider("max", "HBO Max", ("com.hbo.hbonow", "com.wbd.stream"), ("com.hbo.",)),
    Provider("netflix", "Netflix", ("com.netflix.ninja",), ("netflix",)),
    Provider("nflplus", "NFL app", ("com.gotv.nflgamecenter.us.lite",), ("nflgamecenter",)),
    Provider("nba", "NBA app", ("com.nbaimd.gametime.nba2011.amazon", "com.nbaimd.gametime.nba2011"),
             ("com.nbaimd.",)),
    Provider("appletv", "Apple TV", ("com.apple.atve.amazon.appletv", "com.apple.atve.androidtv.appletv"),
             ("com.apple.atve.",)),
    Provider("dazn", "DAZN", ("com.dazn",), ("dazn",)),
    Provider("foxone", "FOX One", ("com.fox.foxone",), ("foxone",)),
)
BY_KEY = {p.key: p for p in PROVIDERS}
RANK = {p.key: i for i, p in enumerate(PROVIDERS)}
# Apps a tuner exists for (built-in: youtubetv/prime/espn; the rest: redzone/tuners/providers.py).
TUNER_APPS = frozenset({"youtubetv", "prime", "espn", "peacock", "fubo", "paramount", "max",
                        "netflix", "nflplus", "nba", "appletv", "dazn"})
# eventlinks provider -> app that plays it.  TV-Everywhere apps (NBC Sports, CBS Sports, TNT,
# truTV, Marquee) are left out on purpose: the same channels are on YouTube TV.
LINK_APPS = {"prime": "prime", "espn": "espn", "espnplus": "espn", "peacock": "peacock",
             "paramount": "paramount", "max": "max", "nflplus": "nflplus", "nba": "nba",
             "dazn": "dazn", "appletv": "appletv", "netflix": "netflix", "fubo": "fubo"}
# Fubo station ids for national channels (community-shared map on the Channels DVR forum;
# ESPN 10179 also tuned in ~12 s in a 2025 ADBTuner log).  Locals differ per market: absent.
FUBO_CHANNELS = {"espn": "10179", "espn2": "12444", "espnews": "16485", "espnu": "71094",
                 "fs1": "94653", "fs2": "69553", "nflnetwork": "45409", "nbatv": "45537",
                 "mlbnetwork": "72650", "cbssn": "69495"}
# Channel keys (redzone.channels.NETWORKS values) that carry a game nationally.
NATIONAL_NETS = frozenset({"espn", "espn2", "espnu", "espnews", "abc", "tnt", "trutv", "tbs", "nbc",
                           "nbcsn", "peacock", "prime", "fox", "fs1", "fs2", "cbs", "cbssn",
                           "nflnetwork", "nbatv", "nhlnetwork", "mlbnetwork", "usa", "netflix",
                           "appletv", "max", "accn", "secn", "btn", "cw"})
# Apps used only when nothing else that isn't risky can play the game.  Everything on Peacock
# is simulcast on NBC / NBCSN, which YouTube TV carries (and can rewind): the owner never
# needs Peacock, so it is the last resort.
LAST_RESORT = frozenset({"peacock"})
# YouTube TV channels that carry Peacock's games, best guess first (NBCSN carries Peacock's
# NBA/NHL nights since its Nov 2025 relaunch; NBC the big ones).
PEACOCK_ON_YTTV = ("nbcsn", "nbc")
# Broadcast-only apps (no event link found yet): say where it is even if we can't tune it.
LABEL_ONLY = {"peacock": "Peacock", "netflix": "Netflix", "appletv": "Apple TV", "max": "HBO Max"}


def roles_from_packages(installed: Iterable[str]) -> dict[str, str]:
    """provider key -> installed package.  Exact candidates first (in order), then hints.

    Pure: feed it the package names from `pm list packages`.  YouTube (not TV) and the
    Fire TV system apps never match because YouTube TV needs an exact name or "tvunplugged".
    """
    pkgs = sorted({p.strip() for p in installed or () if isinstance(p, str) and p.strip()})
    out: dict[str, str] = {}
    for prov in PROVIDERS:
        hit = next((c for c in prov.packages if c in pkgs), None)
        if hit is None:
            hit = next((p for p in pkgs for h in prov.hints if h in p), None)
        if hit:
            out[prov.key] = hit
    return out


def parse_package_list(text: str) -> list[str]:
    """`pm list packages` output -> package names."""
    return re.findall(r"package:([\w.]+)", text or "")


async def scan_apps(stick) -> dict[str, str]:
    """Which provider apps are on this stick: one `pm list packages` (10 s cap).
    Never raises: a failed or hung adb call returns {} (= "don't know", not "none")."""
    try:
        r = await stick.shell("pm list packages", timeout=10)
    except Exception as e:  # a broken stick must not break the app
        log.debug("scan_apps %s failed: %s", getattr(stick, "host", "?"), e)
        return {}
    if not getattr(r, "ok", False):
        return {}
    return roles_from_packages(parse_package_list(r.out))


def merge_roles(packages: dict[str, str], found: Mapping[str, str]) -> list[str]:
    """Add newly found roles to a stick's role->package map.  Existing roles are kept:
    Stick.identify() and the setup doctor already chose those."""
    added = [k for k, v in found.items() if v and k not in packages]
    for k in added:
        packages[k] = found[k]
    return added


# --- reading the screen ---------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """Why a tune didn't show video, read off the TV screen."""
    kind: str      # "signin" | "upsell" | "blackout" | "error"
    phrase: str    # the words that gave it away (shown to you)

    @property
    def stops(self) -> bool:
        """Retrying can't fix these; try another source instead."""
        return self.kind in ("signin", "upsell", "blackout")

    @property
    def step(self) -> str:
        # "blackout" must stay in the text: Controller._blackout_check looks for it.
        return f"{_MARK_STEP[self.kind]}: {self.phrase}"

    def message(self, name: str) -> str:
        return f"{name}: {_MARK[self.kind]} — it showed “{self.phrase}”"


_MARK = {"signin": "signed out", "upsell": "not subscribed", "blackout": "blacked out",
         "error": "app error"}
_MARK_STEP = {"signin": "signed out", "upsell": "not subscribed", "blackout": "blackout",
              "error": "app error"}
_MARK_RE = re.compile(r"(signed out|not subscribed|blacked out) — it showed “([^”]*)”")


def _rx(*phrases: str) -> re.Pattern:
    return re.compile(r"(?<![a-z])(" + "|".join(phrases) + r")(?![a-z])")


_BLACKOUT_RX = _rx(r"blackout", r"blacked out", r"regional restrictions?", r"due to league restrictions",
                   r"not available in your (?:location|area|region)", r"outside (?:of )?your (?:home )?area",
                   r"(?:isn't|is not|not) available (?:in your area|where you are|here)",
                   r"unavailable in your (?:area|region|location)", r"geographic(?:al)? restrictions?",
                   r"unavailable to watch in your (?:location|area|region)",
                   r"currently unavailable in your (?:location|area|region)",
                   r"not available to watch in your (?:location|area|region)")
_UPGRADE_RX = _rx(r"upgrade(?: now| your plan| to [a-z+ ]+)?", r"change your plan",
                  r"(?:choose|select|pick) (?:a|your) plan", r"(?:not|isn't) (?:included|part of) (?:in )?your plan",
                  r"requires? (?:a )?(?:premium|subscription|an upgrade)", r"subscription required",
                  r"add (?:this )?channel", r"subscribe to watch", r"(?:get|buy) (?:nba )?league pass")
_SIGNIN_RX = _rx(r"sign in", r"signin", r"log in", r"login", r"activate (?:your )?device",
                 r"activation code", r"enter (?:the )?code", r"(?:you've|you have) been signed out",
                 r"session (?:has )?expired", r"(?:choose|select) your tv provider",
                 r"sign in with your tv provider", r"link your account")
_TRIAL_RX = _rx(r"start (?:your )?free trial", r"free trial", r"try (?:it )?free", r"sign up",
                r"join now", r"subscribe(?: now)?", r"get started", r"create (?:an )?account",
                r"see plans", r"view plans")
_ERROR_RX = _rx(r"something went wrong", r"error code", r"playback error", r"unable to play",
                r"can't play this", r"cannot play", r"try again later", r"video (?:is )?(?:currently )?unavailable",
                r"content (?:is )?unavailable")
# Anything that could cost money if OK were pressed.  Deliberately broad: when in doubt, don't.
_PURCHASE_RX = re.compile(
    r"(?<![a-z])(free trial|try (?:it )?free|subscribe|sign up|join now|upgrade|buy|rent|purchase|"
    r"choose (?:a|your) plan|select (?:a|your) plan|see plans|view plans|add (?:this )?channel|"
    r"start your|per month|per year|a month|/mo|/month|/yr|billed|payment|credit card|"
    r"place (?:your )?order|1-click|one-click|confirm (?:and|&) pay|accept and continue)(?![a-z])"
    r"|\$\s?\d")


def normalize_text(text: str) -> str:
    """Lowercase, straight apostrophes, single spaces: TV apps print "Who’s watching?"."""
    return re.sub(r"\s+", " ", (text or "").lower().replace("’", "'").replace("‘", "'"))


def classify_screen(text: str) -> Verdict | None:
    """What's on screen when video didn't start: blackout, sign-up wall, sign-in wall, error.

    Order matters: a blackout notice often sits next to a "Sign in" button, and an upgrade
    prompt next to "Free trial"; the most specific reason wins.  None = nothing recognized
    (a loading screen, a profile picker, a detail page) -- the tuner keeps trying.
    """
    t = normalize_text(text)
    if not t:
        return None
    for kind, rx in (("blackout", _BLACKOUT_RX), ("upsell", _UPGRADE_RX), ("signin", _SIGNIN_RX),
                     ("upsell", _TRIAL_RX), ("error", _ERROR_RX)):
        m = rx.search(t)
        if m:
            return Verdict(kind, m.group(1))
    return None


def is_purchase_screen(text: str) -> bool:
    """True if pressing OK on this screen might buy, rent or start a trial/subscription.
    Tuners check this before EVERY button press they make on their own."""
    return bool(_PURCHASE_RX.search(normalize_text(text)))


def parse_verdict(detail: str) -> Verdict | None:
    """Find the reason a tuner wrote into its error/steps (see Verdict.message)."""
    m = _MARK_RE.search(detail or "")
    if not m:
        return None
    kind = {"signed out": "signin", "not subscribed": "upsell", "blacked out": "blackout"}[m.group(1)]
    return Verdict(kind, m.group(2))


# --- sources and the choice -----------------------------------------------------------

@dataclass(frozen=True)
class Source:
    """One way to watch one game."""
    provider: str                 # app key: youtubetv | prime | espn | peacock | fubo | ...
    target: str | None            # what Controller.execute() takes: "<app>:<ref>"; None = not found yet
    label: str                    # plain English: "ESPN · YouTube TV", "Peacock"
    tunable: bool                 # we have everything needed to send the TV there
    kind: str = "event"           # "event" = this exact game | "channel" = a channel showing it
    feed: str | None = None       # national | home | away | alt | mobile | None (unknown)
    team: str | None = None       # whose broadcast this is (home/away feeds)
    geo_ok: bool | None = None    # Apple's per-IP "playable here" hint (weak)
    blackout: bool = False        # known blacked out here: never picked
    confidence: str = "likely"    # sure | likely | unknown | you set it
    note: str = ""


@dataclass
class Pick(Where):
    """A Where (target, label, confidence, tunable) plus why it won and what else exists."""
    provider: str = ""
    reasons: list[str] = field(default_factory=list)
    alternates: list[Source] = field(default_factory=list)

    @property
    def why(self) -> str:
        return " ".join(self.reasons)


@dataclass(frozen=True)
class ProviderState:
    key: str
    installed: bool | None = None   # None = no stick scanned yet: unknown is not "absent"
    subscribed: bool | None = None  # None = assumed yes (the default)
    why: str = ""                   # plain English reason when not usable
    package: str | None = None
    sticks: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()   # sticks where it's signed out / not subscribed (others may work)

    @property
    def usable(self) -> bool:
        return self.installed is not False and self.subscribed is not False

    def why_not(self) -> str:
        name = _name(self.key)
        if self.subscribed is False and self.why:
            return self.why
        if self.installed is False:
            return f"{name} isn't installed on your Fire TV sticks"
        if self.subscribed is False:
            return self.why or f"{name}: not subscribed"
        return ""


def _name(key: str) -> str:
    p = BY_KEY.get(key)
    return p.name if p else key


def _nets(g: GameState) -> list[str]:
    """ESPN broadcast names -> channel keys ("ESPN" -> "espn"), unknown ones dropped."""
    out = []
    for b in g.broadcasts or []:
        n = NETWORKS.get(str(b).lower().strip())
        if n and n not in out:
            out.append(n)
    return out


def _my_side(g: GameState, my_teams) -> str | None:
    mine = _my_abbrs(g, my_teams)
    if g.home.abbr in mine:
        return "home"
    if g.away.abbr in mine:
        return "away"
    return None


def _my_abbrs(g: GameState, my_teams) -> set[str]:
    """my_teams may be {"WSH"} or {"nba": ["WSH"], ...} (the config's shape)."""
    if not my_teams:
        return set()
    if isinstance(my_teams, Mapping):
        lg = g.league.value if isinstance(g.league, League) else str(g.league)
        return {str(t) for t in my_teams.get(lg, ()) or ()}
    return {str(t) for t in my_teams}


def _national_elsewhere(g: GameState) -> bool:
    """NBA game on national TV somewhere other than Prime itself: League Pass (Prime) feeds
    are blacked out in the US during those windows."""
    return any(n in NATIONAL_NETS and n != "prime" for n in _nets(g))


def _feed_rank(s: Source, side: str | None) -> int:
    """Your team's broadcast first (for your teams), else national > home > away; feeds we
    can't place next; alt-casts / Spanish feeds and phone-only "mobile" feeds last."""
    if s.feed == "alt":
        return 5
    if s.feed == "mobile":
        return 6
    if side:
        return 0 if s.feed == side else {"national": 1, None: 2}.get(s.feed, 3)
    return {"national": 0, "home": 1, "away": 2, None: 3}.get(s.feed, 4)


def _risky(s: Source, g: GameState, side: str | None) -> bool:
    """Might be blacked out: Apple says not playable from here, or a League Pass feed while
    the game is on national TV.  Risky sources are used only when nothing safer exists."""
    if s.geo_ok is False:
        return True
    mine = side is not None and s.feed == side
    return (s.provider == "prime" and g.league == League.NBA and not mine
            and s.kind == "event" and _national_elsewhere(g))


def _rank_key(s: Source, g: GameState, side: str | None) -> tuple:
    return (0 if s.tunable else 1, 1 if _risky(s, g, side) else 0,
            1 if s.provider in LAST_RESORT else 0, _feed_rank(s, side),
            RANK.get(s.provider, len(RANK)), 0 if s.kind == "event" else 1)


def _states(available) -> dict[str, ProviderState]:
    """Accept ProviderBook.states(), a {key: bool} map, a set of usable keys, or None (= all)."""
    if available is None:
        return {}
    if isinstance(available, Mapping):
        out = {}
        for k, v in available.items():
            out[k] = v if isinstance(v, ProviderState) else ProviderState(k, installed=bool(v))
        return out
    keys = {str(k) for k in available}
    return {p.key: ProviderState(p.key, installed=p.key in keys) for p in PROVIDERS}


def _why_pick(s: Source, side: str | None) -> str:
    if side and s.feed == side:
        return "your team's own broadcast"
    if s.provider == "youtubetv" and s.note.startswith("Peacock"):
        return s.note
    if s.provider == "youtubetv":
        return "preferred provider for channel playback and configured rewind support"
    if s.feed == "national":
        return "the national broadcast"
    if s.kind == "event":
        return f"a matched event link was found for {_name(s.provider)}"
    return f"{_name(s.provider)} carries this channel"


def best_source(game: GameState, candidates: Iterable[Source], available=None,
                my_teams=None) -> Pick:
    """Pick the best way to watch `game` from `candidates`, with reasons.

    available: ProviderBook.states() (or a set of usable provider keys; None = assume all).
    my_teams:  {"WSH"} or the config's {"nba": ["WSH"], ...}.

    Order: tunable now > not risky (Apple geo flag / League Pass during national TV) > your
    team's own broadcast (national for other games; alt-casts and Spanish feeds last) >
    provider (YouTube TV, Prime Video, ESPN, Peacock, Fubo, others) > exact event link.
    Never picks a source marked blacked out, or an app that isn't installed/subscribed.
    A choice you made on the phone ("you set it") always wins untouched.
    """
    cands = [c for c in candidates or () if isinstance(c, Source)]
    for c in cands:
        if c.confidence == "you set it":
            return Pick(c.target, c.label, c.confidence, c.tunable, provider=c.provider,
                        reasons=["You picked this on the phone."])
    states = _states(available)
    side = _my_side(game, my_teams)
    keep, skipped = [], []
    for c in cands:
        st = states.get(c.provider)
        if st is not None and not st.usable:
            skipped.append((c, st.why_not()))
        elif c.blackout:
            skipped.append((c, f"{c.label}: blacked out here"))
        else:
            keep.append(c)
    if not keep:
        return _nothing(skipped)
    ranked = sorted(keep, key=lambda c: _rank_key(c, game, side))
    return _pick(ranked, skipped, game, side)


def _nothing(skipped: list[tuple[Source, str]]) -> Pick:
    if not skipped:
        return Pick(None, "unknown — set it on the phone", "unknown", False,
                    reasons=["No way to watch this game was found yet."])
    why = list(dict.fromkeys(w for _, w in skipped))
    return Pick(None, why[0], "likely", False, reasons=[f"{w}." for w in why[:4]])


def _pick(ranked: list[Source], skipped, game: GameState, side: str | None) -> Pick:
    best = ranked[0]
    risky = _risky(best, game, side)
    label = best.label + (" — may be blacked out" if risky and "black" not in best.label.lower() else "")
    conf = "likely" if risky and best.confidence == "sure" else best.confidence
    reasons = [f"Watching on {best.label}: {_why_pick(best, side)}."]
    if not best.tunable:
        reasons[0] = f"Best option is {best.label}, but it can't be tuned yet ({best.note or 'link not found yet'})."
    if risky:
        reasons.append("It may be blacked out here; nothing safer was found.")
    # Only explain skips that would otherwise have won (a better-ranked app), once per app.
    better = {c.provider for c, _ in skipped if _rank_key(c, game, side) < _rank_key(best, game, side)}
    for c, why in skipped:
        if c.provider in better:
            reasons.append(f"{why or c.label}.")
            better.discard(c.provider)
    alts = [c for c in ranked[1:] if c.target != best.target][:5]
    return Pick(best.target, label, conf, best.tunable, provider=best.provider,
                reasons=reasons[:5], alternates=alts)


# --- building candidates ----------------------------------------------------------------

def source_from_where(w: Where | None, g: GameState) -> Source | None:
    """ChannelMap.where()'s answer as a Source (YouTube TV channel, Sunday Ticket slot,
    Prime GTI, ESPN id, or whatever you set on the phone)."""
    if w is None or not getattr(w, "target", None):
        return None
    app, _, rest = w.target.partition(":")
    event = (app == "youtubetv" and rest.startswith("ev:")) or (app in ("prime", "espn") and w.tunable) \
        or w.confidence == "you set it"
    team = feed = None
    if app == "youtubetv" and not rest.startswith(("ev:", "espnplus")):
        feed = "national"
    if "monumental" in (w.label or "").lower():
        mon = MONUMENTAL_TEAMS.get(g.league, set())
        feed = "home" if g.home.abbr in mon else "away" if g.away.abbr in mon else None
        team = g.home.abbr if feed == "home" else g.away.abbr if feed == "away" else None
    return Source(app, w.target, w.label, bool(w.tunable), "event" if event else "channel",
                  feed=feed, team=team, confidence=w.confidence,
                  note="" if w.tunable else "channel or link not found yet")


def sources_from_links(g: GameState, links: Mapping[str, list[dict]], skips: Mapping[str, dict] | None = None
                       ) -> list[Source]:
    """Event links (eventlinks.match output, compacted by ProviderBook.remember) -> Sources.
    Every feed is a candidate; ranking decides.  `skips` = targets learned to be blacked out
    or behind a paywall (ProviderBook.skips)."""
    out: list[Source] = []
    for prov, recs in (links or {}).items():
        app = LINK_APPS.get(prov)
        if app is None:
            continue
        for rec in recs or []:
            src = _link_source(g, prov, app, rec)
            if src is not None:
                out.append(apply_skip(src, skips))
    return out


def _link_ref(app: str, rec: dict) -> str | None:
    if app == "appletv":
        return rec.get("event_url") or None
    if app == "prime":
        return rec.get("gti") or rec.get("ref")
    if app == "espn":
        return rec.get("play_id") or rec.get("ref")
    return rec.get("ref")


def _link_label(prov: str, app: str, rec: dict) -> str:
    base = "ESPN+ · ESPN app" if prov == "espnplus" else "ESPN app" if app == "espn" else _name(app)
    extra = rec.get("feed_name") or (rec.get("title") if rec.get("alt") else None)
    return f"{base} — {extra}" if extra else base


# Alternate camera / alt-cast feeds of the same game (Peacock "Performance View", "Action
# Cams", iso cams, ManningCast, Spanish): rank last, never as the main broadcast.
_ALT_TITLE_RX = _rx(r"performance view", r"action cams?", r"cam:", r"iso(?: cams?)?", r"alt(?:ernate)? ?cast",
                    r"en espa[nñ]ol", r"manningcast", r"with peyton", r"deportes")


def _link_source(g: GameState, prov: str, app: str, rec: dict) -> Source | None:
    if not isinstance(rec, dict):
        return None
    ref = _link_ref(app, rec)
    target = f"{app}:{ref}" if ref else None
    alt = bool(rec.get("alt")) or (rec.get("lang") not in (None, "en")) \
        or bool(_ALT_TITLE_RX.search(" ".join(str(rec.get(k) or "") for k in ("title", "feed_name", "network")).lower()))
    feed = "alt" if alt else rec.get("feed")
    team = g.home.abbr if feed == "home" else g.away.abbr if feed == "away" else None
    tunable = bool(ref) and app in TUNER_APPS
    sure = tunable and rec.get("geo_ok") is not False and not alt
    if app == "prime" and g.league == League.NHL and "prime" not in _nets(g):
        sure = False   # usually a paid Prime Video Channel (NESN, Scripps), not Prime's own game
    return Source(app, target, _link_label(prov, app, rec), tunable, "event", feed=feed, team=team,
                  geo_ok=rec.get("geo_ok"), confidence="sure" if sure else "likely",
                  note="" if tunable else "no tuner for this app yet")


def channel_sources(g: GameState, have: Iterable[str] = ()) -> list[Source]:
    """Channel-level fallbacks from the game's TV listing: Fubo (national channels it
    carries) and label-only entries ("on Peacock") for apps with no event link yet."""
    out: list[Source] = []
    nets = _nets(g)
    for n in nets:
        ch = FUBO_CHANNELS.get(n)
        if ch:
            out.append(Source("fubo", f"fubo:{ch}", f"{PRETTY.get(n, n.upper())} · Fubo", True,
                              "channel", feed="national", confidence="likely"))
    have = set(have)
    for n in nets:
        if n in LABEL_ONLY and n not in have:
            out.append(Source(n, None, f"{LABEL_ONLY[n]} (game link not found yet)", False, "channel",
                              feed="national", confidence="likely", note="game link not found yet"))
    return out


def apply_skip(s: Source, skips: Mapping[str, dict] | None) -> Source:
    """A target learned to be blacked out stays a candidate only to explain the skip; one
    behind a paywall or failing twice is kept but not tunable (so others win)."""
    skip = (skips or {}).get(s.target or "") or {}
    if skip.get("kind") == "blackout":
        return replace(s, blackout=True)
    if skip.get("kind") in ("upsell", "failed"):
        return replace(s, tunable=False, note=str(skip.get("why") or "skipped for now"))
    return s


def _merge(o: Source, c: Source) -> Source:
    """Same target from two places: keep the first, fill in what it didn't know."""
    return replace(o, feed=o.feed if o.feed is not None else c.feed, team=o.team or c.team,
                   geo_ok=o.geo_ok if o.geo_ok is not None else c.geo_ok,
                   blackout=o.blackout or c.blackout, tunable=o.tunable and c.tunable,
                   note=o.note or c.note)


def gather_sources(g: GameState, base: Where | None, links: Mapping[str, list[dict]] | None = None,
                   skips: Mapping[str, dict] | None = None,
                   channel_map: ChannelMap | None = None, sunday_ticket: bool = True) -> list[Source]:
    """Every candidate for one game: ChannelMap's answer first, then event links, then
    channel fallbacks.  Duplicate targets are merged; learned skips are applied to all."""
    found = sources_from_links(g, links or {}, skips)
    first = source_from_where(base, g)
    if first and first.provider == "prime" and first.feed in ("home", "away"):
        # "Monumental" comes from the listing, not the chosen GTI: only call it your team's own
        # broadcast when that feed's link record agrees.
        rec = next((c for c in found if c.target == first.target), None)
        if rec is not None and rec.feed != first.feed and "monumental" not in rec.label.lower():
            first = replace(first, feed=rec.feed, team=rec.team, label=first.label + " — feed unverified")
    cands = ([first] if first else []) + found
    if channel_map is not None:
        # A mixed listing (e.g. ESPN+ and ABC) must not hide another usable
        # channel behind ChannelMap.where's first answer. Reuse its per-game
        # market/Sunday Ticket rules rather than assuming every local affiliate
        # carries every game listed on that network.
        for broadcast in g.broadcasts:
            net = NETWORKS.get(broadcast.lower().strip())
            if net and APP_FOR.get(net, "youtubetv") == "youtubetv":
                route = channel_map.where(replace(g, broadcasts=[broadcast]), sunday_ticket=sunday_ticket)
                candidate = source_from_where(route, g)
                if candidate:
                    cands.append(candidate)
    cands += peacock_on_yttv(g, channel_map, {c.target for c in cands if c.target})
    cands += channel_sources(g, have={c.provider for c in cands if c.target})
    out: dict[str, Source] = {}
    for c in cands:
        key = c.target or f"{c.provider}:?"
        out[key] = _merge(out[key], c) if key in out else c
    return [_game_skip(g, apply_skip(c, skips), skips) for c in out.values()]


def peacock_on_yttv(g: GameState, channel_map: ChannelMap | None, have=()) -> list[Source]:
    """A game on Peacock is also on NBC / NBCSN: offer those YouTube TV channels (only the
    ones whose channel codes we have), so Peacock stays the last resort."""
    if channel_map is None or "peacock" not in _nets(g):
        return []
    out, searched = [], []
    for ch in PEACOCK_ON_YTTV:
        t = f"youtubetv:{ch}"
        if t in have:
            continue
        note = f"Peacock games air on {PRETTY.get(ch, ch)}"
        if channel_map.yttv_code(t):
            out.append(Source("youtubetv", t, describe(t), True, "channel", feed="national",
                              confidence="likely", note=note))
        elif channel_map.yttv_search_key(t):
            # No code, but reachable by in-app search (~15-20 s): after every channel with a code.
            searched.append(Source("youtubetv", t, describe(t), True, "channel", feed="national",
                                   confidence="likely", note=note + " (tuned by search)"))
    return out + searched


LEAGUE_PASS_APPS = frozenset({"prime", "nba", "dazn"})
ESPNPLUS_SKIP = "espn:tier:espnplus"   # skips key: the ESPN app's ESPN+ tier isn't subscribed


def _local_blackout(g: GameState) -> bool:
    """A local team's NBA game on League Pass is always blacked out here (unless Prime itself
    is the national broadcaster)."""
    local = LOCAL_TEAMS.get(g.league, set())
    return (g.league == League.NBA and (g.home.abbr in local or g.away.abbr in local)
            and "prime" not in _nets(g))


def game_skip_key(app: str, game_id: str) -> str:
    return f"{app}:game:{game_id}"


def _game_skip(g: GameState, s: Source, skips: Mapping[str, dict] | None) -> Source:
    """Skips learned for one app's feeds of this game apply to all its event feeds: a blacked-out
    game is blacked out on every GTI, and after a failed tune the feeds Apple already flagged
    as not playable here are not worth trying one by one."""
    if s.kind != "event" or not s.target:
        return s
    plus = (skips or {}).get(ESPNPLUS_SKIP) or {}
    if plus.get("kind") == "upsell" and s.provider == "espn" and s.label.startswith("ESPN+"):
        return replace(s, tunable=False, note=str(plus.get("why") or "ESPN+ not subscribed"))
    if s.provider in LEAGUE_PASS_APPS and _local_blackout(g):
        return replace(s, blackout=True, note="blacked out here (local team)")
    skip = (skips or {}).get(game_skip_key(s.provider, g.game_id)) or {}
    if skip.get("kind") == "blackout":
        return replace(s, blackout=True)
    if skip.get("kind") == "failed" and s.geo_ok is False:
        return replace(s, tunable=False, note="likely blacked out (Apple flag + a failed tune)")
    return s


# --- the book: installed apps, subscriptions, learned skips, per-game links ------------

SCAN_EVERY_S = 1800.0          # re-list each stick's apps every 30 minutes
LINKS_KEEP_S = 36 * 3600.0     # forget a game's links 36 h after it started
RECHECK_S = {"signin": 6 * 3600.0, "upsell": 3 * 86400.0}   # auto marks expire: try again later
SKIP_S = {"upsell": 86400.0, "blackout": 36 * 3600.0, "failed": 900.0}
FAILS_BEFORE_SKIP = 2          # a target that fails twice in 15 min is skipped for 15 min
MULTI_TIER = frozenset({"youtubetv", "prime", "espn"})   # add-ons vary per game: never app-wide upsell


class ProviderBook:
    """What's installed on which stick, what's subscribed, links skipped after a bad tune,
    and every provider's event links per game.  Saved to data/providers.json.

    Owner overrides (set_subscribed(..., by="you")) never expire; automatic marks do, so an
    app you sign into or subscribe to later is picked up again without touching anything.
    """

    def __init__(self, path: Path = DATA / "providers.json", clock=time.time):
        self.path = Path(path)
        self._now = clock
        self.installed: dict[str, dict[str, str]] = {}   # stick host -> {provider: package}
        self.scanned: dict[str, float] = {}               # stick host -> last scan time
        self.subs: dict[str, dict] = {}                   # provider -> {subscribed, kind, why, at, by}
        self.skips: dict[str, dict] = {}                  # target -> {kind, why, at, game}
        self.links: dict[str, dict] = {}                  # game id -> {start, providers: {prov: [rec]}}
        # Per stick: one stick can be signed out of an app the other plays fine (Peacock).
        self.stick_subs: dict[str, dict] = {}             # host -> {provider: {kind, why, at}}
        self.stick_skips: dict[str, dict] = {}            # host -> {target: {kind, why, at, game}}
        self.worked: dict[str, dict] = {}                 # provider -> {host: last time video played}
        self._fails: dict[str, list[float]] = {}
        self.load()

    # -- file --------------------------------------------------------------------------

    def load(self) -> None:
        try:
            d = json.loads(self.path.read_text()) if self.path.exists() else {}
        except (OSError, ValueError):
            log.warning("providers.json unreadable; starting fresh")
            d = {}
        d = d if isinstance(d, dict) else {}
        self.installed = _dict(d.get("installed"))
        self.scanned = _dict(d.get("scanned"))
        self.subs = _dict(d.get("subs"))
        self.skips = _dict(d.get("skips"))
        self.links = _dict(d.get("links"))
        self.stick_subs = {h: _dict(v) for h, v in _dict(d.get("stick_subs")).items()}
        self.stick_skips = {h: _dict(v) for h, v in _dict(d.get("stick_skips")).items()}
        self.worked = {k: _dict(v) for k, v in _dict(d.get("worked")).items()}

    def save(self) -> None:
        """Atomic write; a full disk or permissions problem is logged, never raised."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"installed": self.installed, "scanned": self.scanned,
                                       "subs": self.subs, "skips": self.skips, "links": self.links,
                                       "stick_subs": self.stick_subs, "stick_skips": self.stick_skips,
                                       "worked": self.worked,
                                       "saved": self._now()}, indent=1))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("couldn't save providers.json: %s", e)

    # -- installed apps ------------------------------------------------------------------

    def saw_apps(self, host: str, roles: Mapping[str, str]) -> bool:
        """Record which provider apps a stick has. Returns True if anything changed."""
        new = {k: v for k, v in (roles or {}).items() if k in BY_KEY and v}
        changed = self.installed.get(host) != new
        self.installed[host] = new
        self.scanned[host] = self._now()
        return changed

    def due(self, host: str) -> bool:
        return self._now() - float(self.scanned.get(host) or 0) >= SCAN_EVERY_S

    async def refresh_sticks(self, sticks: Iterable, force: bool = False) -> dict[str, list[str]]:
        """Scan due sticks' apps (at most every 30 min each), add new roles to stick.packages
        so tuners find them, and save.  Returns {host: [newly added roles]}.  Never raises."""
        added: dict[str, list[str]] = {}
        changed = False
        for st in list(sticks or ()):
            host = getattr(st, "host", None)
            if not host:
                continue
            # What we already know (e.g. from before a restart) goes on the stick right away.
            known = merge_roles(st.packages, self.installed.get(host) or {})
            if known:
                added[host] = known
            if not (force or self.due(host)):
                continue
            found = await scan_apps(st)
            if not found:
                continue  # scan failed: keep what we knew
            added[host] = added.get(host, []) + merge_roles(st.packages, found)
            changed |= self.saw_apps(host, found)
        if changed:
            self.save()
        return added

    def states(self) -> dict[str, ProviderState]:
        """Every provider: installed (on any scanned stick; None before the first scan),
        subscribed (None = assumed yes), and why not when it isn't usable."""
        now = self._now()
        out = {}
        for p in PROVIDERS:
            hosts = tuple(sorted(h for h, roles in self.installed.items() if p.key in roles))
            inst = (len(hosts) > 0) if self.installed else None
            pkg = self.installed[hosts[0]][p.key] if hosts else None
            sub, why = self._sub(p.key, now)
            have = self._hosts(p.key)
            blocked = tuple(h for h in have if self._stick_mark(h, p.key, now))
            if sub is None and blocked and len(blocked) == len(have):
                # Walled on every stick that has it: the app itself is out for now.
                sub, why = False, str(self._stick_mark(blocked[0], p.key, now).get("why") or "")
            out[p.key] = ProviderState(p.key, inst, sub, why, pkg, hosts, blocked)
        return out

    # -- per stick ---------------------------------------------------------------------

    def _hosts(self, app: str) -> list[str]:
        """Sticks that have this app: the scans' answer, else every stick we've heard about."""
        if self.installed:
            return sorted(h for h, roles in self.installed.items() if app in (roles or {}))
        return sorted(set(self.worked.get(app) or {}) | {h for h, m in self.stick_subs.items() if app in m})

    def _stick_mark(self, host: str, app: str, now: float | None = None) -> dict:
        rec = (self.stick_subs.get(host) or {}).get(app)
        if not isinstance(rec, dict):
            return {}
        now = self._now() if now is None else now
        if now - float(rec.get("at") or 0) > RECHECK_S.get(rec.get("kind"), 86400.0):
            return {}  # stale: give that stick another try
        return rec

    def can_play(self, host: str | None, target: str | None) -> bool:
        """Can this stick play this target, as far as we've learned?  False only when this
        stick hit a sign-in or subscribe wall for the app (or this link) recently, or its
        app scan says the app isn't there.  Unknown = yes.  Never raises."""
        try:
            if not host or not target:
                return True
            app = target.partition(":")[0]
            if app in BY_KEY and host in self.installed and app not in (self.installed.get(host) or {}):
                return False
            if self._stick_mark(host, app):
                return False
            skip = (self.stick_skips.get(host) or {}).get(target)
            return not (isinstance(skip, dict)
                        and self._now() - float(skip.get("at") or 0) < SKIP_S.get(skip.get("kind"), 0))
        except Exception:  # a bad record must never block a tune
            log.exception("can_play %s %s", host, target)
            return True

    def _others(self, target: str, host: str) -> list[str]:
        """Other sticks that have this target's app and aren't walled off from it (or it)."""
        return [h for h in self._hosts(target.partition(":")[0]) if h != host and self.can_play(h, target)]

    def _mark_stick(self, host: str, app: str, kind: str, why: str) -> None:
        self.stick_subs.setdefault(host, {})[app] = {"kind": kind, "why": why, "at": self._now()}
        self.save()

    def _sub(self, key: str, now: float) -> tuple[bool | None, str]:
        rec = self.subs.get(key)
        if not isinstance(rec, dict) or rec.get("subscribed") is None:
            return None, ""
        if rec.get("by") == "auto" and now - float(rec.get("at") or 0) > RECHECK_S.get(rec.get("kind"), 86400.0):
            return None, ""  # stale automatic mark: give it another try
        return bool(rec["subscribed"]), str(rec.get("why") or "")

    def set_subscribed(self, key: str, value: bool | None, why: str = "", by: str = "you") -> None:
        """You (or the app) say whether a provider is subscribed. None = back to "assume yes"."""
        if value is None:
            self.subs.pop(key, None)
        else:
            self.subs[key] = {"subscribed": bool(value), "why": why, "at": self._now(), "by": by,
                              "kind": "you" if by == "you" else None}
        self.save()

    # -- learning from tunes ---------------------------------------------------------------

    def learn(self, target: str | None, ok: bool, detail: str = "", game_id: str | None = None,
              host: str | None = None) -> str:
        """Feed every real (not dry-run) tune outcome here.  Returns a plain-English note
        when something was learned ("" otherwise), e.g. for a toast on the phone.

        host = the stick that tuned (None = unknown).  A sign-in or subscribe wall on one
        stick never marks the whole app out while another stick that has it isn't walled:
        that stick plays the app's games instead (Controller.can_play)."""
        if not target:
            return ""
        app = target.partition(":")[0]
        if ok:
            note = self._learn_ok(app, target)
            if host:
                self.worked.setdefault(app, {})[host] = self._now()
                (self.stick_subs.get(host) or {}).pop(app, None)
                (self.stick_skips.get(host) or {}).pop(target, None)
                self.save()
            return note
        v = parse_verdict(detail)
        if v is None:
            return self._learn_fail(target, game_id)
        if v.kind == "blackout":
            self._skip(target, "blackout", f"{_name(app)}: blacked out here", game_id)
            return f"{_name(app)} is blacked out for this game — using another source"
        if host and v.kind in ("signin", "upsell") and self._others(target, host):
            return self._learn_stick_wall(app, target, v, game_id, host)
        if v.kind == "signin":
            self._mark(app, "signin", f"{_name(app)} is signed out — sign in on the TV once")
            return f"{_name(app)} looks signed out — sign in on the TV; using other apps meanwhile"
        return self._learn_upsell(app, target, v, game_id)

    def _learn_ok(self, app: str, target: str) -> str:
        """Video played: forget failures for this link and any automatic "not subscribed"."""
        self._fails.pop(target, None)
        dropped = (self.skips.get(target) or {}).get("kind") in ("failed", "upsell")
        if dropped:
            self.skips.pop(target, None)
        if app == "espn" and ESPNPLUS_SKIP in self.skips and self._is_espnplus(target):
            self.skips.pop(ESPNPLUS_SKIP, None)
            dropped = True
        rec = self.subs.get(app)
        if isinstance(rec, dict) and rec.get("by") == "auto" and rec.get("subscribed") is False:
            self.subs.pop(app, None)
            self.save()
            return f"{_name(app)} works again"
        if dropped:
            self.save()
        return ""

    def _learn_stick_wall(self, app: str, target: str, v: Verdict, game_id: str | None, host: str) -> str:
        """A wall on one stick while another stick has the app: learn it for this stick only.
        A sign-in wall, or any wall in a single-tier app that has played on another stick (the
        account is fine, so this stick is signed out: Peacock's "Pick a plan / Sign in"), marks
        this stick out of the app.  Otherwise (maybe a per-game add-on) just this link on this
        stick; two such links in a single-tier app mark the stick too."""
        name = _name(app)
        played_elsewhere = any(h != host for h in self.worked.get(app) or {})
        if v.kind == "signin" or (played_elsewhere and app not in MULTI_TIER):
            self._mark_stick(host, app, v.kind, f"{name} is signed out on {host} — it showed “{v.phrase}”")
            return f"{name} is signed out on one stick ({host}) — playing {name} on the other stick"
        now = self._now()
        walls = self.stick_skips.setdefault(host, {})
        walls[target] = {"kind": "upsell", "why": f"{name}: not available on {host} — “{v.phrase}”",
                         "at": now, "game": game_id}
        mine = {t for t, s in walls.items() if t.startswith(app + ":") and isinstance(s, dict)
                and s.get("kind") == "upsell" and now - float(s.get("at") or 0) < SKIP_S["upsell"]}
        if app not in MULTI_TIER and len(mine) >= 2:
            self._mark_stick(host, app, "upsell", f"{name} isn't available on {host} — it showed “{v.phrase}”")
            return f"{name} doesn't play on one stick ({host}) — using the other stick for {name}"
        self.save()
        return f"{name} asked to subscribe on one stick ({host}) — trying the other stick"

    def _learn_fail(self, target: str, game_id: str | None) -> str:
        now = self._now()
        recent = [t for t in self._fails.get(target, []) if now - t < SKIP_S["failed"]] + [now]
        self._fails[target] = recent
        if len(recent) >= FAILS_BEFORE_SKIP:
            self._skip(target, "failed", "didn't start twice in a row", game_id)
            return "That link failed twice — trying another source for a while"
        return ""

    def _learn_upsell(self, app: str, target: str, v: Verdict, game_id: str | None) -> str:
        why = f"{_name(app)}: not subscribed — it showed “{v.phrase}”"
        self._skip(target, "upsell", why, game_id)
        # Two different games behind a paywall = the app itself isn't subscribed.
        walls = {t for t, s in self.active_skips().items()
                 if s.get("kind") == "upsell" and t.startswith(app + ":")}
        if app not in MULTI_TIER and len(walls) >= 2:
            self._mark(app, "upsell", why)
            return f"{_name(app)} doesn't seem to be subscribed — skipping it for now"
        if app == "espn" and self._is_espnplus(target) \
                and len({t for t in walls if self._is_espnplus(t)}) >= 2:
            # ESPN+ is its own tier inside the ESPN app: two walls = no ESPN+, skip every ESPN+ feed.
            self.skips[ESPNPLUS_SKIP] = {"kind": "upsell", "why": "ESPN+ isn't subscribed — " + why,
                                         "at": self._now(), "game": None}
            self.save()
            return "ESPN+ doesn't seem to be subscribed — skipping ESPN+ games for now"
        return f"{_name(app)} asked to subscribe for this game — using another source"

    def _is_espnplus(self, target: str) -> bool:
        ref = target.partition(":")[2]
        return any(isinstance(r, dict) and _link_ref("espn", r) == ref
                   for rec in self.links.values() if isinstance(rec, dict)
                   for r in ((rec.get("providers") or {}).get("espnplus") or []))

    def _mark(self, app: str, kind: str, why: str) -> None:
        self.subs[app] = {"subscribed": False, "kind": kind, "why": why, "at": self._now(), "by": "auto"}
        self.save()

    def _skip(self, target: str, kind: str, why: str, game_id: str | None) -> None:
        self.skips[target] = {"kind": kind, "why": why, "at": self._now(), "game": game_id}
        if game_id and kind in ("blackout", "failed"):
            # Per game too: a League Pass game has 6-8 GTIs; don't burn 2 tunes on each.
            app = target.partition(":")[0]
            self.skips[game_skip_key(app, game_id)] = {"kind": kind, "why": why, "at": self._now(),
                                                       "game": game_id}
        self.save()

    def active_skips(self) -> dict[str, dict]:
        now = self._now()
        return {t: s for t, s in self.skips.items()
                if isinstance(s, dict) and now - float(s.get("at") or 0) < SKIP_S.get(s.get("kind"), 0)}

    # -- per-game links ------------------------------------------------------------------

    def remember(self, g: GameState, found: Mapping[str, dict]) -> int:
        """Keep every provider's feeds for one game from eventlinks.match() (best + alts),
        compacted.  Returns how many links were stored."""
        provs: dict[str, list[dict]] = {}
        for prov, best in (found or {}).items():
            if not isinstance(best, dict):
                continue
            recs = [best] + [a for a in best.get("alts") or [] if isinstance(a, dict)]
            provs[prov] = [_compact(r) for r in recs[:12]]
        if not provs:
            return 0
        self.links[g.game_id] = {"start": g.start_time, "providers": provs}
        return sum(len(v) for v in provs.values())

    def prune(self) -> None:
        """Drop links of games long over and skips that expired."""
        now = self._now()
        self.links = {gid: v for gid, v in self.links.items()
                      if not isinstance(v, dict) or not v.get("start") or now - float(v["start"]) < LINKS_KEEP_S}
        self.skips = self.active_skips()

    def game_for(self, target: str | None) -> str | None:
        """The game whose stored event link is this target (None for channels / unknown)."""
        app, _, ref = (target or "").partition(":")
        if not ref:
            return None
        for gid, rec in self.links.items():
            provs = (rec or {}).get("providers") if isinstance(rec, dict) else None
            for prov, recs in (provs or {}).items():
                if LINK_APPS.get(prov) == app and any(isinstance(r, dict) and _link_ref(app, r) == ref
                                                      for r in recs or []):
                    return gid
        return None

    def sources(self, g: GameState, base: Where | None = None,
                channel_map: ChannelMap | None = None, sunday_ticket: bool = True) -> list[Source]:
        rec = self.links.get(g.game_id) or {}
        return gather_sources(g, base, rec.get("providers") or {}, self.active_skips(), channel_map, sunday_ticket)

    def choose(self, g: GameState, base: Where | None = None, my_teams=None,
               channel_map: ChannelMap | None = None, sunday_ticket: bool = True) -> Pick:
        """The one call per game: ChannelMap's answer + every event link + fallbacks, ranked."""
        try:
            return best_source(g, self.sources(g, base, channel_map, sunday_ticket), self.states(), my_teams)
        except Exception:  # a bad record must never cost us the game: fall back to ChannelMap
            log.exception("choose failed for %s", g.game_id)
            if base is None:
                return Pick(None, "unknown", "unknown", False)
            return Pick(base.target, base.label, base.confidence, base.tunable,
                        provider=(base.target or "").partition(":")[0])


_COMPACT_KEYS = ("ref", "gti", "play_id", "url", "feed", "feed_name", "geo_ok", "alt", "title",
                 "network", "lang", "channel", "event_url", "start")


def _compact(rec: dict) -> dict:
    return {k: rec.get(k) for k in _COMPACT_KEYS if rec.get(k) is not None}


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}
