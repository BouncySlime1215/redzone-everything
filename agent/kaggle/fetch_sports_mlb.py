"""Fetch every Statcast pitch (Baseball Savant csv export, one request per game day) as parquet shards.

    python agent/kaggle/fetch_sports_mlb.py [--first-season 2015] [--last-season 2024] [--min-interval 2.0]

Runs in a Kaggle notebook; no secret, no login. ~4.7k pitches per game day (July 4, 2024), ~720k per season, 118 columns,
2015 on. Rows carry night, source_night_id (mlb:<night>) and the tag "personal-use, not for commercial release"; 2025 on is the
held-out year (sports_common.EVAL_FIRST_NIGHT["mlb"]) and is never requested. Polite: >= 2 s per request (default), robots.txt read
first (Savant's allows all on 2026-11-09). Resumable per month. This is pitch-level Statcast; pitch and action rows from the MLB
Stats API (2008-24) already come from scripts/kaggle_pbp_crew.py (job `baseball`) and are not fetched here.
Why: data job 14: Statcast adds pitch speed, spin, location, launch and win-probability columns the Stats API feed does not carry.
MORE-DATA, why the first run kept only ~4.9 M of ~7 M pitches (our code, three faults): (1) one item was a whole month of ~30 day requests, so one
day that failed after the engine's retries threw the month away (state.json "failed", never in the output); (2) a body cut short
mid-read raises http.client.IncompleteRead, which is not an OSError, so the engine's retry never saw it and the month failed on the first
one; (3) Savant answers a slow query late or with an HTML page, and the 60 s socket timeout plus parsing that page as csv (it then has no
game_date column, so every row is silently dropped) lost days with no error. Now: items are weeks (a failure costs at most 7 days and a
rerun retries exactly those); each day is tried 3 times with back-off on timeouts, short reads, 5xx and HTML bodies, with a 180 s timeout;
a day that still fails raises (never an empty file that looks like "no games"). Months a previous run finished (state.json of --out-dir or
--resume-from, item `statcast-YYYY-MM`) are not requested again.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402
from agent.kaggle import sports_common as sc  # noqa: E402

BASE = ("https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfGT=R%7CF%7CD%7CL%7CW%7C&hfSea={y}%7C"
        "&player_type=pitcher&game_date_gt={d}&game_date_lt={d}&min_pitches=0&min_results=0&group_by=name"
        "&sort_col=pitches&sort_order=desc&type=details")
LICENSE = "MLB Advanced Media terms, personal/non-commercial use only (Baseball Savant / Statcast)"
LICENSE_URL = "https://baseballsavant.mlb.com/csv-docs"


DAY_TRIES, DAY_TIMEOUT_S = 3, 180
SHORT_READ = (http.client.HTTPException, TimeoutError, ConnectionError)     # IncompleteRead is an HTTPException, not an OSError


class DayFailed(RuntimeError):
    """A game day could not be fetched after its tries. Raised, never turned into an empty file: that would look like a day with no games."""


class SavantDays(sc.CsvDayFetcher):
    """CsvDayFetcher that retries one day on the failures the engine's own retry misses, and refuses a non-csv body."""

    def _raw_open(self, url: str, start: int = 0, method: str = "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": sc.UA})
        return urllib.request.urlopen(req, timeout=DAY_TIMEOUT_S)

    def download(self, url: str, dest, start: int) -> None:
        last = None
        for attempt in range(DAY_TRIES):
            try:
                super().download(url, dest, 0)
                head = Path(dest).read_bytes()[:256].lstrip()
                if head[:1] == b"<":
                    raise DayFailed(f"an HTML page, not csv, for {url.split('game_date_gt=')[-1][:10]}")
                return
            except sc.Blocked:
                raise
            except urllib.error.HTTPError as e:
                if e.code in (403, 404, 410):
                    raise
                last = e
            except (DayFailed, urllib.error.URLError, OSError, *SHORT_READ) as e:
                last = e
            self._sleep(min(10 * 3 ** attempt, 120))
        raise DayFailed(f"{url.split('game_date_gt=')[-1][:10]} failed {DAY_TRIES} times: {type(last).__name__}: {last}")


def _finished_months(a) -> set[str]:
    """Month items (`statcast-YYYY-MM`) a previous run finished, from state.json of --out-dir or --resume-from."""
    done: set[str] = set()
    for base in (Path(getattr(a, "out_dir", "") or "."), Path(a.resume_from) if getattr(a, "resume_from", "") else None):
        f = base / SPEC_NAME / "state.json" if base else None
        if f and f.exists():
            done |= set(json.loads(f.read_text()).get("items", {}))
    return done


def items(f, a) -> list[df.Item]:
    """One item per week of game days (days 1-7, 8-14, 15-21, 22-28, 29-end of a month), newest first."""
    out, finished = [], _finished_months(a)
    for y in range(a.last_season, a.first_season - 1, -1):
        day, end, weeks = dt.date(y, 3, 15), dt.date(y, 11, 10), {}
        while day <= end:
            month = day.strftime("%Y-%m")
            if not sc.is_locked("mlb", day.strftime("%Y%m%d")) and f"statcast-{month}" not in finished:
                weeks.setdefault(f"{month}-w{(day.day - 1) // 7 + 1}", []).append(BASE.format(y=y, d=day.isoformat()) + sc.fragment(season=y))
            day += dt.timedelta(days=1)
        out += [df.Item(f"statcast-{w}", urls) for w, urls in sorted(weeks.items(), reverse=True)]
    return out


SPEC_NAME = "sports_statcast"
SPEC = df.SourceSpec(
    name=SPEC_NAME, license_text=LICENSE, license_url=LICENSE_URL, all_strings=True,
    sampling_rule=f"none (every pitch of every game day Mar 15-Nov 10); days on or after {sc.EVAL_FIRST_NIGHT['mlb']} never requested",
    list_items=items, row_filter=sc.lock_filter("mlb", "statcast", LICENSE, ("game_date",)),
    add_args=lambda ap: sc.season_args(ap, 2015, 2024),
    manifest_extra={"tag": sc.PERSONAL_TAG, "plan": "B", "first_locked_night": sc.EVAL_FIRST_NIGHT["mlb"]})

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, lambda a: SavantDays("game_date", min_interval=max(a.min_interval, 2.0))))
