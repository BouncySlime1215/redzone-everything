"""Train the brain's per-league models and print their held-out scorecard.

    .venv/bin/python -m pip install lightgbm           # once (training only; the service doesn't need it)
    .venv/bin/python scripts/build_training.py         # once: data/training/<league>.parquet
    .venv/bin/python scripts/train_brain.py            # every league (~10 min on an M2)
    .venv/bin/python scripts/train_brain.py nba nhl    # just these

Writes redzone/scoring/brain_models/<league>.npz (small, committed) and metrics.json.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import brainfit, datasets  # noqa: E402
from redzone.scoring import brain  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=list(datasets.LEAGUES))
    a = ap.parse_args()
    path = brain.MODEL_DIR / "metrics.json"
    reports = json.loads(path.read_text()) if path.exists() else {}
    for lg in a.leagues:
        t0 = time.time()
        print(f"{lg}: training", flush=True)
        reports[lg] = brainfit.train_league(lg)
        print(f"{lg}: done in {time.time() - t0:.0f}s, {reports[lg]['file_kb']} KB", flush=True)
        brainfit.write_metrics(reports, path)


if __name__ == "__main__":
    main()
