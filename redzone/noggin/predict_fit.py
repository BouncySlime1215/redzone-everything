"""Fit and grade Noggin Predict v1 (pandas + LightGBM; Kaggle CPU / cloud only, never the Mac).

Splits are the factory's whole-night, time-ordered splits. Forward-chaining CV runs on the non-test nights
by season (train on every earlier season, validate on the next); the final model trains on `train`,
calibrates (isotonic) on `calib` and is graded once on the FULL `test` split.
Baseline = base rate x game state: the train-night rate per (period, 2-minute clock bucket, margin bucket).

Why: first added in ebfb9efb (2026-11-04) as "Noggin: NFL data factory + Timing v1 NFL, Predict v1 NBA (both OFF)"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from redzone.noggin import predict as P
from redzone.noggin.features import PREDICT_FEATURES, PREDICT_HORIZONS
from redzone.noggin.timing_fit import ece

PARAMS = dict(objective="binary", num_leaves=31, max_depth=6, min_data_in_leaf=500, learning_rate=0.05,
              feature_fraction=0.9, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1,
              num_threads=4, seed=7)


def _X(t: pd.DataFrame) -> np.ndarray:
    return t[list(PREDICT_FEATURES)].to_numpy(np.float32)


def logloss(y, p) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def pr_auc(y, p) -> float:
    """Average precision (area under the precision-recall curve, step rule)."""
    o = np.argsort(-p, kind="stable")
    y = np.asarray(y)[o]
    tp = np.cumsum(y)
    prec = tp / np.arange(1, len(y) + 1)
    return float((prec * y).sum() / max(1, y.sum()))


def isotonic(p: np.ndarray, y: np.ndarray, bins: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Pool-adjacent-violators on quantile bins of p -> monotone knots (x, y) for np.interp."""
    o = np.argsort(p, kind="stable")
    ps, ys = p[o], y[o].astype(float)
    cuts = np.array_split(np.arange(len(ps)), min(bins, len(ps)))
    xs = [float(ps[c].mean()) for c in cuts if len(c)]
    vs = [float(ys[c].mean()) for c in cuts if len(c)]
    ws = [float(len(c)) for c in cuts if len(c)]
    bx, bv, bw = [], [], []
    for x, v, w in zip(xs, vs, ws):
        bx.append([x]); bv.append(v); bw.append(w)
        while len(bv) > 1 and bv[-2] > bv[-1]:
            v2, w2, x2 = bv.pop(), bw.pop(), bx.pop()
            bv[-1] = (bv[-1] * bw[-1] + v2 * w2) / (bw[-1] + w2)
            bw[-1] += w2
            bx[-1] += x2
    kx = np.array([np.mean(b) for b in bx])
    return kx, np.array(bv)


