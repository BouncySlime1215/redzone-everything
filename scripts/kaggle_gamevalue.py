#!/usr/bin/env python3
"""Train GameValue v0 (state table) and v1 (LightGBM) on Kaggle CPU and export both for the numpy runtime.

    python scripts/kaggle_gamevalue.py --shards <value_shards dir> --out /kaggle/working/gamevalue --raw /kaggle/working/raw --leagues nfl
    python scripts/kaggle_gamevalue.py --shards <dir> --out <dir> --leagues nba,cbb --personal-nba   # --personal-hoopr is the same flag
    RZ_GV_FEATURES=v3 python scripts/kaggle_gamevalue.py --shards <v3 dir> ... --challenger tabpfn --challenger-rows 20000
    RZ_GV_FEATURES=v4 python scripts/kaggle_gamevalue.py --shards <v4 dir> ... --leagues nfl,nba,cbb --personal-nba --trials base,deep,rank,rank_deep --budget-hours 10
    RZ_GV_FEATURES=v4 python scripts/kaggle_gamevalue.py --shards <v4 dir> ... --leagues nfl,nba,cbb --personal-nba --pooled-only --trials base,rank --budget-hours 10

The raw seasons are never downloaded here (github.com release paths are disallowed by its robots file): the NFL ones come
from the staged output of agent/kaggle/fetch_sports_nfl.py (api.github.com), the NBA/CBB ones from fetch_sports_nba.py /
fetch_sports_ncaam.py (scripts/kaggle_value_shards.py stages them into --raw); a missing file stops the run and is named.

Why: the brain's value estimate needs more games than the 8 GB Mac should train on, and every step that could quietly
make the later gates a lie has to refuse instead: leaked rows, a numpy export that differs from the booster, a model
without its licence tag (the-mind steps 3-4, build plan WP-26). Nick launches it; it never runs on the Mac.

Order: find the staged raw seasons, leak check with the raw audit (scripts/value_leak_check.py; any failure exits 2 before a row is read), then per league
forward-chaining folds on the train + calib nights (whole nights, oldest fit; mean and worst fold), then the final fit on
train with isotonic on calib, one score on the test nights, export, reload, and a check that the file gives the same
numbers. Public rows only, with a full 180 s label window: archive rows are Ring 2 and go through scripts/holdset.py.

The K=2 / 4 / 8 capture printed here is scripts/holdset.py's capture, not a look-alike: one shared 180 s clock per night, the
feed read at the decision with no lag, y over (t, t + 180 s], from the raw seasons. The state table, closest score, random
and hindsight are computed on the same nights and printed beside the model (holdset_public.fold_report). The bar that can
promote a model is scripts/kaggle_gamevalue_holdset.py, on the dev test nights against the real Planner on those nights.

Calibration is reported apart from ranking and never changes the capture: per league the Tier 1 head gets Platt scaling when the
calibration nights hold few independent events and isotonic with plenty (research log, 2026-11-09), and the check under `calibration`
leaves out garbage time, where every probability is near zero and flatters the extreme bins.

--objective / --trials try other value heads on the same folds (features v4 and up): LambdaRank across the games of one decision, deeper trees.
The winner is the best mean CV K=4, one per league, and only the winner reaches the test nights; every trial's CV score and the trial count go in the
manifest. --budget-hours stops starting new trials once the clock is spent, and the first trial always runs.

--pooled also trains one model on every attached league (league "all", file gamevalue_all_v1.npz) and reports per-league capture for pooled against
per-league on each league's own test nights, then the cross-sport test: at decisions with games of two leagues live, does the ranking hold across
sports against hindsight. Pooled fits read only nights before the earliest test night of any league, so no night it is graded on is older than a
night it learned from. That costs the newer leagues some recent rows and the manifest says how many. --pooled-only reads per-league files from --out.

--challenger fits another tabular model on a night-stratified sample and grades it on the same shared-clock capture, folds and test
nights as v1. It is report-only: no file is exported and it can never promote. A challenger that fails is recorded, not fatal.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from redzone.models_rt.forest import Nodes  # noqa: E402
from redzone.noggin import gamevalue as G  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from redzone.noggin.predict_fit import isotonic, logloss  # noqa: E402
from redzone.noggin.timing_fit import ece  # noqa: E402
from redzone.scoring.brain3 import flatten  # noqa: E402
from scripts import holdset  # noqa: E402
from scripts import holdset_public as HP  # noqa: E402
from scripts import kaggle_gamevalue_holdset as HOLD  # noqa: E402
from scripts import value_leak_check as LC  # noqa: E402

EXIT_REFUSED, EXIT_MAC = 2, 3
PARAMS = dict(num_leaves=31, max_depth=6, min_data_in_leaf=500, learning_rate=0.05, feature_fraction=0.9, bagging_fraction=0.8,
              bagging_freq=1, lambda_l2=1.0, seed=7, deterministic=True, force_col_wise=True, verbose=-1)
TWEEDIE_POWER = 1.5           # the target is a sum of weighted events with 40-45% zeros (the-mind section 3)
ROUNDS = 150                  # the-mind section 8: 300 trees is at the 5 ms edge for 32 games
SHRINK = 20.0                 # scripts/holdset.py fit
PARITY_TOL, PARITY_ROWS, MAX_BAD_ROWS, SCORE_BATCH = 1e-4, 2000, 0.01, 200_000
KS = (2, 4)
CAL_ISOTONIC_MIN_EVENTS = 10_000   # independent Tier 1 bursts on the calibration nights; below this a 2-parameter Platt fit (research log)
PLATT_KNOTS, PLATT_EPS = 513, 1e-6
GARBAGE_TIME = {"nfl": (17.0, 360.0), "nba": (20.0, 360.0), "cbb": (20.0, 300.0)}   # margin at least, game seconds left at most
CHALLENGER_ROWS, CHALLENGER_BATCH = 20_000, 5_000
RANK_BUCKET_S, RANK_K, RANK_MAX_LABEL = 180.0, 4, 127     # holdset's decision spacing and the K the bar is set at; labels are half points
RANK_KNOT_EPS = 1e-6
POOLED_ROWS = 3_000_000       # most rows from one league in a pooled fit, so the league with the most games does not decide the model
# Tree budget: a measured numpy walk of 500 depth-6 trees takes about 2 ms for 32 games, two heads included, so none of these leaves the 5 ms.


@dataclass(frozen=True)
class Trial:
    """One way to fit the value head. `rounds` None means the trainer's own; `params` go over PARAMS."""
    name: str
    objective: str = "tweedie"
    rounds: int | None = None
    params: dict = field(default_factory=dict)


