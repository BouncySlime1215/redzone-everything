#!/usr/bin/env python3
"""BUZZ runner (Data Crew B, worker B2): pull pageviews + edits + GDELT, then write context fact shards.

    python scripts/context/run_buzz.py live      [--leagues ...] [--schedule s.csv ...]
    python scripts/context/run_buzz.py backfill  --from 2023-09-01 --to 2023-10-01 --max-files 800 [--stride 4]

live (hourly, .github/workflows/context-buzz.yml): today's games (nflverse games.csv for NFL, ESPN scoreboards
  for the rest, plus any --schedule CSVs); pulls the newest 4 GDELT files, the newest published pageview hours,
  daily REST views for teams playing within 36 h, and enwiki edit counts since the last covered bin (29 days on
  the first run). Writes pregame facts (asof kickoff - 1 s) for games that started in the last 12 h or start
  within 15 min, and live facts for bins that ended in the last 3 h.
backfill: for games with kickoff in [from, to): daily REST views [from-35d, to), revisions [from-29d, to),
  up to --hour-files pageview dump hours around games, then GDELT files [from-28d, to) every --stride slot up
  to --max-files / the time budget. Re-runs resume (covered bins are skipped).

Facts -> <out>/facts/<league>_<season>.parquet (redzone/context/tokens.py columns), merged + deduped.
Signals (team counts, numbers only) and coverage -> <out>/{signals,coverage}/; no raw file is ever stored.
Schedule CSV columns: game_id, league, home, away, start_utc (ISO, UTC) [, season].
Why: Runs the buzz pullers and writes the context fact shards (docstring, Data B commit).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gdelt_buzz  # noqa: E402
import wiki_edits  # noqa: E402
import wiki_pageviews  # noqa: E402

from redzone.context.buzz import (  # noqa: E402
    BUZZ_DIR, DAY, GAME_S, HOUR, BUZZ_LEAGUES, Q15, SRC_PV, Http, Signals, Store, TeamIndex, ckey, floor_to, iso,
    live_facts, load_teams, normalize_schedule, parse_utc, pregame_facts, write_shards,
)
from redzone.context.tokens import to_frame  # noqa: E402

log = logging.getLogger("run_buzz")
NFLVERSE_GAMES = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
ESPN_PATH = {"nfl": ("football/nfl", {}), "cfb": ("football/college-football", {"groups": "80", "limit": "400"}),
             "nba": ("basketball/nba", {}), "cbb": ("basketball/mens-college-basketball",
                                                    {"groups": "50", "limit": "400"}),
             "nhl": ("hockey/nhl", {}), "mlb": ("baseball/mlb", {})}


# ---------------------------------------------------------------- schedules
def nflverse_rows(text: str) -> list[dict]:
    """nflverse games.csv -> schedule rows; gameday + gametime are US Eastern."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et, out = ZoneInfo("America/New_York"), []
    for r in csv.DictReader(io.StringIO(text)):
        if not r.get("gameday"):
            continue
        hm = (r.get("gametime") or "13:00")[:5]
        dt = datetime.strptime(f"{r['gameday']} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=et)
        out.append({"game_id": r["game_id"], "league": "nfl", "home": r["home_team"], "away": r["away_team"],
                    "start_utc": int(dt.timestamp()), "season": r.get("season")})
    return out


def espn_rows(league: str, data: dict) -> list[dict]:
    out = []
    for ev in (data or {}).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        side = {c.get("homeAway"): c.get("team", {}) for c in comp.get("competitors", [])}
        if "home" not in side or "away" not in side:
            continue
        name = lambda t: t.get("displayName") or t.get("abbreviation")  # noqa: E731
        out.append({"game_id": f"espn_{league}_{ev['id']}", "league": league, "home": name(side["home"]),
                    "away": name(side["away"]), "start_utc": parse_utc(ev["date"])})
        # season left to season_of(): ESPN names NBA/NHL/CBB seasons by their END year, our shards by the start
    return out


def load_schedules(http: Http, idx: TeamIndex, leagues, csvs=(), nflverse=True, espn_dates=()) -> list[dict]:
    rows: list[dict] = []
    for p in csvs:   # plain or .csv.gz (the committed redzone/context/schedules/*.csv.gz)
        with (gzip.open(p, "rt", newline="") if str(p).endswith(".gz") else open(p, newline="")) as f:
            rows += list(csv.DictReader(f))
    if nflverse and "nfl" in leagues:
        r = http.get(NFLVERSE_GAMES)
        rows += nflverse_rows(r.text) if r is not None else []
    for lg in leagues:
        if lg == "nfl" and nflverse:
            continue                       # nflverse game_ids match the play table
        path, extra = ESPN_PATH[lg]
        for d in espn_dates:
            rows += espn_rows(lg, http.get_json(ESPN.format(path=path), {"dates": d} | extra, cache=False))
    rows = [r for r in rows if str(r.get("league", "")).lower() in leagues]
    games = normalize_schedule(rows, idx)
    uniq = {g["game_id"]: g for g in games}
    return sorted(uniq.values(), key=lambda g: g["start_utc"])


# ---------------------------------------------------------------- modes
def pageview_days(http, idx, store, keys, t0, t1):
    """Daily REST views for `keys` over [t0, t1), skipping teams whose days are already covered."""
    store.flush()
    cov = store.coverage([SRC_PV], t0, t1)
    have = cov[cov["bin_s"] == DAY].groupby("team")["ts"].nunique().to_dict() if len(cov) else {}
    need = (min(floor_to(t1, DAY), floor_to(time.time(), DAY)) - floor_to(t0, DAY)) // DAY
    todo = [k for k in keys if have.get(ckey(*k), 0) < need]
    return wiki_pageviews.pull_history(http, idx, store, t0, t1, teams=todo)


def run_live(a, idx, store, http, http_slow) -> tuple[list, list, tuple]:
    now = int(time.time())
    leagues = a.leagues.split(",")
    dates = sorted({time.strftime("%Y%m%d", time.gmtime(now + d * DAY)) for d in (-1, 0, 1)})
    games = load_schedules(http, idx, leagues, a.schedule, not a.no_nflverse, dates)
    soon = [g for g in games if now - GAME_S[g["league"]] - HOUR <= g["start_utc"] <= now + 36 * HOUR]
    keys = sorted({(g["league"], t) for g in soon for t in (g["home"], g["away"])})
    log.info("live: %d games in window, %d teams", len(soon), len(keys))
    n_gd = gdelt_buzz.pull_files(http_slow, idx, store, [t - Q15 * i for t in [gdelt_buzz.latest(http_slow)]
                                                          if t for i in range(a.gdelt_files)], a.gdelt_files)
    n_pv = pageview_days(http, idx, store, keys, now - 35 * DAY, now)
    hrs = [floor_to(now, HOUR) - HOUR * i for i in range(1, 4)]
    n_h = wiki_pageviews.pull_hours(http, idx, store, hrs, max_files=2)
    n_ed = wiki_edits.pull_history(http, idx, store, now - 29 * DAY, now)
    log.info("live pulls: gdelt %d files, REST %d req, dump hours %d, edit titles %d", n_gd, n_pv, n_h, n_ed)
    pre = [g for g in soon if now - 12 * HOUR <= g["start_utc"] <= now + Q15]
    return soon, pre, (now - 3 * HOUR, now)


def run_backfill(a, idx, store, http, http_slow) -> tuple[list, list, tuple]:
    t_start = time.monotonic()
    t0, t1 = parse_utc(a.t0), parse_utc(a.t1)
    leagues = a.leagues.split(",")
    games = [g for g in load_schedules(http, idx, leagues, a.schedule, not a.no_nflverse)
             if t0 <= g["start_utc"] < t1]
    log.info("backfill %s..%s: %d games", a.t0, a.t1, len(games))
    if not games:
        return [], [], (None, None)
    keys = sorted({(g["league"], t) for g in games for t in (g["home"], g["away"])})
    n_pv = pageview_days(http, idx, store, keys, t0 - 35 * DAY, t1)
    n_ed = wiki_edits.pull_history(http, idx, store, t0 - 29 * DAY, t1)
    hours = sorted({h for g in games for h in range(floor_to(g["start_utc"] - 6 * HOUR, HOUR),
                                                    g["start_utc"] + GAME_S[g["league"]], HOUR)})
    budget = a.max_minutes * 60 - (time.monotonic() - t_start)
    n_h = wiki_pageviews.pull_hours(http, idx, store, hours, a.hour_files, budget * 0.3)
    budget = a.max_minutes * 60 - (time.monotonic() - t_start)
    n_gd = gdelt_buzz.pull_files(http_slow, idx, store, gdelt_buzz.file_times(t0 - 28 * DAY, t1, a.stride),
                                 a.max_files, budget)
    log.info("backfill pulls: REST %d req, edit titles %d, dump hours %d, gdelt files %d", n_pv, n_ed, n_h, n_gd)
    return games, games, (None, None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["live", "backfill"])
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--max-files", type=int, default=600, help="GDELT files per run (backfill)")
    ap.add_argument("--stride", type=int, default=4, help="GDELT: read every Nth 15-min file (backfill)")
    ap.add_argument("--hour-files", type=int, default=40, help="pageview dump hours per run (backfill)")
    ap.add_argument("--gdelt-files", type=int, default=4, help="newest GDELT files per live run")
    ap.add_argument("--max-minutes", type=float, default=95)
    ap.add_argument("--leagues", default=",".join(BUZZ_LEAGUES))
    ap.add_argument("--schedule", action="append", default=[], help="schedule CSV (repeatable)")
    ap.add_argument("--no-nflverse", action="store_true")
    ap.add_argument("--teams", default=None)
    ap.add_argument("--out", default=str(BUZZ_DIR))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if a.mode == "backfill" and not (a.t0 and a.t1):
        ap.error("backfill needs --from and --to")
    out = Path(a.out)
    idx = TeamIndex(load_teams(a.teams, a.leagues.split(",")))
    store = Store(out)
    http, http_slow = Http(rps=5.0, cache_dir=out / "cache"), Http(rps=1.0)
    runner = run_live if a.mode == "live" else run_backfill
    live_games, pre_games, (l0, l1) = runner(a, idx, store, http, http_slow)
    games = {g["game_id"]: g for g in live_games + pre_games}
    if not games:
        log.info("no games; nothing to write")
        return
    ks = [g["start_utc"] for g in games.values()]
    S = Signals.from_store(store, min(ks) - 40 * DAY, max(ks) + 6 * HOUR)
    facts = pregame_facts(pre_games, S) + live_facts(live_games, S, l0, l1)
    counts = write_shards(facts, list(games.values()), out / "facts")
    man = {"mode": a.mode, "ran_at": iso(time.time()), "from": a.t0, "to": a.t1, "games": len(games),
           "facts_this_run": len(facts), "by_field": to_frame(facts)["field"].value_counts().to_dict() if facts
           else {}, "shards": counts}
    (out / f"manifest_{a.mode}.json").write_text(json.dumps(man, indent=1, default=str))
    log.info("wrote %d facts into %d shards", len(facts), len(counts))


if __name__ == "__main__":
    main()
