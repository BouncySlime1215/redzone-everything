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
