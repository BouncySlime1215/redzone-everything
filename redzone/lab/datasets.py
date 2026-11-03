"""Training datasets: per-league game-state snapshots with break / key-moment / hotness labels.

Built by ``scripts/build_training.py`` (logic in ``redzone.lab.sportsdata``) into
``data/training/<league>.parquet`` (git-ignored). One row = the state of a game right
after one logged event (play, shot, stoppage, plate appearance). Games are sorted and
rows within a game are in event order.

Leagues: nfl, cfb, nba, cbb (men's college), wcbb (women's college), nhl, mlb, plus
``espn_<league>`` sets rebuilt from our own ``data/archive`` ESPN summaries (the same
feed the live service reads, classified by ``redzone.sources.espn.stoppage_for``).

Labels
------
break_soon       a real break (timeout / TV timeout / end of period / review / injury /
                 pitching change / post-score break / two-minute warning) starts within the
                 next 60 s of GAME clock (MLB: wall clock; there is no game clock).
break_soon_wall  same, measured on the wall clock where the feed has timestamps.
key_soon         a key moment happens within the next KEY_WINDOW_S[league] game seconds.
key_now          this event itself is a key moment ("must-watch regardless").
hotness          0-100 game-flow score: smoothed blend of trailing and upcoming
                 win-probability movement plus current leverage, as a percentile within
                 the league (50 = a median moment). ``hot_raw`` is the pre-percentile value;
                 ``manifest.json`` stores the 101 percentile knots to map live values.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRAINING_DIR = ROOT / "data" / "training"
MANIFEST = TRAINING_DIR / "manifest.json"

LEAGUES = ("nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb")
ESPN_LEAGUES = ("espn_nfl", "espn_nba", "espn_nhl", "espn_mlb")

# Stoppage codes (string values match redzone.models.Stoppage, plus "game_end").
STOPPAGES = ("none", "whistle", "timeout", "two_minute_warning", "end_period", "halftime",
             "intermission", "review", "injury", "pitching_change", "score_break", "game_end",
             "unknown")
STOPPAGE_CODE = {s: i for i, s in enumerate(STOPPAGES)}
BREAKS = frozenset({"timeout", "two_minute_warning", "end_period", "halftime", "intermission",
                    "review", "injury", "pitching_change", "score_break"})

BREAK_HORIZON_S = 60.0
KEY_WINDOW_S = {"nfl": 180, "cfb": 180, "nba": 120, "cbb": 120, "wcbb": 120, "nhl": 180, "mlb": 300}

META = ["league", "season", "game_id", "seq", "etype"]
FEATURES = [
    "period", "clock_s", "t_game", "frac_left", "is_ot", "wall_s",
    "score_home", "score_away", "score_diff", "abs_diff",
    "wp_home", "wp_lev", "wp_delta", "wp_vol_8", "wp_vol_w",
    "lead_changes", "ties", "run_home", "run_away", "pts_w",
    "possession_home", "yardline_100", "down", "ydstogo",
    "to_home", "to_away", "fouls_home", "fouls_away", "bonus_home", "bonus_away",
    "home_skaters", "away_skaters", "outs", "on_1b", "on_2b", "on_3b", "half_bottom", "balls", "strikes",
    "stoppage", "in_break", "s_since_break", "ev_since_break", "wall_since_break", "crunch",
]
LABELS = ["next_break_s", "break_soon", "next_break_wall_s", "break_soon_wall",
          "next_key_s", "key_soon", "key_now", "hot_raw", "hotness"]


def path_for(league: str, root: Path | None = None) -> Path:
    return (root or TRAINING_DIR) / f"{league}.parquet"


def available(root: Path | None = None) -> list[str]:
    d = root or TRAINING_DIR
    return sorted(p.stem for p in d.glob("*.parquet")) if d.exists() else []


def manifest(root: Path | None = None) -> dict:
    p = (root or TRAINING_DIR) / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load(league: str, columns: list[str] | None = None, seasons: list[int] | None = None,
         root: Path | None = None):
    """Load one league's snapshots as a pandas DataFrame."""
    import pandas as pd

    p = path_for(league, root)
    if not p.exists():
        raise FileNotFoundError(f"{p} missing: run `python scripts/build_training.py {league}`")
    filters = [("season", "in", list(seasons))] if seasons else None
    return pd.read_parquet(p, columns=columns, filters=filters)


def split_of(game_id: str, valid: float = 0.15, test: float = 0.15) -> str:
    """Deterministic train/valid/test split by game, so no game leaks across splits."""
    u = (zlib.crc32(str(game_id).encode()) % 10_000) / 10_000
    return "test" if u < test else "valid" if u < test + valid else "train"


def xy(df, label: str, features: list[str] | None = None):
    """Feature matrix (float32, NaN for not-applicable) and one label column."""
    cols = [c for c in (features or FEATURES) if c in df.columns]
    return df[cols].astype("float32"), df[label]


def splits(df, label: str, features: list[str] | None = None):
    """{'train'|'valid'|'test': (X, y)} split by game id."""
    s = df["game_id"].astype(str).map(split_of)
    out = {}
    for name in ("train", "valid", "test"):
        part = df[s.values == name]
        out[name] = xy(part, label, features)
    return out


def summary(league: str, root: Path | None = None) -> dict:
    """Rows, games and label rates for one league (reads only the label columns)."""
    df = load(league, columns=["game_id", "break_soon", "break_soon_wall", "key_soon", "key_now",
                               "hotness"], root=root)
    out = {"rows": int(len(df)), "games": int(df["game_id"].nunique())}
    for c in ("break_soon", "break_soon_wall", "key_soon", "key_now"):
        v = df[c].dropna()
        out[f"{c}_rate"] = round(float(v.mean()), 4) if len(v) else None
    out["hotness_mean"] = round(float(df["hotness"].mean()), 1)
    return out
