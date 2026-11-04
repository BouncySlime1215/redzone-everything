"""Watchdog degraded modes and limits (agent/watchdog.py), fakes only (no launchctl, adb, model or phone):
- every kind of action counts toward one hourly cap; past it the watchdog only notifies, once;
- prune_disk runs at most once per 30 min;
- an action that raises is a logged result, never the end of the watchdog;
- no model (or it raises): an unknown error becomes a note, never an action;
- the journal can't be written: the check still runs;
- every action is journaled with its result."""
import json
import subprocess

import pytest

from agent import watchdog
from agent.policy import Obs, Verdict


@pytest.fixture
def w(monkeypatch):
    t = [1000.0]
    wd = watchdog.Watchdog(use_model=False, clock=lambda: t[0])
    monkeypatch.setattr(wd.gov, "refresh", lambda *a, **k: None)
    monkeypatch.setattr(wd.gov, "allow", lambda kind: kind != "llm")   # the Mac is "critical": no model
    wd.t = t
    wd.pushed = []
    monkeypatch.setattr(wd, "_push", wd.pushed.append)
    monkeypatch.setattr(watchdog.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "connected", ""))
    monkeypatch.setattr(watchdog, "prune_caches", lambda *a, **k: 5)
    monkeypatch.setattr(watchdog, "deploy_in_progress", lambda now: False)
    return wd


def test_all_actions_share_one_hourly_cap_then_notify_once(w):
    done = []
    for i in range(watchdog.MAX_ACTIONS_HOUR + 3):
        w.t[0] += 1
        v = Verdict("reconnect_stick", "offline", f"h{i}:5555")      # a different stick each time
        done.append(w.act(v, Obs(sticks_offline=[v.target])))
    assert sum(not r.startswith("rate-limited") for r in done) == watchdog.MAX_ACTIONS_HOUR
    assert all(r.startswith("rate-limited") for r in done[watchdog.MAX_ACTIONS_HOUR:])
    assert sum("holding back" in p for p in w.pushed) == 1           # told once, not every check
    w.t[0] += 3600                                                  # the hour clears
    assert w.act(Verdict("reconnect_stick", "x", "z:5555"), Obs(sticks_offline=["z:5555"])) == "connected"


def test_waiting_actions_dont_eat_the_cap(w):
    v = Verdict("reconnect_stick", "x", "h:5555")
    w.act(v, Obs(sticks_offline=["h:5555"]))
    for _ in range(30):
        w.t[0] += 5
        assert w.act(v, Obs(sticks_offline=["h:5555"])) == "reconnected recently; waiting"
    assert len(w.actions) == 1


def test_prune_at_most_every_30_min(w):
    assert w.act(Verdict("prune_disk", "disk"), Obs()) == "freed 5 MB"
    w.t[0] += 600
    assert w.act(Verdict("prune_disk", "disk"), Obs()) == "pruned recently; waiting"
    w.t[0] += watchdog.PRUNE_EVERY_S
    assert w.act(Verdict("prune_disk", "disk"), Obs()) == "freed 5 MB"


def test_an_action_that_raises_is_a_result_not_a_crash(w, monkeypatch):
    def boom(*a, **k):
        raise PermissionError("no access to caches")
    monkeypatch.setattr(watchdog, "prune_caches", boom)
    r = w.act(Verdict("prune_disk", "disk"), Obs())
    assert r.startswith("prune_disk failed: PermissionError")


def test_no_model_or_a_broken_one_turns_new_errors_into_a_note(w, monkeypatch):
    o = Obs(errors={"ERROR redzone.engine: brand new failure KeyError": 3})
    v = w.decide(o)                                 # governor refuses the model
    assert v.action == "notify" and "new error" in v.why
    w.use_model = True
    monkeypatch.setattr(w.gov, "allow", lambda kind: True)

    class Broken:
        def chat(self, msgs):
            raise MemoryError("out of memory")
    monkeypatch.setattr(w, "model", lambda: Broken())
    w.asked.clear()
    v = w.decide(o)
    assert v.action == "notify"


def test_unwritable_journal_and_dead_api_still_check(w, monkeypatch, tmp_path):
    monkeypatch.setattr(watchdog, "JOURNAL", tmp_path / "missing-dir" / "agent.jsonl")
    monkeypatch.setattr(watchdog, "fetch_state", lambda timeout=6.0: None)
    monkeypatch.setattr(watchdog, "recent_errors", lambda *a, **k: {})
    rows = []
    for _ in range(3):
        w.t[0] += 60
        rows.append(w.tick())
    assert rows[-1]["action"] == "restart_service" and rows[-1]["result"] == "restarted"


def test_every_action_is_journaled_with_its_result(w, monkeypatch, tmp_path):
    j = tmp_path / "agent.jsonl"
    monkeypatch.setattr(watchdog, "JOURNAL", j)
    monkeypatch.setattr(watchdog, "fetch_state", lambda timeout=6.0: None)
    monkeypatch.setattr(watchdog, "recent_errors", lambda *a, **k: {})
    for _ in range(3):
        w.t[0] += 61
        w.tick()
    rows = [json.loads(x) for x in j.read_text().splitlines()]
    acted = [r for r in rows if r["action"] != "none"]
    assert acted and all(r["result"] for r in acted)
    assert any(r["action"] == "restart_service" and r["result"] == "restarted" for r in acted)


def test_launchd_exit_78_notifies_the_fix_instead_of_a_restart_loop():
    from agent.policy import rules
    v = rules(Obs(api_ok=False, api_fail_streak=5, launchd_exit=78))
    assert v.action == "notify" and "Full Disk Access" in v.why
    assert rules(Obs(api_ok=False, api_fail_streak=5, launchd_exit=127)).action == "notify"
    assert rules(Obs(api_ok=False, api_fail_streak=5, launchd_exit=1)).action == "restart_service"
    assert rules(Obs(api_ok=False, api_fail_streak=5)).action == "restart_service"


def test_launchd_exit_stays_out_of_the_trained_prompt():
    assert "launchd_exit" not in Obs(launchd_exit=78).text()


def test_launchd_last_exit_parses_launchctl(monkeypatch):
    out = "gui/501/com.redzone.everything = {\n\tstate = not running\n\tlast exit code = 78: EX_CONFIG\n}"
    monkeypatch.setattr(watchdog.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, out, ""))
    assert watchdog.launchd_last_exit() == 78

    def missing(*a, **k):
        raise FileNotFoundError("launchctl")
    monkeypatch.setattr(watchdog.subprocess, "run", missing)
    assert watchdog.launchd_last_exit() is None
