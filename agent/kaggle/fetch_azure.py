"""Fetch the Azure Public Dataset V2 VM CPU readings (public blob files) into parquet shards.

    python agent/kaggle/fetch_azure.py [--cap-gb 18] [--out-dir /kaggle/working/data] [--resume-from <prev output>]

Runs in a Kaggle notebook; needs no secret. Engine, resume and manifest: agent/kaggle/data_fetch.py.
Why: data job 10: PASS in docs/agents/data/2026-11-09-data-confirmation.md (CC-BY-4.0 / MIT listed); 1.9B readings, fetched in hash order until the cap.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402

BASE = "https://azurepublicdatasettraces.blob.core.windows.net/azurepublicdatasetv2/azure_v2/vm_cpu_readings/"


def _args(ap) -> None:
    ap.add_argument("--files", type=int, default=125, help="number of vm_cpu_readings-file-N-of-M files (M)")


def _files(f, a) -> list[df.Item]:
    items = [df.Item(f"file-{n}", [f"{BASE}vm_cpu_readings-file-{n}-of-{a.files}.csv.gz"])
             for n in range(1, a.files + 1)]
    return sorted(items, key=lambda i: df.hash_int(i.name, 1))


SPEC = df.SourceSpec(
    name="azure",
    license_text="Azure Public Dataset: CC-BY-4.0 (data) and MIT (code) as listed on the repo; cite Cortez et al. SOSP 2017.",
    license_url="https://github.com/Azure/AzurePublicDataset",
    sampling_rule="none by rows; files taken in sha256(name) order until the disk cap, so a capped run is a uniform sample of the 125 files (a missing file is logged as failed)",
    list_items=_files,
    csv_columns=["timestamp", "vmid", "min_cpu", "max_cpu", "avg_cpu"],
    all_strings=True,
    add_args=_args,
)

if __name__ == "__main__":
    raise SystemExit(df.cli(SPEC))
