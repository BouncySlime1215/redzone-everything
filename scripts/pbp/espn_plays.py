"""ESPN-format play dicts -> the shared play table, for the leagues lane 1's #169 factory already holds.

Serves: tokenizer v2 for NBA, CBB, NHL, MLB (Retrosheet) and CFB/NFL via the factory's own raw (EXP-1000, EXP-097).
Owner: Data Planner A; called by lane 1 inside the factory.

The factory yields `(gid, night, plays, home, away)` per game (noggin_tokens.games_of). Pass that straight in:

    from pbp import espn_plays
    for gid, night, plays, home, away in noggin_tokens.games_of(league, raw):
        rows = espn_plays.convert_game(league, gid, night, plays, home, away, season=year, source="hoopr",
                                       license=LICENSE, tag=PERSONAL)

game_id = the factory's gid and seq = the factory's play order, so v2 rows line up with the v1 shards' index
1:1 (one row per play dict, nothing dropped or added). Fields the play dicts lack stay null; nothing is invented.
Why: Adapts ESPN-format plays to the shared play table the factory already holds (Data A commit).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from pbp import schema
except ImportError:
    from scripts.pbp import schema

SPORT = {"nba": "basketball", "cbb": "basketball", "wcbb": "basketball", "wnba": "basketball", "nfl": "football",
         "cfb": "football", "nhl": "hockey", "mlb": "baseball"}
LEVEL = {"cbb": "college", "wcbb": "college", "cfb": "college"}


def _team_side(p: dict, home_id: str, away_id: str) -> str:
    tid = str((p.get("team") or {}).get("id") or "")
    return "home" if tid and tid == home_id else "away" if tid and tid == away_id else ""


def convert_game(league: str, gid: str, night: str, plays: list[dict], home: str, away: str, *, season: int,
                 source: str, license: str, tag: str = "", home_id: str = "", away_id: str = "") -> list[dict]:
    from redzone.models import League
    from redzone.sources import espn
    lg = League(league)
    rows, hs0, as0, poss = [], 0, 0, ""
    for i, p in enumerate(plays):
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        hs = hs0 if hs is None else hs
        as_ = as0 if as_ is None else as_
        side = _team_side(p, home_id, away_id)
        actors = [str(espn._get(x, "athlete", "id") or "") for x in p.get("participants") or []]
        stop = espn.stoppage_for(lg, p).value
        value = max(0, hs - hs0) + max(0, as_ - as0)
        kind = espn._kind(lg, p).lower()
        other = "away" if side == "home" else "home" if side else ""
        if side and (value or "turnover" in kind):      # possession only on clear changes, else carried
            poss = other
        elif side and "rebound" in kind:
            poss = side
        rows.append(schema.row(
            source=source, sport=SPORT.get(league, "basketball"), league=league, level=LEVEL.get(league, "pro"),
            season=season, game_id=gid, night=night, seq=i,
            period=espn._int(espn._get(p, "period", "number")),
            clock_s=espn._clock_s(espn._get(p, "clock", "displayValue")), wall_ts=espn._ts(p.get("wallclock")),
            home=home, away=away, home_score=hs, away_score=as_, side=side, possession=poss,
            kind=kind, sub_kind=str(espn._get(p, "type", "abbreviation") or "").lower()[:16],
            actor_id=actors[0] if actors else "", actor2_id=actors[1] if len(actors) > 1 else "",
            value=float(value), stop=stop if stop in schema.STOPS else "unknown", license=license, tag=tag))
        hs0, as0 = hs, as_
    return rows
