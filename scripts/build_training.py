"""Download free play-by-play data and build labeled training snapshots.

    python scripts/build_training.py            # every league
    python scripts/build_training.py nba nhl    # just these
    python scripts/build_training.py --keep-raw nfl

Writes data/training/<league>.parquet and data/training/manifest.json (sizes, label
rates, sources, hotness knots). Raw downloads live in data/training/raw/ and are
deleted after each league is built unless --keep-raw. Everything under data/ is
git-ignored. See README "Training data" for sources and licenses.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from redzone.lab import datasets, sportsdata  # noqa: E402

NFLV = "https://github.com/nflverse/nflverse-data/releases/download"
SDV = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"

SOURCES = {
    "nfl": dict(name="nflverse play-by-play (nflfastR model WP/EPA)", license="CC-BY 4.0 (nflverse data)",
                files={f"nfl_{y}.parquet": f"{NFLV}/pbp/play_by_play_{y}.parquet" for y in (2023, 2024, 2025)}),
    "cfb": dict(name="cfbfastR play-by-play via sportsdataverse-data (ESPN feed + cfbfastR WP/EPA)",
                license="MIT (cfbfastR); underlying ESPN data for personal/non-commercial use",
                files={"cfb_2025.parquet": f"{SDV}/cfbfastR_cfb_pbp/play_by_play_2025.parquet"}),
    "nba": dict(name="hoopR ESPN NBA play-by-play via sportsdataverse-data",
                license="MIT (hoopR); underlying ESPN data for personal/non-commercial use",
                files={f"nba_{y}.parquet": f"{SDV}/espn_nba_pbp/play_by_play_{y}.parquet" for y in (2025, 2026)}),
    "cbb": dict(name="hoopR ESPN men's college basketball play-by-play via sportsdataverse-data",
                license="MIT (hoopR); underlying ESPN data for personal/non-commercial use",
                files={"cbb_2026.parquet": f"{SDV}/espn_mens_college_basketball_pbp/play_by_play_2026.parquet"}),
    # wcbb dropped (Nick Nov 4 7:55 PM): not one of Nick's leagues
    "nhl": dict(name="fastRhockey NHL play-by-play (NHL public API) via sportsdataverse-data",
                license="MIT (fastRhockey); NHL data for personal/non-commercial use",
                files={f"nhl_{y}.parquet": f"{SDV}/nhl_pbp_full/play_by_play_{y}.parquet" for y in (2025, 2026)}),
    "mlb": dict(name="MLB Stats API plate appearances via sportsdataverse-data (baseballr)",
                license="MLBAM data, personal/non-commercial use (MLB Stats API terms)",
                files={"mlb_pbp_2025.parquet": f"{SDV}/mlb_pbp/mlb_pbp_2025.parquet",
                       "mlb_pitches_2025.parquet": f"{SDV}/mlb_pitches/mlb_pitches_2025.parquet"}),
}
for _lg in ("nfl", "nba", "nhl", "mlb"):
    SOURCES[f"espn_{_lg}"] = dict(name=f"our own recorded ESPN summaries (data/archive/{_lg})",
                                  license="ESPN data, personal/non-commercial use", files={})

# Cap very large college-basketball seasons so builds stay well inside RAM / time.
MAX_GAMES = {"cbb": 3500, "wcbb": 3500}
WP_SOURCE = {"nfl": "nflverse home_wp_post", "cfb": "cfbfastR home_wp_after", "nba": "analytic (spread + normal)",
             "cbb": "analytic (spread + normal)", "wcbb": "analytic (spread + normal)",
             "nhl": "analytic (Poisson + man advantage)", "mlb": "analytic (RE24 + normal)",
             "espn_nfl": "ESPN winprobability, analytic fallback", "espn_nba": "ESPN winprobability, analytic fallback",
             "espn_nhl": "analytic (Poisson)", "espn_mlb": "analytic (RE24 + normal)"}
BUDGET_BYTES = 2 * 1024 ** 3


def fetch(league: str, raw: Path) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES[league]["files"].items():
        dest = raw / name
        if dest.exists() and dest.stat().st_size > 0:
            continue
        print(f"  get {url}", flush=True)
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            part = dest.with_suffix(".part")
            with part.open("wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            part.rename(dest)


def du(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=list(datasets.LEAGUES) + list(datasets.ESPN_LEAGUES))
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--out", type=Path, default=datasets.TRAINING_DIR)
    args = ap.parse_args()
    out: Path = args.out
    raw = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    man = datasets.manifest(out)
    man.setdefault("leagues", {})
    for lg in args.leagues:
        t0 = time.time()
        print(f"== {lg}", flush=True)
        if SOURCES[lg]["files"]:
            fetch(lg, raw)
        df, knots = sportsdata.build(lg, raw, archive_dir=ROOT / "data" / "archive", max_games=MAX_GAMES.get(lg))
        path = datasets.path_for(lg, out)
        df.to_parquet(path, compression="zstd", index=False)
        if not args.keep_raw:
            for name in SOURCES[lg]["files"]:
                (raw / name).unlink(missing_ok=True)
        s = datasets.summary(lg, out)
        s.update(file_mb=round(path.stat().st_size / 1e6, 1), seasons=sorted(int(x) for x in df.season.unique()),
                 source=SOURCES[lg]["name"], license=SOURCES[lg]["license"],
                 urls=list(SOURCES[lg]["files"].values()), wp_source=WP_SOURCE[lg],
                 hot_knots=knots, built_s=round(time.time() - t0, 1))
        man["leagues"][lg] = s
        print({k: v for k, v in s.items() if k not in ("hot_knots", "urls")}, flush=True)
        del df
        total = du(out)
        if total > BUDGET_BYTES:
            print(f"!! data/training is {total / 1e9:.2f} GB, over the 2 GB budget", flush=True)
    if raw.exists() and not any(raw.iterdir()):
        shutil.rmtree(raw)
    man["total_mb"] = round(du(out) / 1e6, 1)
    man["labels"] = {"break_horizon_s": datasets.BREAK_HORIZON_S, "key_window_s": datasets.KEY_WINDOW_S}
    (out / "manifest.json").write_text(json.dumps(man, indent=1))
    print(f"total {man['total_mb']} MB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
