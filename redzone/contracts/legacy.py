"""Adapters from today's code to the contracts, so every thread builds against real types now.

Owner: INTEGRATION. Each owner replaces its adapter with a native implementation; the contract
tests (tests/test_contracts.py) run against both.

Why: first added in 418ffc56 (2026-11-03) as "Architecture contract v1: interfaces, stubs, contract tests, ownership, status board"; no separate decision is recorded for this module.
"""
from __future__ import annotations

from redzone.contracts.breaks import BreakForecast, BreakKind
from redzone.contracts.moment import MomentPrediction, empty
from redzone.contracts.sync import SyncEstimate
from redzone.decider.core import Action
from redzone.models import GameState

# breaks.LEN medians only: until the BreakModel owner ships quantiles, q10/q90 are these multiples.
Q10_MULT, Q90_MULT = 0.6, 1.6


class LegacySync:
    """SyncModel over redzone.decider.screen.DelayBook."""

    def __init__(self, book):
        self.book = book

    def estimate(self, target: str, now: float) -> SyncEstimate:
        d = self.book.get(target)
        src = d.source if d.source in ("tap", "screen", "app", "boundary", "predicted", "default", "sync") else "default"
        return SyncEstimate(target, float(d.mu), float(d.current_sigma(now)), src, d.calibrated_at,
                            float(getattr(d, "behind", 0.0)))

    def observe(self, target: str, sample_s: float, kind: str, at: float) -> None:
        if kind == "tap":
            self.book.calibrate(target, sample_s)
        elif kind == "boundary":
            self.book.boundary(target, sample_s, at)
        elif kind == "screen":
            self.book.screen(target, sample_s, at)
        else:
            raise ValueError(f"LegacySync can't take {kind!r} samples")


def _break_kind(why: str, expect_s: float) -> BreakKind:
    w = why.lower()
    if "half" in w or "intermission" in w:
        return BreakKind.HALFTIME
    if "end of" in w or "quarter" in w or "period" in w:
        return BreakKind.QUARTER
    if "team timeout" in w or (0 < expect_s < 90):
        return BreakKind.TEAM_TIMEOUT
    if "inning" in w or ("two" in w and "out" in w):
        return BreakKind.INNINGS
    return BreakKind.COMMERCIAL if expect_s > 0 else BreakKind.NONE


class LegacyBreaks:
    """BreakModel over redzone.scoring.breaks.BreakBook."""

    def __init__(self, book):
        self.book = book

    def observe(self, g: GameState) -> None:
        self.book.observe(g)

    def forecast(self, g: GameState, now: float) -> BreakForecast:
        f = self.book.forecast(g)
        m = float(f.expect_s)
        return BreakForecast(g.game_id, now, 1.0 if f.now else float(f.p), _break_kind(f.why, m), bool(f.now),
                             m * Q10_MULT, m, m * Q90_MULT, None, f.why)


class LegacyMoment:
    """MomentModel over redzone.scoring.brain.Brain."""

    def __init__(self, brain, version: str = "brain-v2"):
        self.brain = brain
        self.version = version

    def predict(self, g: GameState, now: float) -> MomentPrediction:
        if self.brain is None or not self.brain.available(g.league):
            return empty(g.game_id, now)
        r = self.brain.read(g)
        return MomentPrediction(g.game_id, now, r.hotness, r.key_soon_p, r.break_soon_p,
                                reasons=tuple(r.reasons), model_version=self.version)

    def forget(self, game_id: str) -> None:
        if self.brain is not None:
            self.brain.forget(game_id)


def action_from_decision(d, *, home: str | None = None) -> Action:
    """Map a legacy redzone.decider.decider.Decision to the core's Action (for traces and diffs)."""
    from redzone.decider.decider import REEL
    if d.action == "stay":
        verb = "stay"
    elif d.action == "replay":
        verb = "replay"
    elif d.target == REEL:
        verb = "reel"
    elif d.kind == "break_visit":
        verb = "visit"
    elif d.kind == "return" or (home is not None and d.target == home):
        verb = "return"
    else:
        verb = "switch"
    return Action(verb, d.target, reason=d.reason, rewind_s=float(d.rewind_s or 0.0), kind=d.kind)
