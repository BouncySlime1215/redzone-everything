#!/usr/bin/env python3
"""Build GameValue shards: public seasons on Kaggle CPU, or dev archive nights as a small local check.

    python scripts/kaggle_value_shards.py --source public --leagues nfl --out /kaggle/working/value_shards
    nice -n 15 python scripts/kaggle_value_shards.py --source archive --split fit --archive data/archive --out <dir>

Why: labels for the brain need more games than an 8 GB Mac should touch, so this runs on free Kaggle CPU and survives
the 12 h session limit: one shard per league and season, finished shards are never rebuilt, and a rerun with the
previous output attached as an input carries them over (build plan WP-25). Nick launches it; nothing here trains.

Public: only licence-cleared sources (value_shards.public_status). NBA needs --personal-nba, off by default, and is never
downloaded here (github.com's robots rule stopped that route): it is built from the files agent/kaggle/fetch_sports_nba.py
wrote, which must be attached as a notebook input (--input, default /kaggle/input). Missing files stop the run. Nights on
or after the scorecard window are dropped before any feature is built. Archive: DevOnly gives the dev game ids, so a
locked or fresh game's summary is never opened. The leak check runs at the end and sets the exit code.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from redzone.noggin import factory as F  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402

SAFE_ARCHIVE_SPLITS = ("fit", "train", "valid")   # never test or all: locked nights are not readable from here
NBA_COLS = ["id", "sequence_number", "type_text", "text", "period_number", "clock_display_value", "wallclock", "scoring_play",
            "score_value", "home_score", "away_score", "game_id", "season", "season_type", "shooting_play",
            "home_team_abbrev", "away_team_abbrev"]


def _shard_from_games(league, shard, out, games, info):
    """games yields (game id, summary, night, season, playoff, game key). Builds, cross-checks and writes one shard."""
    parts, checked, failed, examples = [], 0, 0, []
    for gid, summary, night, season, playoff, key in games:
        rows = V.build_rows(league, gid, summary, night=night, season=season, source=info["source"],
                            license_line=info["license"], plan=info["plan"], playoff=playoff, game_key=key)
        if rows.empty:
            info["thin_games"] = info.get("thin_games", 0) + 1
            continue
        n, bad = V.prefix_check(league, gid, summary, rows, playoff=playoff)
        checked += n
        failed += len(bad)
        examples.extend(bad[:1])
        parts.append(rows)
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return V.write_shard(out, league, shard, df, {**info, "prefix_checked": checked, "prefix_failed": failed,
                                                  "prefix_examples": examples[:5]})


def _nfl_games(path, season, max_games, stats):
    from scripts.holdset_public import NFL_EXTRA, nfl_summary
    import pyarrow.parquet as pq
    have = set(pq.read_schema(path).names)
    if "time_of_day" not in have:
        return
    cols = list(dict.fromkeys(F.NFL_COLS + NFL_EXTRA + ["home_team", "away_team"]))
    df = pd.read_parquet(path, columns=[c for c in cols if c in have])
    df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
    yield from _public_games(df, "nfl", season, max_games, stats, nfl_summary,
                             lambda g: int(g.season_type.iloc[0] == "POST"),
                             lambda g: (str(g.away_team.iloc[0]), str(g.home_team.iloc[0])) if "home_team" in g else ("", ""))


def _nba_games(path, season, max_games, stats):
    from scripts.holdset_public import hoops_summary
    import pyarrow.parquet as pq
    have = set(pq.read_schema(path).names)
    if "wallclock" not in have:
        return
    df = pd.read_parquet(path, columns=[c for c in NBA_COLS if c in have])
    df = df[df.type_text.fillna("").ne("Substitution") & df.period_number.notna() & df.wallclock.notna()].copy()
    df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
    df = df.sort_values(["game_id", "sq"], kind="stable")
    yield from _public_games(df, "nba", season, max_games, stats, hoops_summary,
                             lambda g: int(g.season_type.iloc[0] == 3) if "season_type" in g else 0,
                             lambda g: (str(g.away_team_abbrev.iloc[0]), str(g.home_team_abbrev.iloc[0]))
                             if "home_team_abbrev" in g else ("", ""))


def _public_games(df, league, season, max_games, stats, make, playoff, teams):
    """Games oldest id first. The eval cut is applied from the first timed play, before any feature exists."""
    cut = F.EVAL_FIRST_NIGHT[league]
    n = 0
    for gid, g in df.groupby("game_id", sort=True):
        summary = make(g)
        plays = V.flat_plays(league, summary)
        if not plays:
            stats["no_wall_games"] = stats.get("no_wall_games", 0) + 1
            continue
        night = F.night_of(plays[0][0])
        if night >= cut:
            stats["dropped_eval_games"] = stats.get("dropped_eval_games", 0) + 1
            continue
        away, home = teams(g)
        yield str(gid), summary, night, season, playoff(g), f"{league}:{night}:{away}@{home}" if home else f"{league}:{night}:{gid}"
        n += 1
        if max_games and n >= max_games:
            return


def nba_input_files(inputs) -> list[Path]:
    """The shard parquet files agent/kaggle/fetch_sports_nba.py wrote, wherever the attached notebook output put them."""
    name = f"sports_{V.public_source('nba', True)[0]}"
    return sorted(Path(p) for p in glob.glob(f"{inputs}/**/{name}/shards/*.parquet", recursive=True))


def stage_nba_raw(files, raw: Path, seasons) -> dict:
    """Write <raw>/nba/pbp_<season>.parquet from the fetched files (one season per file, all of its rows) and return
    {season: rows}. Seasons the files do not hold are left out. Refuses a file whose rows lack the personal-use tag."""
    import pyarrow.parquet as pq
    from scripts import value_leak_check as LC
    where: dict[int, list[Path]] = {}
    for f in files:
        have = set(pq.read_schema(f).names)
        if "season" not in have or "tag" not in have:
            raise ValueError(f"{f} has no season/tag column: not a fetch_sports_nba.py output")
        t = pq.read_table(f, columns=["season", "tag"])
        if set(t.column("tag").unique().to_pylist()) != {LC.PERSONAL_TAG}:
            raise ValueError(f"{f} rows are not all tagged {LC.PERSONAL_TAG!r}")
        for y in t.column("season").unique().to_pylist():
            where.setdefault(int(y), []).append(f)
    (raw / "nba").mkdir(parents=True, exist_ok=True)
    got = {}
    for y in sorted(set(seasons) & set(where)):
        t = pd.concat([pd.read_parquet(f, filters=[("season", "==", y)]) for f in where[y]], ignore_index=True)
        t.to_parquet(raw / "nba" / f"pbp_{y}.parquet", index=False)
        got[y] = len(t)
    return got


def public_season(args):
    """One league-season, start to finish. Returns a report dict; a network failure is left unfinished for the next run.
    NBA is never fetched: its raw season file was staged from the attached fetch output before the jobs started."""
    league, season, out, raw, personal, max_games, deadline = args
    out, raw = Path(out), Path(raw)
    shard = str(season)
    if V.shard_done(out, league, shard):
        return {"league": league, "season": season, "status": "already built"}
    if time.time() > deadline:
        return {"league": league, "season": season, "status": "session deadline"}
    source, licence, plan, url, _ = V.public_source(league, personal)
    dest = raw / league / f"pbp_{season}.parquet"
    if league == "nba":
        have = dest.exists()
    else:
        from scripts.noggin_factory import _fetch
        have = _fetch(url.format(y=season), dest)
    if not have:
        meta = V.write_shard(out, league, shard, pd.DataFrame(), {"source": source, "license": licence, "plan": plan,
                                                                  "note": "no file for this season", "prefix_checked": 0})
        return {"league": league, "season": season, "rows": meta["rows"], "status": "no file"}
    info = {"source": source, "license": licence, "plan": plan, "season": season}
    games = (_nfl_games if league == "nfl" else _nba_games)(dest, season, max_games, info)
    meta = _shard_from_games(league, shard, out, games, info)
    if league != "nba":
        dest.unlink(missing_ok=True)   # NBA keeps its staged file: the raw audit reads it after the run
    return {"league": league, "season": season, "rows": meta["rows"], "games": meta["games"], "status": "built"}


def archive_league(league, split, archive, out, max_games):
    """Dev nights of one league. DevOnly lists the ids; only those files are opened."""
    try:
        dev = DevOnly(archive, league)
    except LockedNightError as e:
        return {"league": league, "status": f"skipped: {e}"}
    wanted = set({"fit": dev.split.train + dev.split.valid, "train": dev.split.train, "valid": dev.split.valid}[split])
    ids = {g: n for g, n in dev.game_ids().items() if n in wanted}
    shard = f"archive_{split}"
    if V.shard_done(out, league, shard):
        return {"league": league, "status": "already built"}
    info = {"source": "archive", "license": f"{V.PERSONAL_USE}; RedZone eval archive, dev nights", "plan": "B", "split": split}

    def games():
        for n, (gid, night) in enumerate(sorted(ids.items(), key=lambda x: (x[1], x[0]))):
            if max_games and n >= max_games:
                return
            f = archive / league / f"summary_{gid}.json"
            if not f.is_file():
                continue
            d = json.loads(f.read_text())
            comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
            side = {c.get("homeAway"): (c.get("team") or {}).get("abbreviation") for c in comp.get("competitors") or []}
            key = f"{league}:{night}:{side.get('away')}@{side.get('home')}" if side.get("home") else f"{league}:{night}:{gid}"
            year = ((d.get("header") or {}).get("season") or {}).get("year") or int(night[:4])
            yield gid, d, night, int(year), int(((d.get("header") or {}).get("season") or {}).get("type") == 3), key
    meta = _shard_from_games(league, shard, out, games(), info)
    return {"league": league, "rows": meta["rows"], "games": meta["games"], "nights": len(meta["nights"]), "status": "built"}


def carry_over(inputs: str, out: Path) -> int:
    """Copy finished shards from a previous session's attached output (sidecar present and complete)."""
    n = 0
    for side in glob.glob(f"{inputs}/**/value_shards/*/*.json", recursive=True):
        src = Path(side)
        if not json.loads(src.read_text()).get("complete") or not src.with_suffix(".parquet").exists():
            continue
        to = out / src.parent.name
        if not (to / src.name).exists():
            to.mkdir(parents=True, exist_ok=True)
            shutil.copy(src.with_suffix(".parquet"), to / (src.stem + ".parquet"))
            shutil.copy(src, to / src.name)
            n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=("public", "archive"), required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--raw", type=Path, default=Path(os.environ.get("RZ_RAW", "/kaggle/working/raw")))
    ap.add_argument("--archive", type=Path, default=None)
    ap.add_argument("--split", choices=SAFE_ARCHIVE_SPLITS, default="fit")
    ap.add_argument("--personal-nba", action="store_true", help="include NBA for personal, non-commercial use (off by default)")
    ap.add_argument("--seasons", default=None, help="e.g. 2021-2024; default: every cleared season, newest first")
    ap.add_argument("--max-games", type=int, default=None, help="smoke tests only")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--session-h", type=float, default=11.5, help="stop starting seasons 15 min before this")
    ap.add_argument("--input", default=os.environ.get("RZ_KAGGLE_INPUT", "/kaggle/input"), help="previous output to resume from")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    report = []
    if a.source == "archive":
        if a.archive is None:
            ap.error("--source archive needs --archive")
        for lg in a.leagues.split(","):
            r = archive_league(lg, a.split, a.archive, a.out, a.max_games)
            report.append(r)
            print(json.dumps(r), flush=True)
    else:
        print("carried over", carry_over(a.input, a.out), "finished shards", flush=True)
        deadline = time.time() + a.session_h * 3600 - 900
        jobs = []
        for lg in a.leagues.split(","):
            status, why = V.public_status(lg, a.personal_nba)
            if status != "ok":
                r = {"league": lg, "status": f"{status}: {why}"}
                report.append(r)
                print(json.dumps(r), flush=True)
                continue
            seasons = F.seasons(a.seasons) if a.seasons else V.public_source(lg, a.personal_nba)[4]
            if lg == "nba":
                files = nba_input_files(a.input)
                try:
                    staged = stage_nba_raw(files, a.raw, seasons) if files else {}
                except ValueError as e:
                    print(f"STOPPED: NBA input refused: {e}", flush=True)
                    return 2
                if not staged:
                    print(f"STOPPED: NBA needs the output of agent/kaggle/fetch_sports_nba.py attached as a notebook input; found no "
                          f"{a.input}/**/sports_{V.public_source('nba', True)[0]}/shards/*.parquet for seasons "
                          f"{min(seasons)}-{max(seasons)}. Attach it (the fetch notebook, e.g. redzone-fetch-nba-ncaam) and rerun. "
                          f"Not downloading NBA from github.com.", flush=True)
                    return 2
            jobs += [(lg, y, str(a.out), str(a.raw), a.personal_nba, a.max_games, deadline) for y in sorted(seasons, reverse=True)]
        if a.jobs <= 1:
            report += [_log(_safe_season(j)) for j in jobs]
        else:
            with ProcessPoolExecutor(a.jobs) as ex:
                report += [_log(r) for r in ex.map(_safe_season, jobs)]
    man = V.finalize(a.out)
    from scripts import value_leak_check as LC
    argv2 = [str(a.out)] + (["--archive", str(a.archive)] if a.archive else []) + (["--allow-personal-nba"] if a.personal_nba else [])
    if a.source == "public" and {p.name for p in a.out.iterdir() if p.is_dir()} == {"nba"}:
        argv2 += ["--raw", str(a.raw)]   # only NBA keeps its raw files; NFL's are deleted after each season
    rc = LC.main(argv2)
    (a.out / "run_report.json").write_text(json.dumps({"report": report, "leak_check_exit": rc}, indent=1))
    print("ROWS PER LEAGUE:", json.dumps({k: {"rows": v["rows"], "games": v["games"], "nights": v["nights"]}
                                          for k, v in man["leagues"].items()}, indent=1))
    return rc


def _log(r: dict) -> dict:
    print(json.dumps(r), flush=True)
    return r


def _safe_season(job):
    try:
        return public_season(job)
    except Exception as e:  # noqa: BLE001 - one bad season never stops the run; no sidecar, so the next run retries it
        return {"league": job[0], "season": job[1], "status": f"error, will retry: {type(e).__name__}: {e}"[:300]}


if __name__ == "__main__":
    sys.exit(main())
