"""Phone control panel: every button is a real engine action (manual hold, resume auto,
TV control, two sticks, RedZone, skip, mute/boost, calibration)."""
import dataclasses
import time

from test_app import client, engine  # noqa: F401  (fixtures)

from redzone.decider.decider import REDZONE, Decision, State
from redzone.models import Phase


def _live(engine):
    """The fixture slate is final; bring two NFL games back to life for these tests."""
    live = [gid for gid, g in engine.merger.games.items() if g.phase == Phase.LIVE]
    if len(live) < 2:
        for gid, g in list(engine.merger.games.items())[:2 - len(live)]:
            g.phase, g.period, g.clock_s = Phase.LIVE, 3, 600.0
            live.append(gid)
        # (re-scored on the next /api/action, inside the server loop)
    return live


def _post(client, action, **kw):
    return client.post("/api/action", json={"action": action, **kw}).json()


def test_watch_this_holds_the_game_until_resume_auto(client, engine):
    a = _live(engine)[0]
    r = _post(client, "hold", game_id=a)
    assert r["ok"], r
    s = client.get("/api/state").json()
    assert s["now"]["game_id"] == a
    hold = s["mode"]["hold"]
    assert hold["game_id"] == a and hold["on"] and hold["auto_resume"] is True
    assert s["mode"]["locked_game"] == a
    # autopilot can't take it away
    engine.decider.user.manual_until = 0
    engine.decide()
    assert engine.decider.current == a
    # Resume auto hands control straight back
    r = _post(client, "release")
    assert r["ok"]
    s = client.get("/api/state").json()
    assert s["mode"]["hold"] is None and s["mode"]["locked_game"] is None
    assert s["mode"]["focus_mode"] == "autopilot" and s["mode"]["paused"] is False
    assert engine.decider.user.manual_until == 0


def test_watch_while_paused_unpauses_and_holds(client, engine):
    a = _live(engine)[0]
    _post(client, "pause")
    assert _post(client, "watch", game_id=a)["ok"]
    assert engine.decider.user.paused is False and engine.hold["game_id"] == a


def test_watch_rejects_a_game_that_isnt_live(client, engine):
    live = _live(engine)
    pre = next(gid for gid, g in engine.merger.games.items() if gid not in live and g.phase != Phase.LIVE)
    assert _post(client, "hold", game_id=pre)["ok"] is False
    assert engine.hold is None


def test_manual_switch_waits_for_a_safe_moment_unless_tapped_again(engine):
    a = _live(engine)[0]
    engine.hold = {"game_id": a, "force": False, "since": time.time(), "auto_resume": True,
                   "resume_at": None, "why": ""}
    d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    assert engine._hold_waits(d)
    # second tap while the switch is still waiting -> force
    engine.decider.user.locked_game = a
    engine._pending_decision = d
    ok, msg = engine.action("hold", a)
    assert ok and "now" in msg
    assert engine.hold["force"] and not engine._hold_waits(d)
    # other user switches (catch-up replays, go back) are untouched
    other = Decision(time.time(), "replay", a, "x", State.PAUSED, kind="user")
    assert not engine._hold_waits(other)


def test_auto_resume_counts_down_when_the_held_game_turns_garbage(client, engine):
    a = _live(engine)[0]
    assert _post(client, "hold", game_id=a)["ok"]
    engine.scored[a] = dataclasses.replace(engine.scored[a], garbage=True)
    engine._hold_check()
    assert engine.hold["resume_at"] and engine.hold["why"] == "is garbage time"
    engine.hold["resume_at"] = time.time() - 1
    engine._hold_check()
    assert engine.hold is None and engine.decider.user.locked_game is None


def test_keep_it_turns_off_auto_resume(client, engine):
    a = _live(engine)[0]
    _post(client, "hold", game_id=a)
    assert _post(client, "hold_auto", value=False)["ok"]
    engine.scored[a] = dataclasses.replace(engine.scored[a], garbage=True)
    engine._hold_check()
    assert engine.hold and engine.hold["resume_at"] is None
    assert client.get("/api/state").json()["mode"]["hold"]["auto_resume"] is False


