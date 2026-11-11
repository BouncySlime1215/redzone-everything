"""Fit, calibrate and grade Noggin Timing v1 from the data factory's table (Kaggle CPU / Actions only).

Why: first added in 95874796 (2026-11-04) as "Noggin Phase 1: data factory (NBA, 1.09M windows) + Timing v1 hazard with conformal bounds (OFF)"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd

from redzone.noggin import timing as T
from redzone.noggin.features import FEATURES

MIN_GROUP = 200        # calibration points a Mondrian group needs, else it falls back to its parent
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=31, max_depth=6, min_data_in_leaf=200,
              feature_fraction=0.9, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1,
              num_threads=4, seed=7)


def checkpoints(t: pd.DataFrame, per_window: int = 6, rng=None) -> pd.DataFrame:
    """(window, elapsed e0) points where the window is still dead: e0 uniform in [0, length)."""
    rng = rng or np.random.default_rng(0)
    L = t.length_s.to_numpy(float)
    idx = np.repeat(np.arange(len(t)), per_window)
    e0 = np.floor(rng.random(len(idx)) * L[idx])
    e0[::per_window] = 0.0                        # always grade the moment the stoppage starts
    keep = e0 < L[idx]
    return pd.DataFrame({"w": idx[keep], "e0": e0[keep], "remain": (L[idx] - e0)[keep]})


def fit(t: pd.DataFrame, league: str, rounds: int = 400, log=print) -> T.TimingModel:
    import lightgbm as lgb
    tr, ca = t[t.split == "train"].reset_index(drop=True), t[t.split == "calib"].reset_index(drop=True)
    X, y, _ = T.expand(tr)
    Xv, yv, _ = T.expand(ca)
    t0 = time.time()
    d = lgb.Dataset(X, y, feature_name=list(T.MODEL_FEATURES), free_raw_data=True)
    dv = lgb.Dataset(Xv, yv, reference=d)
    bst = lgb.train(PARAMS, d, rounds, valid_sets=[dv], callbacks=[lgb.early_stopping(30, verbose=False)])
    log(f"  {league}: {len(X):,} train rows from {len(tr):,} windows, {bst.num_trees()} trees, "
        f"{time.time() - t0:.0f} s")
    arrays, meta = T.flatten_lgb(bst)
    forest = T.Forest(arrays, meta)
    m = T.TimingModel(league, forest, [0.0] * (len(T.E_BUCKETS) - 1), T.ALPHA, {})
    m._arrays, m._meta = arrays, meta
    # numpy == LightGBM check
    k = min(2000, len(Xv))
    diff = float(np.max(np.abs(forest.raw(Xv[:k]) - bst.predict(Xv[:k], raw_score=True)))) if k else 0.0
    # split-conformal offsets per elapsed bucket on the calibration nights
    cp = checkpoints(ca)
    xs = ca[list(FEATURES)].to_numpy(np.float32)[cp.w]
    fast = _LGB(bst)
    t_end, S = T.survival_batch(fast, xs, cp.e0.to_numpy())
    q = T.lower_from(t_end, S, T.ALPHA)
    r = np.log1p(cp.remain.to_numpy()) - np.log1p(q)
    g = np.array([T.bucket(e) for e in cp.e0])
    def conformal(mask) -> float | None:
        rb = np.sort(r[mask])
        if len(rb) < MIN_GROUP:
            return None
        kth = int(math.floor(T.ALPHA * (len(rb) + 1))) - 1
        return min(0.0, float(rb[max(0, kth)]))            # never loosen past the raw bound

    glob = conformal(np.ones(len(r), bool)) or 0.0
    offs = [o if (o := conformal(g == b)) is not None else glob for b in range(len(T.E_BUCKETS) - 1)]
    kinds = ca.kind.to_numpy()[cp.w]
    kind_offs = {}
    for k in pd.unique(kinds):
        ko = [conformal((kinds == k) & (g == b)) for b in range(len(T.E_BUCKETS) - 1)]
        kind_offs[str(k)] = [min(offs[b], o) if o is not None else offs[b] for b, o in enumerate(ko)]
    m.offsets, m.kind_offsets = offs, kind_offs
    m._fast = fast
    m.info = {"train_windows": int(len(tr)), "train_rows": int(len(X)), "calib_windows": int(len(ca)),
              "trees": int(bst.num_trees()), "leaves": int(sum(len(a) for a in [arrays["value"]])),
              "features": len(T.MODEL_FEATURES), "numpy_vs_lgb_max_abs": diff,
              "train_nights": [tr.night.min(), tr.night.max()] if len(tr) else [],
              "calib_nights": [ca.night.min(), ca.night.max()] if len(ca) else [],
              # kinds with enough train rows to trust; the live hook falls back to the floor for any other
              "kinds": sorted(str(k) for k, n in tr.kind.value_counts().items() if n >= MIN_GROUP)}
    return m


class _LGB:
    def __init__(self, bst):
        self.bst = bst

    def raw(self, X):
        return self.bst.predict(X, raw_score=True, num_threads=4)


def _interp_rows(t: float, t_end: np.ndarray, S: np.ndarray) -> np.ndarray:
    """S at time t per row (linear between bin ends, S(0) = 1)."""
    j = (t_end < t).sum(axis=1)
    r = np.arange(len(S))
    t_hi = t_end[r, np.minimum(j, S.shape[1] - 1)]
    s_hi = S[r, np.minimum(j, S.shape[1] - 1)]
    t_lo = np.where(j == 0, 0.0, t_end[r, np.maximum(j - 1, 0)])
    s_lo = np.where(j == 0, 1.0, S[r, np.maximum(j - 1, 0)])
    w = np.clip((t - t_lo) / np.maximum(t_hi - t_lo, 1e-9), 0, 1)
    return s_lo + w * (s_hi - s_lo)


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    out, n = 0.0, len(p)
    for a, b in zip(edges[:-1], edges[1:]):
        k = (p >= a) & (p < b if b < 1 else p <= b)
        if k.any():
            out += k.sum() / n * abs(p[k].mean() - y[k].mean())
    return float(out)


def grade(m: T.TimingModel, t: pd.DataFrame, floor_s: dict | None = None) -> dict:
    """Unseen-night grade: lower-bound coverage, tightness vs today's floors, ECE of P(restart in 30 s)."""
    cp = checkpoints(t, rng=np.random.default_rng(1))
    xs = t[list(FEATURES)].to_numpy(np.float32)[cp.w]
    e0, remain = cp.e0.to_numpy(), cp.remain.to_numpy()
    t_end, S = T.survival_batch(getattr(m, "_fast", None) or m.forest, xs, e0)
    q = T.lower_from(t_end, S, m.alpha)
    g = np.array([T.bucket(e) for e in e0])
    kinds_all = t.kind.to_numpy()[cp.w]
    off = np.array([m.offset(k, b) for k, b in zip(kinds_all, g)])
    lo = np.maximum(0.0, (q + 1.0) * np.exp(off) - 1.0)
    p30 = 1.0 - _interp_rows(30.0, t_end, S)
    y30 = (remain <= 30.0).astype(float)
    kinds = t.kind.to_numpy()[cp.w]
    out = {"windows": int(len(t)), "checkpoints": int(len(cp)), "nights": int(t.night.nunique()),
           "below_lower_pct": round(100 * float((remain < lo).mean()), 2),
           "lower_mean_s": round(float(lo.mean()), 1), "remain_mean_s": round(float(remain.mean()), 1),
           "ece_restart30": round(ece(p30, y30), 4)}
    if floor_s is not None:
        fl = np.array([floor_s.get(k, 0.0) for k in kinds]) - e0
        fl = np.maximum(0.0, fl)
        out["floor_below_pct"] = round(100 * float((remain < fl).mean()), 2)
        out["floor_mean_s"] = round(float(fl.mean()), 1)
    per = {}
    for k in pd.unique(kinds):
        s = kinds == k
        if s.sum() >= 100:
            per[k] = {"n": int(s.sum()), "below_lower_pct": round(100 * float((remain[s] < lo[s]).mean()), 2),
                      "lower_mean_s": round(float(lo[s].mean()), 1)}
    out["by_kind"] = per
    per_b = {}
    for b in range(len(T.E_BUCKETS) - 1):
        s = g == b
        if s.any():
            per_b[f"e{int(T.E_BUCKETS[b])}"] = {"n": int(s.sum()),
                                                 "below_lower_pct": round(100 * float((remain[s] < lo[s]).mean()), 2)}
    out["by_elapsed"] = per_b
    return out
