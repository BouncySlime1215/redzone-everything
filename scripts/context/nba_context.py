#!/usr/bin/env python3
"""NBA context shards (Data Crew B, worker B4): stakes, rest/travel and L2M referee facts + schedule CSVs.

    python scripts/context/nba_context.py --raw RAW --out /mnt/project-files/data-b/shards \
        [--cdn-games RAW/cdn_games.csv] [--l2m RAW/L2M_stats_nba.csv] [--schedules-out DIR] [--commit-schedules] \
        [--nfl-games games.csv] [--manifest M.json]

RAW holds hoopR schedule parquets nba_schedule_<Y>.parquet (Y = season END year; downloaded from the
sportsdataverse-data release when missing). Seasons 2002-03 and 2003-04 only warm Elo up; facts start 2004-05.
Writes <out>/nba-stakes, nba-rest, nba-l2m: <season>.parquet (tokens.COLUMNS, season = start year) + edges.json
(16 buckets per numeric field fit on TRAIN games only: tip before 2022-10-01).
--cdn-games: per NBA.com game id the first action time + tricodes (built from shufinskiy/nba_data cdnnba files by
`cdn-games` mode below) -> nba_game_id crosswalk (2020-21 on), needed for the L2M join.
Why: NBA context shards: stakes, rest and travel, referee facts (docstring, Data B NBA commit).
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from redzone.context.nba import (  # noqa: E402
    HOOPR_SCHEDULE_URL, crosswalk, l2m_facts, load_hoopr, load_l2m, national_tv, rest_travel_facts, schedule_rows, tv_facts,
)
from redzone.context.standings import NBA, compute_league  # noqa: E402
from redzone.context.tokens import apply_edges, fit_edges, ledger_counts, to_frame  # noqa: E402

NBA_TRAIN_CUTOFF = "2022-10-01"
NBA_FIRST_SEASON = 2004          # 2004-05
NBA_WARMUP_FROM = 2002           # Elo warm-up seasons (no facts written)
SCHEDULES_DIR = ROOT / "redzone" / "context" / "schedules"
CDN_URL = "https://raw.githubusercontent.com/shufinskiy/nba_data/main/datasets/cdnnba_{po}{y}.tar.xz"


def fetch_hoopr(raw: Path, last_end_year: int) -> list[Path]:
    raw.mkdir(parents=True, exist_ok=True)
    out = []
    for y in range(NBA_WARMUP_FROM + 1, last_end_year + 1):
        p = raw / f"nba_schedule_{y}.parquet"
        if not p.exists() or (y == last_end_year and time.time() - p.stat().st_mtime > 6 * 3600):
            try:
                urllib.request.urlretrieve(HOOPR_SCHEDULE_URL.format(y=y), p)
            except Exception as e:  # noqa: BLE001
                print(f"hoopR {y}: {e}")
                continue
        out.append(p)
    return out


def cdn_games(tars, out_csv: Path) -> pd.DataFrame:
    """shufinskiy cdnnba_<y>.tar.xz (one CSV inside) -> gameId, t0 (first timeActual), teams (sorted tricodes)."""
    rows = []
    for t in tars:
        try:
            tf = tarfile.open(t)
        except Exception as e:  # noqa: BLE001
            print(f"{t}: {e}")
            continue
        for m in tf.getmembers():
            d = pd.read_csv(tf.extractfile(m), usecols=["gameId", "timeActual", "teamTricode"], low_memory=False)
            g = d.groupby("gameId").agg(t0=("timeActual", "min"),
                                        teams=("teamTricode", lambda s: "|".join(sorted(set(s.dropna())))))
            rows.append(g.reset_index())
    x = pd.concat(rows, ignore_index=True)
    x.to_csv(out_csv, index=False)
    return x


def write_dir(name: str, games: pd.DataFrame, by_season: dict, out: Path) -> dict:
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    frames = {s: to_frame(f) for s, f in sorted(by_season.items()) if f}
    allf = pd.concat(frames.values(), ignore_index=True)
    kick = games.set_index("game_id")["kickoff_utc"]
    train = allf["game_id"].map(kick).lt(pd.Timestamp(NBA_TRAIN_CUTOFF, tz="UTC")).to_numpy()
    edges = fit_edges(allf, train)
    (d / "edges.json").write_text(json.dumps({"train_cutoff_utc": NBA_TRAIN_CUTOFF, "n_buckets": 16,
                                              "edges": edges}, indent=1))
    per = {}
    for s, f in frames.items():
        apply_edges(f, edges).to_parquet(d / f"{s}.parquet", index=False)
        per[str(s)] = int(len(f))
    c = ledger_counts(allf)
    c.update(per_season_facts=per, seasons=[min(frames), max(frames)])
    print(f"{name}: {len(frames)} shards, {c['facts']} facts (new {c['new']}, derived {c['derived']})")
    return c


def by_season(facts, games) -> dict[int, list]:
    s_of = games.set_index("game_id")["season"].to_dict()
    out: dict[int, list] = {}
    for f in facts:
        out.setdefault(int(s_of[f.game_id]), []).append(f)
    return out


def write_schedule(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path).endswith(".gz"):
        with gzip.open(path, "wt", newline="") as f:
            df.to_csv(f, index=False)
    else:
        df.to_csv(path, index=False)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="facts", choices=["facts", "cdn-games"])
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out")
    ap.add_argument("--cdn-games")
    ap.add_argument("--l2m")
    ap.add_argument("--nfl-games", help="nflverse games.csv -> nfl schedule CSV too")
    ap.add_argument("--schedules-out")
    ap.add_argument("--commit-schedules", action="store_true", help="also write redzone/context/schedules/*.csv.gz")
    ap.add_argument("--sims", type=int)
    ap.add_argument("--first", type=int, default=NBA_FIRST_SEASON)
    ap.add_argument("--skip-stakes", action="store_true")
    ap.add_argument("--manifest")
    ap.add_argument("--tv-csv", help="write the NBA national TV list (date, home, away, network, national) here")
    ap.add_argument("--tv-first", type=int, default=2020, help="first season (start year) in --tv-csv")
    a = ap.parse_args(argv)
    raw = Path(a.raw)
    now = pd.Timestamp.now(tz="UTC")
    if a.mode == "cdn-games":
        tars = sorted(raw.glob("cdn*.tar.xz"))
        print(len(cdn_games(tars, raw / "cdn_games.csv")), "NBA.com games")
        return
    t0 = time.time()
    end_year = now.year + 1 if now.month >= 7 else now.year
    games = load_hoopr(fetch_hoopr(raw, end_year))
    games["nba_game_id"] = crosswalk(games, pd.read_csv(a.cdn_games)) if a.cdn_games else ""
    print(f"games {len(games)}; with NBA.com id {int((games['nba_game_id'] != '').sum())}")
    counts = {}
    out = Path(a.out) if a.out else None
    fact_games = games[games["season"] >= a.first]
    if out and not a.skip_stakes:
        st = compute_league(games, NBA, seasons=set(range(a.first, end_year)), now_utc=now, sims=a.sims)
        counts["nba-stakes"] = write_dir("nba-stakes", games, st, out)
    if out:
        counts["nba-rest"] = write_dir("nba-rest", games, by_season(rest_travel_facts(fact_games), games), out)
    if a.tv_csv or out:
        tv = national_tv(fact_games)
        if a.tv_csv:
            tv20 = tv[tv["season"] >= a.tv_first]
            tv20.to_csv(a.tv_csv, index=False)
            cov = tv20.groupby("season").agg(games=("game_id", "size"), known=("broadcast_known", "sum"),
                                             national=("national", "sum"))
            print(f"national TV csv: {len(tv20)} rows -> {a.tv_csv}\n{cov.to_string()}")
            print(tv20[tv20.national].groupby("season")["network"].value_counts().unstack(fill_value=0).to_string())
        if out:
            counts["nba-tv"] = write_dir("nba-tv", games, by_season(tv_facts(tv, games), games), out)
    if out and a.l2m:
        lf = l2m_facts(fact_games, load_l2m(a.l2m))
        counts["nba-l2m"] = write_dir("nba-l2m", games, by_season(lf, games), out)
    sched = {"nba": schedule_rows(games[games["season"] >= a.first])}
    if a.nfl_games:
        from redzone.context.schedules import load_nfl
        nfl = load_nfl(a.nfl_games)
        sched["nfl"] = schedule_rows(nfl, "nfl")
    for lg, df in sched.items():
        if a.schedules_out:
            write_schedule(df, Path(a.schedules_out) / f"{lg}.csv")
        if a.commit_schedules:
            keep = ["game_id", "league", "home", "away", "start_utc", "season"] + (["nba_game_id"] if lg == "nba" else [])
            write_schedule(df[keep], SCHEDULES_DIR / f"{lg}.csv.gz")
        print(f"schedule {lg}: {len(df)} games")
    print(json.dumps({k: {kk: v[kk] for kk in ("facts", "new", "derived", "games")} for k, v in counts.items()}))
    print(f"runtime {time.time() - t0:.0f}s")
    if a.manifest and counts:
        mp = Path(a.manifest)
        m = json.loads(mp.read_text()) if mp.exists() else {}
        m.setdefault("ledger_counts", {}).update(counts)
        m["nba_crosswalk"] = {"games": int(len(games)), "with_nba_game_id": int((games["nba_game_id"] != "").sum())}
        mp.write_text(json.dumps(m, indent=1, default=str))
    return counts


if __name__ == "__main__":
    main()
