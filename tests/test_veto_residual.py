"""scripts/veto_audit.py `residual`: why the catch-up route did not air a moment that is still lost. Info only.

Why: "value lost to rules" has to reach 0, and the next fix is chosen by which cause holds the most value. A wrong cause sends the
next fix at the wrong rule, and the attribution must never move what the route itself airs.
"""
from types import SimpleNamespace as NS

import pytest

from redzone.models import League
from scripts import veto_audit as va

AIR = va.Air(1000.0, "b", "td", 5.0)


def truth(monkeypatch, moments, dead=(True, False, 100.0), wall=(0.0, 9000.0)):
    t = va.Truth()
    t.lag, t.tune_s, t.moments = 0.0, 5.0, moments
    t.replays = {"a": NS(league=League.NFL, wall=list(wall), plays=[{} for _ in wall], gid="a")}
    monkeypatch.setattr(va, "dead_at", dead if callable(dead) else lambda *a: dead)
    return t


def cause(monkeypatch, tally, dead=(True, False, 100.0), unseen=(AIR,), reach=180, wall=(0.0, 9000.0), of=AIR):
    why: dict = {}
    shown, _ = va.route_shown(list(unseen), tally, truth(monkeypatch, {"b": list(unseen)}, dead, wall), reach, why)
    assert of not in shown
    return why[of][0]


def test_every_cause_has_one_name_and_a_shown_moment_has_none(monkeypatch):
    assert len(set(va.CAUSES)) == len(va.CAUSES) and set(va.STICK_BUSY) <= set(va.CAUSES)
    why: dict = {}
    shown, _ = va.route_shown([AIR], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), truth(monkeypatch, {"b": [AIR]}), 180, why)
    assert shown == {AIR: False} and why == {}


def test_no_tick_in_the_window_and_a_shut_route(monkeypatch):
    assert cause(monkeypatch, va.Tally(route_on=True)) == "no_stay_tick"
    assert cause(monkeypatch, va.Tally(route_on=True, route2=[(1170.0, "a", True)])) == "no_stay_tick"      # past the cap: not its window
    assert cause(monkeypatch, va.Tally(route_on=True, route_off=[(1040.0, "a")])) == "route_off"


def test_the_screen_decides_mid_play_too_short_off_air_and_its_own_game(monkeypatch):
    tick = va.Tally(route_on=True, route2=[(1040.0, "a", True)])
    assert cause(monkeypatch, tick, dead=(False, False, 0.0)) == "screen_mid_play"
    assert cause(monkeypatch, tick, dead=(True, False, 15.0)) == "dead_too_short"
    assert cause(monkeypatch, tick, dead=(True, False, 0.0), wall=(0.0, 900.0)) == "screen_not_started"      # the game on screen has ended
    own = va.Air(1000.0, "a", "td", 5.0)
    assert cause(monkeypatch, tick, unseen=(own,), of=own) == "own_game_on_screen"


def test_a_held_game_closes_a_plain_dead_ball(monkeypatch):
    assert cause(monkeypatch, va.Tally(route_on=True, route2=[(1040.0, "a", False)])) == "route_closed"


def test_the_hidden_stick_is_busy_three_ways(monkeypatch):
    assert cause(monkeypatch, va.Tally(route_on=True, route2=[(1040.0, "a", True)], screen=[(1035.0, "a")])) == "stick_prep"     # a cut 10 s ago
    other = va.Air(1001.0, "c", "td", 5.0)
    two = va.Tally(route_on=True, route2=[(1040.0, "a", True), (1050.0, "a", True)])
    got = {}
    shown, _ = va.route_shown([AIR, other], two, truth(monkeypatch, {"b": [AIR], "c": [other]}), 95, got)
    lost = AIR if other in shown else other
    assert len(shown) == 1 and got[lost][0] == "queued"            # the first tick took the other clip; the second was on that clip
    one = va.Tally(route_on=True, route2=[(1024.0, "a", True), (1050.0, "a", True)])
    late = va.Air(1030.0, "c", "td", 5.0)                           # set up only after the first clip took the screen
    got = {}
    shown, _ = va.route_shown([va.Air(1000.0, "b", "td", 5.0), late], one, truth(monkeypatch, {}), 180, got)
    assert late not in shown and got[late][0] == "screen_on_clip"


def test_a_clip_that_started_and_never_finished_is_abandoned(monkeypatch):
    assert cause(monkeypatch, va.Tally(route_on=True, route2=[(1040.0, "a", True)]), dead=(True, False, 25.0)) == "abandoned"


def test_an_opening_after_the_cap_is_counted_as_past_cap(monkeypatch):
    why: dict = {}
    calls = iter([(False, False, 0.0), (True, False, 100.0)])
    t = truth(monkeypatch, {"b": [AIR]}, dead=lambda *a: next(calls))
    va.route_shown([AIR], va.Tally(route_on=True, route2=[(1040.0, "a", True), (1300.0, "a", True)]), t, 180, why)
    assert why[AIR] == ("screen_mid_play", True)
    why = {}
    va.route_shown([AIR], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), truth(monkeypatch, {"b": [AIR]}, (False, False, 0.0)), 180, why)
    assert why[AIR] == ("screen_mid_play", False)


@pytest.mark.parametrize("dead", [(True, False, 100.0), (True, False, 25.0), (False, False, 0.0), (True, True, 60.0)])
def test_asking_for_causes_never_changes_what_the_route_airs(monkeypatch, dead):
    a, b, c = va.Air(1000.0, "b", "td", 5.0), va.Air(1012.0, "c", "rz_trip", 3.0), va.Air(1100.0, "b", "fg", 1.0)
    tally = va.Tally(route_on=True, route2=[(float(n), "a", n % 20 != 0) for n in range(1010, 1400, 10)], route_off=[(1005.0, "a")],
                     screen=[(1150.0, "a")])
    t = truth(monkeypatch, {"b": [a, c], "c": [b]}, dead)
    for reach in va.REACHES:
        plain = va.route_shown([a, b, c], tally, t, reach)
        asked = va.route_shown([a, b, c], tally, t, reach, {})
        assert plain == asked


def test_the_charge_splits_what_is_still_lost_by_cause_and_adds_up(monkeypatch):
    t = truth(monkeypatch, {"b": [AIR]}, dead=(False, False, 0.0))
    tally = va.Tally(route_on=True, route2=[(1040.0, "a", True)], log={"b": [(950.0, "gate:leave", True, False)]})
    row = va.new_row()
    va.charge_night(tally, t, row)
    for reach in map(str, va.REACHES):
        assert row["residual"][reach] == {"screen_mid_play": 5.0} and row["residual_by_rule"][reach] == {"gate:leave|screen_mid_play": 5.0}
        assert sum(row["residual"][reach].values()) == sum(row["lost_after_catchup"][reach].values())
    row["league"], row["brain"], row["card"] = "nfl", "hindsight", {"entries_mid_play": 0, "entries": 0, "cuts_high": 0, "mid_play_leaves": 0}
    text = va.table([row])
    assert "screen_mid_play" in text and "by why the route did not air it (SIMULATED)" in text


def test_without_the_route_flag_there_is_no_residual_table(monkeypatch):
    t = truth(monkeypatch, {"b": [AIR]})
    row = va.new_row()
    va.charge_night(va.Tally(log={"b": [(950.0, "gate:leave", True, False)]}), t, row)
    assert all(not row["residual"][str(r)] for r in va.REACHES) and va.residual_lines([{**row, "league": "nfl"}]) == []
