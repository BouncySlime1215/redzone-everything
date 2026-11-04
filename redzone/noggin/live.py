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
        kinds = (self.model.info or {}).get("kinds")
        if kinds is not None and w.kind not in kinds:
            return None     # a kind the model never trained on (NFL: ESPN's tv_timeout): the floor table
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


class PredictHook:
    """Noggin Predict v1 on the live path: P(big play within 30/60/120 s of the next restart) for one game,
    from its DeadTracker (the window features, same `features.context`) and its Timeline's score events
    (`features.game_state`). Lead changes are counted from what this app has seen of the game."""

    def __init__(self, model, league: League):
        self.model = model
        self.league = league
        self._lc: dict[str, tuple[float, int, int]] = {}   # gid -> (last counted event time, sign, lead changes)
        self._cache: dict[tuple, dict] = {}

    def _lead_changes(self, gid: str, scores) -> int:
        """Count lead changes from score events newer than the last one counted (the Timeline keeps only
        the recent 60, so the count lives here)."""
        last, sign, lc = self._lc.get(gid, (float("-inf"), 0, 0))
        for ev in scores:
            if ev.score is None or ev.seen <= last:
                continue
            s = (ev.score[0] > ev.score[1]) - (ev.score[0] < ev.score[1])
            if s and sign and s != sign:
                lc += 1
            sign, last = s or sign, ev.seen
        self._lc[gid] = (last, sign, lc)
        return lc

    def reading(self, gid: str, tl, now: float, playoff: int = 0) -> dict | None:
        dt = tl.dead
        wins = dt.windows()
        if not wins:
            return None
        by_hi = {m.hi: m for m in dt.marks.values()}
        rows = [(x, by_hi[x.start].period if x.start in by_hi else None,
                 by_hi[x.start].clock if x.start in by_hi else None) for x in wins]
        key = (gid, wins[-1].start, len(tl.scores))
        if key in self._cache:
            return self._cache[key]
        ctx = F.context(self.league, rows[:-1], rows[-1])
        sc = [e for e in tl.scores if e.score is not None]
        walls = np.array([e.seen for e in sc] or [now])
        home = np.array([float(e.score[0]) for e in sc] or [0.0])
        away = np.array([float(e.score[1]) for e in sc] or [0.0])
        ctx.update(F.game_state(walls, home, away, len(sc) - 1 if sc else -1))
        ctx["lead_changes"] = self._lead_changes(gid, tl.scores)
        ctx["playoff"] = playoff
        x = np.array([ctx[f] for f in F.PREDICT_FEATURES], np.float32)
        out = self.model.reading(x)
        if len(self._cache) > 256:
            self._cache.clear()
        self._cache[key] = out
        return out


def predict_hook_for(cfg, league: League, model_dir: Path | None = None) -> PredictHook | None:
    """The Predict hook for this league if `noggin.predict.leagues` turns it on and its model ships."""
    on = {str(x).lower() for x in ((cfg.get("noggin.predict.leagues") if cfg is not None else None) or [])}
    if league.value not in on:
        return None
    from redzone.noggin import predict as PR
    m = PR.model_for(league.value, model_dir)
    return PredictHook(m, league) if m is not None else None
