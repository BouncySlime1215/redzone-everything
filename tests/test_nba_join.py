"""[NBA-JOIN] With slate.enabled on, a basketball join after a shot lands inside the measured safe time or waits.

Why: the walk windows run 14 s, but on 9 fit nights the next live action starts 7 s after a make and 5 s after a defensive
rebound (5th percentile); three `start` and `crunch` joins landed 10-11 s after a shot. Rule #1: never join mid-play.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest
from test_commit_gate import AT, T, _d, _host

from redzone.decider import cuts, gate
from redzone.models import League
from redzone.moments import slate

MU, TUNE = 30.0, 3.0
ARRIVE = AT + TUNE - MU          # the real time on the picture when it comes up: an empty cost spread, so x0 == x1


@pytest.fixture(autouse=True)
def _no_commercial(monkeypatch):
    monkeypatch.setattr(gate, "break_on_arrival", lambda *a: False)


def _join_after(tracker_rows, slate_on=True, kind="better", **cfg):
    """A gate host whose target's timeline holds these (pid, seconds before the arrival, text, type) rows."""
    h = _host(**{"slate.enabled": slate_on, **cfg})
    dead = cuts.DeadTracker(League.NBA)
    for clock, (pid, ago, text, ttype) in zip(range(600, 0, -12), tracker_rows):
        at = ARRIVE - ago
        dead.add(pid, at, at, "", text, ttype, float(clock), 1)
    h.decider.timelines["b"] = NS(dead=dead, clock_events=[])
    h.decider._bj_pic = lambda *a: None
    return h, _d(kind=kind)


MADE = ("1", "A makes 16-foot jumper", "Jump Shot")
DREB = ("1", "B defensive rebound", "Defensive Rebound")
NEXT = "C misses 20-foot jumper"


def _rows(first, ago, next_in):
    return [(first[0], ago, first[1], first[2]), ("2", ago - next_in, NEXT, "Jump Shot")]


def _veto(h, d):
    return gate.commit_gate(h, d, "a", T, {}, None, now=AT)


def test_a_join_10_s_after_a_made_shot_waits_for_a_safer_dead_ball():
    h, d = _join_after(_rows(MADE, 10.0, 16.0))
    assert _veto(h, d) == "landing"


def test_a_join_inside_the_safe_time_after_a_made_shot_goes():
    h, d = _join_after(_rows(MADE, 4.0, 16.0))
    assert _veto(h, d) == ""


def test_a_defensive_rebound_gives_less_time_than_a_make():
    assert slate.Params().walk_safe_rebound_s < slate.Params().walk_safe_made_s
    late, d = _join_after(_rows(DREB, 6.0, 16.0))
    assert _veto(late, d) == "landing"
    fine, d = _join_after(_rows(DREB, 2.0, 16.0))
    assert _veto(fine, d) == ""


def test_college_men_are_judged_like_the_nba_and_women_have_no_walk_to_judge():
    """after_shot lists nba and cbb only (N2: women's college basketball is off): a league with no walk is never held here."""
    for league, want in ((League.CBB, "landing"), (League.WCBB, "")):
        h, d = _join_after(_rows(MADE, 10.0, 16.0))
        h.merger.games["b"].league = league
        assert _veto(h, d) == want, league


def test_the_flag_off_changes_nothing():
    h, d = _join_after(_rows(MADE, 10.0, 16.0), slate_on=False)
    assert _veto(h, d) == ""


def test_a_real_dead_ball_is_never_judged_as_a_walk():
    rows = [("1", 30.0, "Team full timeout", "Full Timeout"), ("2", -60.0, NEXT, "Jump Shot")]
    h, d = _join_after(rows)
    assert _veto(h, d) == ""


def test_a_transition_play_has_no_walk_to_land_in():
    """The next action 5 s after the shot: DeadTracker has no window, so the prior safe point is the plan's to find."""
    h, _ = _join_after(_rows(MADE, 3.0, 5.0))
    dead = h.decider.timelines["b"].dead
    assert dead.walks(cuts.walk_params(h.cfg, League.NBA)) == []


def test_a_jump_counts_its_extra_landing_time():
    """jump_lands_dead's arrival runs land_dead_s later: the safe time is judged against the same arrival."""
    late, d = _join_after(_rows(MADE, 4.0, 16.0), kind="jump")
    assert _veto(late, d) == "landing"
    fine, d = _join_after(_rows(MADE, 2.0, 16.0), kind="jump")
    assert _veto(fine, d) == ""


@pytest.mark.parametrize("key,attr,value", [("slate.walk_safe_made_s", "walk_safe_made_s", 9.0),
                                            ("slate.walk_safe_rebound_s", "walk_safe_rebound_s", 6.0)])
def test_the_safe_times_read_from_config(key, attr, value):
    p = slate.Params.from_config(lambda k, d=None: value if k == key else d)
    assert getattr(p, attr) == value
