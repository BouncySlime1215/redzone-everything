"""[fallback ladder, item 2] REPORT ONLY, not a grader: what the ladder changes when a YouTube TV buffer entry fails.

The frozen grader (redzone/lab/backtest.py HiddenStick) is not edited: this swaps in a subclass for one run, like
scripts/scrub_flip_replay.py. Every switch that would air BUFFERED on a YouTube TV target draws a scrub failure with
probability --fail-p (a separate RNG, the same draws in both arms, so the arms differ only by the fallback):

  keep    (flag off, timing.behind_live_s.scrub_fallback keep): control drops the switch ("buffer entry failed; kept
          the current game") and the target is in scrub backoff for scrub_retry_s (120 s); the decider asks again and
          the next scrub is drawn again. Modelled as the cut waiting 120 s + one more scrub per failure (old game on
          screen), at most 3 failures, then it airs buffered.
  ladder  (fallback_ladder.enabled): the same stick is re-tuned at the live edge (one fresh tune draw) and the cut
          airs at B = 0 (offset -= B, graded where the picture is, as EXP-601 arm B).

usage: python scripts/ladder_replay.py <eval_sports args...> --hidden-stick wait --hs-entry scrub_measured
       --fallback keep|ladder --fail-p 0.1
"""
from __future__ import annotations

import random
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import backtest  # noqa: E402

MODE, FAIL_P, RETRY_S, MAX_FAILS = "keep", 0.1, 120.0, 3
COUNT = {"draws": 0, "failed": 0, "fell_to_live": 0, "kept_waits_s": 0.0}


class LadderStick(backtest.HiddenStick):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        seed = kw.get("seed", a[5] if len(a) > 5 else 0)
        self.frng = random.Random(seed * 91_001 + 2)    # failure draws: identical in both arms
        self.trng = random.Random(seed * 91_001 + 3)    # the ladder's extra tune: never shifts the base draws

    def on_switch(self, sw, watching, targets):
        if sw.kind in backtest.HS_NO_BUFFER:
            return super().on_switch(sw, watching, targets)
        to = (targets or {}).get(sw.to, f"youtubetv:{sw.to}")
        if to.startswith("prime:"):
            return super().on_switch(sw, watching, targets)
        COUNT["draws"] += 1
        fails = 0
        while fails < MAX_FAILS and self.frng.random() < FAIL_P:
            fails += 1
        if not fails:
            return super().on_switch(sw, watching, targets)
        COUNT["failed"] += 1
        if MODE == "keep":
            extra = fails * (RETRY_S + self.hold_of(to))
            COUNT["kept_waits_s"] += extra
            super().on_switch(sw, watching, targets)
            sw.leave_dt += extra
            sw.arrive_dt += extra
            at, what = watching[-1]
            watching[-1] = (at + extra, what)
            return None
        # ladder: the scrub ran and failed (keys + read), then the live rung re-tunes the same stick at the edge
        COUNT["fell_to_live"] += 1
        cut = sw.t + (sw.leave_dt or 0.0)
        self.n += 1
        same = sw.to == self.gid
        first = 0.0 if (same and cut >= self.tuned_at) else self._tune2()
        wait = first + self.hold_of(to) + self._tune2()
        self.retunes += 1
        sw.leave_dt += wait
        sw.arrive_dt += wait
        at, what = watching[-1]
        watching[-1] = (at + wait, what)
        self.waits.append(wait)
        sw.offset -= self.B
        self.unbuffered_t.append(sw.t)
        self.at_edge.add(sw.to)
        left = sw.frm
        self.gid, self.target = left, ((targets or {}).get(left, f"youtubetv:{left}") if left else None)
        self.tuned_at = self.ready_at = cut + wait
        if left in self.at_edge:
            self.ready_at += self.hold_of(self.target)
        return None

    def _tune2(self) -> float:
        lo, hi = backtest.HS_PREREG["tune_clip"]
        return min(hi, max(lo, self.trng.lognormvariate(backtest.HS_PREREG["tune_mu"], backtest.HS_PREREG["tune_sigma"])))

    def card(self) -> dict:
        return {**super().card(), "ladder_replay": {"mode": MODE, "fail_p": FAIL_P, **COUNT}}


def main():
    global MODE, FAIL_P
    args = sys.argv[1:]
    for flag in ("--fallback", "--fail-p"):
        if flag in args:
            i = args.index(flag)
            v = args[i + 1]
            del args[i:i + 2]
            if flag == "--fallback":
                MODE = v
            else:
                FAIL_P = float(v)
    if MODE not in ("keep", "ladder"):
        sys.exit("--fallback keep|ladder")
    backtest.HiddenStick = LadderStick
    sys.argv = [str(ROOT / "scripts/eval_sports.py"), *args]
    try:
        runpy.run_path(str(ROOT / "scripts/eval_sports.py"), run_name="__main__")
    finally:
        print(f"ladder_replay {MODE} p={FAIL_P}: {COUNT}", file=sys.stderr)


if __name__ == "__main__":
    main()
