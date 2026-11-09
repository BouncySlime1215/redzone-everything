#!/usr/bin/env python3
"""Train GameValue v0 (state table) and v1 (LightGBM) on Kaggle CPU and export both for the numpy runtime.

    python scripts/kaggle_gamevalue.py --shards <value_shards dir> --out /kaggle/working/gamevalue --raw /kaggle/working/raw --leagues nfl
    python scripts/kaggle_gamevalue.py --shards <dir> --out <dir> --leagues nba,cbb --personal-nba   # --personal-hoopr is the same flag
    RZ_GV_FEATURES=v3 python scripts/kaggle_gamevalue.py --shards <v3 dir> ... --challenger tabpfn --challenger-rows 20000

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

--challenger fits another tabular model on a night-stratified sample and grades it on the same shared-clock capture, folds and test
nights as v1. It is report-only: no file is exported and it can never promote. A challenger that fails is recorded, not fatal.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
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


def _columns() -> list[str]:
    return ["night", "game_id", "source", "license", "decision_t", "label_full_window", *G.GV_FEATURES, *V.VALUE_LABELS]


class Refused(RuntimeError):
    """Training must not go on: the reason is the message."""


class LightGBMTrainer:
    """The real thing, imported only when a fit runs."""

    def __init__(self, rounds: int = ROUNDS):
        self.rounds = rounds

    def fit(self, X: np.ndarray, y: np.ndarray, objective: str):
        import lightgbm as lgb
        params = dict(PARAMS, objective=objective, num_threads=os.cpu_count() or 4)
        if objective == "tweedie":
            params["tweedie_variance_power"] = TWEEDIE_POWER
        return lgb.train(params, lgb.Dataset(X, y, feature_name=list(G.GV_FEATURES)), self.rounds)


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


def fit_v1(tr: pd.DataFrame, ca: pd.DataFrame, league: str, trainer, leak: dict) -> tuple[dict, dict]:
    """Value head on tr. Tier 1 head on tr, isotonic on ca, which nothing was fit on."""
    X, Xc = _X(tr), _X(ca)
    check = Xc[:PARITY_ROWS]
    value = trainer.fit(X, tr.value_next_3m.to_numpy(float), "tweedie")
    p1 = trainer.fit(X, tr.p_tier1_60s.to_numpy(float), "binary")
    v_arrays, v_depth = _flat(value, check, "value", np.exp)
    p_arrays, p_depth = _flat(p1, check, "p_tier1_60s", lambda m: 1.0 / (1.0 + np.exp(-m)))
    kx, ky, chosen = calibrate(p1.predict(Xc), ca)
    arrays = {**{f"value_{k}": a for k, a in v_arrays.items()}, **{f"p1_{k}": a for k, a in p_arrays.items()},
              "p1_cal_x": kx, "p1_cal_y": ky}
    return arrays, {**_meta(tr, league, "v1", "trees", leak), "value_depth": v_depth, "p1_depth": p_depth, "calib_rows": int(len(ca)), "p1_calibration": chosen,
                    "params": PARAMS, "rounds": getattr(trainer, "rounds", None), "tweedie_power": TWEEDIE_POWER}


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


def train_league(shards: Path, out: Path, league: str, trainer, folds: int, leak: dict, raw: Path, log=print, challenger=None,
                 challenger_rows: int = CHALLENGER_ROWS) -> dict:
    df, info = load_rows(shards, league)
    splits = json.loads((shards / "splits.json").read_text()).get(league) or {}
    train, calib, test = (list(splits.get(s) or []) for s in ("train", "calib", "test"))
    if not train or not calib or not test or not set(df.night) <= set(train + calib + test):
        raise Refused(f"{league}: splits.json does not cover the rows")
    seasons = sorted(p.stem for p in (shards / league).glob("*.parquet"))
    graded = {n for _, val in forward_folds(train + calib, folds) for n in val} | set(test)
    clock = shared_clock(league, raw, seasons, graded)
    rows_cv: dict[str, list] = {"v0": [], "v1": [], "challenger": []}
    challenger_error = None
    fold_nights = []
    for fit_nights, val_nights in forward_folds(train + calib, folds):
        c = max(1, round(len(fit_nights) * 0.15))
        if len(fit_nights) <= c:      # calibration must be on nights the trees never saw
            continue
        tr, ca, va = _gather(df, fit_nights[:-c]), _gather(df, fit_nights[-c:]), _gather(df, val_nights)
        a0, m0 = fit_v0(tr, league, leak)
        rows_cv["v0"].append(grade(G.GameValueModel(a0, m0, league), va, clock, fit_nights, val_nights, league))
        a1, m1 = fit_v1(tr, ca, league, trainer, leak)
        rows_cv["v1"].append(grade(G.GameValueModel(a1, m1, league), va, clock, fit_nights, val_nights, league))
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
    report = {**info, "rows_used": int(len(df)), "nights": {"train": len(train), "calib": len(calib), "test": len(test)},
              "folds": fold_nights, "trials": 1 + int(challenger is not None), "models": {}, "features_version": G.GV_FEATURES_VERSION,
              **license_fields(pd.concat([tr, ca]))}
    for version, (arrays, meta) in (("v0", fit_v0(tr, league, leak)), ("v1", fit_v1(tr, ca, league, trainer, leak))):
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
    ap.add_argument("--challenger", default=None, help="tabpfn or package.module:ClassName: report-only, same shared-clock capture, nothing exported")
    ap.add_argument("--challenger-rows", type=int, default=CHALLENGER_ROWS, help="rows in each fit sample and each grading sample")
    a = ap.parse_args(argv)
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
        for lg in leagues:
            report["leagues"][lg] = train_league(a.shards, a.out, lg, fit_with, a.folds, leak, a.raw, challenger=challenger,
                                                 challenger_rows=a.challenger_rows)
    except (Refused, G.ModelRejected) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    tags = {v["license_tag"] for v in report["leagues"].values() if v["license_tag"]}
    report["license_tag"] = tags.pop() if tags else ""
    (a.out / "manifest.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
