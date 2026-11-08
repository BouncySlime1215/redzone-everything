"""Build the Brain v3 training tables (run in the cloud, never on the Mac: ~6M events, a few GB RAM).

    python scripts/build_training3.py --raw /path/to/raw            # nfl nba nhl mlb + espn_*
    python scripts/build_training3.py --raw /path/to/raw nba espn_nba

Raw files are the same free releases scripts/build_training.py downloads (it keeps them with
--keep-raw): nfl_<year>.parquet, nba_<year>.parquet, nhl_<year>.parquet, mlb_pbp_/mlb_pitches_2025.
Writes data/training3/<league>.parquet and manifest.json. See redzone/lab/brain3data.py.
Why: Builds Brain v3 training tables; too heavy for the Mac, so it runs in the cloud (docstring).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab import brain3data  # noqa: E402

LEAGUES = ("nfl", "nba", "nhl", "mlb", "espn_nfl", "espn_nba", "espn_nhl", "espn_mlb")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=list(LEAGUES))
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "training" / "raw")
    ap.add_argument("--out", type=Path, default=brain3data.TRAINING3_DIR)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    mp = a.out / "manifest.json"
    man = json.loads(mp.read_text()) if mp.exists() else {}
    for lg in a.leagues:
        t0 = time.time()
        df = brain3data.build3(lg, a.raw, ROOT / "data" / "archive")
        df.to_parquet(a.out / f"{lg}.parquet", compression="zstd", index=False)
        man[lg] = {"rows": len(df), "games": int(df.game_id.nunique()), "nights": int(df.night.nunique()),
                   "split_rows": df.split.value_counts().to_dict(),
                   "rates": {c: round(float(df[c].mean()), 4) for c in ("stop15", "stop30", "stop60", "big60", "big180")},
                   "wall_clock_share": round(float(df.clock_wall.mean()), 3), "built_s": round(time.time() - t0, 1)}
        print(lg, man[lg], flush=True)
        del df
    mp.write_text(json.dumps(man, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
