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
           really land in: `land_margin` seconds either side, by the READ STATE of that end's stream (A-01b). The
           margin is MARGIN_SIGMAS (2) x the stream's landing sd, rounded UP to the MARGIN_STEP grid; the sd is the
           root sum of squares of the four error parts the replay's judge draws and the show pads with
           (the show's TimingParams, as park_show imports it; ledger L18 and L29-L30): the position (0.29 s once the tune's place was READ: the
           label is a whole-second floor, L18 (1); a stream NOT known to be read: 2.0 s, the largest measured fresh-tune
           sd, L18 (2): 1.24 s espn n=21, 1.98 s fs1 n=10), K at its floor (K_SD_MIN 0.5 s, L29-L30), the feed stamp
           (NBA 0.33, college 0.5, football 0.40 s) and the key (0.13 s). So NBA: read 1.5 s, unread 4.5 s (the flat
           +-4 s of before was 2 x 2.0 s, the position part alone); college read 2.0 s. A cut with no read state given
           takes the unread margin (never looser than before). The leave is also clean; no made-basket window; and no
           leave while the game is at a high-leverage moment: no high-leverage feed row within HL_LOOK seconds before
           or after the leave (the show's own crunch look, park_show.ShowParams.crunch_look, 90 s). The frozen
           counters do not count a dead-ball leave in a close-and-late stretch; CLAUDE.md ("never leave a high-leverage
           moment") and the replay's judge (park_replay.Judge.flip counts hl_leave on the high-leverage span whether
           or not the ball is dead) read stricter. Which reading is the rule is Nick's call; strict takes the stricter.
           NOT covered by any margin here: a game with NO K tonight (the replay's "nok" case, scenario rung R0): its K
           doubt is the 12 s prior (TimingParams.prior_sd), far past a few seconds of dead ball.

What is and is not shared with the show: NOTHING of the show's own guard is used here. The show's hard guard
(the PARK facts module's `refusal`, `Controller.park_why`) works on what the show BELIEVES at that second (its feed so far, its
padding); this mask works on the recorded TRUTH with the frozen grader. Both draw on the same grader rules (dead_at,
high leverage), but they are different code on different inputs. Track A section 4 asks for the guard's own mask in
training; that needs the show's state per option and is not built (A-02/A-03 wire the director into the planner).

Why: the director ranks options and never approves a jump; every option it sees in training must be legal by the
frozen grader with the measured landing error, and never looser than the grader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from redzone import grading
from redzone.lab import cutgrade, leverage
from redzone.noggin.k_table import K_SD_MIN
from redzone.noggin.park_show import SHOW, TIMING

UNREAD_TUNE_SD = 2.0           # a fresh tune whose place was not read: the largest measured landing sd (L18: 1.98 s fs1)
MARGIN_SIGMAS = 2.0            # the strict margin covers this many landing sds either side of a cut
HL_LOOK = SHOW.crunch_look     # seconds before and after a leave with no high-leverage row (park_show's crunch look)
HIGH_PLAY_LEN = 7.0            # a high-leverage play covers this long before its stamp (veto_audit, park_ceiling)
MARGIN_STEP = 0.5                     # how finely a margin span is checked for a dead ball
MASK_LEVELS = ("strict", "grader")


def landing_sd(league, read: bool) -> float:
    """How far (sd, seconds) a cut on a stream of this league truly lands from the second meant: the root sum of squares
    of the four error parts (position: read or not; K at its floor; feed stamp; key). See the module docstring."""
    lg = str(getattr(league, "value", league))
    pos = TIMING.read_sd if read else UNREAD_TUNE_SD
    return math.sqrt(pos ** 2 + K_SD_MIN ** 2 + TIMING.stamp_sd(lg) ** 2 + TIMING.key_sd ** 2)


def land_margin(league, read: bool) -> float:
    """strict's margin either side of a cut: MARGIN_SIGMAS landing sds, rounded UP to the MARGIN_STEP grid."""
    return math.ceil(MARGIN_SIGMAS * landing_sd(league, read) / MARGIN_STEP - 1e-9) * MARGIN_STEP


LAND_MARGIN = land_margin("nba", False)    # an NBA stream not known to be read (4.5 s); the flat +-4 s before A-01b


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

    def margin(self, g: int, read: bool) -> float:
        """strict's margin for a cut on game g on a stream whose place was (`read`) or was not known to be read."""
        return land_margin(self.rs[g].league, bool(read))

    def _dead_span(self, g: int, st: float, read: bool = False) -> bool:
        m = self.margin(g, read)
        n = int(round(2 * m / MARGIN_STEP))
        return all(self._dead(g, st - m + k * MARGIN_STEP) for k in range(n + 1))

    # ---- the two ends ---------------------------------------------------------------------------------------------
    def join_ok(self, g: int, j: float, read: bool = False) -> Verdict:
        """`read`: the joined stream's place was read at decision time (the show's jump record "read"/"to"); strict only."""
        r = self.rs[g]
        st = self.base + float(j)
        if self.level == "strict":
            return Verdict(True) if self._dead_span(g, st, read) else Verdict(False, "join: dead time does not cover the landing error")
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

    def leave_ok(self, g: int, i: float, read: bool = False) -> Verdict:
        """`read`: the left stream's place was read at decision time (the jump record's "read"/"from"); strict only."""
        r = self.rs[g]
        st = self.base + float(i)
        clean = grading.clean_leave(r.league, r.wall, r.plays, st)
        if self.level == "grader":
            if clean or not self.high_moment(g, i):
                return Verdict(True, "" if clean else "low-leverage mid-play leave")
            return Verdict(False, "leave: a high-leverage moment mid-play")
        if not self._dead_span(g, st, read):
            return Verdict(False, "leave: dead time does not cover the landing error")
        if not clean:
            return Verdict(False, "leave: not clean")
        if self.near_high(g, i):
            return Verdict(False, "leave: a high-leverage moment")
        return Verdict(True)

    def allows(self, g1: int, i: float, g2: int, j: float, leave_read: bool = False, join_read: bool = False) -> Verdict:
        """May the director cut from game g1 at true second i to game g2 at true second j? leave_read / join_read: was
        each stream's place read at decision time (unknown = not read: the wide margin)."""
        lv = self.leave_ok(g1, i, leave_read)
        if not lv.ok:
            return lv
        return self.join_ok(g2, j, join_read)

    def mask(self, options: list) -> list[bool]:
        """options: (g1, i, g2, j) or (g1, i, g2, j, leave_read, join_read) each; True = allowed."""
        return [self.allows(*o).ok for o in options]

    # ---- the show's own cuts, as a diagnostic ----------------------------------------------------------------------
    def audit(self, cuts: list, reads: list | None = None) -> dict:
        """The judge's true cuts (park_replay.Judge.cuts: (g1, true i, g2, true j)): how many the mask would block, by end
        and by why. reads: per cut (leave stream read, join stream read), as the show's jump records hold them (one
        record per cut); None = unknown, so every stream takes the unread margin."""
        if reads is not None and len(reads) != len(cuts):
            raise ValueError(f"{len(reads)} read states for {len(cuts)} cuts")
        reads = reads if reads is not None else [(False, False)] * len(cuts)
        out: dict = {"cuts": len(cuts), "blocked": 0, "blocked_leaves": 0, "blocked_joins": 0, "why": {}}
        for (g1, i, g2, j), (r1, r2) in zip(cuts, reads):
            lv, jn = self.leave_ok(g1, i, r1), self.join_ok(g2, j, r2)
            out["blocked_leaves"] += not lv.ok
            out["blocked_joins"] += not jn.ok
            if not (lv.ok and jn.ok):
                out["blocked"] += 1
                why = lv.why if not lv.ok else jn.why
                out["why"][why] = out["why"].get(why, 0) + 1
        return out
