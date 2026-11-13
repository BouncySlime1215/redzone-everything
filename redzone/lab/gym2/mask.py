"""Gym v2's Rule #1 mask: which cuts a director may make, by the FROZEN grader's own functions (A-01 slice 3).

Rule #1 (CLAUDE.md): never JOIN mid-play; never leave a high-leverage moment; leave mid-play only at low leverage.
The mask is an action mask, not a reward term (track A-director section 4). It only calls the frozen grader, never a
copy of it: `grading.dead_at` (the grader's dead ball), `grading.clean_leave` (sportsgrade's clean-cut rule),
`cutgrade.after_bucket` (the made-basket entry window of grader v49) and `leverage.high_leverage`. None is edited here.

A cut is (leave game g1 at true second i, join game g2 at true second j), seconds on the night's grid as the replay's
judge has them (feed time = base + second). Two levels:
  strict (default)  the join is a grader dead ball; the leave is a grader dead ball AND a clean leave AND not inside a
                    high-leverage play. The show's own engine is this strict (it never sets the any-shot rule).
  rule              the join is a grader dead ball or inside the made-basket window (what cutgrade does not count as
                    mid-play); the leave is clean, or else not at a high-leverage moment (what sportsgrade does not
                    count in cuts_high).
Both never allow a cut the frozen grader counts as a Rule #1 break (tests/test_gym2_mask.py checks it with
cutgrade.grade itself on recorded nights).

Why: the director ranks options; it never creates or approves a jump. The same mask runs in training and serving.
"""
from __future__ import annotations

from dataclasses import dataclass

from redzone import grading
from redzone.lab import cutgrade, leverage

HIGH_PLAY_LEN = 7.0       # a high-leverage play covers this long before its stamp (veto_audit.PLAY_LEN_S, park_ceiling.HIGH_PLAY_LEN)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    why: str = ""


class Rule1Mask:
    """The mask over one slate's recorded games (`replays`: backtest.Replay, in slate order; `base`: the grid's zero)."""

    def __init__(self, replays: list, base: int, *, level: str = "strict"):
        if level not in ("strict", "rule"):
            raise ValueError("level is strict or rule")
        self.rs, self.base, self.level = list(replays), int(base), level

    # ---- the two ends ---------------------------------------------------------------------------------------------
    def join_ok(self, g: int, j: float) -> Verdict:
        r = self.rs[g]
        st = self.base + float(j)
        if grading.dead_at(r.league, r.wall, r.plays, st)[0]:
            return Verdict(True)
        if self.level == "rule" and cutgrade.after_bucket(r.league, r.wall, r.plays, st):
            return Verdict(True, "made-basket window")
        return Verdict(False, "join mid-play")

    def high_moment(self, g: int, i: float) -> bool:
        """At second i, is game g at a high-leverage moment: the feed row on screen is high leverage
        (sportsgrade's cuts_high test)?"""
        r = self.rs[g]
        k = grading.idx(r.wall, self.base + float(i))
        return bool(r.plays) and leverage.high_leverage(r.league, r.plays[max(0, k)])

    def leave_ok(self, g: int, i: float) -> Verdict:
        r = self.rs[g]
        st = self.base + float(i)
        clean = grading.clean_leave(r.league, r.wall, r.plays, st)
        if self.level == "rule":
            if clean or not self.high_moment(g, i):
                return Verdict(True, "" if clean else "low-leverage mid-play leave")
            return Verdict(False, "leave a high-leverage moment mid-play")
        if not grading.dead_at(r.league, r.wall, r.plays, st)[0]:
            return Verdict(False, "leave mid-play")
        if not clean:
            return Verdict(False, "leave not clean")
        if self.in_high_play(g, i):
            return Verdict(False, "leave a high-leverage moment")
        return Verdict(True)

    def in_high_play(self, g: int, i: float) -> bool:
        """Is second i inside a high-leverage play of game g (a play covers HIGH_PLAY_LEN before its stamp: the replay
        judge's and the show's guard's high-leverage span)?"""
        r = self.rs[g]
        st = self.base + float(i)
        k = grading.idx(r.wall, st + HIGH_PLAY_LEN)
        while k >= 0 and r.wall[k] >= st:
            if leverage.high_leverage(r.league, r.plays[k]):
                return True
            k -= 1
        return False

    def allows(self, g1: int, i: float, g2: int, j: float) -> Verdict:
        """May the director cut from game g1 at true second i to game g2 at true second j?"""
        lv = self.leave_ok(g1, i)
        if not lv.ok:
            return lv
        return self.join_ok(g2, j)

    def mask(self, options: list) -> list[bool]:
        """options: (g1, i, g2, j) each; True = allowed."""
        return [self.allows(*o).ok for o in options]

    # ---- the show's own cuts, as a diagnostic ----------------------------------------------------------------------
    def audit(self, cuts: list) -> dict:
        """The judge's true cuts (park_replay.Judge.cuts: (g1, true i, g2, true j)): how many the mask would block, by why."""
        out: dict = {"cuts": len(cuts), "blocked": 0, "why": {}}
        for g1, i, g2, j in cuts:
            v = self.allows(g1, i, g2, j)
            if not v.ok:
                out["blocked"] += 1
                out["why"][v.why] = out["why"].get(v.why, 0) + 1
        return out
