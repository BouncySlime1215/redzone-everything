"""Gym v2's reward terms, each judged where the picture TRULY was (the replay's judge), per step (A-01 slice 3).

Terms (track A-director section 4, "Reward"); each is kept apart in `info["terms"]` and the scalar is their weighted sum:
  value       graded value seen: value2's grade of each play on screen with its lead-in (park_replay.Judge.v2). value2 is
              not frozen yet (task F-08): the term is the replay's own number until it is.
  must_see    the grader's big plays seen live (Judge.big; park_core.Arr.mom)
  live        seconds of live play on screen
  staleness   minus the mean age (minutes) of what the viewer has seen of each game in progress, per second on the show's
              clock (the seen-state scorecard's mean_unseen, park_replay.seen_card); Track E's seen-state replaces it later
Rule #1 is NOT a reward term: it is the action mask (mask.py); breaks are counted in `info["rule1"]` as a diagnostic only.
Weights: only `value` is on (1.0) by default. The others wait for Track D's utility (Nick's picks); set them per
experiment, and log them with the run.

Why: a hybrid reward with checkable terms kept apart, so a reward hack shows up in one term (A11).
"""
from __future__ import annotations

from dataclasses import dataclass

TERMS = ("value", "must_see", "live", "staleness")


@dataclass(frozen=True)
class Weights:
    value: float = 1.0
    must_see: float = 0.0
    live: float = 0.0
    staleness: float = 0.0

    def total(self, terms: dict) -> float:
        return sum(getattr(self, k) * float(terms.get(k, 0.0)) for k in TERMS)


class RewardMeter:
    """Reads the judge after each step; returns the terms earned since the last read. Read only."""

    def __init__(self, judge, a: int):
        self.j = judge
        self.v2, self.big, self.live = judge.v2, judge.big, judge.live_s
        self.next_t = a
        self.last_on = [max(a, x.a0) for x in judge.A]

    def read(self, upto: int) -> dict:
        """Terms for show ticks [next_t, upto): value, must-see and live from the judge's totals; staleness from what
        was on screen each second (judge.tl)."""
        j = self.j
        out = {"value": j.v2 - self.v2, "must_see": j.big - self.big, "live": float(j.live_s - self.live), "staleness": 0.0}
        self.v2, self.big, self.live = j.v2, j.big, j.live_s
        age = 0.0
        for t in range(self.next_t, upto):
            on = j.tl.get(t)
            ages, n = 0.0, 0
            for g, x in enumerate(j.A):
                if not (x.a0 <= t <= x.a1):
                    continue
                if on == g:
                    self.last_on[g] = t
                ages += t - self.last_on[g]
                n += 1
            age += (ages / n) if n else 0.0
        out["staleness"] = -age / 60.0
        self.next_t = max(self.next_t, upto)
        return out


def rule1(judge) -> dict:
    """The judge's Rule #1 counts so far (a diagnostic, never a reward)."""
    return {"mid_entry": judge.mid_entry, "mid_leave": judge.mid_leave, "hl_leave": judge.hl_leave}
