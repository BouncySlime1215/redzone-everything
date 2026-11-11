#!/usr/bin/env python3
"""Wikipedia pageviews per team article -> buzz signals (numbers only). Runs on GitHub Actions / Kaggle.

    python scripts/context/wiki_pageviews.py history --from 2015-07-01 --to 2025-06-30
    python scripts/context/wiki_pageviews.py hours --from 2024-01-05T00 --to 2024-01-05T06 [--max-files 6]

history: Wikimedia REST per-article daily views (agent=user, all-access) for each team's enwiki title plus its
         former titles (moved articles keep old views under the old title), summed per team. Signal metric
         "views", bin 1 day; coverage per team per fetched day. Data starts 2015-07-01.
hours:   hourly dumps pageviews-YYYYMMDD-HH0000.gz streamed line by line and filtered to our titles on the
         en ("en") and English mobile ("en.m") domains; the file is never written to disk. Metric "views_h",
         bin 1 h, coverage "*" per processed hour. We treat the file's HH as the START of the hour it covers and
         make the count usable only at HH+1:00 (if HH were the end, this is one hour late, never early).

Facts are built by run_buzz.py (pageviews_z pregame, pageviews_live_z hourly); see docs/datasources/context-buzz.md.
Why: Wikipedia pageviews per team as buzz signals (docstring, Data B buzz commit).
"""
from __future__ import annotations

import argparse
import gzip
import logging
import sys
import time
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redzone.context.buzz import (  # noqa: E402
    BUZZ_DIR, DAY, HOUR, SRC_PV, Http, Store, TeamIndex, ckey, floor_to, load_teams, parse_utc, title_key,
)

log = logging.getLogger("wiki_pageviews")
REST = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
        "{title}/daily/{start}/{end}")
DUMP = "https://dumps.wikimedia.org/other/pageviews/{t:%Y}/{t:%Y-%m}/pageviews-{t:%Y%m%d-%H}0000.gz"
REST_START = parse_utc("2015-07-01")
DOMAINS = (b"en", b"en.m")


def rest_url(title: str, d0: int, d1: int) -> str:
    f = "%Y%m%d"
    return REST.format(title=quote(title_key(title), safe=""), start=time.strftime(f, time.gmtime(d0)),
                       end=time.strftime(f, time.gmtime(d1)))


def parse_rest(data: dict | None) -> dict[int, int]:
    """REST JSON -> {day epoch: views}. Days with no item had 0 views (the API omits them)."""
    out: dict[int, int] = {}
    for it in (data or {}).get("items", []):
        ts = it["timestamp"]                         # "2024010500"
        t = parse_utc(f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}")
        out[t] = out.get(t, 0) + int(it["views"])
    return out


def pull_history(http: Http, idx: TeamIndex, store: Store, t0: int, t1: int, teams=None) -> int:
    """Daily views for days [t0, t1) (clamped to 2015-07-01 .. yesterday). Returns requests made."""
    t0 = max(floor_to(t0, DAY), REST_START)
    t1 = min(floor_to(t1, DAY), floor_to(time.time(), DAY))
    if t1 <= t0:
        return 0
    n = 0
    for i, key in enumerate(teams or idx.titles_of):
        league, abbr = key
        per_day: dict[int, int] = {}
        ok = True
        for ti in idx.titles_of[key]:
            r = http.get(rest_url(ti, t0, t1 - DAY))
            n += 1
            if r is None:                            # 404 = no views for that title in the range
                continue
            try:
                for d, v in parse_rest(r.json()).items():
                    per_day[d] = per_day.get(d, 0) + v
            except ValueError:
                ok = False
        if not ok:
            log.warning("bad REST payload for %s/%s; leaving its days uncovered", league, abbr)
            continue
        for d, v in per_day.items():
            if t0 <= d < t1:
                store.add(SRC_PV, "views", league, abbr, d, DAY, v)
        # REST omits zero-view days AND days not published yet (lag of a few hours after midnight UTC), so a
        # day counts as covered (missing = 0) only up to the newest day the API returned for this team
        last = max(per_day, default=None)
        for d in range(t0, min(t1, last + DAY) if last is not None else t0, DAY):
            store.cover(SRC_PV, d, DAY, team=ckey(league, abbr))
        if i % 50 == 49:
            store.flush()
    store.flush()
    return n


