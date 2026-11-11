"""[state machines] The engine's implicit states made explicit (redzone/machines/decider.py: engine.*), and the bugs the
loose flags allowed: each bug test here failed before its fix."""
import time

import pytest

from redzone.config import Config
from redzone.control import Outcome
from redzone.decider.decider import Decision, FocusMode, State
from redzone.decider.screen import DelayBook
from redzone.engine import SKIP_S, Engine
from redzone.store import Store


def make(tmp_path, dry=False):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": dry}})
    return Engine(cfg, Store(tmp_path / "t.db"))


@pytest.fixture
def engine(tmp_path):
    return make(tmp_path)


def test_a_tap_while_a_switch_is_in_flight_resolves_the_old_one(engine, monkeypatch):
    """Bug: _act_manual -> _act while a switch was pending overwrote _pending_decision, so the old switch's done()
    returned early: no confirm / reject, no store row, and its proposed break visit (_proposal_home) went stale."""
    e = engine
    dones = []
    monkeypatch.setattr(e.control, "submit", lambda target, back, done, **kw: dones.append(done))
    e.decider.current = "g1"
    d1 = Decision(time.time(), "switch", "g2", "a break visit", State.BREAK, kind="break_visit")
    e.decider._proposal_home = (d1, "g1", None)
    e._act(d1, "g1", {})
    assert e._pending_decision is d1 and e.pending_m.is_("pending")
    e._act_manual("g3")                       # your tap while d1 is still in flight
    rows = e.store.recent_switches(5)
    assert len(rows) == 1 and rows[0]["to_game"] == "g2" and not rows[0]["ok"]
    assert e.decider._proposal_home is None
    assert e._pending_decision is not None and e._pending_decision.target == "g3"
    assert e.decider.start_fails == 0 and e.decider.safe_mode is False   # superseded: no backoff, no error
    assert any("superseded" in h[3] for h in e.pending_m.history)
    dones[0](Outcome(False, None, 1.0, "switch cancelled by a newer command", False))   # the controller's late cancel
    assert len(e.store.recent_switches(5)) == 1 and e._pending_decision.target == "g3"


def test_a_skip_still_unmutes_after_a_restart(tmp_path):
    """Bug: a skip mutes its game and muted is kept 12 h, but skipped (its 5-minute expiry) wasn't: after a restart the
    game stayed muted all night."""
    e = make(tmp_path)
    e.decider.current = "g1"
    assert e.action("skip")[0]
    e.decider.current = "g2"
    e.decide()
    assert e.skip_m.state("g1") == "muted"
    e._save_user_state()
    again = make(tmp_path)
    assert "g1" in again.decider.user.muted and "g1" in again.skipped
    again.skipped["g1"] = time.time() - 1     # its five minutes are up
    again.decide()
    assert "g1" not in again.decider.user.muted and "g1" not in again.skipped
    assert again.skip_m.state("g1") == "none"


def test_an_expired_skip_is_unmuted_at_restart(tmp_path):
    e = make(tmp_path)
    e.decider.user.muted.add("g1")
    e.skipped["g1"] = time.time() - SKIP_S    # long expired by the time it restarts
    e._save_user_state()
    again = make(tmp_path)
    assert "g1" not in again.decider.user.muted and not again.skipped


def test_a_focus_suspended_for_garbage_time_survives_a_restart(tmp_path):
    """Bug: _suspended_focus wasn't persisted: a restart in garbage time lost your focus for good."""
    e = make(tmp_path)
    u = e.decider.user
    u.focus_game, u.focus_mode = "g1", FocusMode.AUTOPILOT
    e._suspended_focus = ("g1", FocusMode.FOCUS)
    assert e.focus_m.is_("suspended")
    e._save_user_state()
    again = make(tmp_path)
    assert again._suspended_focus == ("g1", FocusMode.FOCUS) and again.focus_m.is_("suspended")


def test_standby_notes_clear_when_jump_mode_goes_off(engine):
    """Bug: the jump standby notes were never cleared with jump mode off (jump_status / snapshot kept a stale plan)."""
    e = engine
    e._jump_standby_note = {"standby_plan": "rotation", "want": "g2"}
    e.cfg.update({"jump_mode": {"enabled": False}})
    e._preload_standby({})
    assert e._jump_standby_note == {"standby_plan": "jump mode off"}
    assert e.standby_m.state in ("idle", "planner")
    assert e.snapshot()["machines"]["standby_plan"]["standby_plan"] == "jump mode off"


def test_fail_cool_is_a_keyed_machine_behind_a_dict(engine):
    e = engine
    e._fail_cool["youtubetv:a"] = time.time() + 90
    assert e._fail_cool.m.state("youtubetv:a") == "cooling"
    e._fail_cool.pop("youtubetv:a")
    assert e._fail_cool.m.state("youtubetv:a") == "ok" and "youtubetv:a" not in e._fail_cool
    e._fail_cool = {"prime:x": time.time() + 50}          # the supervisor assigns a plain dict
    assert e._fail_cool == {"prime:x": pytest.approx(time.time() + 50, abs=2)}
    assert e._fail_cool.m.state("prime:x") == "cooling"
    e._fail_cool.clear()
    assert not e._fail_cool and e._fail_cool.m.state("prime:x") == "ok"


def test_hold_is_one_machine(engine):
    e = engine
    e.hold = {"game_id": "g1", "since": time.time(), "auto_resume": True, "resume_at": None, "why": ""}
    assert e.hold_m.is_("held")
    e.hold = None
    assert e.hold_m.is_("none")


def test_a_label_read_before_a_confirmed_fresh_tune_is_dropped():
    """[edge model] A label read stayed this target's behind_live until a fresh tune was REPORTED (_sync_tune); a
    tune the controller confirmed without that report left it stale. A read after the command is kept."""
    t = [1000.0]
    book = DelayBook({"youtubetv": 35.0}, sigma_default=15.0, clock=lambda: t[0])
    book.set_label_behind("youtubetv:espn", 60.0, sd=1.0, ts=990.0)
    assert book.label_m.state("youtubetv:espn") == "read"
    assert not book.clear_label_before("youtubetv:espn", 980.0)      # read after that command: the new tune's own
    assert book.clear_label_before("youtubetv:espn", 995.0)
    assert "youtubetv:espn" not in book.labels and book.label_m.state("youtubetv:espn") == "none"


def test_snapshot_lists_the_machines(engine):
    m = engine.snapshot()["machines"]
    for k in ("owner", "screen", "home", "pending", "standby_plan", "hold", "safe", "override", "lock_wait"):
        assert k in m, k
