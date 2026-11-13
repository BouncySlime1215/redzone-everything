"""Gym v2's Rule #1 mask: which cuts a director may make, judged on the recorded truth (A-01 slice 3).

Rule #1 (CLAUDE.md): never JOIN mid-play; never leave a high-leverage moment; leave mid-play only at low leverage.
The mask is an action mask, not a reward term (track A-director section 4). It calls the frozen grader's own functions,
never a copy: `grading.dead_at`, `grading.clean_leave` (sportsgrade's clean-cut rule), `cutgrade.after_bucket` (the
made-basket entry window, grader v49) and `leverage.high_leverage`. None is edited here.

A cut is (leave game g1 at true second i, join game g2 at true second j), seconds on the night's grid as the replay's
judge has them (feed time = base + second). Two levels (coordinator, Nov 13: the training mask may be STRICTER than the
frozen counters, never looser):
  grader   exactly what the frozen counters do not count as a break: the join is a grader dead ball or inside the
           made-basket window (cutgrade's entries_mid_play); the leave is clean, or else not at a high-leverage moment
           (sportsgrade's cuts_high).
  strict   (default, for training) the join AND the leave are grader dead balls over the whole span the cut may
           really land in: LAND_MARGIN seconds either side, 2 x the largest measured landing sd of a fresh tune (2.0 s,
           ledger L18: 1.24 s espn n=21, 1.98 s fs1 n=10); the leave is also clean; no made-basket window; and no
           leave while the game is at a high-leverage moment: no high-leverage feed row within HL_LOOK seconds before
           or after the leave (the show's own crunch look, park_show.ShowParams.crunch_look, 90 s). The frozen
           counters do not count a dead-ball leave in a close-and-late stretch; CLAUDE.md ("never leave a high-leverage
           moment") and the replay's judge (park_replay.Judge.flip counts hl_leave on the high-leverage span whether
           or not the ball is dead) read stricter. Which reading is the rule is Nick's call; strict takes the stricter.

What is and is not shared with the show: NOTHING of the show's own guard is used here. The show's hard guard
(the PARK facts module's `refusal`, `Controller.park_why`) works on what the show BELIEVES at that second (its feed so far, its
padding); this mask works on the recorded TRUTH with the frozen grader. Both draw on the same grader rules (dead_at,
high leverage), but they are different code on different inputs. Track A section 4 asks for the guard's own mask in
training; that needs the show's state per option and is not built (A-02/A-03 wire the director into the planner).

Why: the director ranks options and never approves a jump; every option it sees in training must be legal by the
frozen grader with the measured landing error, and never looser than the grader.
"""
from __future__ import annotations

from dataclasses import dataclass

from redzone import grading
from redzone.lab import cutgrade, leverage
from redzone.noggin.park_show import SHOW

LAND_MARGIN = 2 * 2.0          # seconds either side of a cut: 2 x the largest measured landing sd (L18)
HL_LOOK = SHOW.crunch_look     # seconds before and after a leave with no high-leverage row (park_show's crunch look)
HIGH_PLAY_LEN = 7.0            # a high-leverage play covers this long before its stamp (veto_audit, park_ceiling)
MARGIN_STEP = 0.5                     # how finely a margin span is checked for a dead ball
MASK_LEVELS = ("strict", "grader")


@dataclass(frozen=True)
class Verdict:
    ok: bool
    why: str = ""


class Rule1Mask:
    """The mask over one slate's recorded games (`replays`: backtest.Replay, in slate order; `base`: the grid's zero)."""

    def __init__(self, replays: list, base: int, *, level: str = "strict"):
        if level not in MASK_LEVELS:
            raise ValueError(f"level is one of {MASK_LEVELS}")
        self.rs, self.base, self.level = list(replays), int(base), level

    def _dead(self, g: int, st: float) -> bool:
        r = self.rs[g]
        return grading.dead_at(r.league, r.wall, r.plays, st)[0]

    def _dead_span(self, g: int, st: float) -> bool:
        n = int(round(2 * LAND_MARGIN / MARGIN_STEP))
        return all(self._dead(g, st - LAND_MARGIN + k * MARGIN_STEP) for k in range(n + 1))

    # ---- the two ends ---------------------------------------------------------------------------------------------
    def join_ok(self, g: int, j: float) -> Verdict:
        r = self.rs[g]
        st = self.base + float(j)
        if self.level == "strict":
            return Verdict(True) if self._dead_span(g, st) else Verdict(False, "join: dead time does not cover the landing error")
        if self._dead(g, st):
            return Verdict(True)
        if cutgrade.after_bucket(r.league, r.wall, r.plays, st):
            return Verdict(True, "made-basket window")
        return Verdict(False, "join mid-play")

    def high_moment(self, g: int, i: float) -> bool:
        """sportsgrade's cuts_high test: is the feed row on screen at second i high leverage?"""
        r = self.rs[g]
        k = grading.idx(r.wall, self.base + float(i))
        return bool(r.plays) and leverage.high_leverage(r.league, r.plays[max(0, k)])

    def near_high(self, g: int, i: float, look: float = HL_LOOK) -> bool:
        """Any high-leverage feed row stamped within `look` s before or after second i (strict's high-leverage moment)."""
        r = self.rs[g]
        st = self.base + float(i)
        k = grading.idx(r.wall, st + look)
        while k >= 0 and r.wall[k] >= st - look:
            if leverage.high_leverage(r.league, r.plays[k]):
                return True
            k -= 1
        return False

    def leave_ok(self, g: int, i: float) -> Verdict:
        r = self.rs[g]
        st = self.base + float(i)
        clean = grading.clean_leave(r.league, r.wall, r.plays, st)
        if self.level == "grader":
            if clean or not self.high_moment(g, i):
                return Verdict(True, "" if clean else "low-leverage mid-play leave")
            return Verdict(False, "leave: a high-leverage moment mid-play")
        if not self._dead_span(g, st):
            return Verdict(False, "leave: dead time does not cover the landing error")
        if not clean:
            return Verdict(False, "leave: not clean")
        if self.near_high(g, i):
            return Verdict(False, "leave: a high-leverage moment")
        return Verdict(True)

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
        """The judge's true cuts (park_replay.Judge.cuts: (g1, true i, g2, true j)): how many the mask would block, by end
        and by why."""
        out: dict = {"cuts": len(cuts), "blocked": 0, "blocked_leaves": 0, "blocked_joins": 0, "why": {}}
        for g1, i, g2, j in cuts:
            lv, jn = self.leave_ok(g1, i), self.join_ok(g2, j)
            out["blocked_leaves"] += not lv.ok
            out["blocked_joins"] += not jn.ok
            if not (lv.ok and jn.ok):
                out["blocked"] += 1
                why = lv.why if not lv.ok else jn.why
                out["why"][why] = out["why"].get(why, 0) + 1
        return out
