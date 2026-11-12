"""park_hands_live.app_running (also used by park_prime_accept): the live tests refuse while ANY copy of the app runs.

The login service (com.redzone.everything) runs the app through `python -c ... scripts/service_runtime.py`, so its
command line never says `-m redzone`; the old check matched only `-m redzone` and called the service "not running".
No device, no real pgrep or launchctl: subprocess.run is replaced.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE_CMD = ('/Users/x/redzone/.venv/bin/python -u -c import os,sys,runpy; R="/Users/x/redzone"; os.chdir(R); '
               'sys.path.insert(0,R); sys.argv=[R+"/scripts/service_runtime.py","--allow-live"]; '
               'runpy.run_path(sys.argv[0], run_name="__main__")')
HAND_CMD = "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python -u -m redzone"
LAUNCHCTL_UP = '{\n\t"Label" = "com.redzone.everything";\n\t"PID" = 4242;\n};\n'
LAUNCHCTL_STOPPED = '{\n\t"Label" = "com.redzone.everything";\n\t"LastExitStatus" = 0;\n};\n'


@pytest.fixture(scope="module")
def phl():
    spec = importlib.util.spec_from_file_location("park_hands_live_under_test", ROOT / "scripts" / "park_hands_live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_run(procs: list[str], launchctl: tuple[int, str] | None):
    """pgrep -f <pattern> over `procs` (extended regex, like macOS pgrep); launchctl list -> (code, out) or absent."""
    def run(cmd, **kw):
        if cmd[0] == "pgrep":
            hits = [str(i + 100) for i, p in enumerate(procs) if re.search(cmd[2], p)]
            return subprocess.CompletedProcess(cmd, 0 if hits else 1, "\n".join(hits), "")
        if cmd[0] == "launchctl":
            if launchctl is None:
                raise FileNotFoundError("launchctl")
            return subprocess.CompletedProcess(cmd, launchctl[0], launchctl[1], "")
        raise AssertionError(f"unexpected command {cmd}")
    return run


def test_login_service_by_its_command_line_counts_as_running(phl, monkeypatch):
    monkeypatch.setattr(phl.subprocess, "run", fake_run([SERVICE_CMD], (113, "")))
    assert phl.app_running()


def test_login_service_by_launchd_pid_counts_as_running(phl, monkeypatch):
    monkeypatch.setattr(phl.subprocess, "run", fake_run([], (0, LAUNCHCTL_UP)))
    assert phl.app_running()


def test_app_started_by_hand_counts_as_running(phl, monkeypatch):
    monkeypatch.setattr(phl.subprocess, "run", fake_run([HAND_CMD], (113, "")))
    assert phl.app_running()


def test_nothing_running(phl, monkeypatch):
    monkeypatch.setattr(phl.subprocess, "run", fake_run(["/bin/zsh -l", "python scripts/park_hands_live.py"], (113, "")))
    assert not phl.app_running()


def test_service_loaded_but_stopped_is_not_running(phl, monkeypatch):
    monkeypatch.setattr(phl.subprocess, "run", fake_run([], (0, LAUNCHCTL_STOPPED)))
    assert not phl.app_running()


def test_no_launchd_falls_back_to_pgrep(phl, monkeypatch):
    monkeypatch.setattr(phl.subprocess, "run", fake_run([], None))
    assert not phl.app_running()


def test_unsure_counts_as_running(phl, monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    monkeypatch.setattr(phl.subprocess, "run", boom)
    assert phl.app_running()
