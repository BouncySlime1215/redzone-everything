"""AUDIT-5: the fill ladder's cut obeys the same 'may anything start now' rules as every other optional switch.

Why: _fill runs after the stages that enforce them (_dwell, the arbiter's ceiling) and legal_options isn't asked, so a fill
could start while a switch was still landing, or whip past the ceiling. legal.py's rule: one place, every option.
"""
from collections import Counter, deque

import pytest

from redzone.decider.decider import Candidate, Decider, Decision, Gate, State
from redzone.moments import fill as F
from redzone.scoring.engine import Scored

NOW = 1000.0


class Cfg:
    def __init__(self, **v):
        self.v = {"fill.enabled": True, **v}

    def get(self, k, d=None):
        return self.v.get(k, d)


def cand(gid, **kw):
    s = Scored.__new__(Scored)
    s.__dict__.update({"is_break": True, "mine": False, "high_leverage": False, "personal": 0, "crunch": False, **kw})
    x = Candidate.__new__(Candidate)
    x.__dict__.update(scored=s, target=f"yt:{gid}")
    return x


def make(**cfg):
    d = Decider.__new__(Decider)
    d.cfg = Cfg(**cfg)
    d.current, d.replaying, d.after_replay, d.safe_mode = "on", False, False, False
    d.slate_scene, d._screen_break, d.fill_log, d.fill_choice = None, set(), Counter(), None
    d.history, d.since, d._arrival_until = deque([(NOW - 500.0, "on", "jump")]), NOW - 500.0, 0.0
    d._jump_hold = lambda x: None
    d._jump_gate = lambda x, n: Gate(True, "dead ball")
    d._fill_games = lambda c, n, no: [F.GameInfo("b", "nfl", "live", joinable=True)]
    d._fill_moments = lambda c, n: []
    d._fill_seen = lambda m, c: set()
    d._bj_pic = lambda *a: None
    d._bj_flip_cost = lambda t: 3.0
    d._lc = lambda k, x, dflt: d.cfg.get(k, dflt)
    d.jump_holds = Counter()
    return d


CANDS = {"on": cand("on"), "b": cand("b")}


def stay():
    return Decision(NOW, "stay", "on", "nothing better", State.WATCHING)


def run(d):
    return d._fill(stay(), CANDS, NOW, "normal")


def test_a_fill_joins_when_nothing_holds_it():
    assert run(make()).action == "switch"


def test_a_fill_waits_while_the_last_switch_is_still_landing():
    """Repro: `arriving` is legal.py's first rule after the user's and _own counts on it, but _fill never asked: it cut
    away from a picture that hadn't come up yet."""
    d = make()
    d._arrival_until = NOW + 5.0
    assert run(d).action == "stay"


def test_a_fill_respects_the_dwell():
    """Repro: _dwell turns switches into stays, but _fill runs after it and returned a switch anyway."""
    d = make(**{"decider.dwell.min_s": 60, "decider.dwell.kinds": ["jump"]})
    d.since = NOW - 10.0
    assert run(d).action == "stay"


def test_a_fill_respects_the_switch_rate_cap():
    d = make(**{"noggin.pick.min_gap_s": 30.0})
    d.history.append((NOW - 5.0, "on", "jump"))
    assert run(d).action == "stay"


@pytest.mark.parametrize("on,cuts,want", [(True, 3, "stay"), (True, 2, "switch"), (False, 3, "switch")])
def test_a_fill_respects_the_arbiter_ceiling(on, cuts, want):
    """Repro: a hard ceiling (3 a minute) that the arbiter holds and _fill walked straight past."""
    d = make(**{"arbiter.enabled": on})
    d.history.extend((NOW - 10.0 - i, "x", "jump") for i in range(cuts))
    assert run(d).action == want


def test_the_ladder_answer_still_reaches_the_phone():
    d = make()
    d._arrival_until = NOW + 5.0
    run(d)
    assert d.fill_choice is not None and d.fill_choice.step == F.STEP_JOIN


# ----- seen is judged at air time, not feed time

def seen_decider():
    d = make()
    d.delays = type("B", (), {"get": staticmethod(lambda t: type("D", (), {"mu": 60.0})())})()
    d._screen_log = deque([(NOW - 200.0, "other"), (NOW + 30.0, "b"), (NOW + 90.0, "other")])
    del d._fill_seen   # the real one
    return d


def test_a_play_that_aired_while_on_screen_counts_as_seen():
    """Repro: feed time NOW, on air NOW+60 with the game on screen NOW+30..NOW+90, read at feed time: 'unseen', and the
    reel replayed a play Nick had just watched."""
    m = F.Moment(NOW, "b", 1, "td", "p1")
    assert seen_decider()._fill_seen([m], CANDS) == {("b", "p1")}


def test_a_play_that_aired_after_the_screen_left_is_unseen():
    """The other half: on screen at feed time, gone by the time it aired."""
    d = seen_decider()
    d._screen_log = deque([(NOW - 200.0, "b"), (NOW + 10.0, "other")])
    assert d._fill_seen([F.Moment(NOW, "b", 1, "td", "p1")], CANDS) == set()
