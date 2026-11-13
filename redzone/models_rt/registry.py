"""Model registry on the hash-chained results ledger: register a run, promote it, load only promoted models.

Why: WORKPLAN F-02 and TRAINING-PLAN section 6 rule 13 (2026-11-12): every run is one registry line
(run id, git sha, code/config/data/prereg hashes, seed, metrics, verdict, artifact + sha256, promoted,
GPU type, hours) and the app loads only promoted models, failing closed.

The rows live in the existing ledger (`scripts/results_ledger.py`, protected; used only through its public
append/verify API, never edited). Each registry row is an ordinary `kind: result` row with
`suite: model-registry` and one `registry` object:

    {"event": "register", "run_id": ..., <every REQUIRED field>, [optional fields]}
    {"event": "promote",  "run_id": ..., "artifact_sha256": ..., "note": ...}
    {"event": "demote",   "run_id": ..., "reason": ...}

A row is never edited: promotion and demotion are new rows, and a ledger `retract` row naming a register row
revokes that run for good. The state of a run is the fold of its rows in chain order.

    register(record)            # every REQUIRED field, promoted must be False, verdict PASS/FAIL/INCONCLUSIVE
    promote(run_id, note=...)   # only a PASS run whose artifact still matches its sha256
    demote(run_id, reason=...)
    resolve_promoted(run_id)    # verified chain + promoted + sha256 match -> absolute Path, else UnpromotedModelError
    read_promoted(run_id)       # the same checks, on the bytes actually returned
    load_forest(run_id)         # resolve_promoted, then models_rt.forest.load_npz

Nothing in the app calls this yet (flag-off / additive): existing model loaders are unchanged. Stdlib only
besides models_rt.forest (numpy) for load_forest.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.util
import math
import re
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
SUITE = "model-registry"
EVENTS = ("register", "promote", "demote")
REG_VERDICTS = ("PASS", "FAIL", "INCONCLUSIVE")
REQUIRED = ("run_id", "git_sha", "code_hash", "config_hash", "data_manifest_hash", "prereg_hash", "seed",
            "metrics", "verdict", "artifact_path", "artifact_sha256", "promoted", "gpu_type", "hours")
OPTIONAL = ("model", "training_cutoff", "license", "note")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HASH_FIELDS = ("code_hash", "config_hash", "data_manifest_hash", "prereg_hash", "artifact_sha256")


class RegistryError(Exception):
    """A registry row is malformed, a rule is broken, or the chain under it does not verify."""


class UnpromotedModelError(RegistryError):
    """The runtime asked for a model that is not promoted (or no longer matches what was promoted). Fail closed."""


def _ledger():
    """scripts/results_ledger.py, loaded by path (scripts/ is not a package). The one ledger implementation."""
    spec = importlib.util.spec_from_file_location("_rz_results_ledger", ROOT / "scripts" / "results_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


L = _ledger()
DEFAULT_DIR = L.DEFAULT_DIR


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_record(rec: dict) -> dict:
    """The register fields, checked. Raises RegistryError naming the first problem; returns a clean copy."""
    if not isinstance(rec, dict):
        raise RegistryError("a registry record is a JSON object")
    missing = [k for k in REQUIRED if k not in rec]
    if missing:
        raise RegistryError(f"record is missing required field(s): {', '.join(missing)}")
    extra = sorted(set(rec) - set(REQUIRED) - set(OPTIONAL) - {"event"})
    if extra:
        raise RegistryError(f"record has unknown field(s): {', '.join(extra)}")
    if not isinstance(rec["run_id"], str) or not _RUN_ID.fullmatch(rec["run_id"]):
        raise RegistryError(f"run_id {rec['run_id']!r}: 1-128 of [A-Za-z0-9._-], starting with a letter or digit")
    if not isinstance(rec["git_sha"], str) or not _GIT_SHA.fullmatch(rec["git_sha"]):
        raise RegistryError("git_sha must be a full 40-hex commit sha")
    for k in _HASH_FIELDS:
        if not isinstance(rec[k], str) or not _SHA256.fullmatch(rec[k]):
            raise RegistryError(f"{k} must be a 64-hex sha256")
    if not isinstance(rec["seed"], int) or isinstance(rec["seed"], bool):
        raise RegistryError("seed must be an integer")
    m = rec["metrics"]
    if not isinstance(m, dict) or not m:
        raise RegistryError("metrics must be a non-empty object")
    for k, v in m.items():
        if not isinstance(v, (str, bool)) and not _num(v):
            raise RegistryError(f"metric {k!r} must be a finite number, string or bool")
    if rec["verdict"] not in REG_VERDICTS:
        raise RegistryError(f"verdict {rec['verdict']!r} not in {REG_VERDICTS}")
    p = rec["artifact_path"]
    if not isinstance(p, str) or not p or "\\" in p:
        raise RegistryError("artifact_path must be a non-empty POSIX path")
    pp = PurePosixPath(p)
    if pp.is_absolute() or ".." in pp.parts or p.startswith("~"):
        raise RegistryError(f"artifact_path {p!r} must be relative to the repo root, without '..' (public repo)")
    if rec["promoted"] is not False:
        raise RegistryError("promoted must be false at registration; promotion is its own row (promote)")
    if not isinstance(rec["gpu_type"], str) or not rec["gpu_type"].strip():
        raise RegistryError("gpu_type must be a non-empty string ('cpu' when there was none)")
    if not _num(rec["hours"]) or rec["hours"] < 0:
        raise RegistryError("hours must be a finite number >= 0")
    for k in OPTIONAL:
        if k in rec and not isinstance(rec[k], str):
            raise RegistryError(f"{k} must be a string")
    return {k: rec[k] for k in (*REQUIRED, *OPTIONAL) if k in rec}


def fold(recs: list[dict]) -> dict[str, dict]:
    """run_id -> state, from verified ledger rows in chain order. Any malformed registry row raises (fail closed).
    State: the register fields, plus promoted (bool), promoted_sha256, revoked (bool), seqs (row numbers)."""
    retracted = {r.get("retracts") for r in recs if r.get("kind") == "retract"}
    runs: dict[str, dict] = {}
    for r in recs:
        if r.get("suite") != SUITE or r.get("kind") != "result":
            continue
        seq, ev = r["seq"], r.get("registry")
        if not isinstance(ev, dict) or ev.get("event") not in EVENTS:
            raise RegistryError(f"ledger row {seq}: a {SUITE} row needs a registry object with an event in {EVENTS}")
        rid, kind = ev.get("run_id"), ev["event"]
        if kind == "register":
            try:
                clean = validate_record(ev)
            except RegistryError as exc:
                raise RegistryError(f"ledger row {seq}: {exc}") from exc
            if rid in runs:
                raise RegistryError(f"ledger row {seq}: run {rid} is already registered (row {runs[rid]['seqs'][0]})")
            runs[rid] = {**clean, "promoted": False, "promoted_sha256": None, "revoked": seq in retracted,
                         "seqs": [seq]}
            continue
        if rid not in runs:
            raise RegistryError(f"ledger row {seq}: {kind} names run {rid!r}, which has no earlier register row")
        st = runs[rid]
        st["seqs"].append(seq)
        if seq in retracted:
            continue                                  # a retracted promote/demote row has no effect
        if kind == "promote":
            if st["verdict"] != "PASS":
                raise RegistryError(f"ledger row {seq}: run {rid} has verdict {st['verdict']}; only PASS is promoted")
            if ev.get("artifact_sha256") != st["artifact_sha256"]:
                raise RegistryError(f"ledger row {seq}: promote sha256 differs from the registered artifact")
            st["promoted"], st["promoted_sha256"] = True, ev["artifact_sha256"]
        else:
            if not ev.get("reason"):
                raise RegistryError(f"ledger row {seq}: a demote row must say why")
            st["promoted"], st["promoted_sha256"] = False, None
    return runs


def state(base: Path | None = None) -> dict[str, dict]:
    """Verify the whole chain (rows, HEAD, summary), then fold the registry rows."""
    base = DEFAULT_DIR if base is None else Path(base)
    try:
        recs = L.verify(base)
    except L.LedgerError as exc:
        raise RegistryError(f"ledger does not verify: {exc}") from exc
    return fold(recs)


@contextlib.contextmanager
def _registry_lock(base: Path):
    """Serialise check-then-append across processes. The ledger's own flock covers only the append, so the
    uniqueness check would race without this. The lock file lives in the temp dir, never in results/."""
    key = hashlib.sha256(str(Path(base).resolve()).encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"rz-model-registry-{key}.lock"
    with path.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _append(base: Path, git_sha: str, ev: dict) -> dict:
    try:
        return L.append(base, {"kind": "result", "suite": SUITE, "commit": git_sha, "registry": ev})
    except L.LedgerError as exc:
        raise RegistryError(f"ledger refused the row: {exc}") from exc


def register(record: dict, base: Path | None = None) -> dict:
    """Append one register row. Refuses a missing or bad field, a duplicate run_id, or promoted=True."""
    base = DEFAULT_DIR if base is None else Path(base)
    clean = validate_record(record)
    with _registry_lock(base):
        if clean["run_id"] in state(base):
            raise RegistryError(f"run {clean['run_id']} is already registered")
        return _append(base, clean["git_sha"], {"event": "register", **clean})


def promote(run_id: str, base: Path | None = None, root: Path | None = None, note: str = "") -> dict:
    """Append one promote row. Only a registered, unrevoked, PASS run whose artifact on disk matches its sha256."""
    base = DEFAULT_DIR if base is None else Path(base)
    with _registry_lock(base):
        runs = state(base)
        st = runs.get(run_id)
        if st is None:
            raise RegistryError(f"run {run_id!r} is not registered")
        if st["revoked"]:
            raise RegistryError(f"run {run_id} was retracted; it can never be promoted")
        if st["verdict"] != "PASS":
            raise RegistryError(f"run {run_id} has verdict {st['verdict']}; only PASS runs are promoted")
        if st["promoted"]:
            raise RegistryError(f"run {run_id} is already promoted")
        path = _artifact(st, root)
        if sha256_file(path) != st["artifact_sha256"]:
            raise RegistryError(f"run {run_id}: {st['artifact_path']} no longer matches its registered sha256")
        ev = {"event": "promote", "run_id": run_id, "artifact_sha256": st["artifact_sha256"]}
        if note:
            ev["note"] = note
        return _append(base, st["git_sha"], ev)


def demote(run_id: str, reason: str, base: Path | None = None) -> dict:
    """Append one demote row (a reason is required). The runtime refuses the model from then on."""
    base = DEFAULT_DIR if base is None else Path(base)
    if not reason:
        raise RegistryError("a demotion must say why")
    with _registry_lock(base):
        st = state(base).get(run_id)
        if st is None or not st["promoted"]:
            raise RegistryError(f"run {run_id!r} is not promoted")
        return _append(base, st["git_sha"], {"event": "demote", "run_id": run_id, "reason": reason})


def _artifact(st: dict, root: Path | None) -> Path:
    root = (ROOT if root is None else Path(root)).resolve()
    path = (root / st["artifact_path"]).resolve()
    if root not in path.parents:
        raise UnpromotedModelError(f"run {st['run_id']}: artifact path escapes the root")
    if not path.is_file():
        raise UnpromotedModelError(f"run {st['run_id']}: artifact {st['artifact_path']} is missing")
    return path


def _promoted(run_id: str, base: Path | None, root: Path | None) -> tuple[Path, str]:
    """(artifact path, promoted sha256) of a promoted, unrevoked run, else UnpromotedModelError."""
    try:
        runs = state(base)
    except RegistryError as exc:
        raise UnpromotedModelError(f"refusing model {run_id!r}: {exc}") from exc
    st = runs.get(run_id)
    if st is None:
        raise UnpromotedModelError(f"refusing model {run_id!r}: not in the registry")
    if st["revoked"]:
        raise UnpromotedModelError(f"refusing model {run_id!r}: its register row was retracted")
    if not st["promoted"]:
        raise UnpromotedModelError(f"refusing model {run_id!r}: not promoted (verdict {st['verdict']})")
    return _artifact(st, root), st["promoted_sha256"]


def resolve_promoted(run_id: str, base: Path | None = None, root: Path | None = None) -> Path:
    """The artifact of a promoted run, or UnpromotedModelError. Never falls back to another file or run."""
    path, sha = _promoted(run_id, base, root)
    if sha256_file(path) != sha:
        raise UnpromotedModelError(f"refusing model {run_id!r}: {path.name} changed since promotion")
    return path


def read_promoted(run_id: str, base: Path | None = None, root: Path | None = None) -> bytes:
    """The promoted artifact's bytes, hashed from the same read that is returned (no swap between check and use)."""
    path, sha = _promoted(run_id, base, root)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise UnpromotedModelError(f"refusing model {run_id!r}: {path.name} changed since promotion")
    return data


def load_forest(run_id: str, features=None, base: Path | None = None, root: Path | None = None):
    """(arrays, meta) of a promoted tree model via the shared loader; refuses anything unpromoted."""
    import io

    from redzone.models_rt.forest import load_npz
    return load_npz(io.BytesIO(read_promoted(run_id, base=base, root=root)), features)