DEEP = {"max_depth": 7, "num_leaves": 63, "min_data_in_leaf": 200, "learning_rate": 0.04}
BASE_TRIAL = Trial("base")
TRIALS = {t.name: t for t in (BASE_TRIAL, Trial("deep", rounds=250, params=DEEP), Trial("rank", "lambdarank", 200, {"min_data_in_leaf": 200}),
                              Trial("rank_deep", "lambdarank", 250, DEEP))}


def _columns() -> list[str]:
    return ["night", "game_id", "source", "license", "decision_t", "label_full_window", *G.GV_FEATURES, *V.VALUE_LABELS]


class Refused(RuntimeError):
    """Training must not go on: the reason is the message."""


class LightGBMTrainer:
    """The real thing, imported only when a fit runs."""

    def __init__(self, rounds: int = ROUNDS):
        self.rounds = rounds

    def fit(self, X: np.ndarray, y: np.ndarray, objective: str, rounds: int | None = None, params: dict | None = None, group=None, weight=None):
        import lightgbm as lgb
        full = dict(PARAMS, **(params or {}), objective=objective, num_threads=os.cpu_count() or 4)
        if objective == "tweedie":
            full["tweedie_variance_power"] = TWEEDIE_POWER
        if objective == "lambdarank":
            full.update(label_gain=[i / 2.0 for i in range(RANK_MAX_LABEL + 1)], lambdarank_truncation_level=RANK_K)
        data = lgb.Dataset(X, y, feature_name=list(G.GV_FEATURES), group=group, weight=weight)
        return lgb.train(full, data, rounds or self.rounds)


def _X(df: pd.DataFrame) -> np.ndarray:
    return df[list(G.GV_FEATURES)].to_numpy(np.float32)


def forward_folds(nights: list[str], folds: int) -> list[tuple[list[str], list[str]]]:
    """(fit nights, validation nights): the newest half splits into blocks; each block is fit on everything older."""
    nights = sorted(nights)
    half = len(nights) // 2
    out = []
    for block in np.array_split(np.array(nights[half:], dtype=object), folds):
        if len(block):
            first = nights.index(block[0])
            if first:
                out.append((nights[:first], [str(x) for x in block]))
    return out


def fit_table(keys: np.ndarray, y: np.ndarray, shrink: float = SHRINK) -> np.ndarray:
    """scripts/holdset.py fit on numpy: each level shrinks toward its parent. Dense, so an unseen state has a value."""
    parent = np.full((), float(y.mean()) if len(y) else 0.0)
    for level in range(1, len(G.GV_TABLE_SHAPE) + 1):
        dims = G.GV_TABLE_SHAPE[:level]
        cell = np.ravel_multi_index(keys[:, :level].T, dims)
        total = np.bincount(cell, y, np.prod(dims)).reshape(dims)
        count = np.bincount(cell, minlength=np.prod(dims)).reshape(dims)
        parent = (total + shrink * parent[..., None]) / (count + shrink)
    return parent


def license_fields(df: pd.DataFrame) -> dict:
    """What the rows came from. Any personal-use line or source tags the whole model (decisions.md, 2026-11-05)."""
    sources, lines = sorted(df.source.astype(str).unique()), sorted(df.license.astype(str).unique())
    personal = any(G.PERSONAL_USE in x for x in lines) or any(s in G.GV_PERSONAL_SOURCES for s in sources)
    return {"sources": sources, "license_lines": lines, "license_tag": G.PERSONAL_USE if personal else ""}


def _meta(df: pd.DataFrame, league: str, version: str, kind: str, leak: dict) -> dict:
    return {"schema": G.GV_SCHEMA, "kind": kind, "version": version, "league": league, "features": list(G.GV_FEATURES),
            "features_version": G.GV_FEATURES_VERSION, "status": G.GV_STATUS, "scale_points": G.GV_SCALE_POINTS, "leak_check": leak,
            "rows": int(len(df)), "nights": [str(df.night.min()), str(df.night.max())], **license_fields(df)}


def fit_v0(tr: pd.DataFrame, league: str, leak: dict) -> tuple[dict, dict]:
    keys = G.table_key(tr[list(G.GV_FEATURES)].to_numpy(float), league)
    arrays = {"value_table": fit_table(keys, tr.value_next_3m.to_numpy(float)), "p1_table": fit_table(keys, tr.p_tier1_60s.to_numpy(float))}
    return arrays, _meta(tr, league, "v0", "table", leak)


KNOT_EPS = 1e-9


def _flat(booster, X_check: np.ndarray, name: str, link) -> tuple[dict, int]:
    """LightGBM dump -> node arrays. Refuses when the numpy walk or the runtime's link differs from the booster on real rows."""
    arrays, info = flatten({"h": [dict(t, slot=0) for t in booster.dump_model()["tree_info"]]}, list(G.GV_FEATURES))
    arrays.pop("slot")
    nodes = Nodes(arrays, info["depth"])
    raw = nodes.value[nodes.leaves_batch(X_check)].sum(axis=1)
    gap = max(float(np.max(np.abs(raw - booster.predict(X_check, raw_score=True)))),
              float(np.max(np.abs(link(raw) - booster.predict(X_check)))))
    if gap > PARITY_TOL:
        raise Refused(f"numpy export differs from the booster on {name}: {gap}")
    return arrays, info["depth"]


