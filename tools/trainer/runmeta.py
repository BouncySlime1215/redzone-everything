"""Run identity for the registry: git sha, code/config/data/prereg hashes, and fence-stamped model metadata.

Why: TRAINING-PLAN section 6 (one registry line per run: run_id, git_sha, code_hash, config_hash,
data_manifest_hash, prereg_hash, seed, metrics, verdict, artifact, gpu_type, hours) and rule 13 (model files carry
training cutoff, data manifest hash and licence). The record is checked with the F-02 registry's own validator
(redzone/models_rt/registry.py) and the model metadata with the F-09 fence (redzone/lab/fence.py), so a trainer
output that the registry or the fence would refuse fails here, before the GPU hours are spent.

The trainer never writes the ledger: the F-03 runner registers the record after the unit's results are pushed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from tools.trainer.data import TRAINER_REPO, repo_module

# Files whose bytes define what the trainer does; their joint hash is the run's code_hash.
CODE_GLOBS = ("tools/trainer/*.py", "scripts/noggin_tokens.py", "redzone/lab/fence.py", "redzone/lab/datamanifest.py")
EXPLORATORY_PREREG = hashlib.sha256(b"exploratory: no pre-registration").hexdigest()
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(canonical(cfg).encode()).hexdigest()


def code_hash(root: Path = TRAINER_REPO) -> str:
    """sha256 over (relative path, sha256 of bytes) of every code file in CODE_GLOBS, sorted by path."""
    root = Path(root)
    rows = []
    for g in CODE_GLOBS:
        for p in sorted(root.glob(g)):
            rows.append(f"{p.relative_to(root).as_posix()} {hashlib.sha256(p.read_bytes()).hexdigest()}")
    if not rows:
        raise FileNotFoundError("no trainer code found to hash")
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


def git_sha(root: Path = TRAINER_REPO) -> str | None:
    """$RZ_GIT_SHA (set by the job runner when the code ships without .git), else `git rev-parse HEAD`."""
    env = os.environ.get("RZ_GIT_SHA", "").strip().lower()
    if _HEX40.match(env):
        return env
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if _HEX40.match(out) else None


def prereg_hash(path) -> tuple[str, bool]:
    """(sha256 of the pre-registration file, True) or (the fixed exploratory hash, False) when there is none.
    An exploratory run cannot unlock a flag (TRAINING-PLAN rule 1)."""
    if path in (None, ""):
        return EXPLORATORY_PREREG, False
    return hashlib.sha256(Path(path).read_bytes()).hexdigest(), True


def model_meta(*, training_cutoff: str, train_nights: list, data_manifest_hash: str, cfg_hash: str, code: str,
               git: str | None, seed: int, step_range: list, license: str, extra: dict | None = None) -> dict:
    """The metadata stamped into every checkpoint and the exported model (fence.read_meta reads it)."""
    fence = repo_module("redzone.lab.fence")
    nights = sorted({fence.night_key(n) for n in train_nights})
    cut = fence.night_key(training_cutoff)
    iso = f"{cut[:4]}-{cut[4:6]}-{cut[6:]}"
    meta = {"training_cutoff": iso, "train_nights": nights, "train_nights_sha256": fence.nights_sha256(nights),
            "data_manifest_hash": data_manifest_hash, "config_hash": cfg_hash, "code_hash": code, "git_sha": git,
            "seed": int(seed), "step_range": [int(step_range[0]), int(step_range[1])], "license": license}
    meta.update(extra or {})
    return meta


def check_model_meta(meta: dict, eval_nights=()) -> None:
    """fence.guard_model on the metadata: FenceError ('REFUSED ...') if the model may not be scored on eval_nights."""
    repo_module("redzone.lab.fence").guard_model(meta, eval_nights, name="trainer output")


def registry_record(*, run_id: str, meta: dict, prereg: str, metrics: dict, verdict: str, artifact_path: str,
                    artifact_sha256: str, gpu_type: str, hours: float, model: str, note: str = "") -> dict:
    """A registry `register` record (F-02 REQUIRED fields + training_cutoff, model, license, note), validated by
    redzone.models_rt.registry.validate_record. promoted is always False: promotion is the registry's own row."""
    rec = {"run_id": run_id, "git_sha": meta.get("git_sha"), "code_hash": meta["code_hash"],
           "config_hash": meta["config_hash"], "data_manifest_hash": meta["data_manifest_hash"],
           "prereg_hash": prereg, "seed": int(meta["seed"]), "metrics": metrics, "verdict": verdict,
           "artifact_path": artifact_path, "artifact_sha256": artifact_sha256, "promoted": False,
           "gpu_type": gpu_type, "hours": float(hours), "model": model,
           "training_cutoff": meta["training_cutoff"], "license": meta.get("license") or "", "note": note}
    if rec["git_sha"] is None:
        raise ValueError("no git sha: run inside the checkout or set RZ_GIT_SHA (the job runner does)")
    return repo_module("redzone.models_rt.registry").validate_record(rec)
