"""Noggin data factory: labeled dead-ball moments from free public play-by-play with real wall clocks.

    python scripts/noggin_factory.py nba --seasons 2019-2026      # Kaggle CPU / GitHub Actions, never the Mac

One row per dead window (a stoppage on the feed, start -> the real restart), labeled by the SAME code the
live app runs (`redzone.decider.cuts.DeadTracker` on ESPN-shaped plays), so a label means exactly what the
live gate reads: `start` is the stoppage entry's wallclock, `end` the restart worked back from the game
clock (exact) or the earliest the next logged action allows (conservative, `exact=False`).

Columns: league, season, playoff, game_id, night (YYYYMMDD ET, 5 AM boundary), split, kind, kind_code,
start, length_s, exact, and the rule / game-state features in FEATURES.

Splits are by WHOLE NIGHT, time-ordered (redzone/lab/splits.py): train = oldest, calib = the next block,
test = newest. The scorecard archive (eval-data-v1) is the end-to-end judge, so every public night on or
after its first night per league is dropped here: its locked nights can never reach training.

Sources (free, public; licenses in SOURCES):
    nba   hoopR ESPN NBA pbp (`wallclock` from 2013-14 on)            cbb   hoopR ESPN MBB pbp 2006+
    wcbb  wehoop ESPN WBB pbp
    nfl   nflfastR / nflverse pbp (`time_of_day` = the snap, like ESPN's NFL wallclock; 2011+)
    nhl   ESPN summaries (fastRhockey / the NHL feeds carry no wall clock): the live parser on the live shape
Queued (loaders not written yet): cfb (cfbfastR 2014+), mlb (pitch timestamps 2008+). See docs/noggin.md § Phases.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from redzone.models import League
from redzone.sources import espn
from redzone.noggin.features import (FEATURES, KIND_CODE, KINDS, MAX_LEN_S, NBA_MARKS, context,  # noqa: F401
                                      season_of, vector, windows_for_game)

SDV = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
SOURCES = {
    "nba": dict(url=SDV + "/espn_nba_pbp/play_by_play_{y}.parquet", first=2002,
                license="MIT (hoopR); underlying ESPN data for personal/non-commercial use"),
    "cbb": dict(url=SDV + "/espn_mens_college_basketball_pbp/play_by_play_{y}.parquet", first=2006,
                license="MIT (hoopR); underlying ESPN data for personal/non-commercial use"),
    "wcbb": dict(url=SDV + "/espn_womens_college_basketball_pbp/play_by_play_{y}.parquet", first=2006,
                 license="MIT (wehoop); underlying ESPN data for personal/non-commercial use"),
    # ESPN summaries carry each play's wallclock (what the live app reads): fetched by
    # scripts/noggin_factory.py on a runner that can reach ESPN (GitHub Actions), never this container.
    "nhl": dict(espn=True, first=2011, license="ESPN public site API; personal/non-commercial use"),
    "nfl": dict(url="https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{y}.parquet",
                first=2011, license="CC-BY 4.0 (nflverse data); underlying NFL data for personal/non-commercial use"),
}
# First night of the pinned scorecard window (data/eval_pin.json, eval-data-v1). Nothing on or after it
# is ever written by the factory. College has no scorecard yet; it uses the NBA date as a safe cut.
EVAL_FIRST_NIGHT = {"nba": "20261020", "cbb": "20261020", "wcbb": "20261020", "nfl": "20250905",
                    "nhl": "20261019"}

def night_of(epoch: float) -> str:
    t = pd.Timestamp(epoch, unit="s", tz="UTC").tz_convert("America/New_York") - pd.Timedelta(hours=5)
    return t.strftime("%Y%m%d")


def _plays_basketball(g: pd.DataFrame) -> list[dict]:
    """hoopR / wehoop rows -> the ESPN summary play dicts the live parser reads."""
    def s(x) -> str:
        return x if isinstance(x, str) else ""

    out = []
    for r in g.itertuples(index=False):
        out.append({"id": str(r.id), "sequenceNumber": r.sequence_number, "type": {"text": s(r.type_text)},
                    "text": s(r.text), "period": {"number": r.period_number},
                    "clock": {"displayValue": s(r.clock_display_value)}, "wallclock": s(r.wallclock),
                    "scoringPlay": bool(r.scoring_play) if r.scoring_play == r.scoring_play else False,
                    "scoreValue": r.score_value, "homeScore": r.home_score, "awayScore": r.away_score})
    return out



def _nfl_type(r) -> str:
    """nflfastR row -> ESPN's NFL play type text (what espn._kind / cuts.after_kind read)."""
    d = (r.desc if isinstance(r.desc, str) else "")
    dl, pt = d.lower(), (r.play_type if isinstance(r.play_type, str) else "")
    if "two-minute warning" in dl:
        return "Two-minute warning"
    if dl.startswith("end quarter 2") or "end of half" in dl:
        return "End of Half"
    if dl.startswith("end game"):
        return "End of Game"
    if dl.startswith("end quarter"):
        return "End Period"
    if r.timeout == 1 or (pt == "no_play" and "timeout #" in dl):
        return "Timeout"
    td = r.touchdown == 1
    if pt == "kickoff":
        return "Kickoff"
    if pt == "punt":
        return "Punt"
    if pt == "field_goal":
        return "Field Goal Good" if r.field_goal_result == "made" else "Field Goal Missed"
    if pt == "extra_point":
        return "Extra Point Good" if r.extra_point_result == "good" else "Extra Point Missed"
    if r.interception == 1:
        return "Interception Return Touchdown" if td else "Pass Interception Return"
    if r.fumble_lost == 1:
        return "Fumble Return Touchdown" if td else "Fumble Recovery (Opponent)"
    if r.sack == 1:
        return "Sack"
    if pt in ("pass", "qb_spike"):
        if td:
            return "Passing Touchdown"
        return "Pass Reception" if r.complete_pass == 1 else "Pass Incompletion"
    if pt in ("run", "qb_kneel"):
        return "Rushing Touchdown" if td else "Rush"
    if r.safety == 1:
        return "Safety"
    if pt == "no_play":
        return "Penalty"
    return ""


