"""[one brain, step 4] One tree walker and one model loader (docs/onebrain-migration.md). Serves: every
North Star line a tree model feeds (big_plays_live, crunch_live_*, entries_mid_play via Noggin PICK and
the timing model), and the tick budget (one numpy walk per tree level).

Why: one brain step 4 (docs/onebrain-migration.md, 2026-11-04): four copies of the tree walker and loader became this one.

Before this, four copies of the LightGBM tree walk lived in scoring/brain.py (Trees, Forest),
scoring/brain3.py (Forest3) and noggin/timing.py (Forest, also used by predict.py and pick.py), and five
npz + meta loaders read the files their own way. Now:

    nodes = Nodes(arrays)                     # any of the two array layouts, normalised
    idx = nodes.leaves(x)                     # one row: the leaf each tree lands in
    idx = nodes.leaves_batch(X)               # (n, F) rows: (n, trees)
    arrays, meta = load_npz(path, features)   # one loader (meta json in a uint8 array; feature check)

Each model keeps its own reduction of the leaves (a plain sum, a per-task or per-slot bincount), so
every prediction is bitwise what it was (tests/test_onebrain_forest.py).

The split rule is LightGBM's on float32 features: missing_type None sends NaN to 0.0, Zero sends
|v| <= 1e-35 the default way, NaN sends NaN the default way; otherwise left when v <= threshold.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
# One registry of where the shipped models live (brain_models/, noggin/models/, reasoner/models/).
MODEL_DIRS = {"brain": ROOT / "scoring" / "brain_models", "noggin": ROOT / "noggin" / "models",
              "reasoner": ROOT / "reasoner" / "models"}


class Nodes:
    """A flattened tree ensemble, normalised so leaves loop to themselves: every tree takes `depth`
    steps. Accepts both layouts in use: leaves with feat -1 and children -1 (scoring.brain), and leaves
    that already point at themselves (scoring.brain3.flatten, the Noggin exports)."""

    def __init__(self, arrays: dict, depth: int):
        feat = arrays["feat"].astype(np.int64)
        left = arrays["left"].astype(np.int64)
        right = arrays["right"].astype(np.int64)
        me = np.arange(len(feat))
        self.leaf = (feat < 0) | (left < 0) | (left == me)
        self.feat = np.where(self.leaf, 0, feat)
        self.left = np.where(self.leaf, me, left)
        self.right = np.where(self.leaf, me, right)
        self.thr = arrays["thr"].astype(np.float64)
        self.dleft = arrays["dleft"].astype(bool)
        self.miss = arrays["miss"].astype(np.int8)
        self.value = arrays["value"].astype(np.float64)
        self.roots = arrays["roots"].astype(np.int64)
        self.depth = int(depth)
        self.miss_zero, self.miss_nan = self.miss == 1, self.miss == 2

    def step(self, idx: np.ndarray, v: np.ndarray) -> np.ndarray:
        """One level: the child of each node in idx for the feature values v (already gathered)."""
        nan = np.isnan(v)
        v = np.where(nan & ~self.miss_nan[idx], 0.0, v)
        to_default = (self.miss_zero[idx] & (np.abs(v) <= 1e-35)) | (self.miss_nan[idx] & nan)
        go_left = np.where(to_default, self.dleft[idx], v <= self.thr[idx])
        return np.where(go_left, self.left[idx], self.right[idx])

    def leaves(self, x: np.ndarray, on_step: Callable[[np.ndarray, np.ndarray, np.ndarray], None] | None = None,
               steps: int | None = None) -> np.ndarray:
        """The leaf of every tree for one feature row. on_step(node, child, feature) sees each level
        (Saabas per-feature reasons)."""
        x = np.asarray(x, dtype=np.float32).astype(np.float64)
        idx = self.roots
        for _ in range(self.depth if steps is None else steps):
            f = self.feat[idx]
            child = self.step(idx, x[f])
            if on_step is not None:
                on_step(idx, child, f)
            idx = child
        return idx

    def leaves_batch(self, X: np.ndarray) -> np.ndarray:
        """(n, F) rows -> (n, trees) leaves."""
        X = np.asarray(X, dtype=np.float32).astype(np.float64)
        n = len(X)
        idx = np.broadcast_to(self.roots, (n, len(self.roots))).copy()
        rows = np.arange(n)[:, None]
        for _ in range(self.depth):
            idx = self.step(idx, X[rows, self.feat[idx]])
        return idx


def read_meta(z) -> dict:
    return json.loads(bytes(z["meta"]).decode())


def load_npz(path: Path, features: list[str] | tuple[str, ...] | None = None) -> tuple[dict, dict]:
    """(arrays, meta) of a model file: every array but "meta", and the meta json. With features, the
    file must have been trained on exactly that feature list."""
    with np.load(path, allow_pickle=False) as z:
        meta = read_meta(z)
        arrays = {k: z[k] for k in z.files if k != "meta"}
    if features is not None and list(meta.get("features") or ()) != list(features):
        raise ValueError(f"{path}: features {meta.get('features')} != {list(features)}")
    return arrays, meta
