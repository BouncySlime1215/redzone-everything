#!/usr/bin/env python3
"""Train Noggin Timing v1 for one league from the data factory's table (Kaggle CPU / GitHub Actions only).

    python scripts/noggin_factory.py nba --seasons 2013-2026 --out data/noggin
    python scripts/train_noggin_timing.py nba --data data/noggin --out redzone/noggin/models

Fits the hazard on the train nights, calibrates the conformal lower bound on the calib nights, grades on
the factory's test nights (all unseen, all before the scorecard window) and writes
<out>/timing_<league>.v1.npz plus <data>/timing_<league>.report.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from redzone.decider import cuts  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin import timing as T  # noqa: E402
from redzone.noggin import timing_fit as TF  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "noggin")
    ap.add_argument("--out", type=Path, default=T.MODEL_DIR)
    ap.add_argument("--rounds", type=int, default=300)
    a = ap.parse_args()
    t = pd.read_parquet(a.data / f"{a.league}_windows.parquet")
    t0 = time.time()
    m = TF.fit(t, a.league, rounds=a.rounds)
    floors = cuts.FLOOR_S.get(League(a.league).base, {})
    rep = {"info": m.info, "offsets": m.offsets, "alpha": m.alpha, "fit_s": round(time.time() - t0)}
    for sp in ("calib", "test"):
        rep[sp] = TF.grade(m, t[t.split == sp].reset_index(drop=True), floors)
    out = a.out / f"timing_{a.league}.v1.npz"
    m.save(out)
    rep["model_bytes"] = out.stat().st_size
    (a.data / f"timing_{a.league}.report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep["test"][k] for k in ("below_lower_pct", "floor_below_pct", "lower_mean_s",
                                                  "floor_mean_s", "ece_restart30")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
