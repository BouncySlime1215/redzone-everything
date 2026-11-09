"""JSON shapes for the phone: game cards, game detail, team directory, rosters.

Screen-time clock (flag `views.screen_clock`, code default OFF, not in default_config.yaml): the TV runs
60-90 s behind the feed, so the feed's clock is a spoiler. `ClockTape` remembers each game's clock and
`screen_item` shows the clock as of `now - delay`. Why: Nick watches on the screen, not the feed; an unknown
delay shows no clock (never the live one) so the phone cannot give a play away.

Pictures (logos, headshots, stadium photos) are ESPN CDN URLs; the phone loads them
through /img/ on the Mac, which caches them on disk.

Why: first added in 67a883b7 (2026-11-02) as "Pictures backend: headshots, leaders, venues, spotlight, team directory, rosters, image cache; se..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import time

from redzone.models import REGULATION, GameState, League, Phase
from redzone.sources import espn

LEAGUE_LOGO = "https://a.espncdn.com/i/teamlogos/leagues/500/{}.png"
COLLEGE_LOGO = {"cbb": "https://a.espncdn.com/i/espn/misc_logos/500/ncaa.png",
                "wcbb": "https://a.espncdn.com/i/espn/misc_logos/500/ncaa.png",
                "cfb": "https://a.espncdn.com/i/espn/misc_logos/500/ncaa_football.png"}


def league_logo(lg: str) -> str:
    """ESPN has no per-league logo for college: the NCAA mark (football gets its own)."""
    return COLLEGE_LOGO.get(lg) or LEAGUE_LOGO.format(lg)
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams"
TEAM_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams/{}"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams/{}/roster"


def team(t) -> dict:
    rec = ""
    if t.wins is not None and t.losses is not None:
        rec = f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else "")
    return {"id": t.id, "abbr": t.abbr, "name": t.name, "score": t.score, "logo": t.logo,
            "logo_dark": t.logo_dark or t.logo, "color": t.color, "alt_color": t.alt_color, "record": rec,
            "rank": t.rank}


def clock_text(g: GameState) -> str:
    if g.clock_s is None:
        return ""
    s = max(0, int(g.clock_s))
    return f"{s // 60}:{s % 60:02d}"


# --- screen-time clock (flag views.screen_clock, OFF) ------------------------------------

SCREEN_CLOCK_FLAG = "views.screen_clock"
TAPE_WINDOW = 300.0   # longer than any screen delay the app models (60-90 s, buffered tunes a few minutes)


def screen_clock_on(cfg) -> bool:
    """The flag, read with a code default of OFF (the key is not in default_config.yaml)."""
    return bool(cfg.get(SCREEN_CLOCK_FLAG, False))


def known_delay_s(delays, target: str | None) -> float | None:
    """Seconds the screen runs behind the feed for the stick this game is on, or None when unknown:
    not on a stick, or only the config default (never measured). Reads without creating a book entry."""
    if not target or delays is None:
        return None
    d = delays.book.get(target)
    if d is None or getattr(d, "source", "default") == "default":
        return None
    return max(0.0, float(d.mu))


class ClockTape:
    """Per game, the feed's (period, clock) over the last TAPE_WINDOW seconds, so the clock as of a
    moment in the past can be read back. Unchanged readings are not stored; the first one keeps its time."""

    def __init__(self, keep_s: float = TAPE_WINDOW):
        self.keep_s = float(keep_s)
        self.rows: dict[str, list[tuple[float, int | None, int | None]]] = {}

    def record(self, gid: str, g: GameState, now: float | None = None) -> None:
        now = time.time() if now is None else now
        clock = None if g.clock_s is None else max(0, int(g.clock_s))
        rows = self.rows.setdefault(gid, [])
        if not rows or rows[-1][1:] != (g.period, clock):
            rows.append((now, g.period, clock))
        cut = now - self.keep_s
        while len(rows) > 1 and rows[1][0] <= cut:   # keep one row at or before the cut as the anchor
            rows.pop(0)

    def at(self, gid: str, when: float) -> tuple[int | None, int | None] | None:
        """(period, clock_s) the feed showed at `when`, or None when the tape does not reach back that far."""
        rows = self.rows.get(gid) or []
        if not rows or rows[0][0] > when:
            return None
        best = rows[0]
        for r in rows:
            if r[0] > when:
                break
            best = r
        return best[1], best[2]


def screen_item(item: dict, gid: str, *, enabled: bool, tape: ClockTape, delay_s: float | None,
                now: float | None = None) -> dict:
    """The game card as the screen would show it. Flag off, or a game that is not live: `item` unchanged
    (same object). Flag on: `clock`/`period` are the feed's as of now - delay_s; an unknown delay, or a tape
    too young to reach back, gives clock "" (never the live clock). `screen_clock` says which: "ok" or "unknown"."""
    if not enabled or item.get("phase") != Phase.LIVE.value:
        return item
    now = time.time() if now is None else now
    out = dict(item)
    got = tape.at(gid, now - delay_s) if delay_s is not None else None
    if got is None:
        out.update(clock="", screen_clock="unknown", screen_delay_s=None)
        return out
    period, clock_s = got
    out.update(period=period, clock="" if clock_s is None else f"{clock_s // 60}:{clock_s % 60:02d}",
               screen_clock="ok", screen_delay_s=round(delay_s, 1))
    return out


def situation(g: GameState) -> dict:
    s = g.situation
    text = ""
    if g.league.base == League.NFL and s.down and s.possession:
        ytg = s.yards_to_goal
        spot = (f"& goal at the {ytg}" if s.goal_to_go else
                f"& {s.distance} at {'OPP' if ytg and ytg < 50 else 'OWN'} {ytg if ytg and ytg < 50 else 100 - (ytg or 50)}")
        text = f"{s.possession} · {_ord(s.down)} {spot}"
    elif g.league == League.NHL and s.power_play:
        text = f"{s.power_play} power play" + (f" ({s.strength})" if s.strength else "")
    elif g.league == League.MLB and s.outs is not None:
        text = f"{(g.half or '').title()} {_ord(g.period or 1)} · {s.outs} out"
    return {"possession": s.possession, "down": s.down, "distance": s.distance, "yards_to_goal": s.yards_to_goal,
            "goal_to_go": s.goal_to_go, "power_play": s.power_play, "strength": s.strength,
            "empty_net": s.empty_net, "outs": s.outs, "runners": list(s.runners) if s.runners else None,
            "half": g.half, "timeouts_home": s.timeouts_home, "timeouts_away": s.timeouts_away, "text": text}


def _ord(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


# --- game detail -------------------------------------------------------------------

def game_detail(g: GameState, summary: dict | None, item: dict) -> dict:
    out = {"game": item, "plays": [], "wp_series": [], "linescore": {"home": [], "away": [], "labels": []},
           "leaders": {"home": [], "away": []}, "venue": g.venue_info}
    if not summary:
        return out
    lg = g.league
    plays = espn.flatten_plays(lg, summary)
    roster = espn.athletes(lg, summary)
    ids = {g.home.id: g.home, g.away.id: g.away}
    feed = []
    for p in reversed(plays[-60:]):
        t = (espn._get(p, "type", "text") or "")
        if lg == League.MLB and t.lower() in ("ball", "strike looking", "strike swinging", "foul ball",
                                              "start batter/pitcher", "end batter/pitcher"):
            continue  # pitch-by-pitch noise; keep results
        tm = ids.get(str(espn._get(p, "team", "id") or espn._get(p, "start", "team", "id") or ""))
        a = espn.play_athlete(lg, p, roster)
        feed.append({"id": str(p.get("id") or ""), "text": p.get("text") or t, "type": t,
                     "clock": espn._get(p, "clock", "displayValue") or "",
                     "period": espn._int(espn._get(p, "period", "number")),
                     "team": tm.abbr if tm else None, "team_logo": (tm.logo_dark or tm.logo) if tm else None,
                     "scoring": bool(p.get("scoringPlay")), "headshot": a["headshot"] if a else None,
                     "player": a["name"] if a else None,
                     "score": f"{p.get('awayScore', '')}-{p.get('homeScore', '')}"})
        if len(feed) >= 25:
            break
    out["plays"] = feed
    wps = [w.get("homeWinPercentage") for w in summary.get("winprobability") or []
           if isinstance(w.get("homeWinPercentage"), (int, float))]
    if len(wps) > 160:  # keep the sparkline light
        step = len(wps) / 160
        wps = [wps[int(i * step)] for i in range(160)] + [wps[-1]]
    out["wp_series"] = [round(x, 3) for x in wps]
    comp = espn._get(summary, "header", "competitions", 0) or {}
    for c in comp.get("competitors") or []:
        side = "home" if c.get("homeAway") == "home" else "away"
        out["linescore"][side] = [ls.get("displayValue") for ls in c.get("linescores") or []]
    n = max(len(out["linescore"]["home"]), len(out["linescore"]["away"]))
    reg = REGULATION[lg]
    out["linescore"]["labels"] = [str(i + 1) if i < reg else ("OT" if lg != League.MLB else str(i + 1))
                                  for i in range(n)]
    by_team = {str(espn._get(x, "team", "id") or ""): x.get("leaders") or [] for x in summary.get("leaders") or []}
    for side, t in (("home", g.home), ("away", g.away)):
        out["leaders"][side] = espn._leaders_from(lg, [(t.abbr, by_team.get(t.id, []))], limit=4)
    return out


# --- team directory ----------------------------------------------------------------

def _team_entry(league: League, t: dict, mine: set[str]) -> dict:
    logos = t.get("logos") or []
    logo = next((x.get("href") for x in logos if "default" in (x.get("rel") or [])), None) \
        or espn._get(logos, 0, "href") or espn.logo_url(league, t.get("abbreviation", ""))
    dark = next((x.get("href") for x in logos if "dark" in (x.get("rel") or [])), None) or logo
    abbr = t.get("abbreviation") or ""
    return {"id": str(t.get("id") or ""), "abbr": abbr, "name": t.get("displayName") or "",
            "short": t.get("shortDisplayName") or t.get("name") or "", "logo": logo, "logo_dark": dark,
            "color": t.get("color") or "", "alt_color": t.get("alternateColor") or "", "mine": abbr in mine}


async def teams(fetcher, my_teams: dict[str, list[str]]) -> dict:
    async def one(lg: League):
        data = await fetcher.get_json(TEAMS_URL.format(espn.PATH[lg]), min_interval=86400)
        raw = espn._get(data, "sports", 0, "leagues", 0, "teams") or []
        mine = set(my_teams.get(lg.value, []))
        rows = [_team_entry(lg, x.get("team") or {}, mine) for x in raw]
        return lg.value, sorted([r for r in rows if r["abbr"]], key=lambda r: r["name"])
    pairs = await asyncio.gather(*(one(lg) for lg in League))
    return dict(pairs)


async def team_detail(fetcher, league: League, team_id: str, my_teams: dict, game_item: dict | None) -> dict:
    info, roster = await asyncio.gather(
        fetcher.get_json(TEAM_URL.format(espn.PATH[league], team_id), min_interval=3600),
        fetcher.get_json(ROSTER_URL.format(espn.PATH[league], team_id), min_interval=21600))
    t = espn._get(info, "team") or {}
    entry = _team_entry(league, t, set(my_teams.get(league.value, [])))
    entry["record"] = espn._get(t, "record", "items", 0, "summary") or ""
    entry["standing"] = t.get("standingSummary") or ""
    players = []
    for grp in espn._get(roster, "athletes") or []:
        items = grp.get("items") if isinstance(grp, dict) and "items" in grp else [grp]
        for a in items or []:
            if not isinstance(a, dict) or not a.get("id"):
                continue
            hs = a.get("headshot")
            players.append({"id": str(a["id"]), "name": a.get("displayName") or a.get("fullName") or "",
                            "short": a.get("shortName") or "", "jersey": a.get("jersey") or "",
                            "position": espn._get(a, "position", "abbreviation") or "",
                            "headshot": (hs.get("href") if isinstance(hs, dict) else hs)
                            or espn.headshot_url(league, a["id"])})
    return {"team": entry, "roster": players, "game": game_item}
