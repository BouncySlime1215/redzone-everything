"""JSON shapes for the phone: game cards, game detail, team directory, rosters.

Pictures (logos, headshots, stadium photos) are ESPN CDN URLs; the phone loads them
through /img/ on the Mac, which caches them on disk.
"""
from __future__ import annotations

import asyncio
import time

from redzone.models import GameState, League, Phase
from redzone.sources import espn

LEAGUE_LOGO = "https://a.espncdn.com/i/teamlogos/leagues/500/{}.png"
TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams"
TEAM_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams/{}"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/{}/teams/{}/roster"


def team(t) -> dict:
    rec = ""
    if t.wins is not None and t.losses is not None:
        rec = f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else "")
    return {"id": t.id, "abbr": t.abbr, "name": t.name, "score": t.score, "logo": t.logo,
            "logo_dark": t.logo_dark or t.logo, "color": t.color, "alt_color": t.alt_color, "record": rec}


def clock_text(g: GameState) -> str:
    if g.clock_s is None:
        return ""
    s = max(0, int(g.clock_s))
    return f"{s // 60}:{s % 60:02d}"


def situation(g: GameState) -> dict:
    s = g.situation
    text = ""
    if g.league == League.NFL and s.down and s.possession:
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
    reg = {League.NFL: 4, League.NBA: 4, League.NHL: 3, League.MLB: 9}[lg]
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
