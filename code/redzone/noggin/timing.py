"""Noggin Timing v1: how long is this dead ball still dead? (docs/noggin.md, Phase 1; absorbs A3)

A discrete-time hazard per league. Each dead window is expanded into BIN_S-second rows, one per bin it
survived into: row (features, elapsed e) -> did play restart inside [e, e + BIN_S)? A LightGBM binary
model learns h(e | x); the survival from elapsed e is S(t | e) = prod over bins in [e, t) of (1 - h).

What the live gate needs is a LOWER bound: "it has been dead e seconds (the data proves at least that);
with probability >= 1 - alpha it stays dead until at least start + q_lo". q_lo comes from S, then a
split-conformal correction on calibration nights (Mondrian by elapsed bucket, so coverage holds early and
late in a break) makes P(restart before q_lo) <= alpha on unseen nights.

Runtime is numpy only: the forest is flattened like Brain v3 (`redzone.scoring.brain3.flatten`) and every
bin of the horizon is scored in one batched walk (< 1 ms per window on a laptop CPU).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from redzone.models_rt.forest import MODEL_DIRS, Nodes, load_npz
from redzone.noggin.features import FEATURES

BIN_S = 5.0
HORIZON_BINS = 72            # 6 minutes of bins; past it the last hazard repeats
ALPHA = 0.02                 # P(restart before the lower bound) target
E_BUCKETS = (0.0, 15.0, 45.0, 90.0, 150.0, 1e9)   # conformal groups by elapsed time
MODEL_FEATURES = FEATURES + ("elapsed",)
MODEL_DIR = MODEL_DIRS["noggin"]


# ---------------------------------------------------------------- training rows

def expand(t, max_bins: int = HORIZON_BINS):
    """Windows -> person-period rows: (X, y, window index). Long windows are capped at max_bins rows
    (the tail past the horizon shares the last bin's hazard)."""
    L = t["length_s"].to_numpy(float)
    nb = np.minimum(np.floor(L / BIN_S).astype(int) + 1, max_bins)
    idx = np.repeat(np.arange(len(t)), nb)
    k = np.concatenate([np.arange(n) for n in nb]) if len(nb) else np.zeros(0, int)
    e = k * BIN_S
    y = ((L[idx] >= e) & (L[idx] < e + BIN_S)).astype(np.int8)
    base = t[list(FEATURES)].to_numpy(np.float32)
    X = np.column_stack([base[idx], e.astype(np.float32)])
    return X, y, idx


# ---------------------------------------------------------------- numpy forest (batched)

class Forest:
    """A flattened LightGBM ensemble walked in batches (models_rt.forest.Nodes, the one walker)."""

    def __init__(self, arrays: dict, meta: dict):
        self.nodes = Nodes(arrays, int(meta["depth"]))
        n = self.nodes
        self.feat, self.thr, self.left, self.right, self.dleft = n.feat, n.thr, n.left, n.right, n.dleft
        self.miss, self.value, self.roots, self.depth = n.miss, n.value, n.roots, n.depth
        self.init = float(meta.get("init", 0.0))

    def raw(self, X: np.ndarray) -> np.ndarray:
        """X (n, F) -> raw margin (n,). Same split rules as LightGBM (float32 features)."""
        return self.value[self.nodes.leaves_batch(X)].sum(axis=1) + self.init


def flatten_lgb(booster) -> tuple[dict, dict]:
    from redzone.scoring.brain3 import flatten
    dump = booster.dump_model()
    trees = [dict(t, slot=0) for t in dump["tree_info"]]
    arrays, meta = flatten({"h": trees}, list(MODEL_FEATURES))
    arrays.pop("slot", None)
    return arrays, meta


# ---------------------------------------------------------------- the model

@dataclass
class TimingModel:
    league: str
    forest: Forest
    offsets: list[float]          # conformal log-scale offset per E_BUCKETS group (<= 0 tightens)
    alpha: float
    info: dict
    kind_offsets: dict | None = None   # kind -> per-bucket offsets (Mondrian; falls back to `offsets`)

    def offset(self, kind: str | None, b: int) -> float:
        ko = (self.kind_offsets or {}).get(kind or "")
        return ko[b] if ko else self.offsets[b]

    def hazards(self, x: np.ndarray, e0: float) -> np.ndarray:
        """Per-bin restart hazard from elapsed e0 onward (HORIZON_BINS values)."""
        k0 = int(e0 // BIN_S)
        es = (np.arange(k0, k0 + HORIZON_BINS) * BIN_S).astype(np.float32)
        X = np.column_stack([np.broadcast_to(np.asarray(x, np.float32), (len(es), len(x))), es])
        z = np.clip(self.forest.raw(X), -40, 40)
        return 1.0 / (1.0 + np.exp(-z))

    def survival(self, x: np.ndarray, e0: float) -> tuple[np.ndarray, np.ndarray]:
        """(t_ends, S): S[j] = P(still dead at e0 + t_ends[j] | dead at e0)."""
        h = self.hazards(x, e0)
        frac = 1.0 - (e0 % BIN_S) / BIN_S          # first bin is partly used up
        h0 = 1.0 - (1.0 - h[0]) ** frac
        s = np.cumprod(np.concatenate([[1.0 - h0], 1.0 - h[1:]]))
        t_end = (np.arange(1, len(h) + 1) * BIN_S) - (e0 % BIN_S)
        return t_end, s

    def raw_lower(self, x, e0: float, alpha: float | None = None) -> float:
        """Seconds of dead time still left with probability >= 1 - alpha, before conformal correction."""
        a = self.alpha if alpha is None else alpha
        t_end, s = self.survival(x, e0)
        j = int(np.searchsorted(-s, -(1.0 - a)))     # first bin where S drops below 1 - a
        if j == 0:
            return 0.0
        return float(t_end[j - 1]) if j < len(s) else float(t_end[-1])

    def lower(self, x, e0: float, kind: str | None = None) -> float:
        """Calibrated lower bound of the REMAINING dead time (seconds from now, >= 0)."""
        q = self.raw_lower(x, e0)
        return max(0.0, (q + 1.0) * math.exp(self.offset(kind, bucket(e0))) - 1.0)

    def reading(self, x, e0: float, kind: str | None = None) -> tuple[float, float]:
        """(calibrated lower bound, median) of the remaining dead time, from one survival curve."""
        t_end, s = self.survival(x, e0)
        j = int((s >= 1.0 - self.alpha).sum())
        q = 0.0 if j == 0 else float(t_end[j - 1])
        lo = max(0.0, (q + 1.0) * math.exp(self.offset(kind, bucket(e0))) - 1.0)
        jm = int(np.searchsorted(-s, -0.5))
        return lo, float(t_end[min(jm, len(s) - 1)])

    def median(self, x, e0: float) -> float:
        t_end, s = self.survival(x, e0)
        j = int(np.searchsorted(-s, -0.5))
        return float(t_end[min(j, len(s) - 1)])

    def p_restart_within(self, x, e0: float, t: float) -> float:
        t_end, s = self.survival(x, e0)
        return float(1.0 - np.interp(t, np.concatenate([[0.0], t_end]), np.concatenate([[1.0], s])))

    # ---- io
    def save(self, path: Path) -> None:
        arrays, meta = self._arrays, dict(self._meta)
        meta.update(league=self.league, offsets=self.offsets, kind_offsets=self.kind_offsets or {},
                    alpha=self.alpha, info=self.info,
                    bin_s=BIN_S, horizon_bins=HORIZON_BINS, e_buckets=list(E_BUCKETS),
                    features=list(MODEL_FEATURES))
        flat = dict(arrays)
        flat["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, **flat)


def bucket(e0: float) -> int:
    for i in range(len(E_BUCKETS) - 1):
        if E_BUCKETS[i] <= e0 < E_BUCKETS[i + 1]:
            return i
    return len(E_BUCKETS) - 2


def load(path: Path) -> TimingModel:
    arrays, meta = load_npz(path)
    if tuple(meta["features"]) != MODEL_FEATURES:
        raise ValueError(f"feature mismatch in {path}")
    m = TimingModel(meta["league"], Forest(arrays, meta), list(meta["offsets"]), float(meta["alpha"]),
                    meta.get("info", {}), meta.get("kind_offsets") or {})
    m._arrays, m._meta = arrays, {k: meta[k] for k in ("depth", "init") if k in meta}
    return m


_CACHE: dict[str, TimingModel | None] = {}


def model_for(league: str, model_dir: Path | None = None) -> TimingModel | None:
    key = f"{model_dir}:{league}"
    if key not in _CACHE:
        p = (model_dir or MODEL_DIR) / f"timing_{league}.v1.npz"
        _CACHE[key] = load(p) if p.exists() else None
    return _CACHE[key]


def survival_batch(forest, x: np.ndarray, e0: np.ndarray, chunk: int = 20000) -> tuple[np.ndarray, np.ndarray]:
    """Many windows at once: (t_end (n, H), S (n, H)), same maths as TimingModel.survival. `forest` is
    the numpy Forest or anything with .raw(X) (training passes LightGBM for speed: identical margins)."""
    x = np.asarray(x, np.float32)
    e0 = np.asarray(e0, float)
    H = HORIZON_BINS
    S = np.empty((len(x), H))
    for a in range(0, len(x), chunk):
        xb, eb = x[a:a + chunk], e0[a:a + chunk]
        k0 = (eb // BIN_S).astype(int)
        es = ((k0[:, None] + np.arange(H)[None, :]) * BIN_S).astype(np.float32)
        X = np.column_stack([np.repeat(xb, H, axis=0), es.reshape(-1)])
        h = 1.0 / (1.0 + np.exp(-np.clip(forest.raw(X), -40, 40))).reshape(len(xb), H)
        frac = 1.0 - (eb % BIN_S) / BIN_S
        h[:, 0] = 1.0 - (1.0 - h[:, 0]) ** frac
        S[a:a + chunk] = np.cumprod(1.0 - h, axis=1)
    t_end = np.arange(1, H + 1)[None, :] * BIN_S - (e0 % BIN_S)[:, None]
    return t_end, S


def lower_from(t_end: np.ndarray, S: np.ndarray, alpha: float) -> np.ndarray:
    j = (S >= 1.0 - alpha).sum(axis=1)          # S is non-increasing: bins still above 1 - alpha
    q = np.where(j == 0, 0.0, t_end[np.arange(len(S)), np.maximum(j - 1, 0)])
    return q
