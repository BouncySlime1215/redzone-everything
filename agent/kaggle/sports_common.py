"""Shared parts of the sports fetch scripts (agent/kaggle/fetch_sports_<league>.py): polite fetching, the lock cut, the tag.

    from agent.kaggle import sports_common as sc    # used by fetch_sports_*.py, run in a Kaggle notebook

What it adds to data_fetch.py: PoliteFetcher (rate limit, robots.txt including Content-Signal ai-train=no, Retry-After
back-off), JsonCsvFetcher (a public JSON API flattened to csv text so the engine can shard it), and lock_filter (every
row gets night + source_night_id + the personal-use tag; rows on or after the league's first locked night are dropped).
Public endpoints only; no login, key, cookie or CAPTCHA step exists here; a block (403, robots) stops the source.
Why: data job 14: the big sports sources are allowed for personal use (decisions.md 2026-11-05 11:20 PM) but only with the
tag, the locked-night cut and polite access, which every league script must share instead of re-implement.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc

from agent.kaggle import data_fetch as df

PERSONAL_TAG = "personal-use, not for commercial release"
UA = "redzone-personal-research/1.0"
# First locked night per league. MUST equal redzone.noggin.factory.EVAL_FIRST_NIGHT (tests/test_fetch_sports.py checks).
# Nothing on or after it is written; the repo's own split rules (redzone/lab/splits.py) cut the rest by whole night.
EVAL_FIRST_NIGHT = {"nba": "20261020", "cbb": "20261020", "wcbb": "20261020", "nfl": "20250905", "cfb": "20250801",
                    "nhl": "20261019", "mlb": "20250301"}
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


class Blocked(df.StopRun):
    """The host says no (robots.txt, Content-Signal ai-train=no, 403). We stop; there is no way around it here."""


# ---------- nights ----------

def night_of(value) -> str | None:
    """YYYYMMDD ET night (5 AM boundary, like the repo) from an ISO string, date or datetime. Date-only means that date."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        t = value if value.tzinfo else value.replace(tzinfo=UTC)
    elif isinstance(value, dt.date):
        return value.strftime("%Y%m%d")
    else:
        s = str(value).strip()
        if len(s) < 10:
            return None
        if len(s) == 10:
            return s.replace("-", "")
        try:
            t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
    return (t.astimezone(ET) - dt.timedelta(hours=5)).strftime("%Y%m%d")


def is_locked(league: str, night: str | None) -> bool:
    cut = EVAL_FIRST_NIGHT[league]
    return night is None or night >= cut     # no date at all = cannot prove it is safe = dropped


def lock_filter(league: str, source: str, license_text: str, date_cols: tuple[str, ...] = (),
                season_night: str = "0901") -> Callable[[pa.Table], pa.Table]:
    """Row filter for the engine. Night comes from the first date column present (or a `night` column the flattener wrote);
    with none, from `season` + season_night (night_exact = 0). Adds league, source, plan, tag, license, night,
    source_night_id (league:night, the form scripts/value_leak_check.py reads) and drops locked or undated rows."""
    def run(t: pa.Table) -> pa.Table:
        names = set(t.column_names)
        col, exact = None, "1"
        for c in ("night",) + tuple(date_cols):
            if c in names:
                col = c
                break
        if col is None and "season" in names:
            col, exact = "season", "0"
        if col is None:
            return t.slice(0, 0)
        enc = pc.dictionary_encode(t.column(col).combine_chunks())
        keys = enc.dictionary.to_pylist()
        if col == "season":
            nights = [f"{int(k)}{season_night}" if k not in (None, "") else None for k in keys]
        elif col == "night":
            nights = [str(k) if k not in (None, "") else None for k in keys]
        else:
            nights = [night_of(k) for k in keys]
        night = pc.take(pa.array(nights, type=pa.string()), enc.indices)
        keep = pc.fill_null(pc.less(night, EVAL_FIRST_NIGHT[league]), False)
        t = t.filter(keep)
        night = night.filter(keep)
        n = t.num_rows
        for name in ("league", "source", "plan", "tag", "license", "night", "night_exact", "source_night_id"):
            if name in t.column_names:
                t = t.drop_columns([name])
        t = t.append_column("night", night)
        t = t.append_column("source_night_id", pc.binary_join_element_wise(pa.array([league + ":"] * n), night, ""))
        for name, val in (("league", league), ("source", source), ("plan", "B"), ("tag", PERSONAL_TAG),
                          ("license", license_text), ("night_exact", exact)):
            t = t.append_column(name, pa.array([val] * n, type=pa.string()))
        return t
    return run


# ---------- polite fetching ----------

def parse_robots(text: str, path: str) -> tuple[bool, bool]:
    """(allowed, ai_train_ok) for `User-agent: *` and this path. Wildcards (*, $) like Google's parser; longest rule wins."""
    rules, signal, group_star, in_rules = [], "", False, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                group_star, in_rules = False, False
            group_star = group_star or v == "*"
        else:
            in_rules = True
            if not group_star:
                continue
            if k in ("allow", "disallow") and v:
                rules.append((k == "allow", v))
            elif k == "content-signal":
                signal += "," + v.replace(" ", "").lower()
    best, allowed = -1, True
    for ok, pat in rules:
        rx = re.escape(pat).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path) and len(pat) > best:
            best, allowed = len(pat), ok
    return allowed, "ai-train=no" not in signal


