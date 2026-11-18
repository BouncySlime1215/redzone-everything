"""Read a job's result back from the page text the coordinator copies out of Colab, and check it against the spec.

Why: WORKPLAN v2 F-03 ("fetch, write registry line") and TRAINING-PLAN rule 11 (numbers a runner transcribes by
hand are only "claimed"): the coordinator reads the notebook output through the Chrome tools, so the one
`RZ-RESULT {json}` line the worker prints is the whole hand-off. It is accepted only when its own line hash
holds (no truncation or edit on the way), its spec hash is this spec's, its outputs are exactly the expected
ones and their combined hash re-computes, and, when the files are at hand, every file re-hashes to the line.

    rep = check_text(page_text, spec, files_dir=None, prev_line=None)   # {'status', 'accepted', 'problems', ...}
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from tools.jobs.spec import JobSpec, canonical

RESULT_TAG = "RZ-RESULT "
PROGRESS_TAG = "RZ-PROGRESS "
STATUSES = ("ok", "partial", "failed")


class ResultError(ValueError):
    """The page holds no readable result line."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tagged(text: str, tag: str) -> list[tuple[str, dict]]:
    out = []
    dec = json.JSONDecoder()
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith(tag):
            continue
        try:
            obj, _end = dec.raw_decode(line[len(tag):])
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append((line, obj))
    return out


def last_result(text: str) -> tuple[str, dict]:
    """(line, parsed json) of the LAST readable RZ-RESULT line; ResultError when there is none."""
    found = _tagged(text, RESULT_TAG)
    if not found:
        raise ResultError("no RZ-RESULT line in the page text (still running, or the output was cut)")
    return found[-1]


def last_progress(text: str) -> dict | None:
    found = _tagged(text, PROGRESS_TAG)
    return found[-1][1] if found else None


def problems(res: dict, spec: JobSpec, files_dir: str | Path | None = None, prev_line: str | None = None) -> list[str]:
    p = []
    body = {k: v for k, v in res.items() if k != "line"}
    if res.get("line") != _sha(canonical(body).encode()):
        p.append("line hash does not match: the result was cut or edited on the way")
    if res.get("spec") != spec.spec_hash():
        p.append("spec hash does not match this spec")
    status = res.get("status")
    if status not in STATUSES:
        p.append(f"unknown status {status!r}")
    if prev_line is not None and res.get("prev") != _sha(prev_line.strip().encode()):
        p.append("chain: prev does not hash the given previous result line")
    if status == "ok":
        outs = res.get("outputs")
        if not isinstance(outs, dict) or sorted(outs) != sorted(spec.outputs):
            p.append("outputs are not exactly the spec's expected outputs")
            outs = {}
        if res.get("outputs_sha256") != _sha(canonical(outs).encode()):
            p.append("outputs hash does not re-compute from the per-output hashes")
        mf = spec.metrics_file
        if mf and res.get("metrics_sha256") != (outs.get(mf) or [None])[0]:
            p.append("metrics hash is not the metrics file's hash")
        if files_dir is not None:
            for name, (want, size) in sorted(outs.items()):
                f = os.path.join(files_dir, name)
                if not os.path.isfile(f):
                    p.append(f"output {name} is not in the files folder")
                elif os.path.getsize(f) != size or _sha(Path(f).read_bytes()) != want:
                    p.append(f"output {name} does not hash to the result")
    return p


def check_text(text: str, spec: JobSpec, files_dir: str | Path | None = None, prev_line: str | None = None) -> dict:
    line, res = last_result(text)
    bad = problems(res, spec, files_dir, prev_line)
    return {"accepted": not bad, "status": res.get("status"), "problems": bad, "job": res.get("job"),
            "step": res.get("step"), "outputs_sha256": res.get("outputs_sha256"), "gpu": res.get("gpu"),
            "git": res.get("git"), "error": res.get("error"), "line_sha256": _sha(line.encode()),
            "progress": last_progress(text)}
