"""Train Brain v3 (one compact model per league) and print its test-night scorecard.

Cloud only (needs lightgbm, ~6M rows; never on the Mac):
    python scripts/build_training3.py --raw <raw dir>     # data/training3/*.parquet
    python scripts/train_brain3.py                          # nfl nba nhl mlb
    python scripts/train_brain3.py nba --live               # + head-to-head vs rules and v2 on data/archive

Writes redzone/scoring/brain_models/<league>.v3.npz (committed), metrics_v3.json and
docs/brain3/calibration_<league>.png.
Why: Trains Brain v3, one compact model per league (docstring, Brain v3 commit).
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import brain3fit  # noqa: E402
from redzone.scoring import brain  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=["nfl", "nba", "nhl", "mlb"])
    ap.add_argument("--live", action="store_true", help="also grade vs rules and v2 through the live path")
    ap.add_argument("--live-only", action="store_true")
    a = ap.parse_args()
    path = brain.MODEL_DIR / "metrics_v3.json"
    reports = json.loads(path.read_text()) if path.exists() else {}
    for lg in a.leagues:
        t0 = time.time()
        rep = reports.get(lg, {})
        if not a.live_only:
            print(f"{lg}: training", flush=True)
            rep = brain3fit.train_league(lg, plots=ROOT / "docs" / "brain3")
        if a.live or a.live_only:
            rep["live"] = brain3fit.live_eval3(lg)
        reports[lg] = rep
        print(f"{lg}: done in {time.time() - t0:.0f}s", flush=True)
        path.write_text(json.dumps(reports, indent=1) + "\n")


if __name__ == "__main__":
    main()
