"""The job worker that runs INSIDE Colab or Kaggle: clone at the pinned sha, run the entry in resumable units, report.

Why: WORKPLAN v2 F-03 and TRAINING-PLAN section 2 (resumable units, atomic results, self-stop before the session
cap, forkserver, "kill and resume equals uninterrupted"). Stdlib only: it runs before the repo exists on the VM,
so it cannot import tools.jobs.spec; its spec hash and checks repeat that module's (a test keeps them equal).

    python worker.py --spec spec.json --work DIR [--repo CHECKOUT] [--dry-run]

Unit protocol (what an entry command sees; one subprocess per unit of `ckpt_every` steps):
    RZ_JOB_STEP_START / RZ_JOB_STEP_END   the unit's step range [start, end)
    RZ_JOB_STATE                          the last committed checkpoint folder ('' on the first unit); read only
    RZ_JOB_NEXT                           an empty folder: write the new state and the outputs here
    RZ_JOB_CONFIG, RZ_JOB_SEED            the config file and the job seed; RZ_JOB_UNIT_SEED seeds this unit
The worker commits RZ_JOB_NEXT as ckpt/step_<end> (CKPT.json with every file's sha256, fsync, rename) only after
the unit exits 0 (under ckpt/<spec hash>/, so specs never mix), so a crash or kill at any point leaves the previous checkpoint whole. Python's random, NumPy and
torch (when listed in `seed_libs`) are seeded per unit in the child; multiprocessing uses forkserver; a child
whose worker dies exits by itself. At the end the expected outputs are copied to out/, out/MANIFEST.json is
written, then DONE. Self-stop: no unit starts that the slowest unit so far would carry past budget_s minus
stop_margin_s, and a unit still running at that deadline (or on SIGTERM) is stopped and discarded.

Output lines: `RZ-PROGRESS {json}` at each checkpoint, then exactly one final `RZ-RESULT {json}` (status ok,
partial or failed; per-output sha256 and bytes; outputs and metrics hashes; `prev` = sha256 of the previous
result line in this work folder; `line` = sha256 of the rest). Exit 0 ok, 4 partial, 1 failed, 2 refused.
No secret is read or printed: the clone URL must be plain https (or file:// for local tests), git never prompts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time

FMT = "redzone-jobspec/1"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
CLONE_URL = re.compile(r"^(https://[A-Za-z0-9.-]+(/[A-Za-z0-9._-]+)+|file:///[A-Za-z0-9._/-]+)$")
LIBS = ("numpy", "torch", "lightgbm", "pandas")
EXIT = {"ok": 0, "partial": 4, "failed": 1, "refused": 2}
BOOT = """import os,sys,time,random,runpy,threading,importlib.util as U,multiprocessing as M
s=int(os.environ['RZ_JOB_UNIT_SEED']);random.seed(s);L=os.environ.get('RZ_JOB_SEED_LIBS','').split(',')
if 'numpy' in L and U.find_spec('numpy'):
 import numpy;numpy.random.seed(s%2**32)
if 'torch' in L and U.find_spec('torch'):
 import torch;torch.manual_seed(s)
try:M.set_start_method('forkserver')
except (ValueError,RuntimeError):pass
P=os.getppid()
def W():
 while os.getppid()==P:time.sleep(0.2)
 os._exit(70)