TWO_MIN_S = 120.0


def _plays_football(g: pd.DataFrame, need_wall: bool = True) -> tuple[list[dict], list[tuple[float, float]]]:
    """nflfastR rows -> the ESPN summary play dicts the live parser reads (NFL), made to look like the
    ESPN feed the live tracker sees (data/archive/nfl, 150 games):
    - need_wall=False (NFM tokens only, seasons before 2011 have no time_of_day) keeps every play, wallclock None;
    - the extra point / two-point try folds into the touchdown entry (ESPN has no separate entry);
    - END QUARTER / END GAME rows (no time_of_day in nflfastR) take the previous play's wallclock, which
      is exactly what ESPN stamps (median 0 s);
    - nflfastR has no two-minute-warning rows: the (prev play, next play) wallclock spans that cross
      2:00 in Q2/Q4 are returned, and build_football drops the windows inside them (an unseen break).
    """
    out, skip, prev_w, prev = [], [], None, None
    for r in g.itertuples(index=False):
        if r.play_type in ("extra_point",) or r.two_point_attempt == 1:
            continue
        tt = _nfl_type(r)
        if not tt:
            continue
        q = int(r.qtr) if r.qtr == r.qtr else None
        wall = r.time_of_day if isinstance(r.time_of_day, str) else None
        if wall is None and need_wall:
            if tt not in ("End Period", "End of Half", "End of Game") or prev_w is None:
                continue
            wall = prev_w
        clock = espn._clock_s(r.time) if isinstance(r.time, str) else None
        if prev is not None and q in (2, 4) and prev[0] == q and prev[1] is not None and clock is not None \
                and prev[1] > TWO_MIN_S >= clock and tt not in ("End Period", "End of Half", "End of Game"):
            skip.append((prev[2], wall))
        out.append({"id": f"{r.game_id}:{int(r.play_id)}", "type": {"text": tt},
                    "text": r.desc if isinstance(r.desc, str) else "", "period": {"number": q},
                    "clock": {"displayValue": r.time if isinstance(r.time, str) else ""},
                    "wallclock": wall, "scoringPlay": bool(r.sp == 1),
                    "homeScore": r.total_home_score, "awayScore": r.total_away_score})
        prev_w, prev = wall, (q, clock, wall)
    return out, [(espn._ts(a), espn._ts(b)) for a, b in skip]


NFL_COLS = ["game_id", "play_id", "time_of_day", "time", "qtr", "play_type", "desc", "timeout", "touchdown",
            "field_goal_result", "extra_point_result", "interception", "fumble_lost", "sack", "complete_pass",
            "safety", "sp", "total_home_score", "total_away_score", "season_type", "two_point_attempt"]


def build_football(league: str, path: Path, max_games: int | None = None) -> pd.DataFrame:
    lg = League(league)
    import pyarrow.parquet as pq
    have = set(pq.read_schema(path).names)
    if "time_of_day" not in have:
        return pd.DataFrame()
    df = pd.read_parquet(path, columns=[c for c in NFL_COLS if c in have])
    df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
    if max_games:
        df = df[df.game_id.isin(df.game_id.unique()[:max_games])]
    out = []
    for gid, g in df.groupby("game_id", sort=False):
        plays, skip = _plays_football(g)
        ws = [w for w in windows_for_game(lg, plays)
              if not any(a - 1.0 <= w["start"] <= b + 1.0 for a, b in skip)]
        playoff = int(g.season_type.iloc[0] == "POST")
        for w in ws:
            w.update(league=league, game_id=str(gid), playoff=playoff)
        out.extend(ws)
    t = pd.DataFrame(out)
    if len(t):
        t["night"] = [night_of(s) for s in t.start]
    return t


