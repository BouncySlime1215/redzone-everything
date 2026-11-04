"""Held-out calibration of the break forecast's rules + brain blend (breaks.blend).

    python scripts/build_training.py espn_nfl espn_nba espn_nhl   # labels from the pinned archive, once
    python scripts/eval_break_blend.py                            # nfl nba nhl, JSON on stdout
    python scripts/eval_break_blend.py nba --training /tmp/training

Reads only archive nights that are neither LOCKED test nights nor nights the brain trained on
(redzone/lab/breakblend.py). Per league: ECE / Brier / AUC of the rules, the brain alone, the old
50/50 blend and the fitted log-odds blend, each graded on forward-chaining held-out folds (mean and
worst), and `ship`: the brain.break_blend coefficients to put in default_config.yaml.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import breakblend, evaldata  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=["nfl", "nba", "nhl"])
    ap.add_argument("--training", type=Path, default=None, help="espn_<league>.parquet folder (data/training)")
    a = ap.parse_args()
    out = {"eval_data": evaldata.stamp()}
    for lg in a.leagues:
        end = evaldata.window_end(lg, None)
        rows = breakblend.collect(lg, root=a.training, end=end)
        rep = breakblend.grade(rows, breakblend.eligible_nights(lg, end=end))
        for f in rep["folds"]:
            f.pop("coef", None)
        out[lg] = rep
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