def test_unlock_and_skip_clear_the_hold(client, engine):
    a = _live(engine)[0]
    _post(client, "hold", game_id=a)
    assert _post(client, "unlock")["ok"] and engine.hold is None
    _post(client, "hold", game_id=a)
    assert _post(client, "skip")["ok"]
    assert engine.hold is None and engine.decider.user.locked_game is None


def test_control_panel_toggles(client, engine):
    assert _post(client, "two_stick", value=False)["ok"]
    assert client.get("/api/state").json()["mode"]["two_stick_enabled"] is False
    assert _post(client, "two_stick", value=True)["ok"]
    assert client.get("/api/state").json()["mode"]["two_stick_enabled"] is True
    assert _post(client, "dry_run", value=True)["ok"]
    assert client.get("/api/state").json()["mode"]["dry_run"] is True
    assert _post(client, "pause")["ok"] and client.get("/api/state").json()["mode"]["paused"] is True
    assert _post(client, "resume")["ok"] and client.get("/api/state").json()["mode"]["paused"] is False
    gid = _live(engine)[0]
    for a in ("boost", "unboost", "mute", "unmute"):
        assert _post(client, a, game_id=gid)["ok"], a
    # calibration is a real action: it refuses honestly in dry run instead of guessing
    r = _post(client, "saw_it")
    assert r["ok"] is False and "Calibrate" in r["message"]


def test_redzone_button_only_when_on_the_air(client, engine):
    assert client.get("/api/state").json()["mode"]["redzone_on"] is False
    r = _post(client, "redzone")
    assert r["ok"] is False and engine.hold is None
    engine._redzone_target = lambda sunday_ticket, dry: "youtubetv:redzone"
    r = _post(client, "redzone")
    assert r["ok"], r
    assert engine.hold["game_id"] == REDZONE and engine.decider.current == REDZONE
    assert client.get("/api/state").json()["mode"]["redzone_on"] is True


def _submit_one(engine, monkeypatch, force: bool, dead_ball_after: bool = False):
    """A held switch to a live game while the game on screen is mid-play: (may_cut, the trace's forced)."""
    import redzone.engine as eng
    from redzone.control import Outcome
    a, b = _live(engine)[:2]
    engine.decider.current = b
    engine.cfg.update({"mode": {"dry_run": False}})
    got, built = {}, {}
    monkeypatch.setattr(engine.control, "submit",
                        lambda target, back, done, may_cut=None, **kw: got.update(done=done, may_cut=may_cut, target=target))
    monkeypatch.setattr(engine, "safe_to_leave", lambda *x, **k: False)   # a play is on screen
    real = eng.trace.build
    monkeypatch.setattr(eng.trace, "build", lambda *x, **k: built.update(k) or real(*x, **k))
    engine.hold = {"game_id": a, "force": False, "since": time.time(), "auto_resume": True, "resume_at": None, "why": ""}
    d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    engine._pending_decision = None
    engine._act(d, b, {})
    first = got["may_cut"]()
    if force:
        engine.hold["force"] = True   # the second "Go now" tap
    if dead_ball_after:
        monkeypatch.setattr(engine, "safe_to_leave", lambda *x, **k: True)   # the dead ball comes
    cut = got["may_cut"]()
    if cut:
        got["done"](Outcome(True, got["target"], 2.0, "ok", False, confirmed_at=time.time(), path="preloaded"))
    return first, cut, built.get("forced")


def test_only_the_second_go_now_tap_is_traced_as_forced(engine, monkeypatch):
    # [Nick, Nov 5] a second "Go now" cuts at once, even mid-play, and its trace says forced (graded apart)
    first, cut, forced = _submit_one(engine, monkeypatch, force=True)
    assert (first, cut, forced) == (False, True, True)


def test_a_single_tap_waits_for_the_dead_ball_and_is_never_forced(engine, monkeypatch):
    first, cut, forced = _submit_one(engine, monkeypatch, force=False, dead_ball_after=True)
    assert (first, cut, forced) == (False, True, False)


