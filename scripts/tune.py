"""Auto-tune the decider against the scorecards (see redzone/lab/tune.py).

    python scripts/tune.py --gens 24             # CMA-ES on the TRAIN half of the nights
    python scripts/tune.py --report              # winner vs today on TRAIN and held-out TEST
    python scripts/tune.py --report --params p.json

Heavy (all CPU cores for ~an hour): run it in the cloud, never on the Mac.
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
    ap.add_argument("--gens", type=int, default=24)
    ap.add_argument("--lam", type=int, default=12)
    ap.add_argument("--sigma", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--params", default=None, help="JSON file of knob values to report on")
    a = ap.parse_args()
    if a.report:
        p = json.loads(Path(a.params).read_text()) if a.params else None
        print(json.dumps(tune.report(p, a.workers), indent=1))
        return
    best = tune.tune(a.gens, a.lam, a.sigma, a.workers, a.seed, log=lambda s: print(s, flush=True))
    print(json.dumps(best, indent=1, default=str))


if __name__ == "__main__":
    main()
