"""Grow the play-by-play corpus: polite, resumable history fetchers with a per-league report (task B-01, Track B).

    python scripts/fetch_history.py --report [--by-season] [--json]     # per-league counts from the local ledgers
    python scripts/fetch_history.py --licences                          # the source registry: terms, status, findings
    python scripts/fetch_history.py --plan [--leagues nba,cbb]          # estimated requests, hours and events (offline)
    python scripts/fetch_history.py --probe --leagues nba --seasons 2025,2015,2005 --samples 2   # how far back ESPN goes
    python scripts/fetch_history.py --accept-restrictive --leagues nba,cbb --seasons 2025-2023 --max-games 200   # ESPN fetch
    python scripts/fetch_history.py --accept-restrictive --source hoopr_nba --seasons 2025-2020                   # hoopR files
    python scripts/fetch_history.py --verify                            # ledger vs files, duplicates, torn state
    python scripts/fetch_history.py --pack                              # deterministic shards + MANIFEST.json (sha256, licences)

Rules built in: at most 1 request per second overall (the floor cannot be lowered), an honest User-Agent, backoff on
429/5xx, a stop on any 403, one writer per data folder, atomic writes, and every game fetched once (kill it any time and
rerun). Output goes to data/history/ (git-ignored; the tool refuses a folder inside a repo that would track it). Evaluation
nights are never fetched. A source whose terms forbid it, or whose licence is unconfirmed, refuses with the finding.
Exit codes: 0 finished or hit a limit; 1 finished with errors (rerun retries them); 3 blocked (403); 4 circuit breaker.
Runbook: docs/memory/runbooks/history-fetch.md.
Why: B-01 (WORKPLAN, tracks/B-foundation-model.md B0): the corpus is about 6% of the plan and the fetches run unattended for hours.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.history import datasets, espn, pack, probe  # noqa: E402
from scripts.history.leagues import DEFAULT_ORDER, LEAGUES, parse_seasons  # noqa: E402
from scripts.history.net import PoliteClient  # noqa: E402
from scripts.history.store import DEFAULT_ROOT, Store, ensure_untracked, format_report, summarize, verify  # noqa: E402

DEFAULT_SEASONS = "2026-2017"
# ESTIMATE (tracks/B-foundation-model.md arithmetic): games a season, plays a game, scoreboard units a season.
PLAN_TABLE = {"nba": (1300, 506, 200), "cbb": (5900, 513, 150), "wcbb": (5500, 500, 150), "nfl": (285, 181, 23),
              "cfb": (800, 170, 17), "nhl": (1400, 315, 200), "mlb": (2500, 560, 200)}


# Leagues whose play rows also come as one file per season (hoopR / wehoop, ESPN rows already flattened): about one request a season.
FILE_ROUTE = {"nba": "hoopr_nba", "cbb": "hoopr_mbb", "wcbb": "wehoop_wbb"}
MEASURED_RATE = 0.87    # requests/second sustained in the 2026-11-13 smoke run (the 1/s cap is never exceeded)


def plan(leagues: list[str], seasons: list[int], rate: float = 1.0) -> str:
    lines = [f"{'league':<6} {'seasons':>7} {'games':>9} {'events':>12} {'requests':>9} {'hours':>7}  {'or by files':<22}  (ESTIMATE, {rate:g} request/second)"]
    tg = te = tr = tr_files = 0
    for code in leagues:
        g, e, u = PLAN_TABLE[code]
        n = len(seasons)
        games, events, req = g * n, g * n * e, g * n + u * n + n
        tg, te, tr = tg + games, te + events, tr + req
        tr_files += n + 1 if code in FILE_ROUTE else req
        route = f"{FILE_ROUTE[code]}: {n + 1} requests" if code in FILE_ROUTE else "-"
        lines.append(f"{code:<6} {n:>7} {games:>9,} {events:>12,} {req:>9,} {req / rate / 3600:>7.1f}  {route:<22}")
    lines.append(f"{'total':<6} {'':>7} {tg:>9,} {te:>12,} {tr:>9,} {tr / rate / 3600:>7.1f}")
    lines.append(f"with the file route for nba, cbb, wcbb: {tr_files:,} requests, {tr_files / rate / 3600:.1f} hours at {rate:g}/s "
                 f"({tr_files / MEASURED_RATE / 3600:.1f} hours at the {MEASURED_RATE} measured)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    for flag, help_ in (("--report", "per-league counts from the ledgers"), ("--licences", "print the source registry and write MANIFEST.json"),
                        ("--plan", "estimate requests, hours and events (offline)"), ("--probe", "how far back each ESPN league goes (numbers only)"),
                        ("--verify", "check ledger against files; exit 1 on a problem"), ("--pack", "write shards and MANIFEST.json")):
        mode.add_argument(flag, action="store_true", help=help_)
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="data folder (default data/history/, git-ignored)")
    ap.add_argument("--source", default="espn", choices=["espn", *datasets.DATASETS], help="espn (default) or a whole-file dataset")
    ap.add_argument("--leagues", default=",".join(DEFAULT_ORDER), help="comma list: " + ",".join(DEFAULT_ORDER))
    ap.add_argument("--seasons", default=None, help=f"e.g. 2025-2020 or 2024,2019 (default {DEFAULT_SEASONS}; fenced nights are skipped)")
    ap.add_argument("--max-games", type=int, default=None, help="stop after this many games (smoke runs: 200)")
    ap.add_argument("--max-requests", type=int, default=None)
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--max-files", type=int, default=None, help="datasets: stop after this many files")
    ap.add_argument("--include-preseason", action="store_true", help="also fetch ESPN season type 1")
    ap.add_argument("--accept-restrictive", action="store_true",
                    help="confirm restrictive terms are accepted for personal use (decisions.md 2026-11-05); the tag stays on every shard")
    ap.add_argument("--samples", type=int, default=2, help="probe: units sampled per season (0 = calendar only)")
    ap.add_argument("--games-per-slate", type=int, default=1, help="probe: summaries read per sampled unit")
    ap.add_argument("--by-season", action="store_true")
    ap.add_argument("--json", action="store_true")
    return ap


def pick_leagues(arg: str) -> list[str]:
    out = [x.strip() for x in arg.split(",") if x.strip()]
    bad = [x for x in out if x not in LEAGUES]
    if bad:
        raise SystemExit(f"unknown league(s) {bad}; choose from {', '.join(LEAGUES)}")
    return out


def main(argv: list[str] | None = None, *, transport=None, sleep=time.sleep, clock=time.monotonic) -> int:
    a = build_parser().parse_args(argv)
    store = Store(a.root)
    seasons = parse_seasons(a.seasons or DEFAULT_SEASONS)
    if a.report:
        s = summarize(store, by_season=a.by_season)
        print(json.dumps(s, indent=1) if a.json else format_report(s))
        return 0
    if a.licences:
        ensure_untracked(store.root)
        m = pack.write_manifest(store, (store.read_json("shards/index.json") or {}).get("shards", []), a.accept_restrictive)
        for k, v in m["licences"].items():
            print(f"{k:<9} {v['terms_class']:<12} {v['status']:<12} {v['licence'][:90]}")
        print(f"wrote {store.root / 'MANIFEST.json'}")
        return 0
    if a.plan:
        print(plan(pick_leagues(a.leagues), seasons))
        return 0
    if a.verify:
        problems = verify(store)
        print("\n".join(problems) if problems else "ok: ledger and files agree")
        return 1 if problems else 0
    if a.pack:
        ensure_untracked(store.root)
        with store.lock():
            m = pack.pack(store, pick_leagues(a.leagues) if a.leagues != ",".join(DEFAULT_ORDER) else None, a.accept_restrictive)
        print(f"{len(m['shards'])} shard rows in {store.root / 'MANIFEST.json'}")
        return 0

    ensure_untracked(store.root)
    stop = {"flag": False}

    def on_signal(signum, frame):   # finish the request in flight, then stop cleanly; a second signal exits at once
        if stop["flag"]:
            raise KeyboardInterrupt
        stop["flag"] = True
        print(f"signal {signum}: stopping after the current request", file=sys.stderr, flush=True)
    old = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        return _fetch_or_probe(a, store, seasons, stop, transport, sleep, clock)
    finally:
        for sig, h in old.items():
            signal.signal(sig, h)


def _fetch_or_probe(a, store: Store, seasons: list[int], stop: dict, transport, sleep, clock) -> int:
    from scripts.history.licences import gate
    started = dt.datetime.now(dt.timezone.utc)
    with store.lock(), PoliteClient(transport=transport, sleep=sleep, clock=clock) as client:
        print(f"CA bundle: {client.tls}; User-Agent: {client.user_agent}; at most 1 request/second", flush=True)
        if a.probe:
            gate("espn", a.accept_restrictive)
            store.write_json("robots_espn.json", espn.start_checks(client))
            ev = probe.probe(client, pick_leagues(a.leagues), seasons, a.samples, per_slate=a.games_per_slate)
            ev.update(generated=started.isoformat(timespec="seconds"), requests=client.stats.requests, ca_bundle=client.tls)
            store.write_json(f"probe/espn_{started.strftime('%Y%m%dT%H%M%SZ')}.json", ev)
            print(probe.table(ev))
            print(f"{client.stats.requests} requests; evidence saved under {store.root / 'probe'}")
            return 0
        deadline = clock() + a.max_minutes * 60 if a.max_minutes else None
        if a.source == "espn":
            gate("espn", a.accept_restrictive)
            rob = espn.start_checks(client)
            store.write_json("robots_espn.json", rob)
            print(f"robots.txt {rob['host']}: HTTP {rob['status']} -> {rob['verdict']}" + (f" ({rob['note']})" if rob.get("note") else ""), flush=True)
            run = espn.Run(client, store, espn.Limits(a.max_games, a.max_requests, deadline), clock=clock, stop_flag=lambda: stop["flag"],
                           types=(1, 2, 3) if a.include_preseason else (2, 3))
            summary = run.run(pick_leagues(a.leagues), seasons)
        else:
            summary = datasets.fetch_dataset(client, store, a.source, seasons, accepted=a.accept_restrictive, max_files=a.max_files)
            st = client.stats
            summary.update(requests=st.requests, retries=st.retries, bytes=st.bytes, by_status=dict(st.by_status), ca_bundle=client.tls)
        summary.update(source=a.source, started=started.isoformat(timespec="seconds"), finished=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        store.write_json(f"runs/{started.strftime('%Y%m%dT%H%M%SZ')}.json", summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, indent=1))
    for e in summary.get("errors", [])[:10]:
        print("ERROR", e, file=sys.stderr)
    reason = summary.get("stop_reason")
    if reason == "blocked":
        return 3
    if reason == "breaker":
        return 4
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