def clean_knots(kx: np.ndarray, ky: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Make isotonic knots a valid curve for the runtime: x and y non-decreasing, y inside 0-1.

    Cause of WP-26D's 'not monotone' refusal: isotonic() takes np.mean of each merged block's bin means, so a block of
    tied predictions (a tree head emits many identical scores) averages to a value 1 ulp off the next block's identical
    mean, and x steps back by ~1e-16. Rule: round-off only. Clip y to [0, 1], then running-max x and y. Anything bigger than
    1e-9 is a real fault, not round-off, and raises so the runtime check is never made to pass by this clean-up.
    """
    kx, ky = np.asarray(kx, float), np.asarray(ky, float)
    fx, fy = np.maximum.accumulate(kx), np.maximum.accumulate(np.clip(ky, 0.0, 1.0))
    if (not np.isfinite(kx).all() or not np.isfinite(ky).all() or np.abs(fx - kx).max(initial=0.0) > KNOT_EPS
            or np.abs(fy - ky).max(initial=0.0) > KNOT_EPS):
        raise Refused("calibration knots are off by more than round-off; not cleaning them up")
    return fx, fy


def effective_events(df: pd.DataFrame) -> int:
    """How many independent Tier 1 bursts the rows hold: a run of positive rows in one game is one event, because rows every 30 s
    of the same moment are near-duplicates and raw row counts would claim isotonic has far more evidence than it does."""
    order = df.sort_values(["game_id", "decision_t"], kind="stable")
    y, game = order.p_tier1_60s.to_numpy(float) > 0, order.game_id.astype(str).to_numpy()
    if not len(y):
        return 0
    starts = np.ones(len(y), bool)
    starts[1:] = (game[1:] != game[:-1]) | ~y[:-1]
    return int((y & starts).sum())


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, PLATT_EPS, 1.0 - PLATT_EPS)
    return np.log(p / (1.0 - p))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500.0, 500.0)))


def platt_fit(p: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float]:
    """Logistic regression of y on logit(p) (Newton, slight ridge). The slope stays >= 0, so the curve cannot invert the ranking."""
    x, y = _logit(np.asarray(p, float)), np.asarray(y, float)
    a, b = 1.0, 0.0
    for _ in range(iters):
        q = np.clip(_sigmoid(a * x + b), PLATT_EPS, 1.0 - PLATT_EPS)
        w = q * (1.0 - q)
        grad = np.array([np.dot(x, q - y) + 1e-6 * a, (q - y).sum() + 1e-6 * b])
        hess = np.array([[np.dot(w, x * x) + 1e-6, np.dot(w, x)], [np.dot(w, x), w.sum() + 1e-6]])
        try:
            da, db = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            break
        a, b = max(a - float(da), 0.0), b - float(db)
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def platt_knots(a: float, b: float) -> tuple[np.ndarray, np.ndarray]:
    """The Platt curve as knots on the logit scale, so the runtime's np.interp reads it with no new file format."""
    x = _sigmoid(np.linspace(_logit(np.array([0.0]))[0], _logit(np.array([1.0]))[0], PLATT_KNOTS))
    x = np.concatenate(([0.0], x, [1.0]))
    return np.maximum.accumulate(x), np.maximum.accumulate(_sigmoid(a * _logit(x) + b))


def calibrate(p: np.ndarray, ca: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, dict]:
    """(knots x, knots y, what was chosen) for one league: Platt when the calibration nights hold few independent events."""
    y, events = ca.p_tier1_60s.to_numpy(float), effective_events(ca)
    if events >= CAL_ISOTONIC_MIN_EVENTS:
        kx, ky = clean_knots(*isotonic(p, y))
        return kx, ky, {"method": "isotonic", "effective_events": events, "min_events_for_isotonic": CAL_ISOTONIC_MIN_EVENTS}
    a, b = platt_fit(p, y)
    kx, ky = clean_knots(*platt_knots(a, b))
    return kx, ky, {"method": "platt", "a": round(a, 6), "b": round(b, 6), "effective_events": events,
                    "min_events_for_isotonic": CAL_ISOTONIC_MIN_EVENTS}


def garbage_time(df: pd.DataFrame, league: str) -> np.ndarray:
    """True where the game is decided: only ever used to leave rows out of the calibration check, never the capture."""
    margin, left = GARBAGE_TIME[league]
    return (df.abs_margin.to_numpy(float) >= margin) & (df.game_left.to_numpy(float) >= 0) & (df.game_left.to_numpy(float) <= left)


@dataclass(frozen=True)
class RankQueries:
    """LambdaRank's view of the rows: one row per game per decision, whole decisions together."""
    idx: np.ndarray
    sizes: np.ndarray
    labels: np.ndarray
    weight: np.ndarray


def rank_queries(df: pd.DataFrame, bucket_s: float = RANK_BUCKET_S) -> RankQueries:
    """Group rows into decisions the way the capture does: every RANK_BUCKET_S of one night, one row per game (nearest the bucket's middle), at least two games.
    Each decision is weighted by the value its best K games hold, which undoes LightGBM's per-query normalisation: the capture is a sum over decisions,
    so a decision with much to win must count for more. Labels are half points, the smallest step the tiers can make."""
    t = df.decision_t.to_numpy(float)
    frame = pd.DataFrame({"pos": np.arange(len(df)), "night": df.night.astype(str).to_numpy(), "game": df.game_id.astype(str).to_numpy(),
                          "b": np.floor(t / bucket_s), "off": np.abs(t - (np.floor(t / bucket_s) + 0.5) * bucket_s), "y": df.value_next_3m.to_numpy(float)})
    frame = frame.sort_values(["off", "pos"], kind="stable").drop_duplicates(["night", "b", "game"]).sort_values(["night", "b", "game"], kind="stable")
    group = frame.groupby(["night", "b"], sort=False).game.transform("size").to_numpy()
    frame = frame[group >= 2]
    sizes = frame.groupby(["night", "b"], sort=False).size().to_numpy()
    y = frame.y.to_numpy(float)
    weight, at = np.zeros(len(y)), 0
    for size in sizes:
        weight[at:at + size] = np.sort(y[at:at + size])[-RANK_K:].sum()
        at += size
    return RankQueries(frame.pos.to_numpy(int), sizes, np.clip(np.round(y * 2.0), 0, RANK_MAX_LABEL).astype(int), weight)


def rank_knots(score: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ranker score -> expected points on nights the trees never saw: isotonic, then strictly increasing so no two scores read the same points."""
    kx, ky = isotonic(np.asarray(score, float), np.asarray(y, float))
    kx, first = np.unique(np.maximum.accumulate(kx), return_index=True)
    ky = np.maximum.accumulate(np.maximum(ky[first], 0.0)) + RANK_KNOT_EPS * np.arange(1, len(first) + 1)
    if len(kx) < 2:
        kx, ky = np.array([kx[0], kx[0] + 1.0]), np.array([ky[0], ky[0] + RANK_KNOT_EPS])
    return kx, ky


def _fit_value(trainer, X: np.ndarray, frame: pd.DataFrame, trial: Trial):
    """The value head of one trial. The default trial makes today's call, so a run without --trials trains what it always did."""
    if trial is BASE_TRIAL:
        return trainer.fit(X, frame.value_next_3m.to_numpy(float), "tweedie")
    if trial.objective == "lambdarank":
        q = rank_queries(frame)
        if not len(q.idx):
            raise Refused("no decision has two games live: nothing to rank")
        return trainer.fit(X[q.idx], q.labels, "lambdarank", rounds=trial.rounds, params=trial.params, group=q.sizes, weight=q.weight)
    return trainer.fit(X, frame.value_next_3m.to_numpy(float), trial.objective, rounds=trial.rounds, params=trial.params)


def fit_v1(tr: pd.DataFrame, ca: pd.DataFrame, league: str, trainer, leak: dict, trial: Trial = BASE_TRIAL) -> tuple[dict, dict]:
    """Value head on tr. Tier 1 head on tr, isotonic on ca, which nothing was fit on."""
    X, Xc = _X(tr), _X(ca)
    check = Xc[:PARITY_ROWS]
    value = _fit_value(trainer, X, tr, trial)
    p1 = trainer.fit(X, tr.p_tier1_60s.to_numpy(float), "binary")
    rank = trial.objective == "lambdarank"
    v_arrays, v_depth = _flat(value, check, "value", (lambda r: r) if rank else np.exp)
    p_arrays, p_depth = _flat(p1, check, "p_tier1_60s", lambda m: 1.0 / (1.0 + np.exp(-m)))
    kx, ky, chosen = calibrate(p1.predict(Xc), ca)
    arrays = {**{f"value_{k}": a for k, a in v_arrays.items()}, **{f"p1_{k}": a for k, a in p_arrays.items()},
              "p1_cal_x": kx, "p1_cal_y": ky}
    meta = {**_meta(tr, league, "v1", "trees", leak), "value_depth": v_depth, "p1_depth": p_depth, "calib_rows": int(len(ca)), "p1_calibration": chosen,
            "params": PARAMS, "rounds": getattr(trainer, "rounds", None), "tweedie_power": TWEEDIE_POWER}
    if trial is not BASE_TRIAL:
        meta.update(trial=trial.name, value_objective=trial.objective, value_rounds=trial.rounds, value_params=trial.params)
    if rank:
        arrays["value_cal_x"], arrays["value_cal_y"] = rank_knots(np.asarray(value.predict(Xc, raw_score=True), float), ca.value_next_3m.to_numpy(float))
        meta["value_link"] = "rank"
    return arrays, meta


def predict_all(model: G.GameValueModel, df: pd.DataFrame) -> G.Batch:
    """In batches: walking every tree over millions of rows at once would need (rows x trees) of index memory."""
    X = df[list(G.GV_FEATURES)].to_numpy(float)
    parts = [model.predict_matrix(X[i:i + SCORE_BATCH], max_rows=None) for i in range(0, len(X), SCORE_BATCH)]
    return G.Batch(*(np.concatenate(a) for a in zip(*parts)))


class SharedClock:
    """The raw seasons of one league as holdset rows, and the summaries a model is ranked from."""

    def __init__(self, league: str, rows: dict, games: dict):
        self.league, self.rows, self.games = league, rows, games

    def report(self, model: G.GameValueModel, fit_nights: list[str], test_nights: list[str]) -> dict:
        """holdset's capture on these nights: the model beside table, closest score, random and hindsight. {} when nothing scores."""
        ranker = HOLD.ModelRanker(model, games=lambda _league, night: self.games.get(night, []))
        got = HP.fold_report(self.league, self.rows, [n for n in fit_nights if self.rows.get(n)],
                             [n for n in test_nights if self.rows.get(n)], ranker)
        out = {}
        for name, cell in (got or {}).items():
            if isinstance(cell, dict):
                cell = dict(cell)
                cell["model_pct"] = cell.pop("planner_pct")
            out[name] = cell
        return out


def shared_clock(league: str, raw: Path, seasons: list[str], nights: set[str]) -> SharedClock:
    """One league's raw seasons, through the same adapter and eval cut as holdset_public. Locked nights never get this far."""
    files = [p for p in (raw / league / f"pbp_{s}.parquet" for s in seasons) if p.exists()]
    if not files:
        raise Refused(f"{league}: no raw seasons in {raw}")
    rows, games = HP.public_rows(league, HP.read_public(league, files), keep_games=nights)
    return SharedClock(league, rows, games)


def fetch_raw(shards: Path, raw: Path, leagues: list[str], personal_nba: bool, log=print) -> None:
    """Check that every built season's raw file is staged where the raw audit reads it. It never downloads.

    Why: every raw source here (nflverse, sportsdataverse hoopR) is a github.com release path, github.com's robots file
    disallows those paths, and Nick ruled to respect robots. Attach the raw seasons as a Kaggle input (or the earlier run's
    output) and point --raw at them; whatever is missing is named and the run stops. `personal_nba` is kept for callers."""
    missing = []
    for league in leagues:
        V.public_source(league, personal_nba)            # raises if the league has no cleared source
        for season in sorted(p.stem for p in (shards / league).glob("*.parquet")):
            have = raw / league / f"pbp_{season}.parquet"
            if not (have.exists() and have.stat().st_size > 0):
                missing.append(str(have))
    if missing:
        shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
        raise Refused(f"raw files are not attached and this script does not download them (github.com release paths are "
                      f"disallowed by its robots file); missing {len(missing)}: {shown}")


def calibration_check(p: np.ndarray, y: np.ndarray, garbage: np.ndarray) -> dict:
    """Calibration of the Tier 1 probability on the rows that are not garbage time, kept apart from the capture numbers."""
    keep = ~garbage
    if not keep.any():
        return {"rows": 0, "garbage_rows_excluded": int(garbage.sum()), "ece": None, "brier": None, "logloss": None}
    return {"rows": int(keep.sum()), "garbage_rows_excluded": int(garbage.sum()), "ece": round(float(ece(p[keep], y[keep])), 4),
            "brier": round(float(np.mean((p[keep] - y[keep]) ** 2)), 4), "logloss": round(logloss(y[keep], p[keep]), 4)}


def grade(model, df: pd.DataFrame, clock: SharedClock, fit_nights: list[str], test_nights: list[str], league: str = "") -> dict:
    b = predict_all(model, df)
    y, p1 = df.value_next_3m.to_numpy(float), df.p_tier1_60s.to_numpy(float)
    keep = b.ok
    shared = clock.report(model, fit_nights, test_nights)
    out = {f"k{k}_pct": (shared.get(f"K{k}") or {}).get("model_pct") for k in KS}
    out["shared_clock"] = shared
    denom = float(((y[keep] - y[keep].mean()) ** 2).sum())
    out["r2"] = round(1.0 - float(((y[keep] - b.raw[keep]) ** 2).sum()) / denom, 4) if denom else None
    out["p1_logloss"] = round(logloss(p1[keep], b.p[keep]), 4)
    out["p1_ece"] = round(float(ece(b.p[keep], p1[keep])), 4)
    out["rows"], out["failed_closed"] = int(len(df)), int((~keep).sum())
    if league:
        out["calibration"] = calibration_check(b.p[keep], p1[keep], garbage_time(df[keep], league))
    return out


def summarize(folds: list[dict]) -> dict:
    """Mean and worst fold of each metric (worst: lowest capture and R2, highest loss and ECE)."""
    out = {}
    for name, bad in (("k2_pct", min), ("k4_pct", min), ("r2", min), ("p1_logloss", max), ("p1_ece", max)):
        vals = [f[name] for f in folds if f.get(name) is not None]
        out[name] = {"mean": round(float(np.mean(vals)), 4), "worst": bad(vals)} if vals else {"mean": None, "worst": None}
    vals = [f["calibration"]["ece"] for f in folds if (f.get("calibration") or {}).get("ece") is not None]
    if vals:
        out["calibration_ece"] = {"mean": round(float(np.mean(vals)), 4), "worst": max(vals)}
    return out


# --- the challenger: another tabular model on the same capture ------------------------------------------------------------


class TabPFNTrainer:
    """TabPFN as a challenger: imported only when it fits, so nothing here needs it unless asked. Attach the package yourself;
    this never installs it, and its weights have their own licence (check it before keeping any result)."""
    name, license = "tabpfn", "TabPFN package and weights: see the model card; report-only here, nothing is exported"

    def fit(self, X: np.ndarray, y: np.ndarray, objective: str):
        from tabpfn import TabPFNClassifier, TabPFNRegressor
        if objective == "binary":
            model = TabPFNClassifier(random_state=7, ignore_pretraining_limits=True).fit(X, y)
            return _Proba(model)
        return TabPFNRegressor(random_state=7, ignore_pretraining_limits=True).fit(X, y)


class _Proba:
    """A classifier as a .predict that returns P(y = 1)."""

    def __init__(self, model):
        self.model = model

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(X)[:, 1]


class ChallengerModel:
    """Duck-types GameValueModel.predict_matrix, so scripts/holdset.py's capture ranks it exactly as it ranks v1."""

    def __init__(self, value, p1, cal_x: np.ndarray, cal_y: np.ndarray):
        self.value, self.p1, self.cal_x, self.cal_y = value, p1, cal_x, cal_y

    def _in_batches(self, head, X: np.ndarray) -> np.ndarray:
        return np.concatenate([np.asarray(head.predict(X[i:i + CHALLENGER_BATCH]), float) for i in range(0, len(X), CHALLENGER_BATCH)])

    def predict_matrix(self, X, max_rows=None) -> G.Batch:
        X = np.asarray(X, float)
        ok = G.valid_rows(X)
        raw, p = np.full(len(X), np.nan), np.full(len(X), np.nan)
        if ok.any():
            Xs = X[ok].astype(np.float32)
            raw[ok] = np.maximum(self._in_batches(self.value, Xs), 0.0)
            p[ok] = np.interp(self._in_batches(self.p1, Xs), self.cal_x, self.cal_y)
        ok &= np.isfinite(raw) & np.isfinite(p)
        raw[~ok], p[~ok] = np.nan, np.nan
        return G.Batch(raw, G.to_scale(raw), p, ok)


def load_challenger(spec: str):
    """'tabpfn' or 'package.module:ClassName'. It must say what it is and under what licence, or it does not run."""
    if spec == "tabpfn":
        return TabPFNTrainer()
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise Refused(f"--challenger {spec!r}: expected tabpfn or package.module:ClassName")
    trainer = getattr(importlib.import_module(module), attr)()
    if not getattr(trainer, "license", "") or not getattr(trainer, "name", ""):
        raise Refused(f"--challenger {spec!r}: a challenger must declare .name and .license")
    return trainer


def sample_rows(df: pd.DataFrame, n: int, seed: int = 7) -> pd.DataFrame:
    """About n rows, the same share of every night and the same rows every run: no night is dropped."""
    if len(df) <= n:
        return df.reset_index(drop=True)
    rng = np.random.default_rng(seed)
    share = n / len(df)
    keep = np.sort(np.concatenate([rng.choice(idx, max(1, int(len(idx) * share)), replace=False) for idx in df.groupby("night").indices.values()]))
    return df.iloc[keep].reset_index(drop=True)


def fit_challenger(tr: pd.DataFrame, ca: pd.DataFrame, trainer, rows: int) -> ChallengerModel:
    """Fit on a sample of the fit nights; calibrate on the calibration nights, which it never saw."""
    fit = sample_rows(tr, rows)
    X = _X(fit)
    value = trainer.fit(X, fit.value_next_3m.to_numpy(float), "tweedie")
    p1 = trainer.fit(X, fit.p_tier1_60s.to_numpy(float), "binary")
    cal = sample_rows(ca, rows)
    kx, ky, _ = calibrate(np.asarray(p1.predict(_X(cal)), float), cal)
    return ChallengerModel(value, p1, kx, ky)


def load_rows(shards: Path, league: str) -> tuple[pd.DataFrame, dict]:
    files = sorted((shards / league).glob("*.parquet"))
    if not files:
        raise Refused(f"{league}: no shards in {shards}")
    versions = {json.loads(f.with_suffix(".json").read_text()).get("features_version") for f in files}
    if versions != {G.GV_FEATURES_VERSION}:
        raise Refused(f"{league}: the shards are features {sorted(map(str, versions))}, this run is {G.GV_FEATURES_VERSION}; "
                      f"build and train under the same {G.GV_FEATURES_ENV}")
    df = pd.concat([pd.read_parquet(f, columns=_columns()) for f in files], ignore_index=True)
    info = {"rows_read": int(len(df))}
    public = df.source.astype(str) != "archive"
    info["archive_rows_skipped"] = int((~public).sum())
    full = df.label_full_window.to_numpy() == 1
    info["short_label_rows_skipped"] = int((public & ~full).sum())
    df = df[public & full].reset_index(drop=True)
    ok = G.valid_rows(df[list(G.GV_FEATURES)].to_numpy(float))
    info["out_of_bounds_rows"] = int((~ok).sum())
    if len(df) == 0 or (~ok).mean() > MAX_BAD_ROWS:
        raise Refused(f"{league}: {info['out_of_bounds_rows']} of {len(df)} rows are outside the runtime's bounds")
    return df[ok].reset_index(drop=True), info


def _gather(df: pd.DataFrame, nights: list[str]) -> pd.DataFrame:
    return df[df.night.isin(set(nights))].reset_index(drop=True)


def pick_trial(cv: dict[str, list[dict]], order: list[str]) -> str:
    """The trial with the best mean CV K=4 among those that finished every fold; the first listed wins a tie."""
    full = max(len(v) for v in cv.values())
    def score(name):
        vals = [f["k4_pct"] for f in cv[name] if f.get("k4_pct") is not None]
        return float(np.mean(vals)) if len(cv[name]) == full and vals else float("-inf")
    return max(order, key=score)


def out_of_time(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() > deadline


def train_league(shards: Path, out: Path, league: str, trainer, folds: int, leak: dict, raw: Path, log=print, challenger=None,
                 challenger_rows: int = CHALLENGER_ROWS, trials: tuple = (BASE_TRIAL,), deadline: float | None = None) -> dict:
    df, info = load_rows(shards, league)
    splits = json.loads((shards / "splits.json").read_text()).get(league) or {}
    train, calib, test = (list(splits.get(s) or []) for s in ("train", "calib", "test"))
    if not train or not calib or not test or not set(df.night) <= set(train + calib + test):
        raise Refused(f"{league}: splits.json does not cover the rows")
    seasons = sorted(p.stem for p in (shards / league).glob("*.parquet"))
    graded = {n for _, val in forward_folds(train + calib, folds) for n in val} | set(test)
    clock = shared_clock(league, raw, seasons, graded)
    rows_cv: dict[str, list] = {"v0": [], "v1": [], "challenger": []}
    trial_cv: dict[str, list] = {t.name: [] for t in trials}
    tried = [trials[0]]
    challenger_error = None
    fold_nights = []
    for fit_nights, val_nights in forward_folds(train + calib, folds):
        c = max(1, round(len(fit_nights) * 0.15))
        if len(fit_nights) <= c:      # calibration must be on nights the trees never saw
            continue
        tr, ca, va = _gather(df, fit_nights[:-c]), _gather(df, fit_nights[-c:]), _gather(df, val_nights)
        a0, m0 = fit_v0(tr, league, leak)
        rows_cv["v0"].append(grade(G.GameValueModel(a0, m0, league), va, clock, fit_nights, val_nights, league))
        for t in trials:
            if t not in tried:
                if len(fold_nights) or out_of_time(deadline):   # a trial starts on the first fold or not at all, so every trial has the same folds
                    continue
                tried.append(t)
            a1, m1 = fit_v1(tr, ca, league, trainer, leak, t)
            trial_cv[t.name].append(grade(G.GameValueModel(a1, m1, league), va, clock, fit_nights, val_nights, league))
        rows_cv["v1"] = trial_cv[trials[0].name]
        if challenger is not None and challenger_error is None:
            try:
                rows_cv["challenger"].append(grade(fit_challenger(tr, ca, challenger, challenger_rows), sample_rows(va, challenger_rows), clock, fit_nights, val_nights, league))
            except Exception as e:  # noqa: BLE001 - a failing challenger must not cost the v0 / v1 run; it is reported below
                challenger_error = f"{type(e).__name__}: {e}"
                log(json.dumps({"league": league, "challenger_error": challenger_error}), flush=True)
        fold_nights.append({"fit": [fit_nights[0], fit_nights[-1]], "validate": [val_nights[0], val_nights[-1]]})
        log(json.dumps({"league": league, "fold": len(fold_nights), "v0": rows_cv["v0"][-1], "v1": rows_cv["v1"][-1]}), flush=True)
    if not fold_nights:
        raise Refused(f"{league}: too few nights for {folds} forward folds")
    tr, ca, te = _gather(df, train), _gather(df, calib), _gather(df, test)
    skipped = [t.name for t in trials if t not in tried]
    best = next(t for t in tried if t.name == pick_trial({t.name: trial_cv[t.name] for t in tried}, [t.name for t in tried]))
    rows_cv["v1"] = trial_cv[best.name]
    report = {**info, "rows_used": int(len(df)), "nights": {"train": len(train), "calib": len(calib), "test": len(test)},
              "folds": fold_nights, "trials": len(tried) + int(challenger is not None), "models": {}, "features_version": G.GV_FEATURES_VERSION,
              **license_fields(pd.concat([tr, ca]))}
    if len(trials) > 1:
        report["trial_search"] = {"chosen": best.name, "skipped_out_of_time": skipped,
                                  "cv": {t.name: summarize(trial_cv[t.name]) for t in tried}}
    for version, (arrays, meta) in (("v0", fit_v0(tr, league, leak)), ("v1", fit_v1(tr, ca, league, trainer, leak, best))):
        path = G.model_path(league, version, out)
        memory = G.GameValueModel(arrays, meta, league)
        stamped = G.save(path, arrays, meta)
        again = G.load(path, league)
        sample = te[:PARITY_ROWS]
        a, b = predict_all(memory, sample), predict_all(again, sample)
        if not (np.array_equal(a.raw, b.raw, equal_nan=True) and np.array_equal(a.p, b.p, equal_nan=True)):
            path.unlink(missing_ok=True)
            raise Refused(f"{league} {version}: reloaded file gives different numbers")
        scored = grade(again, te, clock, train + calib, test, league)
        if scored["k4_pct"] is None:
            path.unlink(missing_ok=True)
            raise Refused(f"{league} {version}: no K=4 decision on the test nights, so there is nothing to compare")
        report["models"][version] = {"file": path.name, "sha256": stamped["sha256"], "bytes": path.stat().st_size, "license_tag": meta["license_tag"],
                                     "p1_calibration": meta.get("p1_calibration"), "cv": {"folds": rows_cv[version], **summarize(rows_cv[version])}, "test": scored}
        log(json.dumps({"league": league, "version": version, "cv": summarize(rows_cv[version]), "test": report["models"][version]["test"]}), flush=True)
    if challenger is not None:
        entry = {"name": getattr(challenger, "name", type(challenger).__name__), "license": getattr(challenger, "license", ""),
                 "rows_cap": challenger_rows, "exported": False, "cv": {"folds": rows_cv["challenger"], **summarize(rows_cv["challenger"])}}
        try:
            if challenger_error is not None:
                raise RuntimeError(challenger_error)
            entry["test"] = grade(fit_challenger(tr, ca, challenger, challenger_rows), sample_rows(te, challenger_rows), clock, train + calib, test, league)
        except Exception as e:  # noqa: BLE001 - recorded in the manifest, the trained models above still stand
            entry["error"] = f"{type(e).__name__}: {e}"
        report["challenger"] = entry
        log(json.dumps({"league": league, "challenger": {k: v for k, v in entry.items() if k != "cv"}}), flush=True)
    may, why = G.promotion(league, None, [])
    report["promotion"] = {"may_promote": may, "why": why,
                           "next": "scripts/kaggle_gamevalue_holdset.py (real Planner on the same dev test nights), then 7 of 7 shadow nights"}
    return report


# --- one model for every sport --------------------------------------------------------------------------------------------


def _night_map(leagues: list[str], shards: Path) -> dict[str, dict[str, list[str]]]:
    all_splits = json.loads((shards / "splits.json").read_text())
    out = {}
    for lg in leagues:
        s = all_splits.get(lg) or {}
        out[lg] = {k: list(s.get(k) or []) for k in ("train", "calib", "test")}
        if not all(out[lg].values()):
            raise Refused(f"{lg}: splits.json does not cover the rows")
    return out


def _clock_inv(value: np.ndarray) -> np.ndarray:
    """0-100 back to raw points."""
    return -G.GV_SCALE_POINTS * np.log1p(-np.minimum(value, 100.0 - 1e-9) / 100.0)


def _pairs(dec: list, leagues: dict[str, str], score) -> tuple[float, int]:
    """Across-league pairs in one decision that hindsight orders: (how many the score orders the same way, how many there are). A tied score is half."""
    good, n = 0.0, 0
    for i, a in enumerate(dec):
        for b in dec[i + 1:]:
            if leagues[a[0]] == leagues[b[0]] or a[3] == b[3]:
                continue
            n += 1
            sa, sb = score(a), score(b)
            good += 0.5 if sa == sb else float((sa > sb) == (a[3] > b[3]))
    return good, n


def cross_sport(clocks: dict, tests: dict[str, list[str]], model_for) -> dict:
    """At every decision with games of two or more leagues live, on nights that are test nights for each league involved: capture at each K, the share of
    across-league pairs ordered as hindsight orders them, and each league's mean predicted points beside its mean actual points. That last pair is the
    scale check: a model whose points mean different things per sport shows a league-sized gap here."""
    nights = sorted({n for lg, ns in tests.items() for n in ns if clocks[lg].rows.get(n)})
    decs, leagues = [], {}
    for night in nights:
        here = [lg for lg in clocks if night in set(tests[lg]) and clocks[lg].rows.get(night)]
        if len(here) < 2:
            continue
        merged = [g for lg in here for g in clocks[lg].rows[night]]
        ranks: dict = {}
        for lg in here:
            ranker = HOLD.ModelRanker(model_for(lg), games=lambda _l, n, lg=lg: clocks[lg].games.get(n, []))
            for t, got in ranker(lg, night, merged).items():
                ranks.setdefault(t, {}).update(got)
            leagues.update({g[0]: lg for g in clocks[lg].rows[night]})
        decs += [d for d in holdset.decisions("nba", merged, ranks) if len({leagues[x[0]] for x in d}) >= 2]
    out: dict = {"decisions": len(decs), "nights": len({n for n in nights})}
    if not decs:
        return out
    for K in KS:
        g, t, best, n = holdset.capture(decs, lambda x: x[5], K, random.Random(0))
        gc, _, _, _ = holdset.capture(decs, lambda x: -x[2], K, random.Random(0))
        if t:
            out[f"K{K}"] = {"decisions": n, "model_pct": round(g / t * 100, 1), "closest_score_pct": round(gc / t * 100, 1), "hindsight_pct": round(best / t * 100, 1)}
    good = total = 0.0
    for d in decs:
        a, b = _pairs(d, leagues, lambda x: x[5])
        good, total = good + a, total + b
    out["across_league_pairs"] = int(total)
    out["pair_order_pct"] = round(good / total * 100, 1) if total else None
    bias: dict = {}
    for lg in clocks:
        rows = [x for d in decs for x in d if leagues[x[0]] == lg and x[5] > float("-inf")]
        if rows:
            pred = _clock_inv(np.array([x[5] for x in rows], float))
            bias[lg] = {"games_seen": len(rows), "mean_pred_points": round(float(pred.mean()), 3), "mean_actual_points": round(float(np.mean([x[3] for x in rows])), 3)}
    out["league_scale"] = bias
    return out


def train_pooled(shards: Path, out: Path, leagues: list[str], trainer, folds: int, leak: dict, raw: Path, trials: tuple, rows_cap: int = POOLED_ROWS,
                 deadline: float | None = None, log=print) -> dict:
    """One model on every attached league, graded per league on the same test nights as the per-league models. Whole nights; nothing newer than the
    earliest test night of any league is fit on."""
    splits = _night_map(leagues, shards)
    cut = min(min(s["test"]) for s in splits.values())
    frames, dropped = {}, {}
    for lg in leagues:
        df, info = load_rows(shards, lg)
        keep = df.night.astype(str) < cut
        dropped[lg] = {"rows_past_earliest_test_night": int((~keep & ~df.night.isin(splits[lg]["test"])).sum()), **info}
        frames[lg] = sample_rows(df[keep | df.night.isin(splits[lg]["test"])], rows_cap)
    fit_nights = {lg: [n for n in splits[lg]["train"] + splits[lg]["calib"] if n < cut] for lg in leagues}
    if not all(fit_nights.values()):
        raise Refused(f"pooled: a league has no nights before the earliest test night {cut}: " + ", ".join(lg for lg in leagues if not fit_nights[lg]))
    owned = {lg: set(ns) for lg, ns in fit_nights.items()}
    union = sorted({n for ns in fit_nights.values() for n in ns})
    fold_list = forward_folds(union, folds)
    clocks = {}
    for lg in leagues:
        graded = set(splits[lg]["test"]) | {n for _, v in fold_list for n in v if n in owned[lg]}
        seasons = sorted(p.stem for p in (shards / lg).glob("*.parquet"))
        clocks[lg] = shared_clock(lg, raw, seasons, graded)
    pool = pd.concat([f[f.night.astype(str) < cut] for f in frames.values()], ignore_index=True)
    trial_cv: dict[str, list] = {t.name: [] for t in trials}
    tried: list = []
    fold_nights = []
    for t in trials:
        if tried and out_of_time(deadline):
            continue
        tried.append(t)
        for fit_n, val_n in fold_list:
            c = max(1, round(len(fit_n) * 0.15))
            if len(fit_n) <= c:
                continue
            arrays, meta = fit_v1(_gather(pool, fit_n[:-c]), _gather(pool, fit_n[-c:]), G.GV_POOLED, trainer, leak, t)
            model = G.GameValueModel({**arrays}, {**meta, "leagues": leagues}, G.GV_POOLED)
            per = {}
            for lg in leagues:
                nights = [n for n in val_n if n in owned[lg]]
                if nights:
                    per[lg] = grade(model, _gather(frames[lg], nights), clocks[lg], [n for n in fit_n if n in owned[lg]], nights, lg)
            k4 = [v["k4_pct"] for v in per.values() if v.get("k4_pct") is not None]
            trial_cv[t.name].append({"k4_pct": round(float(np.mean(k4)), 4) if k4 else None, "per_league": per})
            if t is tried[0]:
                fold_nights.append({"fit": [fit_n[0], fit_n[-1]], "validate": [val_n[0], val_n[-1]]})
    if not fold_nights:
        raise Refused("pooled: too few nights for the forward folds")
    best = next(t for t in tried if t.name == pick_trial({t.name: trial_cv[t.name] for t in tried}, [t.name for t in tried]))
    fit_n = union
    c = max(1, round(len(fit_n) * 0.15))
    arrays, meta = fit_v1(_gather(pool, fit_n[:-c]), _gather(pool, fit_n[-c:]), G.GV_POOLED, trainer, leak, best)
    meta["leagues"] = leagues
    path = G.model_path(G.GV_POOLED, "v1", out)
    stamped = G.save(path, arrays, meta)
    pooled = G.load(path, G.GV_POOLED)
    report: dict = {"file": path.name, "sha256": stamped["sha256"], "leagues": leagues, "fit_cut_before": cut, "rows": dropped, "folds": fold_nights,
                    "trials": len(tried), "license_tag": meta["license_tag"], "features_version": G.GV_FEATURES_VERSION,
                    "cv": {t.name: {"k4_mean": round(float(np.mean([f["k4_pct"] for f in trial_cv[t.name] if f["k4_pct"] is not None] or [float("nan")])), 4)} for t in tried},
                    "chosen": best.name, "test": {}}
    per_models = {}
    for lg in leagues:
        have = G.model_path(lg, "v1", out)
        if have.exists():
            per_models[lg] = G.load(have, lg)
    for lg in leagues:
        te = _gather(frames[lg], splits[lg]["test"])
        fit = splits[lg]["train"] + splits[lg]["calib"]
        pool_got = grade(pooled, te, clocks[lg], fit, splits[lg]["test"], lg)
        row: dict = {"nights_test": len(splits[lg]["test"]), "pooled": {k: pool_got[k] for k in ("k2_pct", "k4_pct", "r2", "shared_clock")}, "per_league": None}
        if lg in per_models:
            got = grade(per_models[lg], te, clocks[lg], fit, splits[lg]["test"], lg)
            row["per_league"] = {k: got[k] for k in ("k2_pct", "k4_pct", "r2")}
            if got["k4_pct"] is not None and pool_got["k4_pct"] is not None:
                row["pooled_minus_per_league_k4"] = round(pool_got["k4_pct"] - got["k4_pct"], 1)
        report["test"][lg] = row
        log(json.dumps({"pooled_vs_per_league": lg, **{k: v for k, v in row.items() if k != "pooled"}, "pooled_k4": row["pooled"]["k4_pct"]}), flush=True)
    tests = {lg: splits[lg]["test"] for lg in leagues}
    report["cross_sport"] = {"pooled": cross_sport(clocks, tests, lambda _lg: pooled)}
    if set(per_models) == set(leagues):
        report["cross_sport"]["per_league"] = cross_sport(clocks, tests, lambda lg: per_models[lg])
    log(json.dumps({"cross_sport": {k: {a: b for a, b in v.items() if a != "league_scale"} for k, v in report["cross_sport"].items()}}), flush=True)
    return report


def main(argv=None, trainer=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shards", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--personal-nba", "--personal-hoopr", dest="personal_nba", action="store_true",
                    help="accept hoopR NBA and men's college basketball (cbb) rows, tagged personal-use (off by default)")
    ap.add_argument("--archive", type=Path, default=None, help="data/archive, needed only if the shards hold archive rows")
    ap.add_argument("--raw", type=Path, default=Path(os.environ.get("RZ_RAW", "/kaggle/working/raw")),
                    help="where <league>/pbp_<season>.parquet are fetched to; the raw audit and the shared-clock capture read them")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--objective", choices=("tweedie", "lambdarank"), default="tweedie", help="the value head when --trials is not given")
    ap.add_argument("--trials", default=None, help=f"comma list from {sorted(TRIALS)}: the winner by mean CV K=4 is exported")
    ap.add_argument("--budget-hours", type=float, default=None, help="start no new trial past this many hours from launch")
    ap.add_argument("--pooled", action="store_true", help="also train one model on every league and compare it per league")
    ap.add_argument("--pooled-only", action="store_true", help="only the pooled model; compares against per-league files already in --out")
    ap.add_argument("--pooled-rows", type=int, default=POOLED_ROWS, help="most rows from one league in a pooled fit")
    ap.add_argument("--challenger", default=None, help="tabpfn or package.module:ClassName: report-only, same shared-clock capture, nothing exported")
    ap.add_argument("--challenger-rows", type=int, default=CHALLENGER_ROWS, help="rows in each fit sample and each grading sample")
    a = ap.parse_args(argv)
    started = time.monotonic()
    deadline = started + a.budget_hours * 3600.0 if a.budget_hours else None
    names = [x for x in (a.trials or ("rank" if a.objective == "lambdarank" else "base")).split(",") if x]
    if not names or any(n not in TRIALS for n in names):
        print(f"REFUSED: --trials {a.trials}: known trials are {sorted(TRIALS)}", flush=True)
        return EXIT_REFUSED
    trials = tuple(TRIALS[n] for n in names)
    if (trials != (BASE_TRIAL,) or a.pooled or a.pooled_only) and G.GV_FEATURES_VERSION != "v4":
        print(f"REFUSED: --trials, --objective and --pooled need {G.GV_FEATURES_ENV}=v4", flush=True)
        return EXIT_REFUSED
    if trainer is None and sys.platform == "darwin":
        print("REFUSED: training never runs on the Mac. Run this on Kaggle CPU.", flush=True)
        return EXIT_MAC
    leagues = [x for x in a.leagues.split(",") if x]
    unknown = [x for x in leagues if x not in G.GV_LEAGUES]
    if not leagues or unknown:
        print(f"REFUSED: only {G.GV_LEAGUES} have a GameValue model, got {a.leagues}", flush=True)
        return EXIT_REFUSED
    for lg in leagues:
        status, why = V.public_status(lg, a.personal_nba)
        if status != "ok":
            print(f"REFUSED: {lg}: {why}", flush=True)
            return EXIT_REFUSED
    try:
        fetch_raw(a.shards, a.raw, leagues, a.personal_nba)
    except Exception as e:  # noqa: BLE001 - no raw source means no audit, so nothing may train
        print(f"REFUSED: the raw seasons are not available ({type(e).__name__}: {e}).", flush=True)
        return EXIT_REFUSED
    rep = LC.check_dir(a.shards, a.archive, a.personal_nba, a.raw)
    if not rep.fails and not rep.checked["raw_rows"]:
        rep.fail("raw_audit", "no row was checked against the raw source")
    if rep.fails:
        for line in rep.fails[:50]:
            print("LEAK CHECK FAIL", line, flush=True)
        print(f"REFUSED: the leak check found {len(rep.fails)} problems; nothing was trained.", flush=True)
        return EXIT_REFUSED
    leak = {"passed": True, **rep.checked}
    print("leak check passed", json.dumps(leak), flush=True)
    fit_with = trainer or LightGBMTrainer()
    try:
        challenger = load_challenger(a.challenger) if a.challenger else None
    except (Refused, ImportError, AttributeError) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    a.out.mkdir(parents=True, exist_ok=True)
    report = {"leak_check": leak, "status": G.GV_STATUS, "features_version": G.GV_FEATURES_VERSION, "rounds": getattr(fit_with, "rounds", None), "leagues": {}}
    try:
        if not a.pooled_only:
            for lg in leagues:
                report["leagues"][lg] = train_league(a.shards, a.out, lg, fit_with, a.folds, leak, a.raw, challenger=challenger,
                                                     challenger_rows=a.challenger_rows, trials=trials, deadline=deadline)
        if a.pooled or a.pooled_only:
            if len(leagues) < 2:
                raise Refused("a pooled model needs at least two --leagues")
            report["pooled"] = train_pooled(a.shards, a.out, leagues, fit_with, a.folds, leak, a.raw, trials, a.pooled_rows, deadline)
    except (Refused, G.ModelRejected) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    tags = {v["license_tag"] for v in report["leagues"].values() if v["license_tag"]} | ({report["pooled"]["license_tag"]} if report.get("pooled", {}).get("license_tag") else set())
    report["license_tag"] = tags.pop() if tags else ""
    (a.out / "manifest.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
