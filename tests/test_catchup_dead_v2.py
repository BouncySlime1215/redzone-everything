"""[timing.dead_v2, off] The live catch-up route's span reads the class and elapsed-time table for an open stoppage on screen.

Why: the route sizes a piece inside the dead time it is sure of; today that is the dead tracker's own floor or as far as the fresh
feed proves no restart (about 20 s ahead of the screen), so an official timeout that surely runs a minute more held no clip. With
the flag on the table's promise at arbiter.catchup_route_alpha may lengthen an open stoppage, never one whose restart is in the data.
"""
from pathlib import Path

import pytest
import yaml

from redzone.decider import cuts
from redzone.decider.decider import Decider, Missed
from redzone.models import League, Play, Stoppage
from redzone.noggin import anchor as A
from redzone.noggin import arbiter
from redzone.scoring import dead_left
from tests.conftest import nfl
from tests.test_decider import calibrate, cands, dec  # noqa: F401  (dec is a fixture)
from tests.test_timing_truth import _night_decisions

CONFIG = yaml.safe_load((Path(A.__file__).parent.parent / "default_config.yaml").read_text())
OFFICIAL, TEAM = ("Official Timeout", "Official Timeout"), ("Timeout", "Timeout #1 by DAL at 07:12.")


def _poll(dec, clock, play, stoppage=Stoppage.NONE):
    g = nfl(gid="a", stoppage=stoppage)
    g.recent_plays, g.last_play, g.updated_at = [play], play, clock.t
    dec.timeline("a", League.NFL).observe(g)


def _timeout(dec, clock, kind=OFFICIAL, **cfg):
    """Game a on screen (delay 40 s), two plays then a timeout of this kind in the feed. (its timeline, when it happened, a's candidate)."""
    dec.cfg.data.setdefault("timing", {})["dead_v2"] = cfg.pop("dead_v2", True)
    dec.cfg.data.setdefault("arbiter", {}).update(cfg)
    calibrate(dec, "youtubetv:a")
    _poll(dec, clock, Play("p0", "run for 3", "rush", wallclock=clock.t - 20, type_text="Rush"))
    clock.t += 10
    _poll(dec, clock, Play("p1", "pass for 9", "pass", wallclock=clock.t - 5, type_text="Pass Reception"))
    clock.t += 10
    t0 = clock.t - 4
    _poll(dec, clock, Play("t1", kind[1], "timeout", wallclock=t0, type_text=kind[0]), Stoppage.TIMEOUT)
    dec._current = "a"
    return dec.timeline("a", League.NFL), t0, cands(a=50)["a"]


def _at(dec, clock, tl, at):
    """A fresh poll with nothing new, at time `at`."""
    clock.t = at
    tl.fresh_at = at
    tl.dead.seen(at, tl.lag_est())


def _old_span(dec, tl, cur, now):
    """The span exactly as it was computed before timing.dead_v2."""
    dl = dec.delays.get(cur.target)
    m = 2.0 * dl.current_sigma(now)
    until = tl.dead.dead_until(now - dl.mu - m)
    return None if until is None else until + dl.mu - m


# ----- the knob and the dead tracker's own fact -------------------------------------------------------------------------------------

def test_the_route_risk_level_ships_at_the_99_percent_level():
    assert CONFIG["arbiter"]["catchup_route_alpha"] == 0.01 == arbiter.Params().catchup_route_alpha
    assert CONFIG["timing"]["dead_v2"] is False
    assert arbiter.Params.from_config({"arbiter.catchup_route_alpha": 0.05}.get).catchup_route_alpha == 0.05
    assert A.risk(0.05, 0.01) == 0.05 and A.risk(0.0, 0.01) == 0.01 == A.risk(1.5, 0.01)       # a bad level never stops a tick


def test_the_dead_tracker_says_whether_a_windows_end_is_a_fact():
    t = cuts.DeadTracker(League.NFL)
    t.add("p0", 100.0, 100.0, "rush", "run for 3", "Rush", None, None)
    t.add("t1", 130.0, 130.0, "timeout", "Official Timeout", "Official Timeout", None, None)
    assert t.open_at(140.0) is True and t.open_at(50.0) is False
    t.add("p2", 200.0, 200.0, "rush", "run for 5", "Rush", None, None)
    assert t.open_at(140.0) is False and t.open_at(250.0) is True          # the timeout has its restart; the huddle after it is open


# ----- flag off: the span is what it was -------------------------------------------------------------------------------------------

def test_flag_off_the_span_is_the_dead_trackers_own(dec, clock, monkeypatch):
    tl, t0, cur = _timeout(dec, clock, arbiter={}, dead_v2=False)

    def boom(*a, **k):
        raise AssertionError("the v2 table was read with timing.dead_v2 off")
    monkeypatch.setattr(Decider, "_dead_v2_until", boom)
    monkeypatch.setattr(cuts.DeadTracker, "open_at", boom)
    assert dec._catchup_span(cur, t0 + 30.0) is None                      # the timeout has not reached the screen: mid-play there
    for after in (42.0, 50.0, 75.0, 110.0):
        _at(dec, clock, tl, t0 + after)
        span = dec._catchup_span(cur, clock.t)
        assert span == (_old_span(dec, tl, cur, clock.t), False) and 15.0 < span[0] - clock.t < 30.0     # as far as the feed leads the screen


# ----- flag on: the table may lengthen an open stoppage ------------------------------------------------------------------------------

