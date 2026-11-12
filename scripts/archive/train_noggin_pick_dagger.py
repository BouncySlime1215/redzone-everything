#!/usr/bin/env python3
"""DAgger for the possession policy: label the states the PICK-cue policy ITSELF visits with the oracle.

    python scripts/train_noggin_pick_dagger.py nba --model M.txt --out DIR --iters 2

Plain imitation (train_noggin_pick.py --cue-s 90) learns from the oracle's own path, so one early mistake
puts the rollout in states it never saw (compounding error: ~92 vs the oracle's ~98 big live). Each DAgger
round rolls the current model out on every train night, labels every visited decision step with the
oracle's best action FROM THAT STATE (oracle.value_to_go_cue: stay, or switch to b landing on its last dead
ball), adds those rows to the aggregate and retrains. Labels come only from the oracle (no Nick signal).
Splits as train_noggin_pick: train seasons label + fit, calib early-stops and picks the margin, test is
graded once on the full set (same grader, same rollout).
Why: DAgger for the possession policy: labels states the policy visits with the oracle (docstring).
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("RZ_PICK_CUE_S", "90")

import train_noggin_pick as T  # noqa: E402
from redzone.lab import oracle as O  # noqa: E402
from redzone.noggin import pick as PK  # noqa: E402


def visited(switches, steps, replays, blk: int):
    """(k, a, o) for every step the policy decides at: on game a, o steps behind live (black steps skipped)."""
    gi = {r.gid: i for i, r in enumerate(replays)}
    sw = sorted(switches, key=lambda s: s.t)
    K = len(steps)
    out = []
    for j, s in enumerate(sw):
        k0 = int(round((s.t - steps[0]) / O.STEP_S)) + blk
        k1 = int(round((sw[j + 1].t - steps[0]) / O.STEP_S)) + 1 if j + 1 < len(sw) else K
        a, o = gi[s.to], int(round((getattr(s, "offset", 0.0) or 0.0) / O.STEP_S))
        out += [(k, a, o) for k in range(k0, min(k1, K))]
    return out


def dagger_rows(args):
    league, path, model_path, margin, every = args
    import lightgbm as lgb
    model = lgb.Booster(model_file=str(model_path))
    out = []
    for night, reps in sorted(T.season_nights(league, path).items()):
        n, steps, tab, X = T.night_pack(reps)
        G, K, F = X.shape
        cue = O.cue_steps(tab, T.CUE_S)
        sc = model.predict(X.reshape(-1, F), num_threads=1).reshape(G, K)
        sw = PK.rollout(sc, X, reps, steps, T.TUNE_S, margin, "pick", cue)
        V, Rs, Ls, cue_, blk, ws = O.value_to_go_cue(n, "combined", tab, T.CUE_S)
        land = X[:, :, PK.PICK_FEATURES.index("land_ok")] > 0
        xs, ys = [], []
        for k, a, o in visited(sw, steps, reps, blk)[::every]:
            q = O.cue_action_values(V, Rs, Ls, cue_, blk, ws, k, a, o)
            best = int(np.argmax(q))
            keep = land[:, k].copy()
            keep[[a, best]] = True
            idx = np.where(keep)[0]
            xs.append(X[idx, k])
            ys.append((idx == best).astype(np.float32))
        if xs:
            out.append((night, np.concatenate(xs), np.concatenate(ys)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--raw", type=Path, default=Path("/home/claude/factory_raw/nba"))
    ap.add_argument("--model", type=Path, required=True, help="round-0 PICK-cue model (LightGBM text)")
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--train", default="2016-2022")
    ap.add_argument("--calib", default="2023-2024")
    ap.add_argument("--test", default="2025-2026")
    ap.add_argument("--iters", type=int, default=2)
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    import lightgbm as lgb
    rng = lambda s: list(range(int(s.split("-")[0]), int(s.split("-")[1]) + 1))   # noqa: E731
    yrs = {k: rng(getattr(a, k)) for k in ("train", "calib", "test")}
    a.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with ProcessPoolExecutor(a.jobs) as ex:
        base = {y: r for y, r in zip(yrs["train"] + yrs["calib"], ex.map(
            T.build_season, [(a.league, a.raw / f"pbp_{y}.parquet", a.every) for y in yrs["train"] + yrs["calib"]]))}
    stack = lambda parts: (np.concatenate([x for _, x, _ in parts]), np.concatenate([y for _, _, y in parts]))  # noqa: E731
    xca, yca = stack([p for y in yrs["calib"] for p in base[y]])
    agg = [p for y in yrs["train"] for p in base[y]]
    model_path, margin, log = a.model, a.margin, []
    for it in range(1, a.iters + 1):
        with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("spawn")) as ex:
            new = [p for part in ex.map(dagger_rows, [(a.league, a.raw / f"pbp_{y}.parquet", model_path, margin, a.every)
                                                     for y in yrs["train"]]) for p in part]
        agg += new
        xtr, ytr = stack(agg)
        m = lgb.train(T.PARAMS, lgb.Dataset(xtr, ytr), a.rounds, valid_sets=[lgb.Dataset(xca, yca)],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        model_path = a.out / f"pick_{a.league}.cue{int(T.CUE_S)}.dagger{it}.txt"
        m.save_model(str(model_path))
        log.append(dict(iter=it, new_rows=int(sum(len(y) for _, _, y in new)), rows=int(len(ytr)), trees=m.num_trees(),
                        seconds=round(time.time() - t0)))
        print("  dagger", log[-1], flush=True)
    margins = (0.0, 0.05, 0.1, 0.2)
    report = {"dagger": log}
    for split in ("calib", "test"):
        jobs = []
        for y in yrs[split]:
            p = a.raw / f"pbp_{y}.parquet"
            nn = sorted(T.season_nights(a.league, p))       # every night of the split (the full set)
            jobs += [(a.league, p, nn[c:c + 24], model_path, margins) for c in range(0, len(nn), 24)]
        with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("spawn")) as ex:
            res = [r for part in ex.map(T.grade_split, jobs) for r in part]
        report[split] = {k: T.totals(res, k) for k in ["oracle", "oracle_live", "hand"] + [f"pick_{m}" for m in margins]}
        if split == "calib":
            def val(t):
                return 3 * t["big_live_pct"] + t["crunch_live_pct"] - 2 * t["dead_air_pct"] - 0.5 * t["switches_per_hour"]
            report["margin"] = max(margins, key=lambda m: val(report["calib"][f"pick_{m}"]))
        print(f"  {split}: " + json.dumps(report[split]), flush=True)
    (a.out / f"pick_{a.league}.cue{int(T.CUE_S)}.dagger.report.json").write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
