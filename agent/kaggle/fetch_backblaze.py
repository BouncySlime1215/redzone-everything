"""Fetch Backblaze Drive Stats quarterly zips: every failure row plus every row of 1 drive in 10, into parquet shards.

    python agent/kaggle/fetch_backblaze.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (cite Backblaze; derivatives allowed); only 21,609 real failures, so those are all kept.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

BASE = "https://f001.backblazeb2.com/file/Backblaze-Hard-Drive-Data/"
KEEP_FRAC = 0.10


def _args(ap) -> None:
    ap.add_argument("--first-year", type=int, default=2016)
    ap.add_argument("--last-year", type=int, default=df.dt.date.today().year)


def _quarters(f, a) -> list[df.Item]:
    return [df.Item(f"Q{q}_{y}", [f"{BASE}data_Q{q}_{y}.zip"])
            for y in range(a.first_year, a.last_year + 1) for q in (1, 2, 3, 4)]


def _thin(table):
    keep = df.keep_hash_rows(table.column("serial_number"), KEEP_FRAC)
    failed = df.pc.equal(table.column("failure"), "1")
    return table.filter(df.pc.or_(keep, failed))


SPEC = df.SourceSpec(
    name="backblaze",
    license_text="No named licence. Backblaze terms: cite Backblaze, you may sell derivative works, you may not sell the raw data.",
    license_url="https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data",
    sampling_rule="all rows with failure=1, plus every row of drives whose sha256(serial_number) falls in the first 10% (whole drive histories); quarters 2016Q1 on, missing quarters are logged as failed",
    list_items=_quarters,
    row_filter=_thin,
    all_strings=True,
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
