"""League registry for the history fetchers: ESPN paths, season labels, calendars and the evaluation fences.

Why: B-01 must fetch seven leagues the same way and must never pull evaluation nights: the fence constants here
mirror agent/kaggle/sports_common.EVAL_FIRST_NIGHT (tests/test_history_fetch.py checks they agree) and the locked
window in tracks/B-foundation-model.md (corpus rule 4, F-09).

Season label = the year the season ends in for winter sports (nba, cbb, wcbb, nhl) and the year it starts in for
nfl, cfb and mlb (scripts/pbp/schema.py). Nights are YYYYMMDD in America/New_York with a 5 AM boundary, like the repo.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

# First night per league that is never fetched. Equal to sports_common.EVAL_FIRST_NIGHT (checked by a test).
EVAL_FIRST_NIGHT = {"nba": "20261020", "cbb": "20261020", "wcbb": "20261020", "wnba": "20261020", "nfl": "20250905",
                    "cfb": "20250801", "nhl": "20261019", "mlb": "20250301"}
# The locked test window (tracks/B-foundation-model.md corpus rule 4): nights inside it are never fetched either.
LOCKED_WINDOW = ("20251212", "20260105")


@dataclass(frozen=True)
class League:
    code: str          # our code
    path: str          # ESPN path under /apis/site/v2/sports
    kind: str          # "day" (one scoreboard per day) or "week" (one scoreboard per season type and week)
    label: str         # "end" or "start": which calendar year names the season
    probe: tuple[int, int]   # month, day of the date used to read a season's calendar (relative to the label year)
    groups: int | None = None   # ESPN `groups` filter that selects the full board (college only)
    fallback: tuple[tuple[int, int], tuple[int, int]] = ((10, 1), (6, 30))   # (month, day) start and end, used if ESPN gives no calendar
    first_hint: int = 2002     # earliest season worth trying; the probe finds the real first season


LEAGUES = {
    "nba": League("nba", "basketball/nba", "day", "end", (1, 15)),
    "cbb": League("cbb", "basketball/mens-college-basketball", "day", "end", (1, 15), 50, ((11, 1), (4, 10))),
    "nfl": League("nfl", "football/nfl", "week", "start", (9, 20), None, ((8, 1), (2, 20))),
    "cfb": League("cfb", "football/college-football", "week", "start", (10, 15), 80, ((8, 20), (1, 20))),
    "wcbb": League("wcbb", "basketball/womens-college-basketball", "day", "end", (1, 15), 50, ((11, 1), (4, 10))),
    "nhl": League("nhl", "hockey/nhl", "day", "end", (1, 15)),
    "mlb": League("mlb", "baseball/mlb", "day", "start", (7, 15), None, ((3, 1), (11, 5))),
}
# Nick's order for the big run: basketball and football first, then hockey and baseball (B0a).
DEFAULT_ORDER = ("nba", "cbb", "nfl", "cfb", "wcbb", "nhl", "mlb")


def night_of(value: str | None) -> str | None:
    """YYYYMMDD night from an ISO timestamp: America/New_York minus 5 hours (the repo's rule). None if unparseable."""
    if not value:
        return None
    s = str(value).strip()
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return (t.astimezone(ET) - dt.timedelta(hours=5)).strftime("%Y%m%d")


def fenced(league: str, night: str | None) -> bool:
    """True when this night must not be fetched: undated, on or after the league's first evaluation night, or in the locked window."""
    if not night:
        return True
    if night >= EVAL_FIRST_NIGHT[league]:
        return True
    return LOCKED_WINDOW[0] <= night <= LOCKED_WINDOW[1]


def season_span(league: str, season: int) -> tuple[str, str]:
    """Rough first and last night (YYYYMMDD) a season can contain; only used to skip whole-season files that touch a fence."""
    lg = LEAGUES.get(league)
    if lg is None or league == "wnba":
        return f"{season}0401", f"{season}1031"
    (m0, d0), (m1, d1) = lg.fallback
    y0 = season - 1 if lg.label == "end" else season
    y1 = season if lg.label == "end" else season + (1 if m1 < m0 else 0)
    return f"{y0}{m0:02d}{d0:02d}", f"{y1}{m1:02d}{d1:02d}"


def season_touches_fence(league: str, season: int) -> bool:
    """True when any night of the season is fenced, so a whole-season file (hoopR, wehoop) cannot be taken as is."""
    first, last = season_span(league, season)
    if last >= EVAL_FIRST_NIGHT[league]:
        return True
    return not (last < LOCKED_WINDOW[0] or first > LOCKED_WINDOW[1])


def season_fully_fenced(league: str, season: int) -> bool:
    """True when the whole season is on or after the league's first evaluation night: nothing to fetch, no request needed."""
    return season_span(league, season)[0] >= EVAL_FIRST_NIGHT[league]


def parse_seasons(spec: str) -> list[int]:
    """'2025-2020' or '2024,2022,2019-2017' -> newest first, no duplicates."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            out += list(range(max(a, b), min(a, b) - 1, -1))
        else:
            out.append(int(part))
    return sorted(set(out), reverse=True)
