"""The Stop hook (.claude/hooks/stop_gate.py) blocks on red checks for changed files, stays quiet on a turn that
changed nothing, and always lets go before the harness's own cap. docs/coordination.md V1."""
import importlib.util
import json
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "stop_gate.py"


@pytest.fixture
def gate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("stop_gate_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod.tempfile, "gettempdir", lambda: str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    return mod


EV = {"session_id": "s1"}


def test_first_failure_blocks_with_the_output(gate, capsys):
    assert gate.decide(EV, ["pytest failed:\nE assert 1 == 2"]) == 2
    err = capsys.readouterr().err
    assert "assert 1 == 2" in err and "BLOCKED" in err


def test_passing_checks_allow_and_clear_the_count(gate):
    gate.decide(EV, ["x"])
    assert gate.decide(EV, []) == 0
    assert gate.load_state(EV) == {}


def test_same_failure_on_a_retry_gives_up_with_blocked(gate, capsys):
    assert gate.decide(EV, ["same"]) == 2
    assert gate.decide({**EV, "stop_hook_active": True}, ["same"]) == 0
    assert "BLOCKED" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_timings_do_not_make_a_failure_look_new(gate):
    assert gate.signature(["boom in 1.23s at 0xabc"]) == gate.signature(["boom in 9.87s at 0xdef"])


def test_changing_failures_still_stop_below_the_harness_cap(gate, capsys):
    active = {**EV, "stop_hook_active": True}
    codes = [gate.decide(active, [f"failure {i}"]) for i in range(gate.MAX_BLOCKS + 1)]
    assert codes == [2] * gate.MAX_BLOCKS + [0]
    assert gate.MAX_BLOCKS < 8
    assert "BLOCKED" in capsys.readouterr().out


def test_sessions_keep_separate_counts(gate):
    gate.decide({"session_id": "a"}, ["x"])
    assert gate.load_state({"session_id": "b"}) == {}


def test_targeted_tests_map_modules_and_keep_tests(gate, tmp_path):
    (tmp_path / "tests").mkdir()
    for n in ("test_fsm.py", "test_decider_rank.py", "test_other.py"):
        (tmp_path / "tests" / n).write_text("")
    changed = ["redzone/fsm.py", "redzone/decider/rank.py", "tests/test_other.py", "tests/conftest.py", "docs/x.md",
               "redzone/nothing.py"]
    assert gate.targeted_tests(changed, tmp_path) == ["tests/test_decider_rank.py", "tests/test_fsm.py", "tests/test_other.py"]


def test_targeted_tests_are_capped(gate, tmp_path):
    (tmp_path / "tests").mkdir()
    names = [f"tests/test_m{i}.py" for i in range(gate.MAX_TESTS + 3)]
    for n in names:
        (tmp_path / n).write_text("")
    assert len(gate.targeted_tests(names, tmp_path)) == gate.MAX_TESTS


@pytest.mark.parametrize("changed,want", [
    (["docs/memory/lessons.md"], ["check_memory"]),
    (["CLAUDE.md"], ["check_memory"]),
    (["docs/agents/rules/x.md"], ["doc_index"]),
    (["redzone/machines/a.py"], ["state_machines"]),
    (["docs/status.md"], []),
])
def test_only_relevant_checks_run(gate, monkeypatch, changed, want):
    monkeypatch.setattr(gate.shutil, "which", lambda _: None)
    assert [n for n, _ in gate.checks_for(changed)] == want


def _fake_runner(results):
    calls = []

    def run(cmd, timeout):
        calls.append(cmd)
        return results.pop(0)
    return run, calls


def test_a_test_timeout_is_a_note_not_a_block(gate, tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    run, _ = _fake_runner([(None, "TimeoutExpired")])
    monkeypatch.setattr(gate, "run", run)
    failures, notes = gate.run_checks(["tests/test_a.py"])
    assert failures == [] and "not finished" in notes[0]


def test_a_real_test_failure_is_reported(gate, tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    run, calls = _fake_runner([(1, "FAILED tests/test_a.py::test_x")])
    monkeypatch.setattr(gate, "run", run)
    failures, _ = gate.run_checks(["tests/test_a.py"])
    assert "test_x" in failures[0] and "-x" in calls[0]


def test_a_second_gate_skips_tests(gate, tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    held = gate.acquire_test_lock()
    assert held is not None
    run, calls = _fake_runner([])
    monkeypatch.setattr(gate, "run", run)
    failures, notes = gate.run_checks(["tests/test_a.py"])
    held.close()
    assert failures == [] and calls == [] and "another stop gate" in notes[0]


def test_a_turn_with_no_changes_runs_nothing(gate, monkeypatch):
    monkeypatch.setattr(gate, "changed_files", list)
    monkeypatch.setattr(gate, "run_checks", lambda c: pytest.fail("ran checks"))
    monkeypatch.setattr(gate.sys, "stdin", __import__("io").StringIO("{}"))
    assert gate.main() == 0


def test_a_crash_allows_the_stop(gate, monkeypatch):
    monkeypatch.setattr(gate.sys, "stdin", __import__("io").StringIO("not json"))
    assert gate.main() == 0