class PoliteFetcher(df.HttpFetcher):
    """HttpFetcher plus: >= min_interval s between requests, robots.txt per host once, back-off on 429/5xx (Retry-After)."""

    def __init__(self, min_interval: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.min_interval, self._sleep, self._clock = min_interval, sleep, clock
        self._last = -1e9
        self._robots: dict[str, str | None] = {}
        self.requests = 0

    def _raw_open(self, url: str, start: int = 0, method: str = "GET"):
        return super()._open(url, start, method)

    def _read_robots(self, host_url: str) -> str | None:
        try:
            with self._raw_open(host_url + "/robots.txt") as r:
                return r.read(1 << 20).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                return None          # no robots file: everything is allowed (RFC 9309)
            raise Blocked(f"robots.txt of {host_url} answered {e.code}; not fetching") from e

    def check(self, url: str) -> None:
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            self._wait()
            self._robots[host] = self._read_robots(host)
        text = self._robots[host]
        if text is None:
            return
        allowed, train_ok = parse_robots(text, p.path + ("?" + p.query if p.query else ""))
        if not train_ok:
            raise Blocked(f"{host} robots.txt says Content-Signal ai-train=no: not fetching training data from it")
        if not allowed:
            raise Blocked(f"{host} robots.txt disallows {p.path}")

    def _wait(self) -> None:
        gap = self.min_interval - (self._clock() - self._last)
        if gap > 0:
            self._sleep(gap)
        self._last = self._clock()

    def _open(self, url: str, start: int = 0, method: str = "GET"):
        self.check(url)
        for attempt in range(5):
            self._wait()
            self.requests += 1
            try:
                return self._raw_open(url, start, method)
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    raise Blocked(f"403 from {url}: a block, not retried or worked around") from e
                if e.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise
                ra = e.headers.get("Retry-After") if e.headers else None
                self._sleep(min(float(ra), 600) if ra and ra.replace(".", "", 1).isdigit() else min(2 ** (attempt + 2), 120))
        raise RuntimeError("unreachable")


class JsonCsvFetcher(PoliteFetcher):
    """Public JSON API -> csv text on disk. `flatten(url, payload)` returns (columns, rows); a 404/410 game writes a header only."""

    def __init__(self, flatten: Callable[[str, object], list[dict]], columns: list[str], **kw):
        super().__init__(**kw)
        self.flatten, self.columns = flatten, columns
        self.missing: list[str] = []

    def download(self, url: str, dest: Path, start: int) -> None:
        try:
            payload = self.get_json(url.split("#")[0])
            rows = self.flatten(url, payload)
        except urllib.error.HTTPError as e:
            if e.code not in (404, 410):
                raise
            self.missing.append(url)
            rows = []
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=self.columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        Path(dest).write_text(buf.getvalue(), encoding="utf-8")


class CsvDayFetcher(PoliteFetcher):
    """Plain csv download where an empty day answers with an empty body: that becomes a header-only file."""

    def __init__(self, header: str, **kw):
        super().__init__(**kw)
        self.header = header

    def download(self, url: str, dest: Path, start: int) -> None:
        super().download(url, dest, 0)
        if Path(dest).stat().st_size == 0:
            Path(dest).write_text(self.header + "\n")


def fragment(**kw) -> str:
    """'#k=v&k=v.csv': carries per-url facts (night, season) to the flattener and tells the engine the file is csv."""
    return "#" + urllib.parse.urlencode(kw) + ".csv"


def frag_params(url: str) -> dict[str, str]:
    f = url.split("#", 1)[1] if "#" in url else ""
    return dict(urllib.parse.parse_qsl(f[:-4] if f.endswith(".csv") else f))


def season_args(ap, default_first: int, default_last: int) -> None:
    ap.add_argument("--first-season", type=int, default=default_first)
    ap.add_argument("--last-season", type=int, default=default_last)
    ap.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests (never below 0.5)")


def main(spec: df.SourceSpec, fetcher_factory: Callable, argv: list[str] | None = None) -> int:
    ap = df.parser(spec)
    args = ap.parse_args(argv)
    args.min_interval = max(getattr(args, "min_interval", 1.0), 0.5)
    try:
        row = df.Run(spec, args, fetcher_factory(args)).run()
    except Blocked as e:
        print(f"STOPPED: {e}")
        return 2
    import json
    print(json.dumps(row, indent=1))
    return 0


# ---------- release-file sources (nflverse, SportsDataverse): one parquet per season ----------

def release_spec(league: str, source: str, url: str, first: int, last: int, license_text: str, license_url: str,
                 date_cols: tuple[str, ...], season_night: str = "0901") -> tuple[df.SourceSpec, Callable]:
    """SourceSpec + fetcher factory for a source that publishes `url.format(y=season)` parquet files. One item per season, newest first."""
    def items(f, a) -> list[df.Item]:
        lo, hi = max(a.first_season, first), min(a.last_season, last)
        return [df.Item(f"{league}-{y}", [url.format(y=y)]) for y in range(hi, lo - 1, -1)]

    spec = df.SourceSpec(
        name=f"sports_{source}", license_text=license_text, license_url=license_url,
        sampling_rule=f"none (all rows of seasons {first}-{last}); rows on or after {EVAL_FIRST_NIGHT[league]} dropped (locked/fresh nights)",
        list_items=items, row_filter=lock_filter(league, source, license_text, date_cols, season_night),
        add_args=lambda ap: season_args(ap, first, last),
        manifest_extra={"tag": PERSONAL_TAG, "plan": "B", "first_locked_night": EVAL_FIRST_NIGHT[league]})
    return spec, lambda a: PoliteFetcher(a.min_interval)
