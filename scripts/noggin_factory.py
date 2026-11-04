#!/usr/bin/env python3
"""Noggin data factory: labeled dead-ball moments from public play-by-play (redzone/noggin/factory.py).

    python scripts/noggin_factory.py nba --seasons 2010-2026 --raw /kaggle/working/raw --out data/noggin

Runs on free Kaggle CPU or GitHub Actions (noggin-factory.yml), never on the Mac. One season per process;
each season's raw parquet is fetched, turned into windows, and deleted unless --keep-raw.
Writes <out>/<league>_windows.parquet and <out>/manifest.json (rows / nights / games per split).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.noggin import factory as F  # noqa: E402


def _fetch(url: str, dest: Path) -> bool:
    import requests
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=180) as r:
        if r.status_code == 404:
            return False
        r.raise_for_status()
        part = dest.with_suffix(".part")
        with part.open("wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        part.rename(dest)
    return True


def _one(args):
    league, year, raw, keep, max_games = args
    src = F.SOURCES[league]
    dest = Path(raw) / league / f"pbp_{year}.parquet"
    if not _fetch(src["url"].format(y=year), dest):
        return year, None
    t = F.build_basketball(league, dest, max_games=max_games)
    if not keep:
        dest.unlink(missing_ok=True)
    return year, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league", choices=sorted(F.SOURCES))
    ap.add_argument("--seasons", default="2015-2026")
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "noggin" / "raw")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "noggin")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--max-games", type=int, default=None, help="per season (smoke tests)")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    t0 = time.time()
    years = [y for y in F.seasons(a.seasons) if y >= F.SOURCES[a.league]["first"]]
    with ProcessPoolExecutor(a.jobs) as ex:
        parts = dict(ex.map(_one, [(a.league, y, str(a.raw), a.keep_raw, a.max_games) for y in years]))
    for y in years:
        print(f"  {a.league} {y}: {0 if parts.get(y) is None else len(parts[y]):,} windows", flush=True)
    t = F.concat(parts.values())
    n_all = len(t)
    t = F.drop_eval_nights(t, a.league)
    t = F.assign_splits(t)
    a.out.mkdir(parents=True, exist_ok=True)
    t.to_parquet(a.out / f"{a.league}_windows.parquet", compression="zstd", index=False)
    mp = a.out / "manifest.json"
    man = json.loads(mp.read_text()) if mp.exists() else {}
    man[a.league] = {"source": F.SOURCES[a.league]["url"], "license": F.SOURCES[a.league]["license"],
                     "seasons": a.seasons, "windows": int(len(t)), "dropped_eval_window": int(n_all - len(t)),
                     "eval_first_night": F.EVAL_FIRST_NIGHT.get(a.league), "features": list(F.FEATURES),
                     "splits": F.manifest(t).get(a.league, {}), "built_s": round(time.time() - t0)}
    mp.write_text(json.dumps(man, indent=1))
    print(json.dumps(man[a.league]["splits"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
