"""Pause, "Watch this", focus and lock survive a service restart (data/user_state.json)."""
import json
import time
from types import SimpleNamespace

import pytest

from redzone import userstate
from redzone.config import Config
from redzone.control import Outcome
from redzone.decider.decider import FocusMode
from redzone.engine import Engine
from redzone.store import Store
from conftest import nfl


def make(tmp_path):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}})
    return Engine(cfg, Store(tmp_path / "t.db"))


def live(e, gid="g1"):
    g = nfl(gid, q=3, clock=600.0)
    e.merger.update("espn_scoreboard", g)
    e.decider.timeline(g.game_id, g.league).observe(g)
    e.discovery.choose = lambda game, base: SimpleNamespace(target="youtubetv:espn", tunable=True, label="ESPN")
    return g


async def test_pause_survives_a_restart(tmp_path):
    e = make(tmp_path)
    e.action("pause")
    e._save_user_state()
    await e.close()
    again = make(tmp_path)
    assert again.decider.user.paused
    assert again.snapshot()["mode"]["paused"] is True
    again.action("resume")
    again._save_user_state()
    await again.close()
    assert not make(tmp_path).decider.user.paused


async def test_focus_and_lock_survive_a_restart(tmp_path):
    e = make(tmp_path)
    u = e.decider.user
    u.focus_game, u.focus_mode, u.locked_game = "g1", FocusMode.FOCUS_MUST_SEE, "g2"
    e._save_user_state()
    await e.close()
    u = make(tmp_path).decider.user
    assert (u.focus_game, u.focus_mode, u.locked_game) == ("g1", FocusMode.FOCUS_MUST_SEE, "g2")


async def test_watch_this_survives_a_restart_and_goes_back_to_the_game(tmp_path):
    e = make(tmp_path)
    g = live(e)
    e.control.submit = lambda *a, **kw: None
    e.scored[g.game_id] = e.scorer.score(g, e.personal())
    ok, _ = e.action("hold", g.game_id)
    assert ok and e.hold
    e.hold["auto_resume"] = False
    e._save_user_state()
    await e.close()

    again = make(tmp_path)
    assert again.hold and again.hold["game_id"] == "g1" and again.hold["auto_resume"] is False
    assert again.decider.user.locked_game == "g1" and not again.decider.user.paused
    # Before the first poll the game isn't known yet: the hold is not "over".
    again._hold_check()
    assert again.hold and again.hold["resume_at"] is None
    calls = []
    again.control.submit = lambda target, back, done, **kw: calls.append((target, done))
    live(again)
    again.decide()
    assert calls and calls[0][0] == "youtubetv:espn"
    assert again.last_decision.target == "g1" and again.last_decision.kind == "user"
    # A failed first try is retried after a backoff, not every tick.
    calls[0][1](Outcome(False, "youtubetv:espn", 1.0, "switch cancelled; playback not confirmed", False))
    again.decide()
    assert len(calls) == 1 and again.snapshot()["next"]["game_id"] == "g1"


async def test_nothing_saved_starts_clean_and_a_tap_is_written(tmp_path):
    e = make(tmp_path)
    u = e.decider.user
    assert not u.paused and u.locked_game is None and e.hold is None
    e._save_user_state()   # nothing changed: nothing written
    assert not (tmp_path / "user_state.json").exists()
    e.action("lock", "g9")
    e._save_user_state()
    assert json.loads((tmp_path / "user_state.json").read_text())["locked_game"] == "g9"


def test_stale_game_choices_are_dropped_but_pause_is_kept(tmp_path):
    p = tmp_path / "user_state.json"
    now = time.time()
    userstate.save(p, {"paused": True, "locked_game": "g1", "focus_game": "g2", "focus_mode": "focus",
                       "hold": {"game_id": "g1", "since": now, "auto_resume": True}},
                   now=now - userstate.STALE_S - 60)
    s = userstate.load(p, now=now)
    assert s["paused"] is True
    assert s["locked_game"] is None and s["focus_game"] is None and s["hold"] is None


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"focus_mode": "bogus", "focus_game": "g1", "saved": 1e18}'])
def test_a_broken_file_never_breaks_start(tmp_path, text):
    p = tmp_path / "user_state.json"
    p.write_text(text)
    e = make(tmp_path)
    assert e.decider.user.focus_mode == FocusMode.AUTOPILOT and e.decider.user.focus_game is None
