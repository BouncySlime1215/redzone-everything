"""Fetch every NCAA men's basketball play-by-play (hoopR/ESPN) row, newest season first, as parquet shards with the personal-use tag.

    python agent/kaggle/fetch_sports_ncaam.py [--first-season 2006] [--last-season 2026] [--cap-gb 18] [--resume-from <prev output>]

Runs in a Kaggle notebook; no secret, no login. One in-repo parquet per season on raw.githubusercontent.com (no robots rules there; the robots check still runs against that host) (7-64 MB per season, 0.3-2.0M rows). Rows carry night,
source_night_id (cbb:<night>) and the tag "personal-use, not for commercial release"; locked and fresh nights
(on or after sports_common.EVAL_FIRST_NIGHT["cbb"]) are dropped. Engine, resume, manifest: agent/kaggle/data_fetch.py.
Why: data job 19: github.com's robots rule stopped the release-file route; raw.githubusercontent.com has none. Data job 14: raw play rows for all seasons feed world-model pretraining; the factory keeps only labeled stoppage moments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import sports_common as sc  # noqa: E402

SPEC, FETCHER = sc.release_spec(
    "cbb", "hoopr_espn_cbb", "https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-data/main/mbb/pbp/parquet/play_by_play_{y}.parquet", 2006, 2026,
    "MIT (code); underlying ESPN data: personal/non-commercial use only (ESPN terms)",
    "https://github.com/sportsdataverse/hoopR-mbb-data",
    date_cols=('game_date_time', 'game_date'), season_night="0901")

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, FETCHER))
