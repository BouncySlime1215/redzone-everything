"""[scrub entry, item 4] REPORT ONLY, not a grader: the EXP-601 replay with the hidden stick buffered by the overlay
scrub (control._scrub_into) instead of a pause hold. The frozen grader (redzone/lab/backtest.py, HiddenStick) is not
edited: this swaps in a subclass for one run. Item 5 owns the real grader change (entry cost in backtest).

What changes vs EXP-601 arm A ("wait": the cut waits until the hidden stick is ready, old game on screen):
- getting ready costs tune + SCRUB_S (presses ~10 s + one label read ~8 s, Nick Nov 6), not tune + B * overrun;
- the picture then airs B s behind live (the scrub asks B - 8 s and the label read adds ~8 s);
- the stick we leave does NOT hold the game we left: YouTube TV pauses when its stick loses the TV input and a pause
  doesn't hold (Nick Nov 6), so it is refreshed: ready tune + SCRUB_S after the cut;
- Prime targets air at B = 0 as in EXP-601 (no scrub on Prime).
The control cut also re-checks a safe moment for up to 25 s after the scrub and drops the switch without one; the
replay grades the cut at the ready time without that re-check (the pessimistic side, as EXP-601).

usage: python scripts/scrub_flip_replay.py <eval_sports args...> [--scrub-s 18]
  (pass --hidden-stick wait --behind-live B; --split must be train/valid/fit)
Why: Report only: EXP-601 replay with the hidden stick buffered by overlay scrub (docstring).
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import backtest  # noqa: E402

SCRUB_S = 18.0


class ScrubStick(backtest.HiddenStick):
    def hold_of(self, target):
        return 0.0 if (target or "").startswith("prime:") else SCRUB_S

    def on_switch(self, sw, watching, targets):
        if sw.kind in backtest.HS_NO_BUFFER:
            return
        super().on_switch(sw, watching, targets)
        # The stick we left: a YouTube TV game is paused by the input loss and reloaded + scrubbed (no free hold).
        left = sw.frm
        if left and not (self.target or "").startswith("prime:"):
            self.ready_at = self.tuned_at + self.tune() + self.hold_of(self.target) + backtest.HS_PREREG["resume"]


def main():
    global SCRUB_S
    args = sys.argv[1:]
    if "--scrub-s" in args:
        i = args.index("--scrub-s")
        SCRUB_S = float(args[i + 1])
        del args[i:i + 2]
    if "--hidden-stick" not in args or args[args.index("--hidden-stick") + 1] != "wait":
        sys.exit("pass --hidden-stick wait (the scrub arm waits for the scrub, old game on screen)")
    backtest.HiddenStick = ScrubStick
    sys.argv = [str(ROOT / "scripts/eval_sports.py"), *args]
    runpy.run_path(str(ROOT / "scripts/eval_sports.py"), run_name="__main__")


if __name__ == "__main__":
    main()
