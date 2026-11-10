"""[NBA-BUCKET] basketball.join_after_bucket: a join may land in the window after a made basket, or it waits.

Why: Nick's "i want it joining right after a bucket in transition" replaces the old divert for made baskets only. The window is
measured (slate.bucket_floor_*, scripts/bucket_join_measure.py); misses, rebounds and steals stay under the old rule.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from redzone.decider import cuts, gate
from redzone.models import League
from redzone.moments import slate

SIGMA = 2.0
LOW = NS(high_leverage=False, crunch=False, crunch_now=False)
PLAIN = {"basketball.join_after_bucket": True}


@pytest.fixture(autouse=True)
def _no_commercial(monkeypatch):
    monkeypatch.setattr(gate, "break_on_arrival", lambda *a: False)


class Cfg(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


def _tl(rows, league=League.NBA):
    """A timeline whose rows are (pid, wall, text, type, clock)."""
    dead = cuts.DeadTracker(league)
    for pid, wall, text, ttype, clock in rows:
        dead.add(pid, wall, wall, "", text, ttype, clock, 1)
    return NS(dead=dead)


MADE = ("1", 100.0, "A makes 16-foot jumper", "Jump Shot", 500.0)
MISS = ("1", 100.0, "A misses 16-foot jumper", "Jump Shot", 500.0)
REB = ("2", 100.0, "B defensive rebound", "Defensive Rebound", 500.0)
STEAL = ("1", 100.0, "B steal", "Lost Ball Turnover", 500.0)
NEXT = ("9", 130.0, "C misses 20-foot jumper", "Jump Shot", 480.0)


def _lands(rows, x0, x1, sigma=SIGMA, scored=LOW, cfg=None, league=League.NBA):
    return gate.bucket_landing(Cfg(cfg if cfg is not None else PLAIN), _tl(rows, league), scored, sigma, x0, x1)


def test_a_landing_right_after_the_make_is_accepted_with_its_doubt():
    land = _lands([MADE, NEXT], 103.5, 105.0)
    assert land is not None and land.state == "dead" and land.kind == "bucket"


def test_a_landing_that_may_be_before_the_make_waits():
    assert _lands([MADE, NEXT], 101.0, 102.5) is None       # doubt reaches back past the ball through the net


def test_a_landing_that_may_be_after_the_next_shot_can_go_up_waits():
    assert _lands([MADE, NEXT], 106.5, 108.0) is None        # doubt runs past the 8 s floor: the halfcourt, a shot


def test_a_known_next_action_inside_the_floor_ends_the_window():
    nxt = ("9", 106.0, "C makes layup", "Layup Shot", 494.0)
    assert _lands([MADE, nxt], 103.5, 105.0) is None         # next stamp less the 2 s guard: the window ends at 104
    assert _lands([MADE, nxt], 102.0, 103.0, sigma=0.5) is not None   # a sure stick still fits before it


def test_a_miss_a_rebound_and_a_steal_open_no_window():
    for row in (MISS, REB, STEAL):
        assert _lands([row, NEXT], 103.5, 105.0) is None, row


def test_a_bucket_join_needs_low_leverage():
    for scored in (NS(high_leverage=True, crunch=False, crunch_now=False),
                   NS(high_leverage=False, crunch=True, crunch_now=False),
                   NS(high_leverage=False, crunch=False, crunch_now=True)):
        assert _lands([MADE, NEXT], 103.5, 105.0, scored=scored) is None


def test_the_big_doubt_of_a_stick_closes_the_window():
    assert _lands([MADE, NEXT], 103.5, 105.0, sigma=4.0) is None


def test_women_are_not_covered_and_men_are():
    assert _lands([MADE, NEXT], 103.5, 105.0, league=League.WCBB) is None
    assert _lands([MADE, NEXT], 103.5, 105.0, league=League.CBB) is not None


@pytest.mark.parametrize("flag", [False, None])
def test_the_flag_off_is_no_window(flag):
    cfg = {} if flag is None else {"basketball.join_after_bucket": flag}
    assert _lands([MADE, NEXT], 103.5, 105.0, cfg=cfg) is None


def test_floors_read_the_measured_numbers():
    p = slate.Params()
    assert slate.bucket_floor_s("nba", p) == 8.0
    assert slate.bucket_floor_s("cbb", p) == 9.0


def test_knobs_read_from_config():
    p = slate.Params.from_config(lambda k, d=None: 6.0 if k == "slate.bucket_floor_nba_s" else d)
    assert slate.bucket_floor_s("nba", p) == 6.0


def test_a_rewind_is_not_a_bucket_join_at_the_gate():
    """A rewind's frame is its own; the live-edge bucket exemption never applies to it."""
    from test_commit_gate import AT, _d, _host
    h = _host(**PLAIN)
    h.decider.timelines["b"] = NS(dead=cuts.DeadTracker(League.NBA), clock_events=[])
    h.decider._bj_pic = lambda *a: None
    d = _d(kind="better")
    d.rewind_s = 30.0
    f = gate.Flip(h, d, "a", "t:b", {}, None, now=AT)
    assert not gate._lands_after_bucket(f)


