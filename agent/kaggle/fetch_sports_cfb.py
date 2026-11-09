"""Fetch every college football play-by-play (ESPN via SportsDataverse, 507 columns, no date column so night = season Sept 1, night_exact=0) row, newest season first, as parquet shards with the personal-use tag.

    python agent/kaggle/fetch_sports_cfb.py [--first-season 2002] [--last-season 2024] [--api-interval 62] [--cap-gb 18] [--resume-from <prev output>]

Runs in a Kaggle notebook; no secret, no login. One file per season (27-60 MB, ~80-165k rows): 2002-2021 from the in-repo parquet on raw.githubusercontent.com (cfbfastR-data, no robots rules), 2022-2024 as release assets through the GitHub REST API on api.github.com (no token; one request per 62 s for the 60/hour limit; robots checked on both hosts and the redirect host). github.com/.../releases/download is never requested. Rows carry night,
source_night_id (cfb:<night>) and the tag "personal-use, not for commercial release"; locked and fresh nights
(on or after sports_common.EVAL_FIRST_NIGHT["cfb"]) are dropped. Engine, resume, manifest: agent/kaggle/data_fetch.py.
Why: data job 18/19 + Nick's approval 2026-11-09 (api.github.com for NFL and CFB). data job 14: raw play rows for all seasons feed world-model pretraining; the factory keeps only labeled stoppage moments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import sports_common as sc  # noqa: E402

SPEC, FETCHER = sc.api_release_spec(
    "cfb", "espn_cfb", "sportsdataverse/sportsdataverse-data", "espn_cfb_pbp", 2002, 2024,
    "MIT (code); underlying ESPN data: personal/non-commercial use only (ESPN terms)",
    "https://github.com/sportsdataverse/sportsdataverse-data",
    date_cols=(), season_night="0901",
    raw_url="https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/pbp/parquet/play_by_play_{y}.parquet", raw_last=2021)

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, FETCHER))
