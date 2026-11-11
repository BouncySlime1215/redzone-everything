"""Fetch a hash-sampled subset of the Criteo 1TB click logs (Criteo's own HF repo, CC BY-NC-SA) into parquet shards.

    python agent/kaggle/fetch_criteo.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (NC, personal use only); 4.37B rows, so sampled by file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

REPO = "criteo/CriteoClickLogs"


def _args(ap) -> None:
    ap.add_argument("--stride", type=int, default=4, help="keep 1 file in N by name hash (default 4)")


SPEC = df.SourceSpec(
    name="criteo",
    license_text="CC BY-NC-SA 4.0: share and adapt for NonCommercial purposes only, ShareAlike. Personal project use only; do not redistribute shards commercially.",
    license_url="https://huggingface.co/datasets/criteo/CriteoClickLogs",
    sampling_rule="1 parquet file in 4 chosen by sha256(file name) % 4 == 0, hash order; files over cap/3 skipped (per-file size is not on the owner's page, so check state.json skipped list)",
    list_items=lambda f, a: df.sampled_items(df.hf_items(f, REPO, ""), a.stride),
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