def test_flag_on_an_official_timeout_lasts_as_long_as_the_table_promises(dec, clock):
    tl, t0, cur = _timeout(dec, clock, OFFICIAL, catchup_route_alpha=0.10)
    assert tl.last("stop_start").stop_class == "official"
    _at(dec, clock, tl, t0 + 42.0)                                         # 2 s on screen
    dl = dec.delays.get(cur.target)
    doubt = 2.0 * dl.current_sigma(clock.t)
    sure = dead_left.guaranteed_left_v2_within("nfl", "timeout", 0.0, 2.0 + doubt, 0.10, "official")
    assert sure == pytest.approx(110.0 - 10.0)                             # it may be up to 10 s in: the start cell's 110 s less 10
    assert dec._catchup_span(cur, clock.t) == (pytest.approx(clock.t + sure), False)
    assert dec._catchup_span(cur, clock.t)[0] - _old_span(dec, tl, cur, clock.t) > 70.0
    _at(dec, clock, tl, t0 + 40.0 + 60.0)                                  # a minute in: the 60 s cell still promises 61 s
    assert dec._catchup_span(cur, clock.t)[0] - clock.t > 40.0


def test_flag_on_a_team_timeout_keeps_the_dead_trackers_span(dec, clock):
    tl, t0, cur = _timeout(dec, clock, TEAM, catchup_route_alpha=0.10)
    assert tl.last("stop_start").stop_class == "team"
    _at(dec, clock, tl, t0 + 42.0)
    assert dec._dead_v2_until(tl, dec.delays.get(cur.target), clock.t) < _old_span(dec, tl, cur, clock.t)     # 26 s at the start, less 10
    assert dec._catchup_span(cur, clock.t) == (_old_span(dec, tl, cur, clock.t), False)                        # never shorter than before


def test_flag_on_at_the_default_level_this_timeout_gains_nothing(dec, clock):
    """At the 99% level an official timeout promises 29 s at its start and 19 s ten seconds in: under the feed's own lead here."""
    tl, t0, cur = _timeout(dec, clock, OFFICIAL)
    _at(dec, clock, tl, t0 + 42.0)
    assert dec._arbiter_params().catchup_route_alpha == 0.01
    assert dec._catchup_span(cur, clock.t) == (_old_span(dec, tl, cur, clock.t), False)


def test_flag_on_a_restart_in_the_data_is_never_stretched(dec, clock):
    tl, t0, cur = _timeout(dec, clock, OFFICIAL, catchup_route_alpha=0.10)
    _at(dec, clock, tl, t0 + 42.0)
    long = dec._catchup_span(cur, clock.t)[0]
    _poll(dec, clock, Play("p2", "run for 2", "rush", wallclock=t0 + 38.0, type_text="Rush"))       # the next snap is in the feed
    assert tl.last("stop_end") is not None and tl.dead.open_at(clock.t - 40.0 - 8.5) is False
    span = dec._catchup_span(cur, clock.t)
    assert span == (_old_span(dec, tl, cur, clock.t), False) and span[0] < long - 60.0
    assert dec._dead_v2_until(tl, dec.delays.get(cur.target), clock.t) == float("-inf")


def test_flag_on_a_stoppage_not_yet_on_screen_reads_no_table(dec, clock):
    tl, t0, cur = _timeout(dec, clock, OFFICIAL, catchup_route_alpha=0.10)
    _at(dec, clock, tl, t0 + 30.0)
    assert dec._dead_v2_until(tl, dec.delays.get(cur.target), clock.t) == float("-inf")
    assert dec._catchup_span(cur, clock.t) is None


def test_flag_on_the_route_airs_the_whole_clip_where_the_feeds_lead_held_only_the_play(dec, clock):
    p = arbiter.Params(catchup_route=True, catchup_route_reach_s=170.0, catchup_route_alpha=0.10)
    pieces = {}
    for flag in (False, True):
        clock.t = 1000.0
        dec.timelines.clear()
        tl, t0, cur = _timeout(dec, clock, OFFICIAL, catchup_route_alpha=0.10, dead_v2=flag)
        _at(dec, clock, tl, t0 + 42.0)
        dec._arbiter_missed_rank, dec._replayable, dec._replay_pool = (lambda m, pp: 0), (lambda x: True), (lambda c: c)
        dec._crunch_on = lambda c: False
        m = Missed("b", "td1", "TD", "td", clock.t - 30.0, 90.0, aired=clock.t - 30.0)
        dec.queue[:] = [m]
        assert dec._catchup_route({"a": cur, "b": cands(b=40)["b"]}, clock.t, p) is m
        pieces[flag] = m.piece_s
    assert 12.0 <= pieces[False] < 14.0          # 22 s of feed lead less two flips and the margin: the play and little more
    assert pieces[True] == p.plan_clip_s         # the whole 30 s clip inside the promised 100 s


# ----- flag off: a whole fixture night decides exactly as before --------------------------------------------------------------------

def test_flag_off_a_night_decides_as_before_the_change(monkeypatch):
    """Same pattern as tests/test_timing_truth.py: with the route's v2 read made to fail, the night is unchanged with the flag off,
    with the arbiter and the route on or not."""
    after = _night_decisions()
    route = {"arbiter.enabled": True, "arbiter.catchup_route": True}
    with_route = _night_decisions(**route)
    assert len(after[0]) > 500 and after[1] and len(with_route[0]) > 500

    def boom(*a, **k):
        raise AssertionError("the route read the v2 table with timing.dead_v2 off")
    monkeypatch.setattr(Decider, "_dead_v2_until", boom)
    monkeypatch.setattr(cuts.DeadTracker, "open_at", boom)
    assert _night_decisions() == after
    assert _night_decisions(**route) == with_route


def test_flag_on_the_night_still_runs():
    on = _night_decisions(**{"arbiter.enabled": True, "arbiter.catchup_route": True, "timing.dead_v2": True})
    assert len(on[0]) > 500
