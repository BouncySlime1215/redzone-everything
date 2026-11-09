"""Test 3c's guards and its watcher. Fakes only: nothing here loads a model, spawns a worker or needs MLX."""
from __future__ import annotations

import builtins
import io
import json
import subprocess

import pytest

from scripts import mac_brain_memory_test as T


@pytest.fixture
def model(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "config.json").write_text("{}")
    return d


def probes(**over):
    base = dict(apps=lambda: [], launchd=lambda: False, free_pct=lambda: 40.0, load1=lambda: 0.5, swap_mb=lambda: 100.0,
                footprint=lambda pid: 1.0, is_apple_silicon=lambda: True)
    return T.Probes(**{**base, **over})


class Child:
    """A worker that runs for a set number of polls, then exits, having printed `out`."""
    pid = 4242

    def __init__(self, out="", polls=3, code=0):
        self.polls, self.returncode, self.stdout, self.killed = polls, None, io.StringIO(out), False
        self.final = code

    def poll(self):
        if self.killed:
            return self.returncode
        if self.polls <= 0:
            self.returncode = self.final
            return self.returncode
        self.polls -= 1
        return None

    def send_signal(self, sig):
        self.killed, self.returncode = True, -sig

    def wait(self):
        return self.returncode


GOOD = "\n".join(json.dumps(e) for e in ({"event": "loaded", "seconds": 4.2}, {"event": "pass", "i": 0, "seconds": 1.5}, {"event": "pass", "i": 1, "seconds": 2.5}))


def go(model, child=None, p=None, *extra, confirm=True):
    argv = ["--model", str(model), "--passes", "2", *extra] + (["--confirm"] if confirm else [])
    spawned = []

    def spawn(a):
        spawned.append(a)
        return child or Child(GOOD)
    return T.main(argv, probes=p or probes(), spawn=spawn, sleep=lambda s: None), spawned


def test_needs_confirm_and_never_starts_the_model(model, capsys):
    code, spawned = go(model, confirm=False)
    assert code == T.EXIT_REFUSED and not spawned and "--confirm" in capsys.readouterr().out


@pytest.mark.parametrize("over,word", [
    ({"apps": lambda: ["python scripts/service_runtime.py"]}, "app is on"),
    ({"launchd": lambda: True}, "app is on"),
    ({"free_pct": lambda: 5.0}, "not idle"),
    ({"load1": lambda: 9.0}, "not idle"),
    ({"is_apple_silicon": lambda: False}, "Apple Silicon"),
])
def test_refuses_when_the_mac_is_not_idle(model, capsys, over, word):
    code, spawned = go(model, p=probes(**over))
    assert code == T.EXIT_REFUSED and not spawned and word in capsys.readouterr().out


def test_a_probe_that_cannot_answer_is_not_idle(model, capsys):
    def broken():
        raise subprocess.CalledProcessError(1, "memory_pressure")
    code, spawned = go(model, p=probes(free_pct=broken))
    assert code == T.EXIT_REFUSED and not spawned and "could not tell" in capsys.readouterr().out


@pytest.mark.parametrize("build", [lambda d: d / "missing", lambda d: _no_config(d)])
def test_refuses_a_path_that_is_not_a_local_model(model, build):
    assert go(build(model))[0] == T.EXIT_REFUSED


def _no_config(d):
    (d / "config.json").unlink()
    return d


def test_a_repo_id_is_not_downloaded(model):
    code, spawned = go("mlx-community/Qwen3-1.7B-4bit")
    assert code == T.EXIT_REFUSED and not spawned


def test_logs_peak_swap_and_seconds_per_pass(model, tmp_path):
    out = tmp_path / "r.json"
    feet = iter([1.2, 1.7, 1.4])
    swaps = iter([100.0, 340.5])
    p = probes(footprint=lambda pid: next(feet), swap_mb=lambda: next(swaps))
    code, _ = go(model, Child(GOOD), p, "--out", str(out))
    got = json.loads(out.read_text())
    assert code == T.EXIT_OK
    assert got["peak_footprint_gb"] == 1.7 and got["swap_change_mb"] == 240.5
    assert got["seconds_per_pass"] == [1.5, 2.5] and got["mean_seconds_per_pass"] == 2.0 and got["load_seconds"] == 4.2
    assert got["stopped_at_limit"] is False


def test_stops_at_the_limit_and_kills_the_child(model, tmp_path):
    out = tmp_path / "r.json"
    child = Child(GOOD, polls=100)
    feet = iter([1.0, 1.9, 2.05, 3.0])
    code, _ = go(model, child, probes(footprint=lambda pid: next(feet)), "--out", str(out))
    got = json.loads(out.read_text())
    assert code == T.EXIT_LIMIT and child.killed and got["stopped_at_limit"] is True and got["peak_footprint_gb"] == 2.05
    assert child.polls > 90        # stopped at the third look, not after the run


def test_the_default_limit_is_two_gb(model):
    assert T.parse(["--model", str(model)]).limit_gb == 2.0 and T.LIMIT_GB == 2.0


def test_a_lower_limit_can_be_set(model):
    child = Child(GOOD, polls=100)
    code, _ = go(model, child, probes(footprint=lambda pid: 1.5), "--limit-gb", "1.0")
    assert code == T.EXIT_LIMIT and child.killed


def test_a_crashed_worker_is_a_failed_run(model):
    assert go(model, Child(GOOD, code=1))[0] == T.EXIT_FAILED


def test_too_few_passes_is_a_failed_run(model):
    assert go(model, Child(json.dumps({"event": "pass", "i": 0, "seconds": 1.0})))[0] == T.EXIT_FAILED


def test_a_process_gone_between_polls_is_not_a_crash(model):
    assert go(model, Child(GOOD), probes(footprint=lambda pid: None))[0] == T.EXIT_OK


def test_the_parent_never_imports_mlx(model, monkeypatch):
    real = builtins.__import__

    def guard(name, *a, **k):
        assert not name.startswith("mlx"), "the watcher must not load MLX"
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", guard)
    assert go(model)[0] == T.EXIT_OK


def test_app_processes_finds_the_service_and_skips_itself(monkeypatch):
    me = T.os.getpid()
    table = f"  {me} python scripts/service_runtime.py\n  77 /venv/python -c runpy scripts/service_runtime.py\n  88 /bin/zsh\n"
    monkeypatch.setattr(T, "_run", lambda cmd: table)
    assert T.app_processes() == ["/venv/python -c runpy scripts/service_runtime.py"]


@pytest.mark.parametrize("line,mb", [("total = 7168.00M  used = 6553.60M  free = 614.40M", 6553.6), ("total = 8.00G  used = 1.50G  free = 6.50G", 1536.0)])
def test_swap_parses_both_units(monkeypatch, line, mb):
    monkeypatch.setattr(T, "_run", lambda cmd: line)
    assert T.swap_used_mb() == mb


def test_free_memory_parses(monkeypatch):
    monkeypatch.setattr(T, "_run", lambda cmd: "System-wide memory free percentage: 26%\n")
    assert T.free_pct() == 26.0
