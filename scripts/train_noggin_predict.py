#!/usr/bin/env python3
"""Train Noggin Predict v1 for one league from the data factory's table (Kaggle CPU / cloud only).

    python scripts/noggin_factory.py nba --seasons 2013-2026 --out data/noggin
    python scripts/train_noggin_predict.py nba --data data/noggin --out redzone/noggin/models

Five-fold season-level forward CV on the non-test nights (mean + worst fold vs the game-state baseline),
then fit on train, isotonic calibration on calib, and ONE grade on the FULL test split. Writes
<out>/predict_<league>.v1.npz and <data>/predict_<league>.report.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from redzone.noggin import predict as P  # noqa: E402
from redzone.noggin import predict_fit as PF  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "noggin")
    ap.add_argument("--out", type=Path, default=P.MODEL_DIR)
    ap.add_argument("--rounds", type=int, default=400)
    ap.add_argument("--folds", type=int, default=5)
    a = ap.parse_args()
    t = pd.read_parquet(a.data / f"{a.league}_windows.parquet")
    t0 = time.time()
    rep = {"rows": int(len(t)), "cv": PF.forward_cv(t, a.folds, a.rounds)}
    m = PF.fit(t, a.league, a.rounds)
    rep["info"] = m.info
    rep["test"] = PF.grade(m, t[t.split == "test"], t[t.split == "train"])
    a.out.mkdir(parents=True, exist_ok=True)
    out = a.out / f"predict_{a.league}.v1.npz"
    m.save(out)
    m2 = P.load(out)
    x = t[list(P.PREDICT_FEATURES)].to_numpy(np.float32)[:8]
    lat = []
    for _ in range(200):
        s = time.perf_counter()
        for hz in m2.forests:
            m2.proba(x, hz)
        lat.append((time.perf_counter() - s) * 1000)
    rep["latency_ms_8_games"] = {"p50": round(float(np.percentile(lat, 50)), 2), "p99": round(float(np.percentile(lat, 99)), 2)}
    rep["model_bytes"] = out.stat().st_size
    rep["total_s"] = round(time.time() - t0)
    (a.data / f"predict_{a.league}.report.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({"test": rep["test"], "cv": {h: {k: v for k, v in c.items() if k != "folds"} for h, c in rep["cv"].items()},
                      "latency": rep["latency_ms_8_games"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
