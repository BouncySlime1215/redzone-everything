"""Copy Zvuk (244M listen interactions) from a Kaggle input mount into parquet shards.

    python agent/kaggle/fetch_zvuk.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (CC BY 4.0); Kaggle-only dataset, so it is attached, not downloaded.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

INPUT = "/kaggle/input/zvuk-dataset"


def _args(ap) -> None:
    ap.add_argument("--input", default=INPUT, help="the attached Kaggle dataset alexxl/zvuk-dataset")


SPEC = df.SourceSpec(
    name="zvuk",
    license_text="CC BY 4.0 as shown on the Kaggle dataset page alexxl/zvuk-dataset (KDD'24 Zvuk dataset); cite the paper.",
    license_url="https://www.kaggle.com/datasets/alexxl/zvuk-dataset",
    sampling_rule="none (all rows); Add Data > alexxl/zvuk-dataset in the notebook first (2.66 GB, no internet needed)",
    list_items=lambda f, a: df.local_items(a.input, "zvuk"),
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
