"""Phone controls land exactly once and fast: tap ids (redzone/commands.py), the /api/action ack and
status, and a game opened on the phone pre-tuning the hidden stick (two_stick.pretune_on_open)."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from redzone.commands import Commands, valid_id
from redzone.config import Config
from redzone.engine import Engine
from redzone.models import Phase
from redzone.store import Store
from redzone.web.server import build_app


# --- the tap log -----------------------------------------------------------------------------

@pytest.mark.playbook("BTN-03")
def test_a_tap_with_an_id_runs_once_and_a_retry_gets_the_first_answer():
    log, calls = Commands(), []
    fn = lambda: calls.append(1) or (True, "Skipping")   # noqa: E731
    a = log.run("tap-000001", "skip", None, None, fn)
    b = log.run("tap-000001", "skip", None, None, fn)
    assert calls == [1] and a["ok"] and not a["duplicate"]
    assert b["duplicate"] and b["message"] == "Skipping" and log.duplicates == 1 and log.runs == 1
    assert log.status("tap-000001")["retries"] == 1


def test_a_tap_without_a_usable_id_runs_every_time():
    log, calls = Commands(), []
    fn = lambda: calls.append(1) or (True, "")   # noqa: E731
    for cid in (None, None, "x", "x", "bad id with spaces!"):
        log.run(cid, "pause", None, None, fn)
    assert len(calls) == 5 and valid_id("x") is None and valid_id("a" * 65) is None


def test_a_crashing_tap_is_an_answer_not_a_crash_and_isnt_rerun():
    log, calls = Commands(), []

    def boom():
        calls.append(1)
        raise RuntimeError("adb went away")
    a = log.run("tap-000002", "skip", None, None, boom)
    b = log.run("tap-000002", "skip", None, None, boom)
    assert not a["ok"] and "adb went away" in a["message"] and b["duplicate"] and calls == [1]


def test_old_taps_expire():
    log = Commands(keep=2, ttl_s=60)
    for i in range(3):
        log.run(f"tap-00000{i}", "pause", None, None, lambda: (True, ""))
    assert log.status("tap-000000") is None and log.status("tap-000002")
    log._log["tap-000002"]["t"] = time.time() - 61
    assert log.status("tap-000002") is None


# --- the server --------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    return Engine(cfg, Store(tmp_path / "rz.db"))


@pytest.mark.playbook("BTN-03")
def test_a_retried_post_runs_the_action_once(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "action", lambda a, g=None, v=None: calls.append((a, g, v)) or (True, "Skipping"))
    c = TestClient(build_app(engine))
    body = {"action": "skip", "id": "phone-abc123"}
    r1 = c.post("/api/action", json=body).json()
    r2 = c.post("/api/action", json=body).json()
    assert calls == [("skip", None, None)]
    assert r1 == {**r1, "ok": True, "id": "phone-abc123", "duplicate": False, "state": "done"}
    assert r2["duplicate"] and r2["message"] == "Skipping"
    s = c.get("/api/action/phone-abc123").json()
    assert s["ok"] and s["action"] == "skip" and s["state"] == "done"
    assert c.get("/api/action/never-sent-1").status_code == 404


def test_a_retried_watch_this_is_not_a_go_now(engine, monkeypatch):
    """The reason ids matter: a second "Watch this" means "Go now" (cut over at once, even mid-play),
    so a lost answer resent must not run it again."""
    calls = []
    monkeypatch.setattr(engine, "action", lambda a, g=None, v=None: calls.append((a, g, v)) or (True, "Switching"))
    c = TestClient(build_app(engine))
    for _ in range(3):
        r = c.post("/api/action", json={"action": "hold", "game_id": "g1", "id": "phone-hold01"}).json()
    assert calls == [("hold", "g1", None)]
    assert r["state"] == "waiting" and r["on_tv"] is False   # not on the TV yet: the phone keeps checking
    engine.decider.current = "g1"
    s = c.get("/api/action/phone-hold01").json()
    assert s["state"] == "done" and s["on_tv"] is True


def test_taps_without_an_id_still_work(engine):
    c = TestClient(build_app(engine))
    r = c.post("/api/action", json={"action": "pause"}).json()
    assert r["ok"] and r["id"] is None and engine.decider.user.paused


# --- a game opened on the phone ------------------------------------------------------------------

@pytest.fixture
def peeking(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(engine.control, "preload", lambda target, back_s=None, replay_id=None, **kw:
                        calls.append((target, kw)) or True)
    engine.merger.games["g2"] = SimpleNamespace(game_id="g2", phase=Phase.LIVE)
    engine.where["g2"] = SimpleNamespace(target="youtubetv:fox")
    engine.decider.current = "g1"
    return engine, calls


def test_opening_a_game_does_nothing_with_the_flag_off(peeking):
    e, calls = peeking
    assert e.action("peek", "g2") == (True, "")
    assert calls == [] and e.peek is None


def test_opening_a_live_game_pretunes_it_and_the_planner_leaves_it(peeking, monkeypatch):
    e, calls = peeking
    e.cfg.update({"two_stick": {"pretune_on_open": True}})
    assert e.action("peek", "g2") == (True, "")
    assert calls == [("youtubetv:fox", {"user": True})]
    monkeypatch.setattr(type(e.control), "two_stick", property(lambda self: True))
    planned = []
    monkeypatch.setattr(e.planner, "plan", lambda *a, **k: planned.append(1))
    monkeypatch.setattr(e, "_standby_options", lambda *a, **k: [])
    e._preload_standby({})
    assert planned == []                                     # your pick stays on the hidden stick
    e.peek = ("g2", "youtubetv:fox", time.time() - 1)        # ... for PEEK_HOLD_S
    e._preload_standby({})
    assert planned == [1] and e.peek is None


def test_opening_the_game_on_screen_or_a_finished_one_tunes_nothing(peeking):
    e, calls = peeking
    e.cfg.update({"two_stick": {"pretune_on_open": True}})
    e.action("peek", "g1")
    e.merger.games["g2"].phase = Phase.POST
    e.action("peek", "g2")
    e.decider.user.paused = True
    e.merger.games["g2"].phase = Phase.LIVE
    e.action("peek", "g2")
    assert calls == []


def test_a_skip_counts_its_five_minutes_from_when_the_tv_leaves(engine):
    """Controls drill, seed 4: Skip during a hockey power play waited ~5 min for a stoppage, the skip
    expired first, and the TV never left. Now it holds while the skipped game is still on screen."""
    e = engine
    e.decider.current = "g1"
    e.skipped["g1"] = time.time() - 1          # five minutes up, but we never left
    e.decider.user.muted.add("g1")
    e.decide()
    assert "g1" in e.decider.user.muted and e.skipped["g1"] > time.time() + 200
    e.decider.current = "g2"                   # the TV left it: now the five minutes run
    e.decide()
    assert "g1" in e.decider.user.muted
    e.skipped["g1"] = time.time() - 1
    e.decide()
    assert "g1" not in e.decider.user.muted and "g1" not in e.skipped


def test_tapping_the_game_on_screen_mid_switch_away_goes_back(engine, monkeypatch):
    """playbook=BTN-05 (controls drill, mid_flip): "Watch this" + "Go now" on the game on screen while
    a switch away from it is landing used to answer "Holding on X" while the TV landed elsewhere."""
    from redzone.decider.decider import Decision, State
    e = engine
    e.merger.games["g1"] = SimpleNamespace(game_id="g1", phase=Phase.LIVE)
    e.decider.current = "g1"
    e._pending_decision = Decision(time.time(), "switch", "g2", "best game on", State.WATCHING, kind="live")
    went = []
    monkeypatch.setattr(e, "_act_manual", lambda gid, replay=None: went.append(gid))
    monkeypatch.setattr(e, "_name", lambda gid: gid)
    ok, msg = e.action("hold", "g1")
    assert ok and went == ["g1"] and "as soon as this switch lands" in msg
    assert e.action("hold", "g1", "force")[0] and e.hold["force"]
    e._hold_check()
    assert e.hold["force"]                      # still landing elsewhere: keep "now"
    e._pending_decision = None
    went.clear()
    assert e.action("hold", "g1")[1].startswith("Holding on g1") and went == []
