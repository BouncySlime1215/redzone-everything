"""Job runner CLI: write a JobSpec, print its Colab cell, check a result read back from the page, prove resume locally.

Why: WORKPLAN v2 F-03 (job runner for Colab and Kaggle; "kill-and-resume equals uninterrupted") and CONTROL.md
"COMPUTE VIA CHROME WORKS": the coordinator drives Colab through Chrome, so this CLI makes the text it pastes
and checks the text it reads back. Nothing here talks to Colab or Kaggle. See docs/jobs.md.

    python scripts/job.py spec --job-id toy-1 --module tools.jobs.toy --commit HEAD --seed 7 --steps 0 100 \
        --ckpt-every 10 --output state.json --output model.bin --output metrics.json --out spec.json
    python scripts/job.py cell spec.json [--embed]          # the one cell to paste (fetch variant by default)
    python scripts/job.py parse spec.json [PAGE.txt|-] [--files DIR] [--prev-line LINE]
    python scripts/job.py local-smoke                       # toy job: uninterrupted vs killed at 50 and resumed

Exit codes: parse 0 accepted ok, 3 accepted partial, 1 refused or failed; local-smoke 0 when the hashes match.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.jobs import cell as cellmod  # noqa: E402
from tools.jobs import parse as parsemod  # noqa: E402
from tools.jobs.spec import PUBLIC_REPO_URL, JobSpec, SpecError, known_manifest_hashes, load  # noqa: E402

TOY_OUTPUTS = ["metrics.json", "model.bin", "state.json"]


def git_out(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"git {args[0]} failed")
    return r.stdout.decode()


def worker_at(commit: str) -> bytes:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{cellmod.WORKER_PATH}"], capture_output=True,
                       timeout=60)
    if r.returncode != 0:
        raise SystemExit(f"commit {commit[:12]} has no {cellmod.WORKER_PATH}: pin a pushed commit that has it")
    return r.stdout


def toy_spec(commit: str, *, job_id: str = "toy-smoke", steps=(0, 100), every: int = 10, budget: float = 600,
             margin: float = 60) -> JobSpec:
    return JobSpec(job_id=job_id, repo_url=PUBLIC_REPO_URL, commit=commit, entry={"module": "tools.jobs.toy",
                   "args": []}, seed=7, steps=list(steps), ckpt_every=every, outputs=list(TOY_OUTPUTS),
                   budget_s=budget, stop_margin_s=margin, seed_libs=["random"])


def start_worker(worker: Path, spec_file: Path, work: Path, env: dict | None = None) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-u", str(worker), "--spec", str(spec_file), "--work", str(work),
                             "--repo", str(ROOT)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, **(env or {})})


def run_worker(worker: Path, spec_file: Path, work: Path, env: dict | None = None,
               kill_at_step: int | None = None) -> tuple[str, dict | None]:
    """(all output text, parsed RZ-RESULT or None). With kill_at_step the worker is SIGKILLed once it reports it."""
    p = start_worker(worker, spec_file, work, env)
    lines = []
    for line in p.stdout:
        lines.append(line)
        if kill_at_step is not None and line.startswith("RZ-PROGRESS "):
            if json.loads(line.split(" ", 1)[1]).get("step", 0) >= kill_at_step:
                p.send_signal(signal.SIGKILL)
                break
    p.stdout.close()
    p.wait(60)
    text = "".join(lines)
    try:
        return text, parsemod.last_result(text)[1]
    except parsemod.ResultError:
        return text, None


def local_smoke(tmp: Path, *, kill_at: int = 50, pause: str = "0.003") -> dict:
    """Run the toy job uninterrupted, then killed at `kill_at` and resumed; the worker is the cell's own bytes."""
    commit = git_out("rev-parse", "HEAD").strip()
    spec = toy_spec(commit).validate()
    worker_bytes = (ROOT / cellmod.WORKER_PATH).read_bytes()          # the working tree's worker (under test)
    decoded = cellmod.decode_cell(cellmod.build_cell(spec, worker_bytes, embed=True))
    assert decoded["worker"] == worker_bytes and json.loads(decoded["spec_json"]) == spec.to_dict()
    worker = tmp / "worker.py"
    worker.write_bytes(decoded["worker"])
    spec_file = tmp / "spec.json"
    spec_file.write_text(decoded["spec_json"])
    env = {"RZ_TOY_SLEEP_S": pause}
    _t, whole = run_worker(worker, spec_file, tmp / "whole", env)
    killed_text, killed = run_worker(worker, spec_file, tmp / "resumed", env, kill_at_step=kill_at)
    text, resumed = run_worker(worker, spec_file, tmp / "resumed", env)
    first = next((json.loads(ln.split(" ", 1)[1]) for ln in text.splitlines() if ln.startswith("RZ-PROGRESS ")), None)
    return {"uninterrupted": (whole or {}).get("outputs_sha256"), "resumed": (resumed or {}).get("outputs_sha256"),
            "uninterrupted_status": (whole or {}).get("status"), "resumed_status": (resumed or {}).get("status"),
            "killed_result_line": killed is not None, "resumed_from": (first or {}).get("step"),
            "resumed_chain_prev": (resumed or {}).get("prev"),
            "equal": bool(whole and resumed and whole.get("outputs_sha256") == resumed.get("outputs_sha256")
                          and whole["status"] == resumed["status"] == "ok")}


