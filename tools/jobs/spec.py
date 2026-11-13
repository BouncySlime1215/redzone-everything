"""JobSpec: one training work unit for the job runner, JSON-serialisable with a canonical sha256.

Why: WORKPLAN v2 F-03 and TRAINING-PLAN section 2 ("a work unit is (config, seed, step range)"; pin code by
commit and data by manifest hash). A spec is refused, never repaired: an abbreviated or unpinned commit, a
missing seed or a data hash that no verified manifest (redzone/lab/datamanifest.py, F-05) carries.

    s = JobSpec(job_id="toy-1", repo_url=PUBLIC_REPO_URL, commit=<40 hex>, entry={"module": "tools.jobs.toy"},
                seed=7, steps=[0, 100], ckpt_every=10, outputs=["state.json"], budget_s=3600, stop_margin_s=600)
    s.validate(known_manifest_hashes())     # SpecError listing every problem
    s.spec_hash()                           # sha256 of canonical JSON; equals worker.spec_hash(s.to_dict())

The worker (tools/jobs/worker.py) repeats a subset of these checks with stdlib only, because it runs before the
repo exists on the VM. Config, args and outputs are scrubbed like a manifest (no absolute paths, addresses,
URLs or credentials) because the spec is pasted into a notebook page.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from redzone.lab import datamanifest

SPEC_FORMAT = "redzone-jobspec/1"
PUBLIC_REPO_URL = "https://github.com/BouncySlime1215/redzone-everything.git"
DEFAULT_SEED_LIBS = ("random", "numpy", "torch")
_JOB_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_HTTPS = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._-]+)+$")
_REL_PATH = re.compile(r"^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$")


class SpecError(ValueError):
    """The spec is refused; the message lists every problem."""


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass
class JobSpec:
    job_id: str
    repo_url: str
    commit: str
    entry: dict
    seed: int | None
    steps: list
    ckpt_every: int
    outputs: list
    budget_s: float
    stop_margin_s: float
    config: dict = field(default_factory=dict)
    data: list = field(default_factory=list)
    metrics_file: str | None = "metrics.json"
    lock: dict | None = None
    seed_libs: list = field(default_factory=lambda: list(DEFAULT_SEED_LIBS))
    format: str = SPEC_FORMAT

    def to_dict(self) -> dict:
        return json.loads(canonical(asdict(self)))

    def to_json(self) -> str:
        return canonical(self.to_dict())

    def spec_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @classmethod
    def from_dict(cls, d: dict) -> JobSpec:
        names = set(cls.__dataclass_fields__)
        extra = sorted(set(d) - names)
        if extra:
            raise SpecError(f"unknown spec fields: {extra}")
        return cls(**d)

    def problems(self, known_data: set | frozenset) -> list[str]:
        p: list[str] = []
        if self.format != SPEC_FORMAT:
            p.append(f"format must be {SPEC_FORMAT}")
        if not isinstance(self.job_id, str) or not _JOB_ID.match(self.job_id):
            p.append("job_id: 3-64 characters of a-z 0-9 . _ -")
        if not isinstance(self.repo_url, str) or not _HTTPS.match(self.repo_url):
            p.append("repo_url must be a plain https URL with no user, password, port or query")
        if not isinstance(self.commit, str) or not _COMMIT.match(self.commit):
            p.append("commit must be a full 40-hex lowercase sha (no branch, tag or abbreviation)")
        e = self.entry
        if not (isinstance(e, dict) and set(e) <= {"module", "args"} and isinstance(e.get("module"), str)
                and _MODULE.match(e["module"]) and isinstance(e.get("args", []), list)
                and all(isinstance(a, str) and "\n" not in a and "\0" not in a for a in e.get("args", []))):
            p.append("entry must be {module: dotted.name, args: [str, ...]} (no shell)")
        if self.seed is None or type(self.seed) is not int or not 0 <= self.seed < 2**63:
            p.append("seed is required: a non-negative integer")
        st = self.steps
        if not (isinstance(st, list) and len(st) == 2 and all(type(x) is int for x in st) and 0 <= st[0] < st[1]):
            p.append("steps must be [start, end) with 0 <= start < end")
        if type(self.ckpt_every) is not int or self.ckpt_every < 1:
            p.append("ckpt_every must be a positive integer")
        if not self.outputs or not all(isinstance(o, str) and _REL_PATH.match(o) and ".." not in o.split("/")
                                       and o not in ("CKPT.json",) for o in self.outputs):
            p.append("outputs must be relative paths of a-z A-Z 0-9 . _ - /")
        elif len(set(self.outputs)) != len(self.outputs):
            p.append("outputs repeat a path")
        if self.metrics_file is not None and self.metrics_file not in (self.outputs or []):
            p.append("metrics_file must be one of the outputs (or null)")
        nums = (self.budget_s, self.stop_margin_s)
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in nums):
            p.append("budget_s and stop_margin_s must be numbers")
        elif not 0 < self.stop_margin_s < self.budget_s:
            p.append("need 0 < stop_margin_s < budget_s")
        if not isinstance(self.data, list) or not all(isinstance(h, str) and _HASH.match(h) for h in self.data):
            p.append("data must be a list of 64-hex manifest hashes")
        else:
            unknown = [h[:12] for h in self.data if h not in known_data]
            if unknown:
                p.append(f"data hashes not in any verified manifest: {unknown}")
        if self.lock is not None and not (isinstance(self.lock, dict) and set(self.lock) == {"path", "sha256"}
                                          and isinstance(self.lock["path"], str) and _REL_PATH.match(self.lock["path"])
                                          and isinstance(self.lock["sha256"], str) and _HASH.match(self.lock["sha256"])):
            p.append("lock must be {path: relative lock file, sha256: 64 hex}")
        if not isinstance(self.seed_libs, list) or not set(self.seed_libs) <= set(DEFAULT_SEED_LIBS):
            p.append(f"seed_libs must be a subset of {list(DEFAULT_SEED_LIBS)}")
        if not isinstance(self.config, dict):
            p.append("config must be a dict")
        else:
            try:
                canonical(self.config)
            except (TypeError, ValueError):
                p.append("config is not JSON-serialisable")
            else:
                args = e.get("args", []) if isinstance(e, dict) else []
                p.extend(datamanifest.scrub_problems({"config": self.config, "args": args,
                                                      "outputs": self.outputs}))
        return p

    def validate(self, known_data: set | frozenset = frozenset()) -> JobSpec:
        bad = self.problems(known_data)
        if bad:
            raise SpecError("job spec refused: " + "; ".join(bad))
        return self


def known_manifest_hashes(directory: str | Path | None = None) -> frozenset:
    """The manifest_sha256 of every manifest in the folder whose stored hash re-computes; tampered ones are left out."""
    d = Path(directory) if directory else datamanifest.default_manifest_dir()
    out = set()
    if not d.is_dir():
        return frozenset()
    for p in sorted(d.glob("*" + datamanifest.MANIFEST_SUFFIX)):
        try:
            doc = datamanifest.read_manifest(p)
        except datamanifest.ManifestError:
            continue
        h = doc.get("manifest_sha256")
        if isinstance(h, str) and h == datamanifest.manifest_hash(doc):
            out.add(h)
    return frozenset(out)


def load(path: str | Path, known_data: set | frozenset = frozenset()) -> JobSpec:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SpecError(f"cannot read spec {Path(path).name}: {type(e).__name__}") from e
    if not isinstance(d, dict):
        raise SpecError("a spec file holds one JSON object")
    return JobSpec.from_dict(d).validate(known_data)
