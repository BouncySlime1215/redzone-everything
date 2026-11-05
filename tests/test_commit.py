"""[commit] EXP-036: the phone's commit tap (my teams / stop after a game / end at a time) lands once,
is kept on the decider's UserState and survives a restart. Serves: EXP-036. Owner: lane 3."""
import time

from fastapi.testclient import TestClient

from redzone import userstate
from redzone.config import Config
from redzone.engine import Engine
from redzone.store import Store
from redzone.web.server import build_app


def make(tmp_path, on=True):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    if on:
        cfg.update({"phone": {"commit": True}})
    return Engine(cfg, Store(tmp_path / "t.db"))


def test_the_flag_is_off_by_default_and_then_the_tap_changes_nothing(tmp_path):
    e = make(tmp_path, on=False)
    ok, msg = e.action("commit", None, {"mode": "my_teams"})
    assert not ok and "isn't on" in msg and e.decider.user.commit is None


def test_each_mode_lands_on_userstate_and_bad_ones_are_refused(tmp_path):
    e, now = make(tmp_path), time.time()
    assert e.action("commit", None, {"mode": "my_teams"})[0] and e.decider.user.commit == {"mode": "my_teams"}
    assert e.action("commit", "g7", {"mode": "stop_after"})[0]
    assert e.decider.user.commit == {"mode": "stop_after", "game_id": "g7"}
    assert e.action("commit", None, {"mode": "end_at", "at": now + 3600})[0]
    assert e.decider.user.commit == {"mode": "end_at", "at": now + 3600}
    for bad in ({"mode": "forever"}, {"mode": "end_at", "at": now - 5}, {"mode": "end_at"}, "soon"):
        ok, _ = e.action("commit", None, bad)
        assert not ok and e.decider.user.commit == {"mode": "end_at", "at": now + 3600}   # a bad tap keeps the last one
    assert e.decider.current is None and not e.action("commit", None, {"mode": "stop_after"})[0]   # no game named
    assert e.action("commit", None, {"mode": "off"})[0] and e.decider.user.commit is None
    assert e.snapshot()["mode"]["commit"] is None


async def test_a_commit_survives_a_restart_and_a_past_end_time_still_ends_the_night(tmp_path):
    e = make(tmp_path)
    e.action("commit", "g7", {"mode": "stop_after", "game_id": "g7"})
    await e.close()
    again = make(tmp_path)
    assert again.decider.user.commit == {"mode": "stop_after", "game_id": "g7"}
    assert again.snapshot()["mode"]["commit"] == {"mode": "stop_after", "game_id": "g7"}
    await again.close()
    path = tmp_path / "user_state.json"
    state = userstate.capture(again.decider.user, None)
    at = time.time() + 60
    userstate.save(path, {**state, "commit": {"mode": "end_at", "at": at}}, now=time.time())
    assert userstate.load(path, now=time.time() + 120)["commit"] == {"mode": "end_at", "at": at}   # passed while down: kept
    assert userstate.load(path, now=time.time() + userstate.STALE_S + 60)["commit"] is None       # tomorrow: gone


def test_a_retried_commit_tap_runs_once_with_the_usual_ack(tmp_path):
    e = make(tmp_path)
    c = TestClient(build_app(e))
    body = {"action": "commit", "value": {"mode": "my_teams"}, "id": "phone-commit1"}
    r1, r2 = c.post("/api/action", json=body).json(), c.post("/api/action", json=body).json()
    assert r1["ok"] and r1["state"] == "done" and not r1["duplicate"] and r2["duplicate"]
    assert e.commands.runs == 1 and c.get("/api/action/phone-commit1").json()["action"] == "commit"
