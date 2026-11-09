"""Fetch every Statcast pitch (Baseball Savant csv export, one request per game day) as parquet shards.

    python agent/kaggle/fetch_sports_mlb.py [--first-season 2015] [--last-season 2024] [--min-interval 2.0]

Runs in a Kaggle notebook; no secret, no login. ~4.7k pitches per game day (July 4, 2024), ~720k per season, 118 columns,
2015 on. Rows carry night, source_night_id (mlb:<night>) and the tag "personal-use, not for commercial release"; 2025 on is the
held-out year (sports_common.EVAL_FIRST_NIGHT["mlb"]) and is never requested. Polite: >= 2 s per request (default), robots.txt read
first (Savant's allows all on 2026-11-09). Resumable per month. This is pitch-level Statcast; pitch and action rows from the MLB
Stats API (2008-24) already come from scripts/kaggle_pbp_crew.py (job `baseball`) and are not fetched here.
Why: data job 14: Statcast adds pitch speed, spin, location, launch and win-probability columns the Stats API feed does not carry.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402
from agent.kaggle import sports_common as sc  # noqa: E402

BASE = ("https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfGT=R%7CF%7CD%7CL%7CW%7C&hfSea={y}%7C"
        "&player_type=pitcher&game_date_gt={d}&game_date_lt={d}&min_pitches=0&min_results=0&group_by=name"
        "&sort_col=pitches&sort_order=desc&type=details")
LICENSE = "MLB Advanced Media terms, personal/non-commercial use only (Baseball Savant / Statcast)"
LICENSE_URL = "https://baseballsavant.mlb.com/csv-docs"


def items(f, a) -> list[df.Item]:
    out = []
    for y in range(a.last_season, a.first_season - 1, -1):
        day, end, months = dt.date(y, 3, 15), dt.date(y, 11, 10), {}
        while day <= end:
            if not sc.is_locked("mlb", day.strftime("%Y%m%d")):
                months.setdefault(day.strftime("%Y-%m"), []).append(BASE.format(y=y, d=day.isoformat()) + sc.fragment(season=y))
            day += dt.timedelta(days=1)
        out += [df.Item(f"statcast-{m}", urls) for m, urls in sorted(months.items(), reverse=True)]
    return out


SPEC = df.SourceSpec(
    name="sports_statcast", license_text=LICENSE, license_url=LICENSE_URL, all_strings=True,
    sampling_rule=f"none (every pitch of every game day Mar 15-Nov 10); days on or after {sc.EVAL_FIRST_NIGHT['mlb']} never requested",
    list_items=items, row_filter=sc.lock_filter("mlb", "statcast", LICENSE, ("game_date",)),
    add_args=lambda ap: sc.season_args(ap, 2015, 2024),
    manifest_extra={"tag": sc.PERSONAL_TAG, "plan": "B", "first_locked_night": sc.EVAL_FIRST_NIGHT["mlb"]})

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, lambda a: sc.CsvDayFetcher("game_date", min_interval=max(a.min_interval, 2.0))))
