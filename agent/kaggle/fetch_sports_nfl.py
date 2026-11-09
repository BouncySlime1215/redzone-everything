"""Fetch every NFL play-by-play (nflfastR, 372 columns) row, newest season first, as parquet shards with the personal-use tag.

    python agent/kaggle/fetch_sports_nfl.py [--first-season 1999] [--last-season 2024] [--api-interval 62] [--cap-gb 18] [--resume-from <prev output>]

Runs in a Kaggle notebook; no secret, no login. One release asset per season (20 MB, ~49k rows) fetched through the GitHub REST API asset endpoint on api.github.com (no token; 60 requests/hour, so one season per 62 s, about 30 min for all 26; a used-up quota waits for its reset and resumes; the robots check runs on api.github.com and on the redirect host). github.com/.../releases/download is never requested: its robots file disallows it. Rows carry night,
source_night_id (nfl:<night>) and the tag "personal-use, not for commercial release"; locked and fresh nights
(on or after sports_common.EVAL_FIRST_NIGHT["nfl"]) are dropped. Engine, resume, manifest: agent/kaggle/data_fetch.py.
Why: data job 18/19 + Nick's approval 2026-11-09 (api.github.com for NFL and CFB). data job 14: raw play rows for all seasons feed world-model pretraining; the factory keeps only labeled stoppage moments.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import sports_common as sc  # noqa: E402

SPEC, FETCHER = sc.api_release_spec(
    "nfl", "nflverse_pbp", "nflverse/nflverse-data", "pbp", 1999, 2024,
    "CC-BY-4.0 (nflverse data, credit nflverse); underlying NFL data: personal/non-commercial use only",
    "https://github.com/nflverse/nflverse-data",
    date_cols=('game_date',), season_night="0901")

if __name__ == "__main__":
    raise SystemExit(sc.main(SPEC, FETCHER))
