#!/usr/bin/env python3
"""Noggin self-play: PICK training rows from the hindsight oracle, on real AND synthetic nights.

    python scripts/noggin_selfplay.py nba --raw RAW/nba/pbp_2019.parquet --season 2019 --out SHARDS \
        --synthetic 6 --seed 7

For one season of public play-by-play (hoopR parquet, the factory's raw file):
  * every real night (games grouped by their 5 AM ET night) is solved by the oracle (redzone/lab/oracle.py,
    objective "combined") and turned into PICK rows (redzone/noggin/pick.py features, label = the game the
    oracle has on screen one tune later), exactly as scripts/train_noggin_pick.py does;
  * `--synthetic K` more slates per real night: K random games of the SAME season (so a season never mixes
    splits: splits are by season, train < calib < test), each re-timed to tip off at a random point of a
    3-hour window, at a random screen lag (`LAGS`). Self-play nights: no Nick input, labels from the oracle.

Writes <out>/<league>/<season>/part-NNNN.parquet (float16 features, y, slate, lag, synthetic; CHUNK slates
per part) and <out>/<league>/<season>.json (rows, slates, parts, complete). Parts that exist are skipped, so a
season cut short by its budget resumes where it stopped.
Locked/eval nights are never read (train_noggin_pick.season_nights drops the scorecard window).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.lab import oracle as O  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.noggin import pick as PK  # noqa: E402

LAGS = (80.0, 116.0, 150.0)       # screen lag (channel delay + time-shift buffer) of a synthetic slate
WINDOW_S = 3 * 3600.0             # synthetic tip-offs spread over this window


def _pack(reps, lag: float, T):
    t0 = min(r.wall[0] for r in reps) - 600.0
    t1 = max(r.wall[-1] for r in reps) + 600.0
    n = O.Night(reps, T.MINE, lag, T.TUNE_S, t0, t1)
    steps = np.arange(t0, t1, O.STEP_S)
    tab = O.tables(n, steps)
    X = PK.features_for_night(reps, tab, steps, lag, T.MINE)
    return n, steps, tab, X


def _shift(r: Replay, start: float, tag: str) -> Replay:
    d = start - r.wall[0]
    return Replay(r.league, f"{r.gid}~{tag}", {}, r.plays, [w + d for w in r.wall], r.matchup)


def synthetic_slates(nights: dict, k: int, rng: np.random.Generator):
    """(slate id, replays, lag) for k random slates per real night, games drawn from the whole season."""
    pool = [r for reps in nights.values() for r in reps]
    sizes = [len(v) for v in nights.values()]
    for night in sorted(nights):
        base = min(r.wall[0] for r in nights[night])
        for j in range(k):
            g = int(rng.choice(sizes))
            pick = rng.choice(len(pool), size=min(g, len(pool)), replace=False)
            reps = [_shift(pool[i], base + float(rng.uniform(0, WINDOW_S)), f"s{j}") for i in pick]
            yield f"{night}s{j}", reps, float(rng.choice(LAGS))


CHUNK = 100                       # slates per part file (~3M rows, ~150 MB in RAM): the unit of resume


def build(league: str, raw: Path, season: int, out: Path, synthetic: int, seed: int, every: int,
          budget_s: float = float("inf")) -> dict:
    """Write <out>/<league>/<season>/part-NNNN.parquet (CHUNK slates each) and <out>/<league>/<season>.json.

    The slate list is deterministic (seeded), so a part that exists is skipped: a run cut short by its
    budget continues where it stopped. An unfinished chunk is dropped, never written half-done."""
    import pandas as pd
    import pyarrow.parquet as pq

    import train_noggin_pick as T
    meta = out / league / f"{season}.json"
    if meta.exists() and json.loads(meta.read_text()).get("complete"):
        return json.loads(meta.read_text())                      # done in an earlier session
    t0 = time.time()
    nights = T.season_nights(league, raw)
    rng = np.random.default_rng([seed, season])
    work = [(n, reps, T.LAG_S, 0) for n, reps in sorted(nights.items())]
    work += [(sid, reps, lag, 1) for sid, reps, lag in synthetic_slates(nights, synthetic, rng)]
    work = [w for w in work if len(w[1]) >= 2]
    d = out / league / str(season)
    d.mkdir(parents=True, exist_ok=True)
    n_chunks = (len(work) + CHUNK - 1) // CHUNK
    rows, slates, new_parts, cut = 0, 0, 0, False
    for ci in range(n_chunks):
        part = d / f"part-{ci:04d}.parquet"
        chunk = work[ci * CHUNK:(ci + 1) * CHUNK]
        if part.exists():
            rows += pq.ParquetFile(part).metadata.num_rows
            slates += len(chunk)
            continue
        if cut:
            continue
        parts = []
        for sid, reps, lag, syn in chunk:
            if time.time() - t0 > budget_s:
                cut = True
                break
            n, steps, tab, X = _pack(reps, lag, T)
            _, tgt = T.labels(n, steps, tab)
            x, y = T.rows(X, tgt, every)
            df = pd.DataFrame(x.astype(np.float16), columns=list(PK.PICK_FEATURES))
            df["y"] = y.astype(np.int8)
            df["slate"] = sid
            df["lag"] = np.float16(lag)
            df["synthetic"] = np.int8(syn)
            parts.append(df)
        if cut:
            continue
        t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(PK.PICK_FEATURES))
        tmp = part.with_suffix(".tmp")
        t.to_parquet(tmp, compression="zstd", index=False)
        tmp.rename(part)
        rows += len(t)
        slates += len(chunk)
        new_parts += 1
    done = sum(1 for ci in range(n_chunks) if (d / f"part-{ci:04d}.parquet").exists())
    info = {"league": league, "season": season, "rows": int(rows), "real_nights": len(nights),
            "slates": slates, "slates_total": len(work), "parts": done, "parts_total": n_chunks,
            "new_parts": new_parts, "synthetic_per_night": synthetic, "every": every, "seed": seed,
            "complete": done == n_chunks,
            "bytes": sum(f.stat().st_size for f in d.glob("part-*.parquet")), "seconds": round(time.time() - t0)}
    meta.write_text(json.dumps(info))
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league", choices=["nba", "cbb"])
    ap.add_argument("--raw", type=Path, required=True)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--synthetic", type=int, default=6)
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--budget-s", type=float, default=float("inf"))
    a = ap.parse_args()
    print(json.dumps(build(a.league, a.raw, a.season, a.out, a.synthetic, a.seed, a.every, a.budget_s)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
