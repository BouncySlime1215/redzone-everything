"""Fetch KuaiRec (Kuaishou watch-ratio matrix, fully observed small part) from Zenodo into parquet shards.

    python agent/kaggle/fetch_kuairec.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: cleared by docs/agents/data/2026-11-09-viewer-preference-logs.md (CC BY 4.0), clean unbiased watch-ratio ground truth.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

SPEC = df.SourceSpec(
    name="kuairec",
    license_text="CC BY 4.0 on the Zenodo record 18164998 (project site badge says CC BY-SA 4.0). Cite Kuaishou KuaiRec.",
    license_url="https://zenodo.org/records/18164998",
    sampling_rule="none (all rows of every csv in the archive, ~0.43 GB zip)",
    list_items=lambda f, a: df.zenodo_items(f, 18164998, lambda k: k.endswith((".zip", ".tar.gz", ".csv"))),
    all_strings=True,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
