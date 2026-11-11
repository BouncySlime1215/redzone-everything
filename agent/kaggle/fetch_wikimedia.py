"""Fetch a hash-sampled subset of the Wikimedia pageview time series (HF mirror, CC0) into parquet shards.

    python agent/kaggle/fetch_wikimedia.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (CC0); 686 GB listed, so sampled by file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

REPO = "jeremycochoy/wikimedia-pageview-timeseries-raw"


def _args(ap) -> None:
    ap.add_argument("--stride", type=int, default=20, help="keep 1 file in N by name hash (default 20)")


SPEC = df.SourceSpec(
    name="wikimedia",
    license_text="CC0-1.0 (the HF dataset card; follows the upstream Wikimedia pageview dumps, which are CC0).",
    license_url="https://huggingface.co/datasets/jeremycochoy/wikimedia-pageview-timeseries-raw",
    sampling_rule="1 parquet file in 20 chosen by sha256(file name) % 20 == 0, processed in hash order so any cut-off is still uniform; files over cap/3 skipped",
    list_items=lambda f, a: df.sampled_items(df.hf_items(f, REPO, ""), a.stride),
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
