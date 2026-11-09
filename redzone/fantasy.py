"""ESPN Fantasy Football: boost games where your (or your opponent's) starters are about to score.

Setup (one time, ~5 minutes):
  1. Settings → Fantasy: paste your league ID (the number in your league's URL: ...leagueId=12345).
  2. Private league only: on fantasy.espn.com in your browser, copy the cookies `espn_s2` and `SWID`
     into .env as ESPN_S2=... and ESPN_SWID={...}. They never leave your Mac.

Uses ESPN's unofficial fantasy API (the same one the espn-api project uses). Read-only.

Why: first added in b56e3411 (2026-11-02) as "ESPN fantasy: red-zone boosts for your and your opponent's starters, live matchup score; requirem..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from redzone.models import GameState, League, Phase
from redzone.tv import _env

log = logging.getLogger(__name__)
API = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
PRO_TEAM = {1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
            11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
            20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH",
            29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}
POSITION = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
BENCH_SLOTS = {20, 21}  # bench, IR


@dataclass
class Starter:
    name: str
    pos: str
    team: str          # NFL abbr
    mine: bool


@dataclass
class Matchup:
    me: str = ""
    opponent: str = ""
    my_points: float | None = None
    opp_points: float | None = None
    starters: list[Starter] = field(default_factory=list)
    fetched: float = 0.0
    error: str = ""


async def fetch(client: httpx.AsyncClient, league_id: str, season: int, team_id: int | None = None) -> Matchup:
    m = Matchup(fetched=time.time())
    cookies = {}
    if _env("ESPN_S2") and _env("ESPN_SWID"):
        cookies = {"espn_s2": _env("ESPN_S2"), "SWID": _env("ESPN_SWID")}
    try:
        r = await client.get(API.format(season=season, league=league_id),
                             params=[("view", "mTeam"), ("view", "mRoster"), ("view", "mMatchupScore"),
                                     ("view", "mStatus")], cookies=cookies, timeout=10)
    except httpx.HTTPError as e:
        m.error = f"fantasy unreachable: {e}"
        return m
    if r.status_code in (401, 403):
        m.error = "private league: add ESPN_S2 and ESPN_SWID to .env"
        return m
    if r.status_code != 200:
        m.error = f"fantasy HTTP {r.status_code}"
        return m
    try:
        d = r.json()
    except ValueError:
        m.error = "fantasy returned something that isn't JSON"
        return m
    return parse(d, team_id, (_env("ESPN_SWID") or "").upper())


def parse(d: dict, team_id: int | None, swid: str) -> Matchup:
    m = Matchup(fetched=time.time())
    teams = {t.get("id"): t for t in d.get("teams") or []}
    me = teams.get(team_id) if team_id else None
    if me is None and swid:
        me = next((t for t in teams.values() if swid in [o.upper() for o in t.get("owners") or []]), None)
    if me is None:
        m.error = "couldn't tell which team is yours — set fantasy.team_id in Settings"
        return m
    period = (d.get("status") or {}).get("currentMatchupPeriod") or d.get("scoringPeriodId")
    game = next((g for g in d.get("schedule") or [] if g.get("matchupPeriodId") == period and
                 me["id"] in (g.get("home", {}).get("teamId"), g.get("away", {}).get("teamId"))), None)
    opp = None
    if game:
        my_side, opp_side = ("home", "away") if game.get("home", {}).get("teamId") == me["id"] else ("away", "home")
        opp = teams.get(game.get(opp_side, {}).get("teamId"))
        m.my_points = _pts(game.get(my_side, {}))
        m.opp_points = _pts(game.get(opp_side, {}))
    m.me = _name(me)
    m.opponent = _name(opp) if opp else ""
    for team, mine in ((me, True), (opp, False)):
        for e in ((team or {}).get("roster") or {}).get("entries") or []:
            if e.get("lineupSlotId") in BENCH_SLOTS:
                continue
            p = (e.get("playerPoolEntry") or {}).get("player") or {}
            abbr = PRO_TEAM.get(p.get("proTeamId"))
            if abbr:
                m.starters.append(Starter(p.get("fullName") or "", POSITION.get(p.get("defaultPositionId"), ""),
                                          abbr, mine))
    return m


def _pts(side: dict) -> float | None:
    v = side.get("totalPointsLive", side.get("totalPoints"))
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def _name(t: dict) -> str:
    return t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or t.get("abbrev", "")


def boosts(m: Matchup | None, g: GameState, mine_pts: float, opp_pts: float) -> tuple[float, str] | None:
    """(points, label) when one of the matchup's starters is with the ball in the red zone."""
    if m is None or g.league != League.NFL or g.phase != Phase.LIVE:
        return None
    s = g.situation
    if not s.possession or s.yards_to_goal is None or s.yards_to_goal > 20:
        return None
    best = None
    for st in m.starters:
        if st.team != s.possession or st.pos in ("D/ST", "K"):
            continue
        pts = mine_pts if st.mine else opp_pts
        label = f"{'your' if st.mine else 'their'} {st.pos} {st.name.split()[-1]} in the red zone"
        if best is None or pts > best[0]:
            best = (pts, label)
    return best
