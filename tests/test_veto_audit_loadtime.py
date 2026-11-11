"""The audit's load-time what-if (--prep-s) and the name of the gate that shut the route: info only, the old numbers never move.

Why: Nick asked "can u load on time". The catch-up route is simulated with a 15 s load; the TV measured 18-25 s (Nov 9).
Ported from the unmerged ZERO-LOSS-5 branch (commit 99ab8f45 and its tests), the evidence columns left out.
"""
from types import SimpleNamespace as NS

from redzone.decider.decider import FocusMode
from redzone.noggin import catchup as C
from scripts import veto_audit as va
from tests.test_exits_catchup import Cfg, truth


def test_the_default_load_time_is_the_route_default():
    assert va.DEFAULT_PREP_S == va.PREP_S == C.Route().prep_s == 15.0


def test_a_slower_stick_is_ready_later(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})
    early, late = va.Tally(route_on=True, route2=[(1020.0, "a", True)]), va.Tally(route_on=True, route2=[(1065.0, "a", True)])
    assert va.route_shown([air], early, t, 180)[0] == {air: False}
    monkeypatch.setattr(va, "PREP_S", 60.0)
    assert va.route_shown([air], early, t, 180)[0] == {}             # 20 s after it aired a 60 s stick is not on the clip yet
    assert va.route_shown([air], late, t, 180)[0] == {air: False}


def test_run_one_takes_the_load_time_from_its_job(monkeypatch):
    monkeypatch.setattr(va, "replay", lambda *a: (_ for _ in ()).throw(RuntimeError("stop here")))
    monkeypatch.setattr(va, "PREP_S", 15.0)
    try:
        va.run_one(("nba", "hindsight", {}, 1, 45.0))
    except RuntimeError:
        pass
    assert va.PREP_S == 45.0


def gate_host(**kw):
    d = va.AuditDecider.__new__(va.AuditDecider)
    d.cfg = Cfg(**kw.pop("cfg", {}))
    d.user = NS(paused=False, locked_game=None, manual_until=0.0, focus_mode=FocusMode.AUTOPILOT)
    d._home, d.safe_mode = None, False
    for k, v in kw.items():
        setattr(d, k, v)
    return d


def test_the_gate_named_is_the_first_one_catchup_gates_checks():
    cur = object()
    assert gate_host()._route_gate(None, 100.0) == "screen_game_not_live"
    assert gate_host(_home="h")._route_gate(cur, 100.0) == "home_visit"
    assert gate_host(_home="h")._route_gate(None, 100.0) == "screen_game_not_live"
    assert gate_host(cfg={"replay.enabled": False})._route_gate(cur, 100.0) == "replays_off"
    assert gate_host(cfg={"jump_mode.enabled": True, "buffered_jump.enabled": True})._route_gate(cur, 100.0) == "buffered_jump"
    d = gate_host()
    d.user.manual_until = 200.0
    assert d._route_gate(cur, 100.0) == "manual_pause" and d._route_gate(cur, 300.0) == "unnamed"
