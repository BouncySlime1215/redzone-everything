#!/usr/bin/env python3
"""Test 3c: load a 4-bit model on the Mac and log peak memory, swap change and seconds per pass, stopping at the 2 GB limit.

    .venv/bin/python scripts/mac_brain_memory_test.py --model <local 4-bit MLX dir> --confirm [--adapter <dir>] [--passes 5] [--out out.json]

Why: the-mind section 8 caps the Mac's LLM at about 2 GB and says the 1.7 B footprint was never measured. Before any brain
that size is trusted, this measures it, and the 8 GB Mac that runs the live app must not pay for the measurement.

Refuses (exit 2) without --confirm, off Apple Silicon, on a model that is not a local folder, when the app is on, or when the
Mac is not idle. Failing to tell counts as not idle. Never downloads anything. The model runs in a child process that a parent
watches every 0.1 s; at the limit the child is killed and the run exits 4.

Memory is the kernel's phys_footprint, not RSS: MLX allocates through Metal, and RSS misses most of those pages.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

EXIT_OK, EXIT_REFUSED, EXIT_FAILED, EXIT_LIMIT = 0, 2, 3, 4
LIMIT_GB = 2.0
POLL_S = 0.1
MIN_FREE_PCT = 20.0          # below this, the test itself would push the Mac into swap
MAX_LOAD1 = 3.0
APP_MARKS = ("scripts/service_runtime.py", "redzone.demo", "redzone.lab.night")
LAUNCHD_LABEL = "com.redzone.everything"
PROMPT = ("Rate the next three minutes of this game.\nleague=nfl period=4 clock_left=133 game_left=133 is_ot=0 late=1 abs_margin=3 "
          "total_points=41 pts_5m=7 swing_5m=7 lead_changes=2 plays_5m=12 since_head_s=21 since_score_s=95 since_start_s=10500 "
          "t1_5m=1 t2_5m=2 t3_5m=0 t1_game=3 value_10m=14 down=2 distance=7 yards_to_goal=34 playoff=0\nvalue=")
GB = 1024.0 ** 3


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True).stdout


def app_processes() -> list[str]:
    """Commands of the live app (matched on its script names), other than this one."""
    me = os.getpid()
    hits = []
    for line in _run(["ps", "-axo", "pid=,command="]).splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != me and any(m in cmd for m in APP_MARKS):
            hits.append(cmd[:120])
    return hits


def launchd_running() -> bool:
    """True when launchd reports the app service running; False when it is not loaded."""
    p = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], capture_output=True, text=True, timeout=20)
    return p.returncode == 0 and "state = running" in p.stdout


def free_pct() -> float:
    m = re.search(r"free percentage:\s*(\d+)%", _run(["memory_pressure", "-Q"]))
    if not m:
        raise RuntimeError("memory_pressure printed no free percentage")
    return float(m[1])


def swap_used_mb() -> float:
    m = re.search(r"used\s*=\s*([\d.]+)([MG])", _run(["sysctl", "-n", "vm.swapusage"]))
    if not m:
        raise RuntimeError("vm.swapusage printed no used figure")
    return float(m[1]) * (1024.0 if m[2] == "G" else 1.0)


class _RUsage(ctypes.Structure):
    """The head of struct rusage_info_v0 from <sys/resource.h>."""
    _fields_ = [("uuid", ctypes.c_uint8 * 16), ("user", ctypes.c_uint64), ("system", ctypes.c_uint64), ("idle_wkups", ctypes.c_uint64),
                ("interrupt_wkups", ctypes.c_uint64), ("pageins", ctypes.c_uint64), ("wired", ctypes.c_uint64), ("resident", ctypes.c_uint64),
                ("footprint", ctypes.c_uint64), ("start", ctypes.c_uint64), ("exit", ctypes.c_uint64)]


def footprint_gb(pid: int) -> float | None:
    """phys_footprint of pid, in GB. None when the process is gone."""
    info = _RUsage()
    rc = ctypes.CDLL("libproc.dylib").proc_pid_rusage(pid, 0, ctypes.byref(info))
    return None if rc != 0 else info.footprint / GB


@dataclass
class Probes:
    """What the checks read. Tests pass fakes, so none of them need a Mac, MLX or a model."""
    apps: object = app_processes
    launchd: object = launchd_running
    free_pct: object = free_pct
    load1: object = lambda: os.getloadavg()[0]
    swap_mb: object = swap_used_mb
    footprint: object = footprint_gb
    is_apple_silicon: object = lambda: sys.platform == "darwin" and platform.machine() == "arm64"


def refusal(a, probes: Probes) -> str | None:
    """Why the test must not run, or None. A probe that cannot answer counts as 'not idle'."""
    if not a.confirm:
        return "needs --confirm: this loads a 1.7 B model on the 8 GB Mac"
    if not probes.is_apple_silicon():
        return "only meaningful on the Apple Silicon Mac it measures"
    if not a.model.is_dir() or not (a.model / "config.json").exists():
        return f"{a.model}: not a local model folder (this test never downloads)"
    try:
        apps = probes.apps()
        if apps:
            return f"the app is on: {apps[0]}"
        if probes.launchd():
            return f"the app is on: launchd reports {LAUNCHD_LABEL} running"
        free, load = probes.free_pct(), probes.load1()
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as e:
        return f"could not tell whether the Mac is idle ({type(e).__name__}: {e})"
    if free < MIN_FREE_PCT:
        return f"only {free:.0f}% memory free, need {MIN_FREE_PCT:.0f}%: not idle"
    if load > MAX_LOAD1:
        return f"1-minute load {load:.1f}, need at most {MAX_LOAD1}: not idle"
    return None


def worker(model: str, adapter: str | None, passes: int, max_tokens: int) -> int:
    """Child: load, then generate `passes` times, printing one JSON line per pass. The parent decides when it stops."""
    from mlx_lm import generate, load
    t0 = time.monotonic()
    m, tok = load(model, adapter_path=adapter)
    print(json.dumps({"event": "loaded", "seconds": round(time.monotonic() - t0, 2)}), flush=True)
    for i in range(passes):
        t1 = time.monotonic()
        generate(m, tok, prompt=PROMPT, max_tokens=max_tokens, verbose=False)
        print(json.dumps({"event": "pass", "i": i, "seconds": round(time.monotonic() - t1, 3)}), flush=True)
    return 0


def _spawn(a) -> subprocess.Popen:
    cmd = [sys.executable, str(Path(__file__).resolve()), "--worker", "--model", str(a.model), "--passes", str(a.passes), "--max-tokens", str(a.max_tokens)]
    if a.adapter:
        cmd += ["--adapter", str(a.adapter)]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)


def _kill(child) -> None:
    try:
        child.send_signal(signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def _events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def measure(a, probes: Probes, spawn=_spawn, sleep=time.sleep) -> tuple[dict, int]:
    """(report, exit code). Watches the child; at the limit it kills it and says so."""
    swap_before = probes.swap_mb()
    child = spawn(a)
    peak, stopped = 0.0, False
    while child.poll() is None:
        now = probes.footprint(child.pid)
        if now is not None:
            peak = max(peak, now)
            if now >= a.limit_gb:
                stopped = True
                _kill(child)
                break
        sleep(POLL_S)
    out = child.stdout.read() if child.stdout else ""
    events = _events(out)
    secs = [e["seconds"] for e in events if e.get("event") == "pass"]
    loaded = next((e["seconds"] for e in events if e.get("event") == "loaded"), None)
    report = {"model": str(a.model), "adapter": str(a.adapter) if a.adapter else None, "limit_gb": a.limit_gb, "peak_footprint_gb": round(peak, 3),
              "swap_before_mb": swap_before, "swap_after_mb": probes.swap_mb(), "stopped_at_limit": stopped, "load_seconds": loaded,
              "seconds_per_pass": secs, "mean_seconds_per_pass": round(sum(secs) / len(secs), 3) if secs else None, "exit_child": child.returncode}
    report["swap_change_mb"] = round(report["swap_after_mb"] - swap_before, 1)
    if stopped:
        return report, EXIT_LIMIT
    return report, EXIT_OK if child.returncode == 0 and len(secs) == a.passes else EXIT_FAILED


def parse(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", type=Path, required=True, help="local 4-bit MLX folder")
    ap.add_argument("--adapter", type=Path, default=None, help="mlx adapter folder from qwen_finetune")
    ap.add_argument("--confirm", action="store_true", help="required: yes, this Mac is idle and may load the model")
    ap.add_argument("--passes", type=int, default=5)
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--limit-gb", type=float, default=LIMIT_GB)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def main(argv=None, probes: Probes | None = None, spawn=_spawn, sleep=time.sleep) -> int:
    a = parse(argv)
    if a.worker:
        return worker(str(a.model), str(a.adapter) if a.adapter else None, a.passes, a.max_tokens)
    probes = probes or Probes()
    why = refusal(a, probes)
    if why:
        print(f"REFUSED: {why}", flush=True)
        return EXIT_REFUSED
    report, code = measure(a, probes, spawn, sleep)
    text = json.dumps(report, indent=1, sort_keys=True)
    print(text, flush=True)
    if a.out:
        a.out.write_text(text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
