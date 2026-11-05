#!/usr/bin/env python3
"""Wikipedia edit rate per team article -> buzz signals (counts per 15 min only). Runs on GitHub Actions / Kaggle.

    python scripts/context/wiki_edits.py listen --seconds 3300          # live: EventStreams SSE
    python scripts/context/wiki_edits.py history --from 2024-01-01 --to 2024-02-01   # backfill: revisions API

listen:  https://stream.wikimedia.org/v2/stream/recentchange (Server-Sent Events). Keeps events with
         wiki == "enwiki", type edit/new, title in our set, and counts them per title per 15-min bin. Only the
         title and the timestamp are read: usernames, IPs and comments are never stored or logged. Bins fully
         inside the listening span are marked covered ("*"); reconnects resume with Last-Event-ID.
history: MediaWiki API prop=revisions, rvprop=timestamp only, paged (rvlimit=max), for all our titles.
         Coverage "*" for the 15-min bins of the span, written only if every title succeeded.
         Page moves carry their revision history, so the current title is enough.
Signal metric "edits", bin 900 s. Facts (edit_rate_z, edit_rate_live_z) are built by run_buzz.py.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redzone.context.buzz import (  # noqa: E402
    BUZZ_DIR, Q15, SRC_ED, Http, Store, TeamIndex, floor_to, iso, load_teams, parse_utc, title_key,
)

log = logging.getLogger("wiki_edits")
SSE_URL = "https://stream.wikimedia.org/v2/stream/recentchange"
API = "https://en.wikipedia.org/w/api.php"


# ---------------------------------------------------------------- SSE
def iter_sse(lines):
    """Yield (event id, data dict) from an SSE byte/str line iterator (multi-line data joined per spec)."""
    data, eid = [], None
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield eid, json.loads("\n".join(data))
                except ValueError:
                    pass
            data = []
            continue
        if line.startswith(":"):
            continue
        field, _, val = line.partition(":")
        val = val[1:] if val.startswith(" ") else val
        if field == "data":
            data.append(val)
        elif field == "id":
            eid = val


def edit_of(ev: dict, idx: TeamIndex) -> tuple[int, list] | None:
    """(timestamp, [(league, abbr)]) for an enwiki edit/new on one of our titles, else None. Reads only
    wiki/type/namespace/title/timestamp."""
    if ev.get("wiki") != "enwiki" or ev.get("type") not in ("edit", "new") or ev.get("namespace", 0) != 0:
        return None
    keys = idx.by_title.get(title_key(str(ev.get("title", ""))))
    if not keys:
        return None
    ts = ev.get("timestamp")
    if ts is None:
        ts = parse_utc(ev.get("meta", {}).get("dt"))
    return int(ts), keys


class BinCounter:
    def __init__(self):
        self.c: dict[tuple, int] = {}

    def add(self, ts: int, keys):
        b = floor_to(ts, Q15)
        for k in keys:
            self.c[(k, b)] = self.c.get((k, b), 0) + 1

    def dump(self, store: Store):
        for ((league, abbr), b), v in self.c.items():
            store.add(SRC_ED, "edits", league, abbr, b, Q15, v)
        self.c.clear()


def listen(http: Http, idx: TeamIndex, store: Store, seconds: float, lines_factory=None) -> int:
    """Count matching edits for `seconds`. lines_factory(last_event_id) -> line iterator (tests inject one)."""
    t_start = time.time()
    t_end = t_start + seconds
    counter, last_id, n = BinCounter(), None, 0

    def http_lines(last):
        h = {"Accept": "text/event-stream"} | ({"Last-Event-ID": last} if last else {})
        r = http.get(SSE_URL, stream=True, headers=h)
        return r.iter_lines() if r is not None else iter(())

    lines_factory = lines_factory or http_lines
    while time.time() < t_end:
        try:
            for eid, ev in iter_sse(lines_factory(last_id)):
                last_id = eid or last_id
                hit = edit_of(ev, idx)
                if hit:
                    counter.add(*hit)
                    n += 1
                if time.time() >= t_end:
                    break
            else:
                if lines_factory is not http_lines:
                    break                                   # injected finite stream
        except Exception as e:                              # network drop: resume from last_id
            log.warning("SSE dropped (%s); reconnecting", e)
            time.sleep(5)
    t_stop = min(time.time(), t_end)
    counter.dump(store)
    for b in range(floor_to(t_start, Q15) + (Q15 if t_start % Q15 else 0), floor_to(t_stop, Q15), Q15):
        store.cover(SRC_ED, b, Q15)                         # only bins fully inside [t_start, t_stop)
    store.flush()
    return n


# ---------------------------------------------------------------- history (revisions API)
def revision_times(http: Http, title: str, t0: int, t1: int) -> list[int]:
    """Revision timestamps of `title` in [t0, t1), newest first paging."""
    out, cont = [], {}
    while True:
        p = {"action": "query", "prop": "revisions", "titles": title.replace("_", " "), "rvprop": "timestamp",
             "rvlimit": "max", "rvdir": "older", "rvstart": iso(t1 - 1), "rvend": iso(t0), "redirects": 1,
             "format": "json", "formatversion": 2} | cont
        d = http.get_json(API, p, cache=False) or {}
        for page in d.get("query", {}).get("pages", []):
            out += [parse_utc(r["timestamp"]) for r in page.get("revisions", [])]
        if "continue" not in d:
            return [t for t in out if t0 <= t < t1]
        cont = dict(d["continue"])


def pull_history(http: Http, idx: TeamIndex, store: Store, t0: int, t1: int) -> int:
    """Edit counts for [t0, t1) for ALL our titles, resuming after the already-covered prefix. Coverage is
    source-wide ("*"), so it is written only if every title was fetched (a failed title leaves the span
    uncovered rather than silently zero)."""
    t0, t1 = floor_to(t0, Q15), min(floor_to(t1, Q15), floor_to(time.time(), Q15))
    done = store.covered_ts(SRC_ED, t0, t1)
    a = t0
    while a in done and a < t1:
        a += Q15
    if a >= t1:
        return 0
    counter, n, failed = BinCounter(), 0, 0
    for key, title in idx.main_title.items():
        try:
            for ts in revision_times(http, title, a, t1):
                counter.add(ts, [key])
            n += 1
        except Exception as e:
            failed += 1
            log.warning("revisions failed for %s: %s", title, e)
    counter.dump(store)
    if failed:
        log.warning("%d titles failed; span %s..%s left uncovered", failed, iso(a), iso(t1))
    else:
        for b in range(a, t1, Q15):
            store.cover(SRC_ED, b, Q15)
    store.flush()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["listen", "history"])
    ap.add_argument("--seconds", type=float, default=3300)
    ap.add_argument("--from", dest="t0")
    ap.add_argument("--to", dest="t1")
    ap.add_argument("--leagues", default="nfl,cfb,nba,cbb,nhl,mlb")
    ap.add_argument("--teams", default=None)
    ap.add_argument("--out", default=str(BUZZ_DIR))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    idx = TeamIndex(load_teams(a.teams, a.leagues.split(",")))
    store, http = Store(Path(a.out)), Http(rps=5.0)
    if a.mode == "listen":
        log.info("listen: %d matching edits", listen(http, idx, store, a.seconds))
    else:
        log.info("history: %d titles", pull_history(http, idx, store, parse_utc(a.t0), parse_utc(a.t1)))


if __name__ == "__main__":
    main()
