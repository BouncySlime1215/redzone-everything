"""Catch-up packages (redzone/noggin/catchup_pack.py): which missed plays get a clip, from which source, and when.

Pure: no clock, no device. Times are the PARK show's grid seconds; the live edge is delay_s (35) ahead.

Why: the selection and the source plan decide what the viewer sees after "the last time we checked in"; each rule here
(story order, the cap, queue to the snap, never 3 minutes late, the rewind's reach, Nick's source order) is pinned.
"""
from __future__ import annotations

import math
from dataclasses import replace

import pytest

from redzone.noggin import catchup_pack as cp
from redzone.noggin.catchup import HARD_CAP_S

P = cp.PackParams()


def play(at, start=None, value=0.5, **kw):
    return cp.Play(game=0, at=float(at), start=float(at - 6 if start is None else start), value=value, **kw)


# ---------------------------------------------------------------------------------------------- worth and selection
def test_worth_weights_big_plays_then_the_story_by_leverage():
    base = play(100, value=0.5)
    assert cp.worth(base, P) == pytest.approx(0.5)
    assert cp.worth(replace(base, big=5.0), P) == pytest.approx(0.5 + 2.0 * 5.0)
    assert cp.worth(replace(base, lead_change=True, lev=3.0), P) == pytest.approx(0.5 + 3.0)
    assert cp.worth(replace(base, tie=True), P) == pytest.approx(1.0)
    assert cp.worth(replace(base, run=8), P) == pytest.approx(1.5)          # the score that makes an 8-0 run
    assert cp.worth(replace(base, run=7), P) == pytest.approx(0.5)
    assert cp.worth(replace(base, value=-0.25), P) == 0.0                   # a dead-ball row is worth nothing


def test_select_takes_only_missed_and_known_plays_most_worth_first_in_story_order():
    plays = [play(90, value=5.0), play(110, value=0.4), play(150, value=2.0), play(200, value=1.0), play(400, value=9.0)]
    got = cp.select(plays, since=100, until=300, P=P)
    assert [p.at for p in got] == [110, 150, 200]                         # 90 was seen, 400 not reported yet
    assert cp.select([play(150, value=0.1)], 100, 300, P) == []            # under min_worth: no clip


def test_select_keeps_at_most_max_plays_and_stays_under_the_cap():
    plays = [play(100 + 60 * k, value=1.0 + k) for k in range(8)]
    got = cp.select(plays, 0, 10_000, P)
    assert len(got) == P.max_plays
    assert [p.value for p in got] == [4.0, 5.0, 6.0, 7.0, 8.0]             # the five worth most, oldest first
    assert cp.screen_len(got, P) <= P.cap_s
    tight = replace(P, cap_s=40.0)
    got = cp.select(plays, 0, 10_000, tight)
    assert cp.screen_len(got, tight) <= 40.0 and len(got) == 2


def test_a_clip_starts_at_its_plays_start_less_the_lead_never_later():
    p = play(500, start=488)
    lo, hi = cp.clip_span(p, P)
    assert lo == 488 - P.lead_s and hi == 500 + P.tail_s
    assert cp.select([cp.Play(0, 500.0, 505.0, value=1.0)], 0, 1000, P) == []   # a start after its result is not a play


def test_close_plays_play_straight_through_but_never_past_seg_max():
    a, b = play(100, start=94), play(112, start=106)                        # 6 s apart: one clip
    assert len(cp._segments([a, b], P)) == 1
    far = play(160, start=154)
    assert len(cp._segments([a, far], P)) == 2
    chain = [play(100 + 10 * k, start=94 + 10 * k) for k in range(5)]     # each close to the last, 54 s in all
    assert all(cp.clip_span(s[-1], P)[1] - cp.clip_span(s[0], P)[0] <= P.seg_max_s for s in cp._segments(chain, P))


# ---------------------------------------------------------------------------------------------- the rewind's reach
def test_rewind_floor_measured_watch_and_library():
    s = cp.Stick("yttv", on_since=0.0)
    assert cp.rewind_floor(1000.0, s, P) == 1000.0 + P.delay_s - P.reach_s           # 175 s behind live, measured
    assert cp.rewind_floor(1000.0, s, replace(P, rewind_rule="watch")) == pytest.approx(0.0 + P.delay_s)
    near = cp.Stick("yttv", on_since=950.0)
    assert cp.rewind_floor(1000.0, near, replace(P, rewind_rule="watch")) == 1000.0 + P.delay_s - P.reach_s
    assert cp.rewind_floor(1000.0, s, replace(P, rewind_rule="library")) == -math.inf


def test_one_play_keeps_the_three_minute_cap_and_two_or_more_have_none():
    """Nick, Nov 11: "the catchup can be old as it wants (assuming its multiple clips) if we catching up from 1 play then
    ideally we saw it within the 3 min cap"."""
    assert P.max_age_s == HARD_CAP_S == 180.0 and cp.age_cap(1, P) == 180.0 and cp.age_cap(2, P) == math.inf
    assert cp.age_ok(lo=1000.0, air=1000.0 + 144.0, P=P)                    # 144 + 35 = 179 s behind live
    assert not cp.age_ok(lo=1000.0, air=1000.0 + 145.0, P=P)                # 180 s: already too late for one play
    assert cp.age_ok(lo=1000.0, air=1000.0 + 3600.0, P=P, n_plays=2)        # a package: no age cap


