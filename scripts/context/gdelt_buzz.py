#!/usr/bin/env python3
"""GDELT 2.0 GKG news volume and tone per team -> buzz signals (counts only). Runs on GitHub Actions / Kaggle.

    python scripts/context/gdelt_buzz.py live [--files 4]
    python scripts/context/gdelt_buzz.py backfill --from 2023-09-01 --to 2023-10-01 --max-files 800 [--stride 4]

GDELT publishes a GKG file every 15 minutes: http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip
(lastupdate.txt names the newest). Each zip (~10-30 MB) is read in memory, parsed line by line and dropped;
nothing is written to disk. A file stamped T covers articles seen in [T-15 min, T) and is usable from T.

Per record (one article) we read only: V1ORGANIZATIONS (col 13), V2.1ALLNAMES (col 23, names only), V1.5TONE
(col 15, first value = average tone) and the <PAGE_TITLE> inside V2EXTRASXML (col 26). An article counts for a
team if one of the team's multi-word names/aliases equals an organization or a named entity, or appears as a
whole-word n-gram of the page title. Per team per file: "articles" (count) and "tone_sum" (sum of tone).
URLs, titles and names are never stored. Coverage "*" per processed file (bin start = T - 15 min).

Backfill reads every `stride`-th file (stride 4 = one per hour) up to --max-files / --max-minutes; rates are per
covered bin, so sampled coverage still gives unbiased z-scores. Re-runs skip covered files.
Why: GDELT news volume and tone per team as buzz signals, counts only (docstring, Data B buzz commit).
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redzone.context.buzz import (  # noqa: E402
    BUZZ_DIR, Q15, SRC_GD, Http, Store, TeamIndex, floor_to, load_teams, parse_utc,
)

log = logging.getLogger("gdelt_buzz")
BASE = "http://data.gdeltproject.org/gdeltv2/"
LASTUPDATE = BASE + "lastupdate.txt"
GDELT2_START = parse_utc("2015-02-19")
C_ORGS, C_TONE, C_NAMES, C_XML = 13, 15, 23, 26
_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)


def file_url(t: int) -> str:
    return BASE + time.strftime("%Y%m%d%H%M%S", time.gmtime(t)) + ".gkg.csv.zip"


def ts_of_url(url: str) -> int:
    s = url.rsplit("/", 1)[-1][:14]
    return parse_utc(f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}")


def parse_lastupdate(text: str) -> int | None:
    """lastupdate.txt ("size md5 url" x3) -> timestamp of the newest GKG file."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[-1].endswith(".gkg.csv.zip"):
            return ts_of_url(parts[-1])
    return None


def record_teams(cols: list[str], idx: TeamIndex) -> set:
    names = [x for x in cols[C_ORGS].split(";") if x] if len(cols) > C_ORGS else []
    if len(cols) > C_NAMES and cols[C_NAMES]:
        names += [x.rsplit(",", 1)[0] for x in cols[C_NAMES].split(";") if x]
    hit = idx.match_names(names)
    if len(cols) > C_XML and "<PAGE_TITLE>" in cols[C_XML]:
        m = _TITLE.search(cols[C_XML])
        if m:
            hit |= idx.match_text(m.group(1))
    return hit


def parse_gkg(lines, idx: TeamIndex) -> dict[tuple, list]:
    """GKG 2.1 tab-separated lines -> {(league, abbr): [articles, tone_sum]}."""
    out: dict[tuple, list] = {}
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        cols = line.rstrip("\r\n").split("\t")
        if len(cols) <= C_TONE:
            continue
        teams = record_teams(cols, idx)
        if not teams:
            continue
        try:
            tone = float(cols[C_TONE].split(",")[0])
        except ValueError:
            tone = 0.0
        for k in teams:
            acc = out.setdefault(k, [0, 0.0])
            acc[0] += 1
            acc[1] += tone
    return out


def pull_file(http: Http, idx: TeamIndex, store: Store, t: int) -> bool:
    r = http.get(file_url(t))
    if r is None:
        return False                     # GDELT skips some 15-min slots
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            with z.open(z.namelist()[0]) as f:
                counts = parse_gkg(f, idx)
    except zipfile.BadZipFile:
        log.warning("bad zip %s", file_url(t))
        return False
    b = t - Q15
    for (league, abbr), (n, tone) in counts.items():
        store.add(SRC_GD, "articles", league, abbr, b, Q15, n)
        store.add(SRC_GD, "tone_sum", league, abbr, b, Q15, tone)
    store.cover(SRC_GD, b, Q15)
    return True


def file_times(t0: int, t1: int, stride: int = 1) -> list[int]:
    """File stamps T with T-15min in [t0, t1), every `stride`-th slot."""
    first = floor_to(max(t0, GDELT2_START), Q15) + Q15
    return list(range(first, floor_to(t1, Q15) + Q15, Q15 * max(1, stride)))


def pull_files(http: Http, idx: TeamIndex, store: Store, times, max_files: int, max_seconds: float = 1e9,
               first_from: int | None = None) -> int:
    """Pull GDELT files at `times`. With `first_from`, files at or after it (the games' own window) go first and the
    baseline is filled newest-first, so a capped run still yields facts (oldest-first used to spend the whole cap
    on the 28-day baseline and never reach the games)."""
    times = sorted(times)
    if not times:
        return 0
    done = store.covered_ts(SRC_GD, times[0] - Q15, times[-1])
    if first_from is not None:
        times = [t for t in times if t >= first_from] + [t for t in reversed(times) if t < first_from]
    n, t_start = 0, time.monotonic()
    for t in times:
        if n >= max_files or time.monotonic() - t_start > max_seconds:
            break
        if t - Q15 in done:
            continue
        if pull_file(http, idx, store, t):
            n += 1
            if n % 20 == 0:
                store.flush()
                log.info("gdelt: %d files, at %s", n, file_url(t).rsplit("/", 1)[-1])
    store.flush()
    return n


def latest(http: Http) -> int | None:
    r = http.get(LASTUPDATE)
    return parse_lastupdate(r.text) if r is not None else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["live", "backfill"])
    ap.add_argument("--files", type=int, default=4, help="live: newest N files")
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--max-files", type=int, default=400)
    ap.add_argument("--max-minutes", type=float, default=90)
    ap.add_argument("--leagues", default="nfl,cfb,nba,cbb,nhl,mlb")
    ap.add_argument("--teams", default=None)
    ap.add_argument("--out", default=str(BUZZ_DIR))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    idx = TeamIndex(load_teams(a.teams, a.leagues.split(",")))
    store, http = Store(Path(a.out)), Http(rps=1.0)      # GDELT: be gentle, one file per second at most
    if a.mode == "live":
        t = latest(http)
        times = [t - Q15 * i for i in range(a.files)] if t else []
        n = pull_files(http, idx, store, times, a.files)
    else:
        n = pull_files(http, idx, store, file_times(parse_utc(a.t0), parse_utc(a.t1), a.stride), a.max_files,
                       a.max_minutes * 60)
    log.info("gdelt %s: %d files", a.mode, n)


if __name__ == "__main__":
    main()
