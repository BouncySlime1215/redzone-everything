#!/usr/bin/env python3
"""Nightly feed-arrival logger: polls the night's games politely and logs when each feed row first shows up.

    nohup taskpolicy -b nice -n 19 python3 scripts/feedlag_logger.py --exit-when-done > data/feedlag/logger.out 2>&1 &
    python3 scripts/feedlag_logger.py --stop          # stop it (SIGTERM; it writes its stop row and exits)
    python3 scripts/feedlag_logger.py --dry-run       # print tonight's plan (two or three requests), poll nothing

A standalone sidecar: the show never imports it, it touches no TV and no stick, and it needs only the standard
library. Per game it asks ESPN's summary and (NBA only) the NBA liveData play-by-play on the S3 mirror the show
already uses, and appends metadata rows to data/feedlag/YYYY-MM-DD.jsonl (git-ignored; the row format is in
redzone/lab/feedlag.py, the numbers come from scripts/feedlag_report.py). Runbook: docs/memory/runbooks/feedlag.md.

Politeness, all enforced in code: at least 10 s between two requests for the same game and source (the --interval
floor); at most --max-rps requests per second across everything (hard ceiling 4); exponential backoff per host on
429/5xx/network errors (30 s doubling to 10 min, Retry-After honoured); a missing NBA play-by-play file (S3 answers
403/404 before the tip) backs off that one game only; conditional GETs (ETag / If-Modified-Since) so a quiet
10 s costs a 304; a request timeout; an honest User-Agent that names no person and no project.

Why: C-02 (v2 WORKPLAN, tracks/C-picture-audio-timing.md C2): the show assumes an 8 s feed but ESPN's summary arrives about 36 s late and the NBA feed about 11 s; replays need the real arrival lags, so this logs them every night.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab import feedlag  # noqa: E402
from redzone.models import League, Phase  # noqa: E402
from redzone.sources import espn, leagues  # noqa: E402

USER_AGENT = "feedlag-logger/1 (low-rate metadata sidecar; at most one request per 10 s per game)"
MAX_BYTES = 8_000_000                  # a response bigger than this is a broken feed, not a game
ESPN_HOST = "site.api.espn.com"
LEAGUES = {"nba": League.NBA, "cbb": League.CBB}
BACKOFF_FIRST = 30.0                   # host backoff: 30 s, doubling...
BACKOFF_CAP = 600.0                    # ...to 10 minutes
MISSING_FIRST = 15.0                   # one game's file not there yet (before the tip): 15 s, 30 s, 60 s, then every minute
MISSING_CAP = 60.0
STATUS_EVERY = 60.0
MIN_NAP = 0.01


# --- settings -------------------------------------------------------------------------------------------------

@dataclass
class Settings:
    out_dir: Path = ROOT / "data" / "feedlag"
    leagues: tuple = ("nba",)
    sources: tuple = feedlag.FEED_SOURCES
    interval: float = feedlag.FLOOR_INTERVAL      # seconds between requests for one game and source
    max_rps: float = 3.0                          # requests per second across everything (ceiling 4)
    max_games: int = 12                           # NBA games followed at once
    cbb_max_games: int = 4                        # college basketball games followed at once (ranked games first)
    discover_every: float = 300.0                 # re-read the scoreboards this often
    lead: float = 600.0                           # start polling a game this long before its scheduled tip
    post_grace: float = 600.0                     # keep polling a finished game this long (feeds back-fill late rows)
    max_hours: float = 14.0                       # stop by itself after this long
    exit_when_done: bool = False                  # stop once every game of the slate is over
    date: str | None = None                       # force the slate (YYYY-MM-DD); default is tonight's
    timeout: float = 8.0

    def __post_init__(self):
        self.out_dir = Path(self.out_dir)
        self.interval = max(float(self.interval), feedlag.FLOOR_INTERVAL)       # never lower than the floor
        self.max_rps = min(max(float(self.max_rps), 0.1), feedlag.HARD_RPS)     # never higher than the ceiling


# --- HTTP -----------------------------------------------------------------------------------------------------

@dataclass
class Resp:
    status: int                  # HTTP status; 0 = no answer (network error, timeout, unreadable body)
    data: object = None
    headers: dict = field(default_factory=dict)
    err: str = ""


class UrlFetcher:
    """GET JSON with conditional requests. One thread, no retries: the poller decides what a failure means."""

    def __init__(self, timeout: float = 8.0, user_agent: str = USER_AGENT):
        self.timeout, self.ua = timeout, user_agent
        self._tags: dict[str, tuple[str | None, str | None]] = {}

    def get(self, url: str, conditional: bool = True) -> Resp:
        h = {"User-Agent": self.ua, "Accept": "application/json", "Accept-Encoding": "gzip"}
        etag, modified = self._tags.get(url, (None, None)) if conditional else (None, None)
        if etag:
            h["If-None-Match"] = etag
        if modified:
            h["If-Modified-Since"] = modified
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=self.timeout) as r:
                status, hdrs, body = r.status, r.headers, r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:           # 304 and every 4xx/5xx arrive here
            return Resp(e.code, None, {k.lower(): v for k, v in e.headers.items()})
        except Exception as e:                         # URLError, timeout, reset, bad status line...
            return Resp(0, None, {}, type(e).__name__)
        hdrs = {k.lower(): v for k, v in hdrs.items()}
        if len(body) > MAX_BYTES:
            return Resp(0, None, hdrs, "too_big")
        try:
            if hdrs.get("content-encoding") == "gzip":
                body = gzip.decompress(body)
            data = json.loads(body)
        except (OSError, EOFError, ValueError):
            return Resp(0, None, hdrs, "bad_body")
        self._tags[url] = (hdrs.get("etag"), hdrs.get("last-modified"))
        return Resp(status, data, hdrs)


def classify(status: int, host: str) -> str:
    """ok / unchanged / missing (this game's file only) / host (back the whole host off)."""
    if status == 200:
        return "ok"
    if status == 304:
        return "unchanged"
    if status == 404 or (status == 403 and host.endswith(".s3.amazonaws.com")):
        return "missing"        # S3 says 403 or 404 for a key that is not there yet
    if status == 0 or status == 429 or status >= 500 or status == 403:
        return "host"
    return "missing"


def host_of(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


# --- the poller -----------------------------------------------------------------------------------------------

@dataclass
class Job:
    key: tuple                   # (source, espn game id)
    url: str
    host: str
    tracker: feedlag.ArrivalTracker
    parse: object                # payload -> [FeedRow]
    due: float = 0.0             # monotonic time of the next request
    missing: int = 0             # consecutive "file not there yet" answers


class Poller:
    """Schedules every request: per-job interval, global spacing, host backoff. Clocks and fetcher are injected."""

    def __init__(self, settings: Settings, fetcher=None, clock=time.time, mono=time.monotonic, sleep=time.sleep,
                 log: feedlag.DayLog | None = None, say=None, dry: bool = False):
        self.s = settings
        self.fetcher = fetcher or UrlFetcher(settings.timeout)
        self.clock, self.mono, self.sleep = clock, mono, sleep
        self.say = say or (lambda m: print(m, flush=True))
        self.dry = dry                                   # a dry run writes nothing
        self.log = log or feedlag.DayLog(settings.out_dir)
        self.spacing = 1.0 / settings.max_rps
        self.next_send = 0.0
        self.host_until: dict[str, float] = {}
        self.host_fails: dict[str, int] = {}
        self.jobs: dict[tuple, Job] = {}
        self.post_since: dict[str, float] = {}
        self.slate = settings.date or feedlag.slate_date(self.clock())
        self.known: dict[tuple[str, str], set[str]] = {} if dry else self.log.known(self.slate)
        self.disc_due = 0.0
        self.done = False
        self.requests = self.errors = self.rows = 0
        self.sent_log: list[tuple[float, str]] = []      # (monotonic send time, url): kept short, for the tests' eyes
        self.last_sent = 0.0
        self.started = self.mono()
        self.last_status = self.started

    # -- one request, paced -----------------------------------------------------------------------------------
    def _request(self, url: str, conditional: bool = True) -> tuple[Resp, float, float]:
        """One paced GET. Scoreboards are asked for whole (conditional=False): a 304 would carry no games."""
        wait = self.next_send - self.mono()
        if wait > 0:
            self.sleep(wait)
        sent_m, sent = self.mono(), self.clock()
        self.next_send = sent_m + self.spacing
        resp = self.fetcher.get(url) if conditional else self.fetcher.get(url, conditional=False)
        received = self.clock()
        self.requests += 1
        self.last_sent = sent_m
        self.sent_log.append((sent_m, url))
        del self.sent_log[:-2000]
        return resp, sent, received

    def _emit(self, events: list[dict]) -> None:
        self.rows += sum(1 for e in events if e.get("ev") == "row")
        if not self.dry:
            self.log.append(self.slate, events)

    def _host_failed(self, host: str, resp: Resp, now_m: float, game: str | None, src: str) -> None:
        n = self.host_fails[host] = self.host_fails.get(host, 0) + 1
        delay = min(BACKOFF_CAP, BACKOFF_FIRST * 2 ** (n - 1))
        retry = resp.headers.get("retry-after", "")
        if retry.isdigit():
            delay = max(delay, min(float(retry), BACKOFF_CAP))
        self.host_until[host] = now_m + delay
        self.errors += 1
        self._emit([{"ev": "err", "t": round(self.clock(), 3), "src": src, "game": game, "host": host,
                     "what": resp.err or resp.status, "backoff": delay}])

    # -- discovery --------------------------------------------------------------------------------------------
    def _new_slate(self, slate: str) -> None:
        self.slate = slate
        self.jobs.clear()
        self.post_since.clear()
        self.known = {} if self.dry else self.log.known(slate)
        self._emit([{"ev": "slate", "t": round(self.clock(), 3), "slate": slate}])

    def discover(self) -> None:
        now_w = self.clock()
        self.disc_due = self.mono() + self.s.discover_every
        if self.host_until.get(ESPN_HOST, 0.0) > self.mono():
            self.disc_due = self.host_until[ESPN_HOST]                         # ESPN is backing off: ask again after
            return
        slate = self.s.date or feedlag.slate_date(now_w)
        if slate != self.slate:
            self._new_slate(slate)
        ymd = slate.replace("-", "")
        wanted: list[tuple] = []       # (League, GameState)
        all_post, any_game = True, False
        for name in self.s.leagues:
            lg = LEAGUES[name]
            urls = espn.scoreboard_urls(lg, None, ymd)
            games = []
            for u in urls:
                resp, _, recv = self._request(u, conditional=False)
                if classify(resp.status, ESPN_HOST) != "ok":
                    if classify(resp.status, ESPN_HOST) == "host":
                        self._host_failed(ESPN_HOST, resp, self.mono(), None, "scoreboard")
                    self.disc_due = self.mono() + min(60.0, self.s.discover_every)
                    return
                self.host_fails[ESPN_HOST] = 0
                self._skew(resp, recv)
                games += espn.parse_scoreboard(lg, resp.data)
            if lg.college:
                games = [g for g in games if espn.keep_college(g, set())]
            cap = self.s.max_games if lg == League.NBA else self.s.cbb_max_games
            live = [g for g in games if g.phase == Phase.LIVE]
            soon = sorted((g for g in games if g.phase == Phase.PRE and g.start_time
                           and g.start_time - now_w <= self.s.lead), key=lambda g: g.start_time)
            recent = []
            for g in games:
                any_game = True
                if g.phase != Phase.POST:
                    all_post = False
                    continue
                since = self.post_since.setdefault(g.game_id, now_w)
                if any((src, g.game_id) in self.jobs for src in self.s.sources) and now_w - since <= self.s.post_grace:
                    recent.append(g)
                    all_post = False
            wanted += [(lg, g) for g in (live + soon + recent)[:cap]]
        cdn_idx = {}
        if feedlag.SRC_CDN in self.s.sources and any(lg == League.NBA for lg, _ in wanted):
            resp, _, recv = self._request(leagues.NBA_SCOREBOARD, conditional=False)
            kind = classify(resp.status, host_of(leagues.NBA_SCOREBOARD))
            if kind == "ok":
                cdn_idx = leagues.nba_index(resp.data)
                self.host_fails[host_of(leagues.NBA_SCOREBOARD)] = 0
            else:
                if kind == "host":
                    self._host_failed(host_of(leagues.NBA_SCOREBOARD), resp, self.mono(), None, "nba_scoreboard")
                self.disc_due = self.mono() + min(60.0, self.s.discover_every)   # try the mapping again soon
        self._sync_jobs(wanted, cdn_idx, now_w)
        self.done = (not any_game or all_post) and not self.jobs

    def _skew(self, resp: Resp, recv: float) -> None:
        """The server's Date header against our clock (1 s resolution): a wrong Mac clock would bias every lag."""
        try:
            skew = parsedate_to_datetime(resp.headers["date"]).timestamp() - recv
        except (KeyError, TypeError, ValueError):
            return
        self._emit([{"ev": "clock", "t": round(recv, 3), "skew": round(skew, 2)}])

    def _sync_jobs(self, wanted: list[tuple], cdn_idx: dict, now_w: float) -> None:
        keep: set[tuple] = set()
        fresh: list[Job] = []
        for lg, g in wanted:
            wants = [(feedlag.SRC_ESPN, None, espn.summary_url(lg, g.game_id))]
            if lg == League.NBA and feedlag.SRC_CDN in self.s.sources:
                gm = leagues.nba_match(g, cdn_idx) if cdn_idx else None
                if gm and gm.get("gameId"):
                    wants.append((feedlag.SRC_CDN, str(gm["gameId"]), leagues.NBA_PBP.format(id=gm["gameId"])))
            for src, gid, url in wants:
                if src not in self.s.sources:
                    continue
                key = (src, g.game_id)
                keep.add(key)
                if key in self.jobs:
                    continue
                tr = feedlag.ArrivalTracker(src, lg.value, g.game_id, gid, primed=g.phase == Phase.PRE,
                                            known=self.known.get(key))
                parse = (lambda d, _lg=lg: feedlag.espn_rows(_lg, d)) if src == feedlag.SRC_ESPN else feedlag.cdn_rows
                fresh.append(Job(key, url, host_of(url), tr, parse))
                self._emit([{"ev": "track", "t": round(now_w, 3), "src": src, "lg": lg.value, "game": g.game_id,
                             "gid": gid, "state": g.phase.value, "away": g.away.abbr, "home": g.home.abbr}])
        for k in list(self.jobs):
            if k not in keep:
                j = self.jobs.pop(k)
                self._emit([{"ev": "drop", "t": round(now_w, 3), "src": k[0], "game": k[1], "logged": j.tracker.logged,
                             "baseline": j.tracker.baseline}])
        base = self.mono()
        for i, j in enumerate(fresh):                    # spread first polls across one interval
            j.due = base + self.s.interval * i / max(1, len(fresh))
            self.jobs[j.key] = j

    # -- one scheduling step ----------------------------------------------------------------------------------
    def tick(self) -> float:
        """Do the one thing that is due, or say how long (seconds) until something is. 0 = did something."""
        now = self.mono()
        if now - self.last_status >= STATUS_EVERY:
            self.last_status = now
            self.say(f"[{time.strftime('%H:%M:%S')}] slate {self.slate} feeds {len(self.jobs)} requests {self.requests} "
                     f"rows {self.rows} errors {self.errors}")
        disc_ready = max(self.disc_due, self.next_send)
        best, best_ready = None, None
        for j in self.jobs.values():
            ready = max(j.due, self.next_send, self.host_until.get(j.host, 0.0))
            if best is None or ready < best_ready:
                best, best_ready = j, ready
        if best is None or disc_ready <= best_ready:
            if disc_ready > now:
                return disc_ready - now
            self.discover()
            return 0.0
        if best_ready > now:
            return best_ready - now
        self._poll(best)
        return 0.0

    def _poll(self, job: Job) -> None:
        resp, sent, received = self._request(job.url)
        now_m = self.mono()
        job.due = self.last_sent + self.s.interval                   # the interval counts from the send, not the answer
        kind = classify(resp.status, job.host)
        src, game = job.key
        if kind in ("ok", "unchanged"):
            self.host_fails[job.host] = 0
            job.missing = 0
            if kind == "ok":
                self._emit(job.tracker.observe(job.parse(resp.data), sent, received))
            elif job.tracker.primed:
                job.tracker.prev = sent
        elif kind == "missing":
            job.missing += 1
            job.tracker.empty_poll(sent)
            job.due = now_m + max(self.s.interval, min(MISSING_CAP, MISSING_FIRST * 2 ** (job.missing - 1)))
        else:
            self._host_failed(job.host, resp, now_m, game, src)

    def run(self, stop=lambda: False) -> str:
        """Poll until stopped, out of time, or (--exit-when-done) the slate is over. Returns why it ended."""
        crashes = 0
        while True:
            if stop():
                return "signal"
            if self.mono() - self.started >= self.s.max_hours * 3600:
                return "max_hours"
            if self.done and self.s.exit_when_done:
                return "done"
            try:
                wait = self.tick()
                crashes = 0
            except Exception as e:                         # a bug in one parse must not end the night
                crashes += 1
                self._emit([{"ev": "err", "t": round(self.clock(), 3), "what": f"{type(e).__name__}: {e}"[:120]}])
                if crashes >= 20:
                    return "crash"
                wait = 5.0
            if wait > 0:
                self.sleep(max(MIN_NAP, min(wait, 1.0)))        # never a busy spin, never longer than a second


# --- running it -----------------------------------------------------------------------------------------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return "feedlag_logger" in r.stdout


def _pidfile(out_dir: Path) -> Path:
    return out_dir / "logger.pid"


def stop_running(out_dir: Path) -> int:
    pf = _pidfile(out_dir)
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        print("no logger.pid: nothing to stop")
        return 0
    if not _alive(pid):
        print(f"pid {pid} is not a running feedlag_logger; removing the stale pid file")
        pf.unlink(missing_ok=True)
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        time.sleep(0.5)
        if not _alive(pid):
            print(f"stopped pid {pid}")
            return 0
    print(f"pid {pid} did not exit in 15 s; kill {pid} by hand")
    return 1


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Log when each feed row first shows up (metadata only).")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data" / "feedlag")
    ap.add_argument("--leagues", default="nba", help="comma list: nba, cbb (college basketball, ESPN only)")
    ap.add_argument("--sources", default=",".join(feedlag.FEED_SOURCES), help="espn_summary,nba_cdn")
    ap.add_argument("--interval", type=float, default=feedlag.FLOOR_INTERVAL,
                    help="seconds between requests for one game and source (floor 10)")
    ap.add_argument("--max-rps", type=float, default=3.0, help="global requests per second (ceiling 4)")
    ap.add_argument("--max-games", type=int, default=12)
    ap.add_argument("--cbb-max-games", type=int, default=4)
    ap.add_argument("--max-hours", type=float, default=14.0)
    ap.add_argument("--date", help="slate to follow, YYYY-MM-DD (default: tonight, US Eastern)")
    ap.add_argument("--exit-when-done", action="store_true", help="stop once every game of the slate is over")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit; polls no game")
    ap.add_argument("--stop", action="store_true", help="stop the running logger")
    return ap.parse_args(argv)


def settings_from(a: argparse.Namespace) -> Settings:
    names = tuple(x for x in a.leagues.split(",") if x)
    bad = [x for x in names if x not in LEAGUES]
    if bad:
        raise SystemExit(f"unknown league {bad}; use nba and/or cbb")
    srcs = tuple(x for x in a.sources.split(",") if x)
    bad = [x for x in srcs if x not in feedlag.FEED_SOURCES]
    if bad:
        raise SystemExit(f"unknown source {bad}; use {', '.join(feedlag.FEED_SOURCES)}")
    return Settings(out_dir=a.out_dir, leagues=names, sources=srcs, interval=a.interval, max_rps=a.max_rps,
                    max_games=a.max_games, cbb_max_games=a.cbb_max_games, max_hours=a.max_hours,
                    exit_when_done=a.exit_when_done, date=a.date)


def main(argv=None) -> int:
    a = parse_args(argv)
    s = settings_from(a)
    if a.stop:
        return stop_running(s.out_dir)
    s.out_dir.mkdir(parents=True, exist_ok=True)
    if a.dry_run:
        p = Poller(s, dry=True)
        p.discover()
        print(f"slate {p.slate}: would follow {len(p.jobs)} feeds")
        for (src, game), j in sorted(p.jobs.items()):
            print(f"  {src:13} {j.tracker.lg} game {game} {'(before the tip)' if j.tracker.primed else '(under way)'}")
        print(f"interval {s.interval:g} s per feed, at most {s.max_rps:g} requests/s overall, {s.out_dir}")
        return 0
    pf = _pidfile(s.out_dir)
    try:
        other = int(pf.read_text().strip())
        if other != os.getpid() and _alive(other):
            print(f"already running as pid {other}; --stop first")
            return 1
    except (OSError, ValueError):
        pass
    pf.write_text(str(os.getpid()))
    flag = {"stop": False}
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: flag.update(stop=True))
    p = Poller(s)
    p._emit([{"ev": "start", "t": round(p.clock(), 3), "schema": feedlag.LOG_VERSION, "pid": os.getpid(),
              "interval": s.interval, "max_rps": s.max_rps, "leagues": list(s.leagues), "sources": list(s.sources),
              "ua": USER_AGENT}])
    why = "error"
    try:
        why = p.run(lambda: flag["stop"])
    finally:
        p._emit([{"ev": "stop", "t": round(p.clock(), 3), "why": why, "requests": p.requests, "rows": p.rows,
                  "errors": p.errors}])
        pf.unlink(missing_ok=True)
    print(f"stopped ({why}): {p.requests} requests, {p.rows} rows, {p.errors} errors", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
