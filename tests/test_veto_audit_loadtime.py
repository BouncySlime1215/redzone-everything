"""The audit's load-time what-if (--prep-s) and its evidence columns: info only, the old numbers never move.

Why: Nick asked "can u load on time". The catch-up route is simulated with a 15 s load; the TV may need 60 s. And the residual
table named causes without saying which gate, which join or which stick job was behind them.
"""
from collections import Counter
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


def test_a_clip_cut_short_still_showed_the_play(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]}, dead=(True, False, 25.0))       # room for the play (11 s), not the whole 30 s clip
    why, info = {}, {}
    shown, stats = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), t, 180, why, info)
    assert shown == {} and stats["pieces"] == 1 and why[air][0] == "abandoned"       # the old column still counts it lost
    assert info["played"] == {air}                                                    # the new one does not: the whole play aired
    row = va.new_row()
    tally = va.Tally(route_on=True, route2=[(1040.0, "a", True)], screen=[(900.0, "a")])
    tally.log["b"].append((980.0, "gate:leave", True, False))
    va.charge_night(tally, t, row)
    assert row["lost_after_catchup"]["180"] == Counter({"gate:leave": 5.0}) and not row["lost_play_unseen"]["180"]


def test_a_moment_never_started_is_lost_in_both_columns(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]}, dead=(False, False, 0.0))
    row = va.new_row()
    tally = va.Tally(route_on=True, route2=[(1040.0, "a", True)], screen=[(900.0, "a")])
    tally.log["b"].append((980.0, "gate:leave", True, False))
    va.charge_night(tally, t, row)
    assert row["lost_after_catchup"]["180"] == row["lost_play_unseen"]["180"] == Counter({"gate:leave": 5.0})


def test_route_off_names_its_gate(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})
    tally = va.Tally(route_on=True, route_off=[(1030.0, "a"), (1040.0, "a"), (1050.0, "a")],
                     route_off_gate=["screen_game_not_live", "home_visit", "home_visit"], screen=[(900.0, "a")])
    why, info = {}, {}
    va.route_shown([air], tally, t, 180, why, info)
    assert why[air][0] == "route_off" and info["gates"][air] == Counter(home_visit=2, screen_game_not_live=1)
    tally.log["b"].append((980.0, "gate:leave", True, False))
    row = va.new_row()
    va.charge_night(tally, t, row)
    assert row["route_off_gate"]["180"] == Counter(home_visit=5.0)


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


def test_own_game_says_when_the_picture_came_up():
    m = va.Air(1000.0, "g", "td", 5.0)                      # the play airs 993 to 1000
    assert va.own_game_why(m, [(900.0, "a"), (996.0, "g")]) == "joined_during_the_play|was_elsewhere"
    assert va.own_game_why(m, [(900.0, "a"), (1010.0, "g")]) == "joined_0_15s_after|was_elsewhere"
    assert va.own_game_why(m, [(900.0, "a"), (1040.0, "g")]) == "joined_15_60s_after|was_elsewhere"
    assert va.own_game_why(m, [(900.0, "a"), (1100.0, "g")]) == "joined_60s_plus_after|was_elsewhere"
    assert va.own_game_why(m, [(800.0, "g"), (950.0, "a"), (1010.0, "g")]) == "joined_0_15s_after|left_it_in_the_run_up"
    assert va.own_game_why(m, [(700.0, "g"), (800.0, "a"), (1010.0, "g")]) == "joined_0_15s_after|was_elsewhere"   # left long before
    assert va.own_game_why(m, [(900.0, "a")]) == "never_joined"


def test_the_hidden_stick_job_is_seek_only_on_the_game_it_holds(monkeypatch):
    b, c = va.Air(1000.0, "b", "td", 5.0), va.Air(1100.0, "c", "td", 5.0)
    t = truth(monkeypatch, {"b": [b], "c": [c]})
    # the screen went b -> a at 945 (picture up 950): the hidden stick still holds b. Then a clip of b, then one of c.
    tally = va.Tally(route_on=True, screen=[(800.0, "b"), (950.0, "a")], route2=[(1030.0, "a", True), (1130.0, "a", True)])
    info = {}
    shown, _ = va.route_shown([b, c], tally, t, 180, {}, info)
    assert set(shown) == {b, c} and info["stick"] == {b: "seek_only", c: "retune"}
    first = va.Tally(route_on=True, screen=[(950.0, "a")], route2=[(1030.0, "a", True)])       # nothing was on screen before: a cold stick
    info = {}
    va.route_shown([b], first, t, 180, {}, info)
    assert info["stick"] == {b: "cold"}


def test_the_detail_lines_print_both_lost_columns():
    row = va.new_row()
    row["league"], row["brain"], row["card"] = "nfl", "hindsight", {"entries_mid_play": 0, "entries": 0, "cuts_high": 0, "mid_play_leaves": 0}
    row["residual"]["180"]["abandoned"] = 5.0
    row["lost_after_catchup"]["180"]["gate:leave"] = 8.0
    row["lost_play_unseen"]["180"]["gate:leave"] = 3.0
    text = "\n".join(va.detail_lines([row]))
    assert "lost_after_catchup (clip not finished)" in text and "8.0" in text and "lost_play_unseen (the play never aired)" in text and "3.0" in text
    assert va.detail_lines([va.new_row() | {"league": "nfl"}]) == []          # route off: nothing extra is printed