threading.Thread(target=W,daemon=True).start()
m=sys.argv[1];sys.argv=[m]+sys.argv[2:];runpy.run_module(m,run_name='__main__',alter_sys=True)
"""
STOP = []                                   # set by SIGTERM: stop the running unit, report partial


class JobError(Exception):
    pass


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def spec_hash(spec: dict) -> str:
    return sha(canon(spec).encode())


def fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes) -> None:
    """Temp file in the same folder, flush, fsync, rename, fsync the folder: readers see old or new, never half."""
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fsync_dir(os.path.dirname(path) or ".")


def emit(kind: str, obj: dict) -> None:
    print(f"{kind} {canon(obj)}", flush=True)


def check_spec(spec) -> list:
    """The checks the worker can make without the repo (tools/jobs/spec.py makes the full set before upload)."""
    if not isinstance(spec, dict) or spec.get("format") != FMT:
        return ["not a " + FMT + " spec"]
    p = []
    if not SHA40.match(str(spec.get("commit", ""))):
        p.append("commit is not a full 40-hex sha")
    if type(spec.get("seed")) is not int:
        p.append("seed missing or not an integer")
    st = spec.get("steps")
    if not (isinstance(st, list) and len(st) == 2 and all(type(x) is int for x in st) and 0 <= st[0] < st[1]):
        p.append("steps must be [start, end) with 0 <= start < end")
    if type(spec.get("ckpt_every")) is not int or spec["ckpt_every"] < 1:
        p.append("ckpt_every must be a positive integer")
    if not CLONE_URL.match(str(spec.get("repo_url", ""))):
        p.append("repo_url must be a plain https URL")
    outs = spec.get("outputs")
    if not outs or not all(isinstance(o, str) and o and not o.startswith("/") and ".." not in o.split("/")
                           for o in outs):
        p.append("outputs must be relative paths")
    if not isinstance(spec.get("budget_s"), (int, float)) or not isinstance(spec.get("stop_margin_s"), (int, float)):
        p.append("budget_s and stop_margin_s must be numbers")
    return p


def git(*args: str, cwd: str | None = None) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise JobError(f"git {args[0]} failed (exit {r.returncode})")
    return r.stdout.strip()


def clone(url: str, commit: str, dest: str) -> str:
    """Fetch exactly `commit` from a public URL into dest (reused when it already sits at that commit)."""
    if not CLONE_URL.match(url):
        raise JobError("refused clone URL: plain https only, no user or password part")
    if os.path.isdir(os.path.join(dest, ".git")):
        try:
            if git("rev-parse", "HEAD", cwd=dest) == commit:
                return dest
        except JobError:
            pass
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    git("init", "-q", dest)
    git("fetch", "-q", "--depth", "1", url, commit, cwd=dest)
    git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=dest)
    return dest


def tree(d: str) -> dict:
    """{relative path: [sha256, bytes]} for every file under d except CKPT.json."""
    out = {}
    for base, dirs, files in os.walk(d):
        dirs.sort()
        for n in sorted(files):
            full = os.path.join(base, n)
            rel = os.path.relpath(full, d).replace(os.sep, "/")
            if rel != "CKPT.json":
                out[rel] = [sha_file(full), os.path.getsize(full)]
    return out


def valid_ckpt(d: str, sh: str):
    """The checkpoint's step when its CKPT.json names this spec and every file re-hashes; else None."""
    try:
        with open(os.path.join(d, "CKPT.json"), "rb") as f:
            ck = json.loads(f.read())
        if ck.get("spec") == sh and tree(d) == ck.get("files"):
            return ck["step"]
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    return None


def ckpt_root(work: str, sh: str) -> str:
    """Each spec keeps its own checkpoints, so another spec's run in the same folder can never prune or feed them."""
    return os.path.join(work, "ckpt", sh[:16])


def latest(work: str, sh: str):
    """(step, folder) of the newest checkpoint that verifies; a corrupt one falls back to the one before."""
    root = ckpt_root(work, sh)
    names = sorted((n for n in os.listdir(root) if n.startswith("step_")), reverse=True) if os.path.isdir(root) else []
    for n in names:
        step = valid_ckpt(os.path.join(root, n), sh)
        if step is not None:
            return step, os.path.join(root, n)
    return None, None


def commit_unit(nxt: str, work: str, sh: str, step: int, keep: int = 2) -> str:
    atomic_write(os.path.join(nxt, "CKPT.json"), canon({"spec": sh, "step": step, "files": tree(nxt)}).encode())
    fsync_dir(nxt)
    root = ckpt_root(work, sh)
    os.makedirs(root, exist_ok=True)
    dest = os.path.join(root, f"step_{step:010d}")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(nxt, dest)
    fsync_dir(root)
    ck = os.path.join(dest, "CKPT.json")
    atomic_write(os.path.join(work, "LATEST.json"), canon({"step": step, "ckpt": os.path.basename(dest),
                                                         "ckpt_sha256": sha_file(ck)}).encode())
    for n in sorted(n for n in os.listdir(root) if n.startswith("step_"))[:-keep]:
        shutil.rmtree(os.path.join(root, n), ignore_errors=True)
    return dest