ESPN_KEEP = ("id", "sequenceNumber", "type", "text", "period", "clock", "wallclock", "homeScore", "awayScore",
             "scoringPlay", "scoreValue")


def espn_game(summary: dict) -> dict | None:
    """One ESPN summary -> the compact record the factory keeps (plays with only the fields the live
    parser reads, plus the season type)."""
    plays = summary.get("plays") or [p for d in (summary.get("drives") or {}).get("previous", [])
                                      for p in d.get("plays", [])]
    plays = [{k: p.get(k) for k in ESPN_KEEP if k in p} for p in plays]
    if not any(p.get("wallclock") for p in plays):
        return None
    hdr = summary.get("header") or {}
    side = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation") or "?"
            for c in ((hdr.get("competitions") or [{}])[0].get("competitors") or [])}
    return {"id": str(hdr.get("id") or ""), "season_type": (hdr.get("season") or {}).get("type"), "plays": plays,
            "matchup": f"{side.get('away', '?')} @ {side.get('home', '?')}"}


def build_espn(league: str, path: Path, max_games: int | None = None) -> pd.DataFrame:
    """ESPN summaries (jsonl.gz of espn_game records) -> windows, through the exact live parser."""
    import gzip
    import json
    lg = League(league)
    out = []
    with gzip.open(path, "rt") as f:
        for n, line in enumerate(f):
            if max_games and n >= max_games:
                break
            g = json.loads(line)
            ws = windows_for_game(lg, g["plays"])
            for w in ws:
                w.update(league=league, game_id=g["id"], playoff=int(g.get("season_type") == 3))
            out.extend(ws)
    t = pd.DataFrame(out)
    if len(t):
        t["night"] = [night_of(s) for s in t.start]
    return t


def build(league: str, path: Path, max_games: int | None = None) -> pd.DataFrame:
    if SOURCES.get(league, {}).get("espn"):
        return build_espn(league, path, max_games)
    return (build_football if League(league).base == League.NFL else build_basketball)(league, path, max_games)


def build_basketball(league: str, path: Path, max_games: int | None = None) -> pd.DataFrame:
    lg = League(league)
    cols = ["id", "sequence_number", "type_text", "text", "period_number", "clock_display_value", "wallclock",
            "scoring_play", "score_value", "home_score", "away_score", "game_id", "season", "season_type"]
    import pyarrow.parquet as pq
    have = set(pq.read_schema(path).names)
    if "wallclock" not in have:      # hoopR seasons before ~2013 carry no wall clock: nothing to label
        return pd.DataFrame()
    df = pd.read_parquet(path, columns=[c for c in cols if c in have])
    df = df[df.type_text.fillna("").ne("Substitution") & df.period_number.notna() & df.wallclock.notna()]
    df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
    df = df.sort_values(["game_id", "sq"], kind="stable")
    if max_games:
        df = df[df.game_id.isin(df.game_id.unique()[:max_games])]
    out = []
    for gid, g in df.groupby("game_id", sort=False):
        ws = windows_for_game(lg, _plays_basketball(g))
        playoff = int(g.season_type.iloc[0] == 3) if "season_type" in g else 0
        for w in ws:
            w.update(league=league, game_id=str(gid), playoff=playoff)
        out.extend(ws)
    t = pd.DataFrame(out)
    if len(t):
        t["night"] = [night_of(s) for s in t.start]
    return t


def drop_eval_nights(t: pd.DataFrame, league: str) -> pd.DataFrame:
    cut = EVAL_FIRST_NIGHT.get(league)
    return t[t.night < cut].reset_index(drop=True) if cut and len(t) else t


def assign_splits(t: pd.DataFrame, calib_frac: float = 0.15, test_frac: float = 0.15) -> pd.DataFrame:
    """Whole nights, oldest -> newest: train | calib | test."""
    nights = sorted(t.night.unique())
    n = len(nights)
    n_test = max(1, int(round(n * test_frac)))
    n_cal = max(1, int(round(n * calib_frac)))
    test = set(nights[n - n_test:])
    cal = set(nights[n - n_test - n_cal:n - n_test])
    t = t.copy()
    t["split"] = np.where(t.night.isin(test), "test", np.where(t.night.isin(cal), "calib", "train"))
    return t


def manifest(t: pd.DataFrame) -> dict:
    by = {}
    for (lg, sp), g in t.groupby(["league", "split"]):
        by.setdefault(lg, {})[sp] = {"rows": int(len(g)), "nights": int(g.night.nunique()),
                                     "games": int(g.game_id.nunique()), "first": g.night.min(),
                                     "last": g.night.max()}
    return by


def seasons(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    return list(range(int(a), int(b or a) + 1))


def concat(parts: Iterable[pd.DataFrame]) -> pd.DataFrame:
    parts = [p for p in parts if p is not None and len(p)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