def test_autopilot_switches_are_never_forced(engine, monkeypatch):
    engine.hold = None
    a = _live(engine)[0]
    d = Decision(time.time(), "switch", a, "x", State.PAUSED, kind="auto")
    assert not engine._forced_go(d)


def test_trace_events_carry_forced_only_when_set():
    from redzone import trace
    class D: target, action, kind, reason, at = "g", "switch", "user", "x", 100.0
    class O: ok, dry_run, confirmed_at, path, detail = True, False, 102.0, "preloaded", ""
    for f in (True, False):
        t = trace.build(D(), "h", "youtubetv:x", O(), 100.0, now=103.0, forced=f)
        dec = [e for e in trace.events(t) if e.stage.value == "decided"][0]
        assert t["forced"] is f and dec.detail.get("forced") == (True if f else None)


def test_forced_reaches_the_grader_through_the_stored_row_and_clears_after(engine, monkeypatch):
    """The grader's path: store switch row -> trace.of_row -> ["forced"] (Supervisor.grade_cut). And a
    later switch to the same held game, once _hold_check cleared hold.force, isn't forced."""
    from redzone import trace
    from redzone.control import Outcome
    a, b = _live(engine)[:2]
    engine.cfg.update({"mode": {"dry_run": False}})
    got = {}
    monkeypatch.setattr(engine.control, "submit",
                        lambda target, back, done, may_cut=None, **kw: got.update(done=done, may_cut=may_cut, target=target))
    safe = [False]
    monkeypatch.setattr(engine, "safe_to_leave", lambda *x, **k: safe[0])

    def switch(force: bool) -> dict:
        engine.decider.current = b
        engine._pending_decision = None
        d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
        engine._act(d, b, {})
        if force:
            engine.hold["force"] = True
        else:
            safe[0] = True   # a single tap: the dead ball comes
        assert got["may_cut"]()
        got["done"](Outcome(True, got["target"], 2.0, "ok", False, confirmed_at=time.time(), path="preloaded"))
        safe[0] = False
        return trace.of_row(engine.store.recent_switches(1)[0])

    engine.hold = {"game_id": a, "force": False, "since": time.time(), "auto_resume": True, "resume_at": None, "why": ""}
    engine.decider.user.locked_game = a
    assert switch(force=True)["forced"] is True
    engine.decider.current = a
    engine._pending_decision = None
    engine._hold_check()                       # on the held game: the second tap's force is spent
    assert engine.hold is not None and engine.hold["force"] is False
    assert switch(force=False)["forced"] is False


def test_watch_this_refuses_a_game_with_no_channel_instead_of_retrying(engine):
    # Nov 4 live: a hold on a game with no channel failed "no channel for this game" 13 times in 6 s
    from redzone.channels import Where
    a = _live(engine)[0]
    engine.cfg.update({"mode": {"dry_run": False}})
    engine.where[a] = Where(None, "no channel", "unknown", False)
    ok, msg = engine.action("hold", a)
    assert not ok and "No channel" in msg and engine.hold is None and engine.decider.user.locked_game is None


def _plan(ok, rewind=0.0):
    from redzone.decider.cuts import EntryPlan
    return EntryPlan(ok=ok, rewind_s=rewind, landing=None, why="test")


def test_tap_timed_flag_ships_off():
    """[EXP-741] decider.entry.user_timed is off until lane 3's grade turns it on."""
    from redzone.config import default
    assert default("decider.entry.user_timed") is False


def test_tap_timed_waits_for_a_dead_ball_in_the_game_it_joins(engine, monkeypatch):
    """[EXP-741, Nick Nov 4 10:55 PM] one tap: a safe leave is not enough, the joined game's picture must
    come up on a dead ball; a landing that needs a rewind waits too (the tap carries none)."""
    engine.cfg.update({"decider": {"entry": {"user_timed": True}}})
    a = _live(engine)[0]
    d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    engine.scored.setdefault(a, next(iter(engine.scored.values())))
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(False))
    assert engine._tap_lands_dead(d, "youtubetv:espn") is False
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True, 12.0))
    assert engine._tap_lands_dead(d, "youtubetv:espn") is False
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True))
    assert engine._tap_lands_dead(d, "youtubetv:espn") is True
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: None)   # no timing evidence: as before
    assert engine._tap_lands_dead(d, "youtubetv:espn") is True