def run_unit(cmd: list, env: dict, cwd: str, deadline: float):
    """The child's exit code, or 'stopped' when the deadline or a SIGTERM came first (child group terminated)."""
    p = subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True)
    while p.poll() is None:
        if STOP or time.monotonic() > deadline:
            for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
                try:
                    os.killpg(p.pid, sig)
                except OSError:
                    pass
                try:
                    p.wait(wait)
                    break
                except subprocess.TimeoutExpired:
                    continue
            return "stopped"
        time.sleep(0.02)
    return p.returncode


def unit_seed(seed: int, start: int) -> int:
    return int(sha(f"{seed}:{start}".encode())[:8], 16)


def run_meta(repo: str) -> dict:
    from importlib import metadata
    libs = {}
    for n in LIBS:
        try:
            libs[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            pass
    gpu = None
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=20)
            first = (r.stdout.strip().splitlines() or [""])[0]
            gpu = re.sub(r"\s*\(UUID:[^)]*\)", "", first) or None
        except (OSError, subprocess.SubprocessError):
            gpu = None
    return {"git": git("rev-parse", "HEAD", cwd=repo), "python": platform.python_version(), "libs": libs, "gpu": gpu}


def finish(spec: dict, sh: str, work: str, status: str, info: dict) -> int:
    """Print the one RZ-RESULT line (chained to the previous one in this work folder) and return the exit code."""
    log = os.path.join(work, "results.jsonl")
    prev = None
    if os.path.isfile(log):
        with open(log, "rb") as f:
            lines = [ln for ln in f.read().decode().splitlines() if ln.strip()]
        prev = sha(lines[-1].encode()) if lines else None
    res = {"job": spec.get("job_id"), "spec": sh, "status": "failed" if status == "refused" else status,
           "prev": prev, **info}
    res["line"] = sha(canon(res).encode())
    line = f"RZ-RESULT {canon(res)}"
    if os.path.isdir(work):
        with open(log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
    print(line, flush=True)
    return EXIT[status]


def outputs_of(out: str, names: list) -> dict:
    return {n: [sha_file(os.path.join(out, n)), os.path.getsize(os.path.join(out, n))] for n in sorted(names)}


def done_info(spec: dict, out: str, outputs: dict) -> dict:
    mf = spec.get("metrics_file")
    return {"outputs": outputs, "outputs_sha256": sha(canon(outputs).encode()),
            "metrics_sha256": outputs[mf][0] if mf in outputs else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run one RedZone job spec in resumable units.")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--repo", help="use this checkout instead of cloning (local smoke); HEAD must be the commit")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    with open(a.spec, "rb") as f:
        spec = json.loads(f.read())
    sh = spec_hash(spec)
    work = os.path.abspath(a.work)
    bad = check_spec(spec)
    if bad:
        return finish(spec if isinstance(spec, dict) else {}, sh, work, "refused", {"error": "; ".join(bad)})
    start, end = spec["steps"]
    n = spec["ckpt_every"]
    repo = os.path.abspath(a.repo) if a.repo else os.path.join(work, "repo")
    if a.dry_run:
        emit("RZ-DRY", {"spec": sh, "job": spec.get("job_id"), "clone": None if a.repo else spec["repo_url"],
                        "commit": spec["commit"], "repo": repo, "entry": spec["entry"], "units": -(-(end - start) // n),
                        "steps": [start, end], "outputs": spec["outputs"], "lock": spec.get("lock"),
                        "deadline_s": spec["budget_s"] - spec["stop_margin_s"], "work": work})
        return 0
    signal.signal(signal.SIGTERM, lambda *_: STOP.append(1))
    t0, m0 = time.time(), time.monotonic()
    deadline = m0 + spec["budget_s"] - spec["stop_margin_s"]
    os.makedirs(work, exist_ok=True)
    out = os.path.join(work, "out")
    try:
        with open(os.path.join(out, "DONE"), "rb") as f:
            if json.loads(f.read()).get("spec") == sh:
                info = done_info(spec, out, outputs_of(out, spec["outputs"]))
                return finish(spec, sh, work, "ok", {**info, "step": end, "reused": True})
    except (OSError, ValueError, AttributeError):
        pass
    try:
        if not a.repo:
            clone(spec["repo_url"], spec["commit"], repo)
        if git("rev-parse", "HEAD", cwd=repo) != spec["commit"]:
            raise JobError("checkout HEAD is not the pinned commit")
        lock = spec.get("lock")
        if lock:
            lp = os.path.join(repo, lock["path"])
            if not os.path.isfile(lp) or sha_file(lp) != lock["sha256"]:
                raise JobError("lock file missing or its sha256 differs from the spec")
            r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--require-hashes", "-r", lp])
            if r.returncode != 0:
                raise JobError(f"pip install from the lock failed (exit {r.returncode})")
        meta = run_meta(repo)
    except (JobError, OSError, subprocess.SubprocessError) as e:
        return finish(spec, sh, work, "failed", {"error": str(e)[:300]})
    atomic_write(os.path.join(work, "config.json"), canon(spec.get("config", {})).encode())
    step, ck = latest(work, sh)
    step = start if step is None else step
    emit("RZ-PROGRESS", {"job": spec.get("job_id"), "step": step, "end": end, "resumed": ck is not None})
    env = dict(os.environ, PYTHONHASHSEED=str(spec["seed"] % 2**32), RZ_JOB_SEED=str(spec["seed"]),
               RZ_JOB_CONFIG=os.path.join(work, "config.json"), RZ_JOB_WORK=work,
               RZ_JOB_SEED_LIBS=",".join(spec.get("seed_libs", ["random", "numpy", "torch"])),
               PYTHONPATH=repo + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.setdefault("OMP_NUM_THREADS", "1")
    cmd = [sys.executable, "-c", BOOT, spec["entry"]["module"], *spec["entry"].get("args", [])]
    for stale in os.listdir(work):
        if stale.startswith("next_"):
            shutil.rmtree(os.path.join(work, stale), ignore_errors=True)
    status, slowest, err = "ok", 0.0, None
    while step < end:
        stop = start + ((step - start) // n + 1) * n
        stop = min(stop, end)
        if STOP or time.monotonic() + slowest > deadline:
            status = "partial"
            break
        nxt = os.path.join(work, f"next_{os.getpid()}_{step}")
        shutil.rmtree(nxt, ignore_errors=True)
        os.makedirs(nxt)
        u0 = time.monotonic()
        rc = run_unit(cmd, {**env, "RZ_JOB_STEP_START": str(step), "RZ_JOB_STEP_END": str(stop),
                            "RZ_JOB_STATE": ck or "", "RZ_JOB_NEXT": nxt,
                            "RZ_JOB_UNIT_SEED": str(unit_seed(spec["seed"], step))}, repo, deadline)
        if rc != 0:
            shutil.rmtree(nxt, ignore_errors=True)
            status, err = ("partial", None) if rc == "stopped" else ("failed", f"entry exited {rc} in unit {step}")
            break
        ck = commit_unit(nxt, work, sh, stop)
        slowest = max(slowest, time.monotonic() - u0)
        step = stop
        emit("RZ-PROGRESS", {"job": spec.get("job_id"), "step": step, "end": end,
                             "elapsed_s": round(time.monotonic() - m0, 1)})
    info = {"step": step, "git": meta["git"], "gpu": meta["gpu"], "python": meta["python"],
            "started": round(t0, 3), "ended": round(time.time(), 3)}
    if ck:
        info["ckpt_sha256"] = sha_file(os.path.join(ck, "CKPT.json"))
    if status != "ok":
        return finish(spec, sh, work, status, {**info, "error": err})
    missing = [o for o in spec["outputs"] if not os.path.isfile(os.path.join(ck, o))]
    if missing:
        return finish(spec, sh, work, "failed", {**info, "error": f"missing outputs: {missing[:5]}"})
    for o in spec["outputs"]:
        os.makedirs(os.path.dirname(os.path.join(out, o)), exist_ok=True)
        with open(os.path.join(ck, o), "rb") as f:
            atomic_write(os.path.join(out, o), f.read())
    outputs = outputs_of(out, spec["outputs"])
    atomic_write(os.path.join(out, "run_meta.json"), canon({**meta, **info, "spec": sh}).encode())
    atomic_write(os.path.join(out, "MANIFEST.json"), canon({"spec": sh, "outputs": outputs}).encode())
    info.update(done_info(spec, out, outputs))
    atomic_write(os.path.join(out, "DONE"), canon({"spec": sh, "outputs_sha256": info["outputs_sha256"]}).encode())
    return finish(spec, sh, work, "ok", info)


if __name__ == "__main__":
    sys.exit(main())