# ---------------------------------------------------------------------------------------------- the plan
def test_plan_rewinds_recent_plays_and_drops_ones_out_of_reach():
    now = 1000.0
    fresh = play(now - 30, value=1.0)
    old = play(now - 400, value=3.0)
    pkg = cp.plan([old, fresh], now, cp.Stick("yttv"), P)
    assert pkg is not None and [p.at for p in pkg.plays] == [fresh.at]
    assert pkg.sources == {"rewind": 1}
    assert pkg.dropped and pkg.dropped[0][0] is old
    assert pkg.prep_s == pytest.approx(P.seek_base_s + P.seek_per_s * (now + P.delay_s - cp.clip_span(fresh, P)[0]))
    c = pkg.clips[0]
    assert cp.age_ok(c.lo, c.air, P) and c.gap == 0.0


def test_plan_schedules_clips_back_to_back_with_a_reseek_between():
    now = 1000.0
    a, b = play(now - 100, value=1.0), play(now - 40, value=1.0)
    pkg = cp.plan([a, b], now, cp.Stick("yttv"), P, flip_s=3.0)
    assert pkg is not None and len(pkg.clips) == 2
    c0, c1 = pkg.clips
    assert c0.air == pytest.approx(now + pkg.prep_s + 3.0)
    assert c1.gap == P.seek_gap_s and c1.air == pytest.approx(c0.air + c0.secs + P.seek_gap_s)
    assert pkg.screen_s == pytest.approx(c1.air + c1.secs - c0.air)


def test_a_package_is_limited_only_by_the_rewinds_reach_but_one_play_keeps_the_cap():
    now = 1000.0
    old, older = play(now - 400, value=3.0), play(now - 450, value=2.0)
    assert cp.plan([old, older], now, cp.Stick("yttv"), P) is None         # 175 s rewind: out of reach
    ten = replace(P, reach_s=600.0)
    pkg = cp.plan([old, older], now, cp.Stick("yttv"), ten)
    assert pkg is not None and len(pkg.plays) == 2 and pkg.sources == {"rewind": 2}
    assert max(cp.age_s(c.lo, c.air, P) for c in pkg.clips) > 180.0     # older than 3 minutes: fine for a package
    assert cp.plan([old], now, cp.Stick("yttv"), ten) is None               # in reach, but one play 3+ min old
    assert cp.plan([old], now, cp.Stick("yttv"), replace(P, rewind_rule="library")) is None
    pkg = cp.plan([old, older], now, cp.Stick("yttv"), replace(P, rewind_rule="library"))
    assert pkg is not None and len(pkg.plays) == 2


def test_a_package_that_falls_to_one_play_gets_the_three_minute_cap_back():
    now = 1000.0
    fresh, old = play(now - 30, value=0.4), play(now - 400, value=3.0)
    ten = replace(P, reach_s=600.0, cap_s=25.0)                             # room for one clip only
    two = cp.plan([fresh, old], now, cp.Stick("yttv"), replace(ten, min_value=0.3, cap_s=60.0))
    assert two is not None and len(two.plays) == 2                          # both fit: a package, no age cap
    # over the cap the least-worth play (the fresh one) goes; the old one alone is a one-play catch-up 3+ min old: none
    assert cp.plan([fresh, old], now, cp.Stick("yttv"), replace(ten, min_value=0.3)) is None


def test_sources_go_in_nicks_order_rewind_then_reel_then_moments():
    now = 2000.0
    recent, old = play(now - 30, value=1.0), play(now - 900, value=1.0)
    reel = replace(P, reel=True, max_age_s=2000.0)
    pkg = cp.plan([old, recent], now, cp.Stick("yttv"), reel)
    assert pkg is not None and pkg.sources == {"reel": 1, "rewind": 1}     # old: past the rewind, in the reel (12 min lag)
    assert [c.source for c in pkg.clips] == ["reel", "rewind"]
    assert cp.plan([play(now - 300, value=1.0)], now, cp.Stick("yttv"), reel) is None   # too new for the reel, too old to rewind


def test_prime_has_no_rewind_and_no_driver_so_no_package_unless_the_what_if():
    now = 1000.0
    p = play(now - 90, value=2.0)
    assert cp.plan([p], now, cp.Stick("prime"), P) is None
    assert cp.plan([play(now - 120, value=2.0)], now, cp.Stick("prime"), replace(P, moments=True)) is None   # 3+ min late by then
    km = replace(P, moments=True)
    pkg = cp.plan([p], now, cp.Stick("prime"), km)
    assert pkg is not None and pkg.sources == {"moments": 1} and pkg.clips[0].secs == km.moments_play_s
    assert cp.plan([play(now - 10, value=2.0)], now, cp.Stick("prime"), km) is None    # not in Key Moments yet (lag)


def test_a_package_worth_too_little_is_no_package():
    now = 1000.0
    assert cp.plan([play(now - 30, value=0.4)], now, cp.Stick("yttv"), P) is None
    assert cp.plan([play(now - 30, value=0.4)], now, cp.Stick("yttv"), replace(P, min_value=0.3)) is not None


def test_recap_line_is_templated_from_the_plays():
    plays = [play(100, run=10, lead_change=True), play(130, lead_change=True), play(160)]
    assert cp.recap_line(plays, "2nd 5:10", P) == "Since 2nd 5:10: 3 plays, a 10-0 run, 2 lead changes"
    assert cp.recap_line(plays[2:], "the start", P) == "Since the start: 1 play"


def test_params_come_from_park_catchup_keys_and_a_bad_value_keeps_the_default():
    conf = {"park.catchup.cap_s": 90, "park.catchup.reel": True, "park.catchup.rewind_rule": "library",
            "park.catchup.max_plays": "lots"}
    got = cp.PackParams.from_config(lambda k, d=None: conf.get(k, d))
    assert got.cap_s == 90.0 and got.reel is True and got.rewind_rule == "library" and got.max_plays == P.max_plays
    bad = cp.PackParams.from_config(lambda k, d=None: {"park.catchup.rewind_rule": "dvr"}.get(k, d))
    assert bad.rewind_rule == "measured"
