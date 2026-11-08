#!/usr/bin/env python3
"""Stop and SubagentStop hook: a turn can't end on red checks for the files it changed.

Why: W1-RULES rule 10. "Done" was a claim; this makes the fast checks part of done. PLAN G-03, coordination V1.

Runs only what the working tree's changes make relevant, one process at a time, inside TOTAL_S:
  check_memory          when CLAUDE.md or docs/memory/ changed
  doc_index --check     when docs/agents/ changed
  state_machines --check  when redzone/machines/ or docs/state-machines.md changed
  ruff                  on changed .py files, if ruff is installed
  pytest -q -x          on the tests matching changed modules (at most MAX_TESTS files, TESTS_S seconds)
Never scorecards or anything else heavy (G4): the Mac runs the live app.

Exit 2 with the failures on stderr blocks the stop and sends them to the agent. It gives up and allows the
stop, with a BLOCKED: note, in two cases, so it never loops:
  - the same failures come back on a stop the hook already blocked (docs/coordination.md section 2);
  - MAX_BLOCKS blocks in a row. The harness ends the turn itself after eight, so this stays below it.
A check that times out or can't start is reported and does not block. A crash here allows the stop (fail open,
like guard.py); CI runs the same checks. A second gate running at once skips its tests (one pytest at a time).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
TOTAL_S = 60        # settings.json gives the hook 90
TESTS_S = 40
CHECK_S = 15
MAX_TESTS = 8
MAX_BLOCKS = 7      # the harness stops honoring blocks after eight in a row
MAX_CHARS = 1500


def python() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def run(cmd: list[str], timeout: float) -> tuple[int | None, str]:
    """(exit code, output). Code None: it timed out or could not start, so it says nothing about the code."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=max(1.0, timeout), env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, type(exc).__name__
    return r.returncode, (r.stdout + r.stderr).strip()


def changed_files() -> list[str]:
    """Tracked changes against HEAD plus untracked files. Empty on a turn that changed nothing."""
    out = []
    for cmd in (["git", "diff", "--name-only", "HEAD"], ["git", "ls-files", "-o", "--exclude-standard"]):
        code, text = run(cmd, CHECK_S)
        if code == 0:
            out += [ln.strip() for ln in text.splitlines() if ln.strip()]
    return sorted(set(out))


def targeted_tests(changed: list[str], root: Path | None = None) -> list[str]:
    """tests/test_x.py as itself; a/b/x.py as tests/test_x.py or tests/test_b_x.py when they exist."""
    root, found = root or ROOT, []
    for f in changed:
        p = Path(f)
        if p.suffix != ".py" or p.name == "conftest.py":
            continue
        if p.parts[0] == "tests":
            candidates = [p] if p.name.startswith("test_") else []
        else:
            candidates = [Path("tests") / f"test_{p.stem}.py"]
            if len(p.parts) > 1:
                candidates.append(Path("tests") / f"test_{p.parent.name}_{p.stem}.py")
        found += [str(c) for c in candidates if (root / c).exists()]
    return sorted(set(found))[:MAX_TESTS]


def checks_for(changed: list[str]) -> list[tuple[str, list[str]]]:
    def touched(*prefixes: str) -> bool:
        return any(f.startswith(prefixes) for f in changed)
    py, out = python(), []
    if touched("CLAUDE.md", "docs/memory/"):
        out.append(("check_memory", [py, "scripts/check_memory.py"]))
    if touched("docs/agents/"):
        out.append(("doc_index", [py, "scripts/doc_index.py", "--check"]))
    if touched("redzone/machines/", "docs/state-machines.md"):
        out.append(("state_machines", [py, "scripts/state_machines.py", "--check"]))
    pyfiles = [f for f in changed if f.endswith(".py") and (ROOT / f).exists()]
    ruff = shutil.which("ruff")
    if pyfiles and ruff:
        out.append(("ruff", [ruff, "check", "--output-format", "concise", *pyfiles]))
    return out


