"""scripts/deploy.py's lifecycle as one explicit, persisted state (deploy.lifecycle) and the bugs it fixed."""
from __future__ import annotations

import json

import pytest

from scripts import deploy as D
from test_deploy import T0, state, world  # noqa: F401  (world is a fixture)


def test_a_deploy_persists_its_state_through_the_lifecycle(world):  # noqa: F811
    w = world
    seen = []
    dep = w.deployer()
    real_watch = dep.watch

    def watch(before, base_err):
        seen.append((state(w)["machine"]["state"], D.deploy_in_progress(w.mac, w.fake.now()),
                     (w.mac / "data/deploy/in_progress.json").exists()))
        return real_watch(before, base_err)
    dep.watch = watch
    assert dep.deploy(skip_tests=True) == D.EXIT_OK
    assert seen == [("watching", True, True)]             # gate, watchdog marker and state agree mid-watch
    st = state(w)
    assert st["machine"]["state"] == "deployed" and "until" not in st["machine"]
    assert not D.deploy_in_progress(w.mac, w.fake.now())


def test_a_dead_deploy_process_reads_as_interrupted_for_gate_watchdog_and_status(world):  # noqa: F811
    """Bug: the watchdog read only the time-limited marker and the gate only `pending`. Now both read the
    one state: ACTIVE until `until` for the watchdog, unfinished for the gate, whatever the marker says."""
    w = world
    w.deployer()._save_state({"good": {"commit": w.a, "snapshot": None}, "pending": {"commit": w.b},
                              "machine": {"state": "watching", "since": T0, "until": T0 + 600}})
    assert D.deploy_in_progress(w.mac, T0 + 10)            # no marker file at all, still standing aside
    assert not D.deploy_in_progress(w.mac, T0 + 700)
    lines = []
    dep = w.deployer()
    dep.out = lines.append
    dep.status()
    assert any(line.startswith("deploy state:   interrupted") for line in lines)
    with pytest.raises(D.DeployError, match="never finished"):
        w.deployer().deploy(skip_tests=True)
    assert state(w)["machine"]["state"] == "interrupted"
    assert not D.deploy_in_progress(w.mac, T0 + 10)        # interrupted is not ACTIVE: restarts allowed again


def test_confirm_refuses_after_a_failed_rollback_unless_forced(world):  # noqa: F811
    """Bug: confirm() dropped `pending` even when rollback_failed was set, marking HEAD good unchecked."""
    w = world
    w.fake.behaviour[w.b] = "dead"
    w.fake.behaviour[w.a] = "dead"
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_ROLLBACK_FAILED
    assert state(w)["machine"]["state"] == "rollback_failed"
    with pytest.raises(D.DeployError, match="rollback failed"):
        w.deployer().confirm()
    assert state(w)["pending"]["rollback_failed"]           # untouched
    assert D.main(["confirm"], system=w.fake, root=w.mac) == D.EXIT_ERROR
    assert D.main(["confirm", "--force"], system=w.fake, root=w.mac) == D.EXIT_OK
    st = state(w)
    assert "pending" not in st and st["machine"]["state"] == "deployed"


def test_old_state_file_with_rollback_failed_is_read_the_same_way(world):  # noqa: F811
    w = world
    w.deployer()._save_state({"good": {"commit": w.a, "snapshot": None},
                              "pending": {"commit": w.b, "rollback_failed": True}})
    with pytest.raises(D.DeployError, match="rollback failed"):
        w.deployer().confirm()
    assert w.deployer().confirm(force=True) == D.EXIT_OK


def test_rollback_to_keeps_previous_and_snapshots_the_config_it_left_in_place(world):  # noqa: F811
    """Bug: `rollback --to` stored snapshot=None and dropped `previous`."""
    w = world
    assert w.deployer().deploy(skip_tests=True) == D.EXIT_OK          # good = b, previous = a
    (w.mac / "data/config.yaml").write_text("teams: {nfl: [WSH], nba: [WAS]}\n")
    assert w.deployer().rollback(to=w.a) == D.EXIT_OK
    st = state(w)
    assert st["good"]["commit"] == w.a and st["good"]["snapshot"]
    assert st["previous"]["commit"] == w.b                             # the version we left, still protected
    assert st["machine"]["state"] == "rolled_back"
    snap = w.deployer().snaps.get(st["good"]["snapshot"])
    assert "data/config.yaml" in snap["files"]                         # the config as it was left


def test_status_says_when_a_deploy_holds_the_lock(world):  # noqa: F811
    """Bug: status() read the state with no regard for a running deploy."""
    w = world
    d = w.deployer()
    d._save_state({"machine": {"state": "watching", "since": T0, "until": T0 + 600}, "pending": {"commit": w.b}})
    lines = []
    reader = w.deployer()
    reader.out = lines.append
    with d.lock():
        reader.status()
    assert "watching (a deploy is running now)" in lines[0]
    assert json.loads((w.mac / "data/deploy/state.json").read_text())["machine"]["state"] == "watching"