def test_tap_timed_off_changes_nothing_and_go_now_still_cuts_mid_play(engine, monkeypatch):
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(False))   # play live in the target
    a = _live(engine)[0]
    d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    assert engine._tap_lands_dead(d, "youtubetv:espn") is True          # flag off
    engine.cfg.update({"decider": {"entry": {"user_timed": True}}})
    first, cut, forced = _submit_one(engine, monkeypatch, force=True)   # the second "Go now" tap
    assert (cut, forced) == (True, True)
    first, cut, forced = _submit_one(engine, monkeypatch, force=False, dead_ball_after=True)
    assert (first, cut) == (False, False) and not forced                # leave is safe, landing isn't


def test_tap_rewind_fallback_after_grace(engine, monkeypatch):
    """[EXP-741 B] decider.entry.user_rewind_after_s: a tap that needs a rewind waits out the grace, then
    goes with that rewind armed on the switch at the cut (lane 3: no-rewind taps waited > 180 s)."""
    from redzone.config import default
    assert default("decider.entry.user_rewind_after_s") is None        # ships off
    engine.cfg.update({"decider": {"entry": {"user_timed": True, "user_rewind_after_s": 15}}})
    a = _live(engine)[0]
    engine.scored.setdefault(a, next(iter(engine.scored.values())))
    armed = []
    monkeypatch.setattr(engine.control, "arm_entry_rewind", lambda s: armed.append(s), raising=False)
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True, 30.0))
    fresh = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    assert engine._tap_lands_dead(fresh, "youtubetv:espn") is False and armed == []   # inside the grace
    old = Decision(time.time() - 16, "switch", a, "you selected this game", State.PAUSED, kind="user")
    assert engine._tap_lands_dead(old, "youtubetv:espn") is True and armed == [30.0]
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(False))   # no landing in reach
    assert engine._tap_lands_dead(old, "youtubetv:espn") is False
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True))    # dead now, no rewind
    assert engine._tap_lands_dead(fresh, "youtubetv:espn") is True and armed[-1] == 0.0


def test_arm_entry_rewind_sets_the_hidden_seek_only_on_the_hidden_cue():
    from redzone.control import Controller
    c = Controller.__new__(Controller)
    c._entry_cue = "hidden"
    c.arm_entry_rewind(30)
    assert (c._entry_rewind, c._hidden_entry) == (30.0, 30.0)
    c._entry_cue = "seek"
    c.arm_entry_rewind(30)
    assert (c._entry_rewind, c._hidden_entry) == (30.0, 0.0)


def test_tap_rewind_cap_keeps_waiting(engine, monkeypatch):
    """[EXP-741 D] decider.entry.user_rewind_max_s: a tap never rewinds further than the cap."""
    from redzone.config import default
    assert default("decider.entry.user_rewind_max_s") is None
    engine.cfg.update({"decider": {"entry": {"user_timed": True, "user_rewind_after_s": 15, "user_rewind_max_s": 60}}})
    a = _live(engine)[0]
    engine.scored.setdefault(a, next(iter(engine.scored.values())))
    armed = []
    monkeypatch.setattr(engine.control, "arm_entry_rewind", lambda s: armed.append(s), raising=False)
    old = Decision(time.time() - 30, "switch", a, "you selected this game", State.PAUSED, kind="user")
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True, 93.0))
    assert engine._tap_lands_dead(old, "youtubetv:espn") is False and armed == []
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True, 45.0))
    assert engine._tap_lands_dead(old, "youtubetv:espn") is True and armed == [45.0]


