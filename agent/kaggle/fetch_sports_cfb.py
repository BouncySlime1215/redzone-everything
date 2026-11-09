"""Fetch every college football play-by-play (ESPN via SportsDataverse, 507 columns, no date column so night = season Sept 1, night_exact=0) row, newest season first, as parquet shards with the personal-use tag.

    python agent/kaggle/fetch_sports_cfb.py [--first-season 2004] [--last-season 2024] [--cap-gb 18] [--resume-from <prev output>]

Runs in a Kaggle notebook; no secret, no login. One public release file per season (27-60 MB per season, ~80-165k rows). Rows carry night,
source_night_id (cfb:<night>) and the tag "personal-use, not for commercial release"; locked and fresh nights
(on or after sports_common.EVAL_FIRST_NIGHT["cfb"]) are dropped. Engine, resume, manifest: agent/kaggle/data_fetch.py.
Why: data job 14: raw play rows for all seasons feed world-model pretraining; the factory keeps only labeled stoppage moments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import sports_common as sc  # noqa: E402

SPEC, FETCHER = sc.release_spec(
    "cfb", "espn_cfb", "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_cfb_pbp/play_by_play_{y}.parquet", 2004, 2024,
    "MIT (code); underlying ESPN data: personal/non-commercial use only (ESPN terms)",
    "https://github.com/sportsdataverse/sportsdataverse-data",
    date_cols=(), season_night="0901")

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, FETCHER))
