"""Fetch every NBA play-by-play (hoopR/ESPN) row, newest season first, as parquet shards with the personal-use tag.

    python agent/kaggle/fetch_sports_nba.py [--first-season 2002] [--last-season 2026] [--cap-gb 18] [--resume-from <prev output>]

Runs in a Kaggle notebook; no secret, no login. One in-repo parquet per season on raw.githubusercontent.com (no robots rules there; the robots check still runs against that host) (12-21 MB per season, ~600k rows). Rows carry night,
source_night_id (nba:<night>) and the tag "personal-use, not for commercial release"; locked and fresh nights
(on or after sports_common.EVAL_FIRST_NIGHT["nba"]) are dropped. Engine, resume, manifest: agent/kaggle/data_fetch.py.
Why: data job 19: github.com's robots rule stopped the release-file route; raw.githubusercontent.com has none. Data job 14: raw play rows for all seasons feed world-model pretraining; the factory keeps only labeled stoppage moments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import sports_common as sc  # noqa: E402

SPEC, FETCHER = sc.release_spec(
    "nba", "hoopr_espn_nba", "https://raw.githubusercontent.com/sportsdataverse/hoopR-nba-data/main/nba/pbp/parquet/play_by_play_{y}.parquet", 2002, 2026,
    "MIT (code); underlying ESPN data: personal/non-commercial use only (ESPN terms)",
    "https://github.com/sportsdataverse/hoopR-nba-data",
    date_cols=('game_date_time', 'game_date'), season_night="0901")

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, FETCHER))
