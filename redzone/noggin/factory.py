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
Queued (loaders not written yet): nfl (nflfastR `time_of_day` 2011+), cfb (cfbfastR 2014+), nhl
(fastRhockey + archived ESPN wallclock), mlb (pitch timestamps 2008+). See docs/noggin.md § Phases.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from redzone.models import League
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
}
# First night of the pinned scorecard window (data/eval_pin.json, eval-data-v1). Nothing on or after it
# is ever written by the factory. College has no scorecard yet; it uses the NBA date as a safe cut.
EVAL_FIRST_NIGHT = {"nba": "20261020", "cbb": "20261020", "wcbb": "20261020", "nfl": "20250905",
                    "nhl": "20261019"}

def _iso(s: pd.Series) -> np.ndarray:
    t = pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")
    return (t.astype("int64") / 1e9).where(t.notna(), np.nan).to_numpy(float)


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