def tail(text: str) -> str:
    return text if len(text) <= MAX_CHARS else "..." + text[-MAX_CHARS:]


def _state_dir() -> Path:
    tag = hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    d = Path(tempfile.gettempdir()) / f"rz_stop_gate_{tag}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def acquire_test_lock():
    """A held non-blocking lock, or None if another gate is running tests."""
    import fcntl
    f = (_state_dir() / "pytest.lock").open("w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def run_checks(changed: list[str]) -> tuple[list[str], list[str]]:
    """(failures, notes). Failures block; notes (timeouts, skips) never do."""
    start, failures, notes = time.monotonic(), [], []

    def left(limit: float) -> float:
        return min(limit, TOTAL_S - (time.monotonic() - start))

    for name, cmd in checks_for(changed):
        code, out = run(cmd, left(CHECK_S))
        if code is None:
            notes.append(f"{name}: not finished ({out})")
        elif code != 0:
            failures.append(f"{name} failed:\n{tail(out)}")
    tests = targeted_tests(changed)
    if tests and left(TESTS_S) >= 5:
        lock = acquire_test_lock()
        if lock is None:
            notes.append("tests: skipped, another stop gate is running them")
        else:
            with lock:
                code, out = run([python(), "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", *tests],
                                left(TESTS_S))
            if code is None:
                notes.append(f"tests: not finished ({out})")
            elif code == 1:
                failures.append(f"pytest failed ({' '.join(tests)}):\n{tail(out)}")
            elif code not in (0, 5):
                notes.append(f"tests: pytest could not run (exit {code})")
    elif tests:
        notes.append("tests: skipped, out of time")
    return failures, notes


def signature(failures: list[str]) -> str:
    """Stable across runs: timings and memory addresses removed."""
    text = re.sub(r"\b\d+(\.\d+)?s\b|0x[0-9a-f]+", "", "\n".join(failures))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _state_path(ev: dict) -> Path:
    key = f"{ev.get('session_id', '')}:{ev.get('agent_id', '')}"
    return _state_dir() / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"


def load_state(ev: dict) -> dict:
    try:
        return json.loads(_state_path(ev).read_text())
    except (OSError, ValueError):
        return {}


def save_state(ev: dict, state: dict) -> None:
    _state_path(ev).write_text(json.dumps(state))


def give_up(reason: str, failures: list[str]) -> int:
    first = failures[0].splitlines()[0] if failures else "checks failed"
    print(json.dumps({"systemMessage": f"BLOCKED: stop gate gave up ({reason}): {first}"}))
    return 0


def decide(ev: dict, failures: list[str]) -> int:
    """0 allows the stop, 2 blocks it."""
    if not failures:
        save_state(ev, {})
        return 0
    state, sig = load_state(ev), signature(failures)
    if ev.get("stop_hook_active") and state.get("sig") == sig:
        save_state(ev, {})
        return give_up("same failure after a retry", failures)
    blocks = int(state.get("blocks", 0)) + 1
    if blocks > MAX_BLOCKS:
        save_state(ev, {})
        return give_up(f"{MAX_BLOCKS} blocks in a row", failures)
    save_state(ev, {"sig": sig, "blocks": blocks})
    sys.stderr.write("Stop gate: fix these before stopping, or write BLOCKED: <reason> "
                     "(docs/coordination.md V1).\n\n" + "\n\n".join(failures) + "\n")
    return 2


def main() -> int:
    try:
        ev = json.load(sys.stdin)
        changed = changed_files()
        if not changed:
            return 0
        failures, notes = run_checks(changed)
        for n in notes:
            sys.stderr.write(f"stop gate note: {n}\n")
        return decide(ev, failures)
    except Exception as exc:  # fail open, say so
        sys.stderr.write(f"stop_gate.py error (allowed): {type(exc).__name__}: {exc}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
