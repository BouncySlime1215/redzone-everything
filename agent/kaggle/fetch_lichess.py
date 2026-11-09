"""Fetch a hash-sampled subset of Lichess standard games (HF, CC0) into parquet shards.

    python agent/kaggle/fetch_lichess.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (CC0); 7.14B games, so sampled by month file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

REPO = "Lichess/standard-chess-games"


def _args(ap) -> None:
    ap.add_argument("--stride", type=int, default=6, help="keep 1 file in N by name hash (default 6)")


SPEC = df.SourceSpec(
    name="lichess",
    license_text="CC0-1.0 (Lichess open database; the HF card states CC0).",
    license_url="https://huggingface.co/datasets/Lichess/standard-chess-games",
    sampling_rule="1 parquet file in 6 chosen by sha256(file name) % 6 == 0, hash order so any cut-off is uniform; files over cap/3 skipped (so the largest recent months drop out)",
    list_items=lambda f, a: df.sampled_items(df.hf_items(f, REPO, "data"), a.stride),
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