def cmd_spec(a) -> int:
    commit = git_out("rev-parse", a.commit).strip() if a.commit == "HEAD" else a.commit
    config = json.loads(Path(a.config).read_text()) if a.config else {}
    lock = None
    if a.lock:
        import hashlib
        lock = {"path": a.lock, "sha256": hashlib.sha256((ROOT / a.lock).read_bytes()).hexdigest()}
    spec = JobSpec(job_id=a.job_id, repo_url=a.repo_url, commit=commit, entry={"module": a.module, "args": a.arg},
                   seed=a.seed, steps=list(a.steps), ckpt_every=a.ckpt_every, outputs=a.output, budget_s=a.budget_s,
                   stop_margin_s=a.margin_s, config=config, data=a.data, lock=lock,
                   metrics_file=a.metrics_file if a.metrics_file != "none" else None)
    try:
        spec.validate(known_manifest_hashes(a.manifests))
    except SpecError as e:
        print(e, file=sys.stderr)
        return 2
    text = json.dumps(spec.to_dict(), indent=1, sort_keys=True) + "\n"
    if a.out:
        Path(a.out).write_text(text)
        print(f"wrote {a.out} spec {spec.spec_hash()}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_cell(a) -> int:
    spec = load(a.spec, frozenset(json.loads(Path(a.spec).read_text()).get("data", [])))
    text = cellmod.build_cell(spec, worker_at(spec.commit), embed=a.embed)
    sys.stdout.write(text)
    print(f"[{len(text)} characters, spec {spec.spec_hash()[:12]}]", file=sys.stderr)
    return 0


def cmd_parse(a) -> int:
    spec = load(a.spec, frozenset(json.loads(Path(a.spec).read_text()).get("data", [])))
    text = sys.stdin.read() if a.page in (None, "-") else Path(a.page).read_text(encoding="utf-8", errors="replace")
    try:
        rep = parsemod.check_text(text, spec, files_dir=a.files, prev_line=a.prev_line)
    except parsemod.ResultError as e:
        print(json.dumps({"accepted": False, "problems": [str(e)],
                          "progress": parsemod.last_progress(text)}, indent=1, sort_keys=True))
        return 1
    print(json.dumps(rep, indent=1, sort_keys=True))
    if not rep["accepted"] or rep["status"] == "failed":
        return 1
    return 3 if rep["status"] == "partial" else 0


def cmd_local_smoke(_a) -> int:
    with tempfile.TemporaryDirectory(prefix="rz-job-smoke-") as tmp:
        rep = local_smoke(Path(tmp))
    print(json.dumps(rep, indent=1, sort_keys=True))
    return 0 if rep["equal"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("spec")
    s.add_argument("--job-id", required=True)
    s.add_argument("--module", required=True)
    s.add_argument("--arg", action="append", default=[])
    s.add_argument("--commit", required=True, help="full 40-hex sha, or HEAD (resolved here)")
    s.add_argument("--repo-url", default=PUBLIC_REPO_URL)
    s.add_argument("--seed", type=int)
    s.add_argument("--steps", type=int, nargs=2, required=True)
    s.add_argument("--ckpt-every", type=int, required=True)
    s.add_argument("--output", action="append", default=[])
    s.add_argument("--metrics-file", default="metrics.json", help="'none' for no metrics file")
    s.add_argument("--data", action="append", default=[], help="a manifest_sha256 the job reads")
    s.add_argument("--manifests", help="manifests folder (default: datamanifest.default_manifest_dir())")
    s.add_argument("--config", help="a JSON file")
    s.add_argument("--lock", help="repo-relative hashed lock file (pip --require-hashes)")
    s.add_argument("--budget-s", type=float, default=6 * 3600)
    s.add_argument("--margin-s", type=float, default=900)
    s.add_argument("--out")
    c = sub.add_parser("cell")
    c.add_argument("spec")
    c.add_argument("--embed", action="store_true", help="put the worker inside the cell (no fetch; larger)")
    p = sub.add_parser("parse")
    p.add_argument("spec")
    p.add_argument("page", nargs="?")
    p.add_argument("--files")
    p.add_argument("--prev-line")
    sub.add_parser("local-smoke")
    a = ap.parse_args(argv)
    return {"spec": cmd_spec, "cell": cmd_cell, "parse": cmd_parse, "local-smoke": cmd_local_smoke}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
