"""Atomic, hash-checked checkpoints with a LATEST pointer and fallback to the previous good one.

Why: TRAINING-PLAN section 2, job runner item 4 and "Resume design": results are written atomically (temporary
file, flush, rename); LATEST.json holds the checkpoint's sha256; a corrupt checkpoint falls back to the previous.

A checkpoint is a folder `step_<00000123>/` holding the backend's file(s), `state.json` (loop state, RNG, the
run's identity and its training cutoff) and `MANIFEST.json` (sha256 of each file). LATEST.json names the newest.
Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

CKPT_FORMAT = "redzone-trainer-ckpt/1"
CKPT_PREFIX = "step_"


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode())


def ckpt_name(step: int) -> str:
    return f"{CKPT_PREFIX}{step:08d}"


def save(ckpt_dir: Path, step: int, state: dict, write_backend, keep: int = 2) -> Path:
    """Write checkpoint `step`: write_backend(folder) writes the backend files, then state.json, then the
    MANIFEST, then LATEST.json (the commit point). Older checkpoints beyond `keep` are removed afterwards."""
    ckpt_dir = Path(ckpt_dir)
    final = ckpt_dir / ckpt_name(step)
    work = ckpt_dir / (ckpt_name(step) + ".partial")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    write_backend(work)
    atomic_write_json(work / "state.json", {"format": CKPT_FORMAT, **state})
    files = {p.name: sha256_path(p) for p in sorted(work.iterdir()) if p.is_file()}
    atomic_write_json(work / "MANIFEST.json", {"format": CKPT_FORMAT, "step": step, "files": files})
    if final.exists():
        shutil.rmtree(final)
    os.replace(work, final)
    atomic_write_json(ckpt_dir / "LATEST.json", {"format": CKPT_FORMAT, "step": step, "dir": final.name,
                                                 "manifest_sha256": sha256_path(final / "MANIFEST.json")})
    for old in sorted(p for p in ckpt_dir.iterdir() if p.is_dir() and p.name.startswith(CKPT_PREFIX)
                      and not p.name.endswith(".partial"))[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
    return final


def verify(folder: Path) -> list[str]:
    """Problems with one checkpoint folder; [] when every file re-hashes to its MANIFEST."""
    folder = Path(folder)
    try:
        man = json.loads((folder / "MANIFEST.json").read_text())
    except (OSError, ValueError) as e:
        return [f"{folder.name}: MANIFEST unreadable ({type(e).__name__})"]
    out = []
    for name, sha in man.get("files", {}).items():
        p = folder / name
        if not p.is_file():
            out.append(f"{folder.name}/{name}: missing")
        elif sha256_path(p) != sha:
            out.append(f"{folder.name}/{name}: sha256 mismatch")
    if "state.json" not in man.get("files", {}):
        out.append(f"{folder.name}: no state.json")
    return out


def latest(ckpt_dir: Path) -> tuple[Path | None, list[str]]:
    """(the newest checkpoint folder that verifies, notes about any that did not). LATEST.json first, then the
    other step folders newest first. (None, notes) when there is nothing to resume."""
    ckpt_dir = Path(ckpt_dir)
    notes: list[str] = []
    if not ckpt_dir.is_dir():
        return None, notes
    order: list[Path] = []
    try:
        lt = json.loads((ckpt_dir / "LATEST.json").read_text())
        p = ckpt_dir / lt["dir"]
        if sha256_path(p / "MANIFEST.json") != lt.get("manifest_sha256"):
            notes.append(f"LATEST.json names {lt['dir']} but its MANIFEST hash differs")
        else:
            order.append(p)
    except (OSError, ValueError, KeyError) as e:
        if (ckpt_dir / "LATEST.json").exists():
            notes.append(f"LATEST.json unreadable ({type(e).__name__})")
    order += sorted((p for p in ckpt_dir.iterdir() if p.is_dir() and p.name.startswith(CKPT_PREFIX)
                     and not p.name.endswith(".partial") and p not in order), reverse=True)
    for p in order:
        bad = verify(p)
        if not bad:
            return p, notes
        notes += bad
    return None, notes


def read_state(folder: Path) -> dict:
    st = json.loads((Path(folder) / "state.json").read_text())
    if st.get("format") != CKPT_FORMAT:
        raise ValueError(f"{folder}: not a {CKPT_FORMAT} checkpoint")
    return st
