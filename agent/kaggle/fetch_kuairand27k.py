"""Fetch KuaiRand-27K (Kuaishou short-video watch logs, random-exposure rows) from Zenodo into parquet shards.

    python agent/kaggle/fetch_kuairand27k.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: cleared by docs/agents/data/2026-11-09-viewer-preference-logs.md (CC BY 4.0), indirect keep-watching evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

SPEC = df.SourceSpec(
    name="kuairand27k",
    license_text="CC BY 4.0 on the Zenodo record 10439422 (project site badge says CC BY-SA 4.0; either allows reuse with credit). Cite Kuaishou KuaiRand.",
    license_url="https://zenodo.org/records/10439422",
    sampling_rule="none (all rows of every csv in the 27K archive, ~9.9 GB tar.gz, one session); columns kept as text, cast downstream",
    list_items=lambda f, a: df.zenodo_items(f, 10439422, lambda k: "27k" in k.lower() and k.endswith(".tar.gz")),
    all_strings=True,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