def filter_dump(lines, titles: dict[bytes, list]) -> dict[tuple, int]:
    """Dump lines b"en Washington_Wizards 123 0" -> {(league, abbr): views} for our titles (en + en.m)."""
    out: dict[tuple, int] = {}
    for line in lines:
        if not (line.startswith(b"en ") or line.startswith(b"en.m ")):
            continue
        parts = line.split(b" ")
        if len(parts) < 3 or parts[0] not in DOMAINS:
            continue
        t = parts[1]
        keys = titles.get(t)
        if keys is None and b"%" in t:
            keys = titles.get(title_key(unquote(t.decode("utf-8", "replace"))).encode())
        if not keys:
            continue
        try:
            v = int(parts[2])
        except ValueError:
            continue
        for k in keys:
            out[k] = out.get(k, 0) + v
    return out


def title_bytes(idx: TeamIndex) -> dict[bytes, list]:
    return {t.encode(): keys for t, keys in idx.by_title.items()}


def pull_hour(http: Http, idx: TeamIndex, store: Store, hour: int, titles=None) -> bool:
    """Stream one hourly dump (hour = epoch of HH:00). False if not published yet."""
    url = DUMP.format(t=_dt(hour))
    r = http.get(url, stream=True)
    if r is None:
        return False
    titles = titles or title_bytes(idx)
    with r, gzip.GzipFile(fileobj=r.raw) as gz:
        counts = filter_dump(gz, titles)
    for (league, abbr), v in counts.items():
        store.add(SRC_PV, "views_h", league, abbr, hour, HOUR, v)
    store.cover(SRC_PV, hour, HOUR)
    log.info("pageviews %s: %d teams matched", url.rsplit("/", 1)[-1], len(counts))
    return True


def _dt(t: int):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc)


def pull_hours(http: Http, idx: TeamIndex, store: Store, hours, max_files: int, max_seconds: float = 1e9) -> int:
    """Hours not yet covered, oldest first, within the file and time budgets. Returns files processed."""
    hours = sorted(set(hours))
    if not hours:
        return 0
    done = store.covered_ts(SRC_PV, hours[0], hours[-1] + 1)
    titles, n, t_start = title_bytes(idx), 0, time.monotonic()
    for h in hours:
        if n >= max_files or time.monotonic() - t_start > max_seconds:
            break
        if h in done:
            continue
        if pull_hour(http, idx, store, h, titles):
            n += 1
            if n % 10 == 0:
                store.flush()
    store.flush()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["history", "hours"])
    ap.add_argument("--from", dest="t0", required=True)
    ap.add_argument("--to", dest="t1", required=True)
    ap.add_argument("--max-files", type=int, default=24)
    ap.add_argument("--leagues", default="nfl,cfb,nba,cbb,nhl,mlb")
    ap.add_argument("--teams", default=None, help="teams.csv (default data/context/teams.csv, else the seed)")
    ap.add_argument("--out", default=str(BUZZ_DIR))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    idx = TeamIndex(load_teams(a.teams, a.leagues.split(",")))
    store, http = Store(Path(a.out)), Http(rps=5.0)
    t0, t1 = parse_utc(a.t0), parse_utc(a.t1)
    if a.mode == "history":
        n = pull_history(http, idx, store, t0, t1)
        store.flush()
        log.info("history: %d REST requests", n)
    else:
        n = pull_hours(http, idx, store, range(floor_to(t0, HOUR), t1, HOUR), a.max_files)
        log.info("hours: %d dump files", n)


if __name__ == "__main__":
    main()
