"""Fetch every WNBA play-by-play (wehoop/ESPN) row, newest season first, as parquet shards with the personal-use tag.

    python agent/kaggle/fetch_sports_wnba.py [--first-season 2002] [--last-season 2025] [--cap-gb 18] [--resume-from <prev output>]

Runs in a Kaggle notebook; no secret, no login. One in-repo parquet per season on raw.githubusercontent.com (wehoop-wnba-data,
`wnba/pbp/parquet/play_by_play_{y}.parquet`; no robots rules there, and the robots check still runs against that host). Rows carry night,
source_night_id (wnba:<night>) and the tag "personal-use, not for commercial release"; locked and fresh nights
(on or after sports_common.EVAL_FIRST_NIGHT["wnba"]) are dropped. Engine, resume, manifest: agent/kaggle/data_fetch.py.
A season the host does not have is a 404 for that item only (state.json lists it); the same hoopR-style columns as fetch_sports_nba.py
(wallclock, type_text, period_number, ...) are expected, and seasons whose files carry no wallclock build empty value shards, with the reason.
github.com/.../releases/download is never requested (its robots file disallows it); scripts/pbp/wehoop.py's release URL is not used here.
Why: MORE-DATA: WNBA was only in scripts/kaggle_pbp_crew.py through a github.com release route the robots rule closed, so it never reached the value shards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import sports_common as sc  # noqa: E402

SPEC, FETCHER = sc.release_spec(
    "wnba", "wehoop_espn_wnba", "https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/main/wnba/pbp/parquet/play_by_play_{y}.parquet", 2002, 2025,
    "MIT (code); underlying ESPN data: personal/non-commercial use only (ESPN terms)",
    "https://github.com/sportsdataverse/wehoop-wnba-data",
    date_cols=('game_date_time', 'game_date'), season_night="0901")

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, FETCHER))
