"""[WV loop EXP-1725] Reachable crunch: replay eval_sports with NBA run_up + window rows needing the margin to be
catchable in the time left (|margin| <= 3 * (1 + clock_s // poss_s), poss_s = 12). Graded rows untouched.
Usage: python scripts/exp1725.py [--poss-s 12] <eval_sports args>. personal-use, not for commercial release."""
from __future__ import annotations
import dataclasses, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from redzone import crunch  # noqa: E402


def patch(poss_s: float, league: str = "nba", kinds=("run_up", "window")) -> None:
    for k in kinds:
        r = crunch.TABLE[k].get(league)
        if r is not None:
            crunch.TABLE[k][league] = dataclasses.replace(r, poss_s=poss_s)


if __name__ == "__main__":
    args = sys.argv[1:]
    ps = 12.0
    if args[:1] == ["--poss-s"]:
        ps, args = float(args[1]), args[2:]
    if ps > 0:
        patch(ps)
    import eval_sports
    sys.argv = [sys.argv[0]] + args
    raise SystemExit(eval_sports.main())