def test_tap_never_lands_on_play_may_be_live(engine, monkeypatch):
    """[EXP-741 F] entry_plan's 'unknown' landing (play may be live) is ok for autopilot, never for a tap."""
    from redzone.decider.cuts import EntryPlan, Landing
    engine.cfg.update({"decider": {"entry": {"user_timed": True}}})
    a = _live(engine)[0]
    engine.scored.setdefault(a, next(iter(engine.scored.values())))
    d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    monkeypatch.setattr(engine.decider, "entry_plan",
                        lambda *x, **k: EntryPlan(True, 0.0, Landing("unknown", why="play may be live")))
    assert engine._tap_lands_dead(d, "youtubetv:espn") is False
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: EntryPlan(True, 0.0, Landing("dead", "foul")))
    assert engine._tap_lands_dead(d, "youtubetv:espn") is True


def test_tap_bug_gate_on_prime(engine, monkeypatch):
    """[EXP-741 E] user_bug_gate on: the hidden stick's bug clock decides a Prime tap; unreadable -> feed plan."""
    from redzone.config import default
    from redzone.screen.scorebug import Bug
    assert default("decider.entry.user_bug_gate") == "off"
    engine.cfg.update({"decider": {"entry": {"user_timed": True, "user_bug_gate": "on"}}})
    a = _live(engine)[0]
    engine.scored.setdefault(a, next(iter(engine.scored.values())))
    d = Decision(time.time(), "switch", a, "you selected this game", State.PAUSED, kind="user")
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(False))   # feed says wait
    tgt, now = "prime:x", time.time()
    engine.tap_bug.reset()
    assert engine._tap_lands_dead(d, tgt) is False                                     # unreadable: feed plan
    engine.tap_bug.add(tgt, now - 3, Bug(50, 48, "3RD", "7:21"))
    engine.tap_bug.add(tgt, now - 1, Bug(50, 48, "3RD", "7:21"))
    assert engine._tap_lands_dead(d, tgt) is True                                      # clock stopped
    engine.tap_bug.add(tgt, now, Bug(50, 48, "3RD", "7:19"))
    assert engine._tap_lands_dead(d, tgt) is False                                     # running again
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True))
    assert engine._tap_lands_dead(d, tgt) is False                                     # bug beats the feed
    engine.cfg.update({"decider": {"entry": {"user_bug_gate": "shadow"}}})
    assert engine._tap_lands_dead(d, tgt) is True                                      # shadow: feed decides


def test_tap_bug_reads():
    from redzone.screen.scorebug import Bug
    from redzone.screen.tap_bug import TapBug
    tb, t = TapBug(), 1000.0
    assert tb.says("p", t) == "unknown"
    tb.add("p", t, Bug(1, 2, "2ND", "45.6"))
    tb.add("p", t + 0.5, Bug(1, 2, "2ND", "45.6"))
    assert tb.says("p", t + 1) == "unknown"            # too close together to call
    tb.add("p", t + 2, Bug(1, 2, "2ND", "45.6"))
    assert tb.says("p", t + 3) == "stopped"
    assert tb.says("p", t + 8) == "unknown"            # stale
    tb.add("p", t + 4, None)                            # unreadable frame doesn't count
    assert tb.says("p", t + 5) == "stopped"
    tb.add("p", t + 6, Bug(1, 2, "3RD", "12:00"))
    assert tb.says("p", t + 6) == "unknown"            # period changed


def test_tap_rewind_grace_counts_from_the_tap_not_the_retry(engine, monkeypatch):
    """A held tap is re-submitted after each 25 s safe-wait give-up: the grace counts from the hold."""
    engine.cfg.update({"decider": {"entry": {"user_timed": True, "user_rewind_after_s": 15}}})
    a = _live(engine)[0]
    engine.scored.setdefault(a, next(iter(engine.scored.values())))
    monkeypatch.setattr(engine.control, "arm_entry_rewind", lambda s: None, raising=False)
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(True, 30.0))
    d = Decision(time.time(), "switch", a, "you locked this game", State.PAUSED, kind="user")
    engine.hold = None
    assert engine._tap_lands_dead(d, "youtubetv:espn") is False
    engine.hold = {"game_id": a, "since": time.time() - 40, "force": False}
    try:
        assert engine._tap_lands_dead(d, "youtubetv:espn") is True
    finally:
        engine.hold = None
