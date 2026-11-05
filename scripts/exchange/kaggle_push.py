#!/usr/bin/env python3
"""Kaggle adapter for the compute exchange (item d): push a notebook as a Kaggle kernel, or the shard dir as a
private Kaggle dataset (the mirror of the HF dataset). Runs where the Kaggle CLI has Nick's credentials (his Mac or
a Colab/Kaggle secret); this script never reads or prints them.

    python scripts/exchange/kaggle_push.py kernel colab/nfm_tpu_kaggle.ipynb --slug rz-nfm-ladder --tpu
    python scripts/exchange/kaggle_push.py dataset SHARDS_DIR --slug rz-shards-v2

Both are private, and the dataset's description carries the personal-use tag. Kaggle secrets (HF_TOKEN) are
attached by Nick in the kernel's Add-ons > Secrets; the notebook reads them via kaggle_secrets, never inline.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PERSONAL = "personal-use, not for commercial release"


def kernel_meta(user: str, slug: str, nb: str, tpu: bool, gpu: bool, datasets: list[str]) -> dict:
    return {"id": f"{user}/{slug}", "title": slug, "code_file": nb, "language": "python", "kernel_type": "notebook",
            "is_private": True, "enable_gpu": gpu and not tpu, "enable_tpu": tpu, "enable_internet": True,
            "dataset_sources": datasets, "competition_sources": [], "kernel_sources": []}


def dataset_meta(user: str, slug: str) -> dict:
    return {"title": slug, "id": f"{user}/{slug}", "licenses": [{"name": "other"}],
            "subtitle": "RedZone token shards (private)", "description": f"Token shards. {PERSONAL}. Never redistribute."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("kernel", "dataset"))
    ap.add_argument("path", type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--user", default="", help="Kaggle username (default: from the CLI config)")
    ap.add_argument("--tpu", action="store_true")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--dataset", action="append", default=[], help="attach <user>/<slug> datasets to a kernel")
    ap.add_argument("--dry-run", action="store_true", help="write the metadata only")
    a = ap.parse_args(argv)
    if not shutil.which("kaggle") and not a.dry_run:
        raise SystemExit("kaggle CLI not found (pip install kaggle; Nick's credentials, never typed here)")
    user = a.user or (subprocess.run(["kaggle", "config", "view"], capture_output=True, text=True).stdout
                      .split("username:")[-1].split()[0] if not a.dry_run else "USER")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        if a.cmd == "kernel":
            shutil.copy(a.path, d / a.path.name)
            (d / "kernel-metadata.json").write_text(json.dumps(
                kernel_meta(user, a.slug, a.path.name, a.tpu, a.gpu, a.dataset), indent=1))
            cmd = ["kaggle", "kernels", "push", "-p", str(d)]
        else:
            shutil.copytree(a.path, d / "data")
            (d / "data" / "dataset-metadata.json").write_text(json.dumps(dataset_meta(user, a.slug), indent=1))
            cmd = ["kaggle", "datasets", "create", "-p", str(d / "data"), "-r", "tar"]
        if a.dry_run:
            print(" ".join(cmd))
            print((d / ("kernel-metadata.json" if a.cmd == "kernel" else "data/dataset-metadata.json")).read_text())
            return 0
        r = subprocess.run(cmd, text=True)
        if r.returncode and a.cmd == "dataset":         # exists already: new version
            r = subprocess.run(["kaggle", "datasets", "version", "-p", str(d / "data"), "-r", "tar",
                                "-m", f"update [{PERSONAL}]"], text=True)
        return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
