"""Kaggle backend STUB: build kernel-metadata.json and the one-file script bundle for `kaggle kernels push`.

UNTESTED against the real service and UNUSED: there is no Kaggle CLI or API token on the Mac, and creating one
is Nick's call (WORKPLAN v2 N1). Until then the coordinator runs Kaggle by hand in Chrome, or uses Colab.
Unit tests drive it with a fake runner only; nothing here reads a token or calls the network.

Why: WORKPLAN v2 F-03 ("push, poll, fetch" on Kaggle and Colab) and the harness research note
(kernel-metadata fields; `kaggle kernels push -p DIR`, `status`, `output -p DIR`; status text varies by CLI
version; internet must be on to clone; /kaggle/working is the writable output folder).

    files = build_bundle(spec, worker_bytes, owner="<kaggle user>", out_dir=DIR)   # kernel-metadata.json + run.py
    run(push_cmd(DIR), runner)                   # runner(list[str]) -> (exit code, text); no default on purpose
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from tools.jobs.cell import build_cell
from tools.jobs.spec import JobSpec

KAGGLE_WORK_ROOT = "/kaggle/working/rz_job"
_OWNER = re.compile(r"^[a-z0-9][a-z0-9-]{1,49}$")
Runner = Callable[[list], tuple]


def kernel_slug(spec: JobSpec) -> str:
    return "rz-" + re.sub(r"[^a-z0-9-]+", "-", spec.job_id).strip("-")[:46]


def kernel_metadata(spec: JobSpec, owner: str, *, gpu: bool = False, machine_shape: str | None = None) -> dict:
    if not _OWNER.match(owner):
        raise ValueError("owner must be a Kaggle user name")
    md = {"id": f"{owner}/{kernel_slug(spec)}", "title": kernel_slug(spec), "code_file": "run.py",
          "language": "python", "kernel_type": "script", "is_private": True, "enable_gpu": bool(gpu),
          "enable_internet": True, "dataset_sources": [], "competition_sources": [], "kernel_sources": []}
    if machine_shape:
        md["machine_shape"] = machine_shape
    return md


def build_bundle(spec: JobSpec, worker: bytes, owner: str, out_dir: str | Path, **kw) -> list[Path]:
    """Write kernel-metadata.json and run.py (the embedded-worker cell: a push uploads only the code file)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = out / "kernel-metadata.json"
    md.write_text(json.dumps(kernel_metadata(spec, owner, **kw), indent=1, sort_keys=True) + "\n")
    run_py = out / "run.py"
    run_py.write_text(build_cell(spec, worker, embed=True, work_root=KAGGLE_WORK_ROOT))
    return [md, run_py]


def push_cmd(bundle_dir: str | Path) -> list:
    return ["kaggle", "kernels", "push", "-p", str(bundle_dir)]


def status_cmd(kernel_id: str) -> list:
    return ["kaggle", "kernels", "status", kernel_id]


def output_cmd(kernel_id: str, dest: str | Path) -> list:
    return ["kaggle", "kernels", "output", kernel_id, "-p", str(dest), "-o"]


def parse_status(text: str) -> str:
    """'complete' | 'running' | 'queued' | 'error' | 'unknown' from either CLI spelling (COMPLETE, KernelWorkerStatus.X)."""
    t = text.lower()
    for word, status in (("error", "error"), ("fail", "error"), ("cancel", "error"), ("complete", "complete"),
                         ("running", "running"), ("queued", "queued")):
        if word in t:
            return status
    return "unknown"


def run(cmd: list, runner: Runner) -> tuple:
    """Hand the command to a runner the caller supplies (tests: a fake). There is deliberately no default runner."""
    if runner is None:
        raise RuntimeError("no runner: the Kaggle backend is unused until Nick provides a Kaggle API token")
    return runner(list(cmd))
