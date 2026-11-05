"""STAKES context shards (Crew B, worker B3): point-in-time standings, Elo and playoff-odds swing per game.

    python scripts/context/stakes.py --league nfl --games games.csv --out /mnt/project-files/data-b/shards/
    python scripts/context/stakes.py --league mlb --retro-dir gamelogs/ --out ...      # GL<year>.TXT files

Inputs (both public, no key):
  nfl  nflverse schedules  https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv
  mlb  Retrosheet game logs https://raw.githubusercontent.com/chadwickbureau/retrosheet/master/seasons/<Y>/GL<Y>.TXT
       (2024 is spelled gl2024.txt there)

Writes <out>/<league>-stakes/<season>.parquet (tokens.COLUMNS) and <out>/<league>-stakes/edges.json (16 quantile
buckets per field, fit on TRAIN games only: kickoff before 2022-09-01 NFL / 2022-03-01 MLB). Unplayed games of the
current season within 8 days of now are written with asof = now (the pregame "tonight" case).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redzone.context.schedules import load_nfl, load_retrosheet  # noqa: E402
from redzone.context.standings import LEAGUES, compute_league  # noqa: E402
from redzone.context.tokens import apply_edges, fit_edges, ledger_counts, to_frame  # noqa: E402

TRAIN_CUTOFF = {"nfl": "2022-09-01", "mlb": "2022-03-01"}


def write_shards(league: str, games: pd.DataFrame, by_season: dict, out: Path, log=print) -> dict:
    d = out / f"{league}-stakes"
    d.mkdir(parents=True, exist_ok=True)
    frames = {s: to_frame(f) for s, f in by_season.items() if f}
    allf = pd.concat(frames.values(), ignore_index=True)
    kick = games.set_index("game_id")["kickoff_utc"]
    cutoff = pd.Timestamp(TRAIN_CUTOFF[league], tz="UTC")
    edges = fit_edges(allf, allf["game_id"].map(kick).lt(cutoff).to_numpy())
    (d / "edges.json").write_text(json.dumps({"train_cutoff_utc": TRAIN_CUTOFF[league], "n_buckets": 16,
                                              "edges": edges}, indent=1))
    per = {}
    for s, f in frames.items():
        f = apply_edges(f, edges)
        f.to_parquet(d / f"{s}.parquet", index=False)
        per[str(s)] = ledger_counts(f)["facts"]
    counts = ledger_counts(allf)
    counts["per_season_facts"] = per
    counts["seasons"] = [min(frames), max(frames)]
    log(f"{league}: wrote {len(frames)} shards to {d}/  {counts['facts']} facts")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", choices=sorted(LEAGUES), required=True)
    ap.add_argument("--games", help="nflverse games.csv (nfl)")
    ap.add_argument("--retro-dir", help="folder of Retrosheet GL<year>.TXT (mlb)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--first", type=int, default=0)
    ap.add_argument("--sims", type=int)
    ap.add_argument("--manifest", help="merge ledger counts into this manifest json")
    a = ap.parse_args(argv)
    t0 = time.time()
    games = load_nfl(a.games) if a.league == "nfl" else load_retrosheet(a.retro_dir)
    games = games[games["season"] >= a.first]
    by = compute_league(games, LEAGUES[a.league], now_utc=pd.Timestamp.now(tz="UTC"), sims=a.sims)
    counts = write_shards(a.league, games, by, Path(a.out))
    counts["runtime_s"] = round(time.time() - t0, 1)
    print(json.dumps({k: v for k, v in counts.items() if k != "per_season_facts"}))
    if a.manifest:
        mp = Path(a.manifest)
        m = json.loads(mp.read_text()) if mp.exists() else {}
        m.setdefault("ledger_counts", {})[f"{a.league}-stakes"] = counts
        mp.write_text(json.dumps(m, indent=1))
    return counts


if __name__ == "__main__":
    main()