def _running_clock_join(**cfg):
    """A gate host whose target is 3 s into a made-basket window with the game clock running: the clock-running bar's veto."""
    from test_commit_gate import AT, T, _Cand, _d, _host
    h = _host(**cfg)
    make = AT + 3.0 - 30.0 - 3.0
    dead = cuts.DeadTracker(League.NBA)
    dead.add("1", make, make, "", "A makes 16-foot jumper", "Jump Shot", 500.0, 1)
    dead.add("2", make + 12.0, make + 12.0, "", "C misses 20-foot jumper", "Jump Shot", 488.0, 1)
    h.decider.timelines["b"] = NS(dead=dead, clock_events=[(make, 1, 500.0, "jump shot"), (make + 12.0, 1, 488.0, "jump shot")])
    h.decider._bj_pic = lambda *a: None
    h.delays = NS(get=lambda t: NS(mu=30.0, behind=0.0, current_sigma=lambda n: 2.0))
    return gate.commit_gate(h, _d(kind="better"), "a", T, {"b": _Cand(LOW, T)}, None, now=AT)


def test_a_join_in_the_window_goes_through_the_running_clock_bar():
    assert _running_clock_join(**PLAIN) == ""


def test_the_running_clock_bar_still_holds_with_the_flag_off():
    assert _running_clock_join() == "landing"


# ----- [NBA-YES] basketball.join_after_bucket_crunch: the same window, into a crunch or high-leverage game too ------------------------

TIGHT = (NS(high_leverage=True, crunch=False, crunch_now=False), NS(high_leverage=False, crunch=True, crunch_now=False),
         NS(high_leverage=False, crunch=False, crunch_now=True))
CRUNCH = {**PLAIN, "basketball.join_after_bucket_crunch": True}


def test_the_crunch_flag_ships_off():
    from pathlib import Path

    import yaml
    cfg = yaml.safe_load((Path(gate.__file__).parent.parent / "default_config.yaml").read_text())
    assert cfg["basketball"] == {"join_after_bucket": False, "join_after_bucket_crunch": False}


@pytest.mark.parametrize("scored", TIGHT)
def test_with_the_crunch_flag_a_tight_game_takes_the_same_window(scored):
    assert _lands([MADE, NEXT], 103.5, 105.0, scored=scored) is None                       # off: today's rule
    land = _lands([MADE, NEXT], 103.5, 105.0, scored=scored, cfg=CRUNCH)
    assert land is not None and land.kind == "bucket" and land.resume == 108.0
    assert _lands([MADE, NEXT], 101.0, 102.5, scored=scored, cfg=CRUNCH) is None           # may be before the make: still waits
    assert _lands([MADE, NEXT], 106.5, 108.0, scored=scored, cfg=CRUNCH) is None           # may be after the 8 s floor: still waits
    assert _lands([MADE, NEXT], 103.5, 105.0, sigma=4.0, scored=scored, cfg=CRUNCH) is None
    for row in (MISS, REB, STEAL):
        assert _lands([row, NEXT], 103.5, 105.0, scored=scored, cfg=CRUNCH) is None        # made baskets only


def test_the_crunch_flag_alone_opens_nothing_and_changes_no_low_leverage_landing():
    alone = {"basketball.join_after_bucket_crunch": True}
    assert _lands([MADE, NEXT], 103.5, 105.0, cfg=alone) is None and _lands([MADE, NEXT], 103.5, 105.0, scored=TIGHT[0], cfg=alone) is None
    for x0, x1 in ((103.5, 105.0), (101.0, 102.5), (106.5, 108.0), (102.0, 103.0)):
        a, b = _lands([MADE, NEXT], x0, x1), _lands([MADE, NEXT], x0, x1, cfg=CRUNCH)
        assert (a is None) == (b is None) and (a is None or (a.kind, a.resume) == (b.kind, b.resume))


def test_flag_off_a_night_decides_as_before_the_change(monkeypatch):
    """After: as shipped. Before: the new key does not exist (a read of it raises). Without join_after_bucket the crunch flag opens nothing."""
    from redzone.config import Config
    from tests.test_timing_truth import _night_decisions
    after = _night_decisions()
    assert len(after[0]) > 500 and after[1]
    assert _night_decisions(**{"basketball.join_after_bucket_crunch": True}) == after
    real = Config.get

    def get(self, key, default=None):
        assert key != "basketball.join_after_bucket_crunch", "read with join_after_bucket off"
        return real(self, key, default)
    monkeypatch.setattr(Config, "get", get)
    assert _night_decisions() == after