def _key(t: pd.DataFrame) -> pd.Series:
    margin = pd.cut(t.score_diff, [-1, 3, 7, 12, 1e9], labels=False).astype(int).astype(str)
    clock = (t.clock_left.fillna(0) // 120).astype(int).astype(str)
    return t.period.clip(upper=5).astype(int).astype(str) + "_" + clock + "_" + margin


def baseline(tr: pd.DataFrame, te: pd.DataFrame, label: str, prior: float = 50.0) -> np.ndarray:
    """Base rate x game state, smoothed toward the overall train rate."""
    base = float(tr[label].mean())
    g = tr.groupby(_key(tr))[label].agg(["sum", "count"])
    rate = (g["sum"] + prior * base) / (g["count"] + prior)
    return _key(te).map(rate).fillna(base).to_numpy(float)


def _train(tr: pd.DataFrame, label: str, va: pd.DataFrame | None, rounds: int):
    import lightgbm as lgb
    d = lgb.Dataset(_X(tr), tr[label].to_numpy(), feature_name=list(PREDICT_FEATURES))
    if va is None:
        return lgb.train(PARAMS, d, rounds)
    dv = lgb.Dataset(_X(va), va[label].to_numpy(), reference=d)
    return lgb.train(PARAMS, d, rounds, valid_sets=[dv], callbacks=[lgb.early_stopping(30, verbose=False)])


def scores(y, p) -> dict:
    return {"logloss": round(logloss(y, p), 4), "pr_auc": round(pr_auc(y, p), 4), "ece": round(ece(p, y), 4)}


def forward_cv(t: pd.DataFrame, folds: int = 5, rounds: int = 300, log=print) -> dict:
    """Season-level forward chaining on the non-test nights: fold k trains on every season before s_k."""
    pool = t[t.split != "test"]
    seasons = sorted(pool.season.unique())[-folds:]
    out = {}
    for hz in PREDICT_HORIZONS:
        label, rows = f"big_next{hz}", []
        for s in seasons:
            tr, va = pool[pool.season < s], pool[pool.season == s]
            if len(tr) < 5000 or len(va) < 1000:
                continue
            bst = _train(tr, label, None, rounds)
            p, y = bst.predict(_X(va)), va[label].to_numpy()
            b = baseline(tr, va, label)
            rows.append({"season": int(s), "n": int(len(va)), "model": scores(y, p), "baseline": scores(y, b),
                         "beats": bool(logloss(y, p) < logloss(y, b) and pr_auc(y, p) > pr_auc(y, b))})
            log(f"  cv h{hz} {s}: model {rows[-1]['model']} baseline {rows[-1]['baseline']}")
        ll = [r["model"]["logloss"] for r in rows]
        out[hz] = {"folds": rows, "mean_logloss": round(float(np.mean(ll)), 4) if ll else None,
                   "worst_logloss": round(float(np.max(ll)), 4) if ll else None,
                   "every_fold_beats_baseline": bool(rows) and all(r["beats"] for r in rows)}
    return out


def fit(t: pd.DataFrame, league: str, rounds: int = 400, log=print) -> P.PredictModel:
    tr, ca = t[t.split == "train"], t[t.split == "calib"]
    forests, iso, trees = {}, {}, {}
    t0 = time.time()
    for hz in PREDICT_HORIZONS:
        label = f"big_next{hz}"
        bst = _train(tr, label, ca, rounds)
        from redzone.scoring.brain3 import flatten
        dump = bst.dump_model()
        arrays, meta = flatten({"h": [dict(x, slot=0) for x in dump["tree_info"]]}, list(PREDICT_FEATURES))
        arrays.pop("slot", None)
        f = P.Forest(arrays, meta)
        k = min(2000, len(ca))
        diff = float(np.max(np.abs(f.raw(_X(ca)[:k]) - bst.predict(_X(ca)[:k], raw_score=True)))) if k else 0.0
        if diff > 1e-4:
            raise RuntimeError(f"numpy forest != LightGBM for h{hz}: {diff}")
        forests[hz] = f
        iso[hz] = isotonic(bst.predict(_X(ca)), ca[label].to_numpy())
        trees[hz] = int(bst.num_trees())
    # base rate on the newest non-test nights (calib): three-point volume drifts up every season
    info = {"rate": {str(hz): round(float(ca[f"big_next{hz}"].mean()), 4) for hz in PREDICT_HORIZONS},
            "train_windows": int(len(tr)), "calib_windows": int(len(ca)), "trees": trees,
            "features": len(PREDICT_FEATURES), "fit_s": round(time.time() - t0),
            "train_nights": [tr.night.min(), tr.night.max()], "calib_nights": [ca.night.min(), ca.night.max()]}
    log(f"  {league}: predict trees {trees}, {info['fit_s']} s")
    return P.PredictModel(league, forests, iso, info)


def grade(m: P.PredictModel, t: pd.DataFrame, ref: pd.DataFrame) -> dict:
    """Grade on the FULL split t (no sampling); the baseline's rates come from `ref` (train nights)."""
    X, out = _X(t), {"windows": int(len(t)), "nights": int(t.night.nunique())}
    for hz in m.forests:
        label = f"big_next{hz}"
        y, p = t[label].to_numpy(), m.proba(X, hz)
        b = baseline(ref, t, label)
        out[hz] = {"rate": round(float(y.mean()), 4), "model": scores(y, p), "baseline": scores(y, b),
                   "beats": bool(logloss(y, p) < logloss(y, b) and pr_auc(y, p) > pr_auc(y, b))}
    return out
