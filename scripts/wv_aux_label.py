#!/usr/bin/env python3
"""Shared harness for auxiliary Watch Value labels (Data G EXP-1704, Data H EXP-1800/1804).

One label source per run, each graded separately. It turns #166's swing label `wv` plus ONE auxiliary
per-play label `aux` (0-1, NaN where the source has no coverage) into the arm targets every aux row grades:

  A0 baseline   wv
  A1 candidate  (1 - mix) * wv + mix * aux        (aux gaps filled by `fill`, e.g. a soft head's probability)
  A2 placebo    same as A1, but aux is shuffled among the plays of the SAME game (seeded): any lift that
                survives the shuffle is label smoothing, not the source's content
  A3 derived    optional: same mix with a label computed from WP only (e.g. drama math), novelty 0. A real
                human-data label must beat this arm too, or the source adds nothing we could not compute.

The plug-in contract for a label source is one function: rows of ONE game (play order, the dicts
scripts/train_watch_value.py builds: gid, night, wall, wp_home, ...) -> np.ndarray aux (len(rows)), values in
[0, 1] or NaN. Labels only: an aux label is known after the moment and never becomes a model input.
"""
from __future__ import annotations

from typing import Callable, Iterable

import numpy as np

LabelFn = Callable[[list[dict]], np.ndarray]


def rank01(x: np.ndarray) -> np.ndarray:
    """Within-game rank to [0, 1] (ties averaged); NaN stays NaN. Makes sources on different scales mixable."""
    x = np.asarray(x, np.float64)
    out = np.full(len(x), np.nan)
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n == 0:
        return out
    if n == 1:
        out[ok] = 0.5
        return out
    v = x[ok]
    order = v.argsort(kind="mergesort")
    ranks = np.empty(n)
    ranks[order] = np.arange(n, dtype=np.float64)
    for u in np.unique(v):  # average ties
        m = v == u
        if m.sum() > 1:
            ranks[m] = ranks[m].mean()
    out[ok] = ranks / (n - 1)
    return out


def placebo(aux: np.ndarray, seed: int) -> np.ndarray:
    """Shuffle the covered values among the covered plays of one game; gaps stay gaps."""
    aux = np.asarray(aux, np.float64).copy()
    ok = np.flatnonzero(np.isfinite(aux))
    rng = np.random.default_rng(seed)
    aux[ok] = aux[rng.permutation(ok)]
    return aux


def mix(wv: np.ndarray, aux: np.ndarray, w: float, fill: np.ndarray | float | None = None) -> np.ndarray:
    """(1-w)*wv + w*aux. Where aux is NaN: use `fill` if given, else keep wv (the play is left unchanged)."""
    wv = np.asarray(wv, np.float64)
    a = np.asarray(aux, np.float64).copy()
    gap = ~np.isfinite(a)
    if fill is not None:
        f = np.broadcast_to(np.asarray(fill, np.float64), a.shape)
        a[gap] = f[gap]
        gap = ~np.isfinite(a)
    y = (1.0 - w) * wv + w * np.where(gap, wv, a)
    return np.clip(y, 0.0, 1.0)


def arm_targets(games: Iterable[tuple[list[dict], np.ndarray]], label_fn: LabelFn, w: float, seed: int,
                derived_fn: LabelFn | None = None) -> dict[str, np.ndarray]:
    """games: (rows of one game, that game's wv) in a fixed order. Returns arm -> concatenated target, plus
    'aux' (the raw source label) and 'coverage' (1 where the source covered the play)."""
    out: dict[str, list[np.ndarray]] = {k: [] for k in ("A0", "A1", "A2", "aux", "coverage")}
    if derived_fn is not None:
        out["A3"] = []
    for g, (rows, wv) in enumerate(games):
        aux = np.asarray(label_fn(rows), np.float64)
        if aux.shape != (len(rows),):
            raise ValueError(f"label source returned shape {aux.shape} for {len(rows)} rows")
        if np.nanmax(np.where(np.isfinite(aux), aux, 0.0), initial=0.0) > 1.0 or np.nanmin(aux, initial=0.0) < 0.0:
            raise ValueError("aux labels must be in [0, 1] (use rank01)")
        out["A0"].append(np.asarray(wv, np.float64))
        out["A1"].append(mix(wv, aux, w))
        out["A2"].append(mix(wv, placebo(aux, seed * 100003 + g), w))
        out["aux"].append(aux)
        out["coverage"].append(np.isfinite(aux).astype(np.float64))
        if derived_fn is not None:
            out["A3"].append(mix(wv, np.asarray(derived_fn(rows), np.float64), w))
    return {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in out.items()}
