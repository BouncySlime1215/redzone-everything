"""Noggin Timing v1 on the live path: a calibrated "surely still dead" bound for an open dead window.

Behind `noggin.timing.leagues` (default [] = OFF). When a league is on, `cuts.DeadTracker` asks
`TimingHook.lower_until(dt, w, e)` instead of its fixed FLOOR_S table: "the data proves this window has
been dead e seconds; with probability >= 1 - alpha it stays dead until at least the returned real time".
The expected restart (`resume_eta`) uses the model's median the same way, in place of MEDIAN_S.

Readings are cached per (window, 5 s elapsed bin), so each tick costs at most one ~72-bin forest walk per
open window (measured < 2 ms, see tests/test_noggin_timing.py).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from redzone.models import League
from redzone.noggin import features as F
from redzone.noggin import timing as T


class TimingHook:
    def __init__(self, model: T.TimingModel, league: League):
        self.model = model
        self.league = league
        self._cache: dict[tuple, tuple[float, float]] = {}

    def _features(self, dt, w) -> np.ndarray | None:
        by_hi = {m.hi: m for m in dt.marks.values()}
        hist, cur = [], None
        for x in dt.windows():
            m = by_hi.get(x.start)
            row = (x, m.period if m else None, m.clock if m else None)
            if x.start == w.start:
                cur = row
                break
            hist.append(row)
        if cur is None:
            return None
        return F.vector(F.context(self.league, hist, cur))

    def reading(self, dt, w, e: float) -> tuple[float, float] | None:
        """(lower bound, median) of the window's TOTAL dead time in seconds from w.start, given it has
        been dead at least e seconds."""
        # Read at the start of the elapsed bin: conditioning on less proven dead time can only lower
        # the bound, so it stays safe, and one reading serves every tick of the bin.
        e = math.floor(max(0.0, float(e)) / T.BIN_S) * T.BIN_S
        key = (w.start, w.kind, e)
        if key in self._cache:
            return self._cache[key]
        x = self._features(dt, w)
        if x is None:
            return None
        lo, med = self.model.reading(x, e, w.kind)
        out = (e + lo, e + med)
        if len(self._cache) > 512:
            self._cache.clear()
        self._cache[key] = out
        return out

    def lower_until(self, dt, w, e: float) -> float | None:
        r = self.reading(dt, w, e)
        return None if r is None else w.start + r[0]

    def median_until(self, dt, w, e: float) -> float | None:
        r = self.reading(dt, w, e)
        return None if r is None else w.start + r[1]


def hook_for(cfg, league: League, model_dir: Path | None = None) -> TimingHook | None:
    """The hook for this league if `noggin.timing.leagues` turns it on and its model ships."""
    on = {str(x).lower() for x in ((cfg.get("noggin.timing.leagues") if cfg is not None else None) or [])}
    if league.value not in on:
        return None
    m = T.model_for(league.value, model_dir)
    return TimingHook(m, league) if m is not None else None
