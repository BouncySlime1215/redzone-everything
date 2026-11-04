"""Auto-tune the decider against the scorecards (see redzone/lab/tune.py).

    python scripts/tune.py                       # 5-fold forward-chaining CV, writes data/lab/tune.json
    python scripts/tune.py --resume              # continue after a stop (per-fold checkpoints)

Heavy (all CPU cores for over an hour): run it in the cloud, never on the Mac.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import tune  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gens-fold", type=int, default=10, help="generations for folds 1-4")
    ap.add_argument("--gens", type=int, default=24, help="generations for the last fold (the candidate)")
    ap.add_argument("--lam", type=int, default=12)
    ap.add_argument("--sigma", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--resume", action="store_true", help="continue from the data/lab/tune_fold*.json checkpoints")
    a = ap.parse_args()
    out = tune.cv(a.gens_fold, a.gens, a.lam, a.sigma, a.workers, a.seed,
                  log=lambda s: print(s, flush=True), resume=a.resume)
    print(json.dumps({k: v for k, v in out.items() if k != "folds"}, indent=1, default=str))


if __name__ == "__main__":
    main()
