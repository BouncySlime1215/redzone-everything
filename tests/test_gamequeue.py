"""Queue core (redzone/noggin/gamequeue.py, WP-02): table tests on every boundary and a hostile fuzz."""
from __future__ import annotations

import ast
import dataclasses as dc
import importlib.util
import json
import math
import random
import sys
import time
from pathlib import Path

import pytest

from redzone.machines.control import STANDBY, STANDBY_READY
from redzone.noggin import gamequeue as gq
from redzone.noggin.gamequeue import Entry, Failure, Level, Route, Rung, Step, Stick

ROOT = Path(__file__).resolve().parent.parent
P = gq.QUEUE_PARAMS
NOW = 1000.0
FREE = Stick()
YT = Route("youtubetv", "y")
SEARCH = Route("youtubetv", "s", search=True)
PRIME = Route("prime", "p", klass=gq.LONG_LEAD)
ESPN = Route("espn", "e", klass=gq.LIVE_EDGE)
BAD = (math.nan, math.inf, -math.inf, "5", [], True)


def game(name="A", route=YT, lvl=Level.R2, **kw):
    return Entry(name, (route,) if route else (), lvl, **kw)


def prep(e, stick=FREE, now=NOW, p=P):
    return gq.prep_s(e, stick, now, p)


# ---- levels and prep_s -------------------------------------------------------------------------


@pytest.mark.parametrize("route,lvl,want", [
    (YT, Level.R0, math.inf), (YT, Level.R1, 60.0), (YT, Level.R2, 60.0), (YT, Level.R3, 56.0),
    (YT, Level.R4, 0.0), (YT, Level.R5, 0.0),
    (SEARCH, Level.R1, 69.0), (SEARCH, Level.R3, 56.0),
    (PRIME, Level.R1, 120.0), (PRIME, Level.R3, 75.0), (PRIME, Level.R4, 0.0),
    (ESPN, Level.R1, 20.0), (ESPN, Level.R3, 0.0),                  # a live-edge route's goal is R3
    (Route("sling", "x"), Level.R1, 86.0),                          # unknown app: the "other" row
])
def test_prep_by_route_and_level(route, lvl, want):
    assert prep(game(route=route, lvl=lvl)) == want


def test_prep_no_route_is_never_even_at_a_high_level():
    assert prep(game(route=None, lvl=Level.R4)) == math.inf
    assert prep(game(route=dc.replace(YT, flagged=True), lvl=Level.R2)) == math.inf


def test_prep_uses_this_sticks_median_and_ignores_garbage():
    assert prep(game(), Stick(tune_s={"youtubetv": 2.0}, buffer_s={"youtubetv": 30.0})) == 32.0
    assert prep(game(), Stick(tune_s={"youtubetv": math.nan}, buffer_s={"youtubetv": -1})) == 60.0
    assert prep(game(route=SEARCH), Stick(tune_s={"youtubetv:search": 9.0})) == 65.0


def test_prep_prime_buffer_is_a_pause_so_it_costs_at_least_b():
    assert prep(game(route=PRIME), Stick(buffer_s={"prime": 10.0})) == 120.0
    assert prep(game(route=PRIME), Stick(buffer_s={"prime": 90.0})) == 135.0


@pytest.mark.parametrize("stick,want", [
    (Stick(busy_until=NOW + 10), 70.0),                                     # another game's job first
    (Stick(busy_until=NOW - 10), 60.0),                                     # a job already over costs 0
    (Stick(busy_until=NOW), 60.0),
    (Stick(busy_until=None), 60.0),
    (Stick(busy_until=NOW + 3, job_game="A", job_to=Level.R3), 59.0),       # its own tune: not paid twice
    (Stick(busy_until=NOW + 3, job_game="A", job_to=Level.R4), 3.0),
    (Stick(busy_until=NOW + 3, job_game="B", job_to=Level.R4), 63.0),
    (Stick(busy_until=NOW + 3, job_game="A", job_to=99), 63.0),
    (Stick(busy_until=math.nan), math.inf),
    (Stick(busy_until=math.inf), math.inf),
])
def test_prep_counts_the_current_job_once(stick, want):
    assert prep(game(), stick) == want


def test_prep_at_goal_needs_no_stick():
    assert prep(game(lvl=Level.R4), None) == 0.0


def test_prep_never_rises_as_level_rises():
    costs = [prep(game(lvl=v)) for v in Level]
    assert costs == sorted(costs, reverse=True)


@pytest.mark.parametrize("state", sorted(STANDBY.states))
def test_level_of_every_standby_state(state):
    lv = gq.level_of(True, True, state, buffered=True)
    if state in STANDBY_READY and state != "replay_parked":
        assert lv >= Level.R3
    else:
        assert lv == Level.R2                                           # never looks flippable


@pytest.mark.parametrize("args,want", [
    ((False, True, "ready", True), Level.R0), ((None, True, "ready", True), Level.R0),
    ((True, False), Level.R1), ((True, True), Level.R2), ((True, "yes"), Level.R1),
    ((True, True, "ready", False), Level.R3), ((True, True, "ready", True), Level.R4),
    ((True, False, "snap_parked"), Level.R5), ((True, False, "left_hold"), Level.R5),
    ((True, True, "no_such_state", True), Level.R2),
])
def test_level_of_table(args, want):
    assert gq.level_of(*args) == want


@pytest.mark.parametrize("lvl,flag,want", [
    (Level.R0, True, False), (Level.R1, True, False), (Level.R2, True, False), (Level.R3, False, False),
    (Level.R3, True, True), (Level.R4, False, True), (Level.R5, False, True), (9, True, False), (-1, True, False),
    ("R4", True, False), (True, True, False),
])
def test_flippable(lvl, flag, want):
    assert gq.flippable(game(lvl=lvl), dc.replace(P, r3_flippable=flag)) is want


def test_flagged_route_is_never_flippable():
    assert gq.flippable(game(route=dc.replace(YT, flagged=True), lvl=Level.R5)) is False


# ---- reach --------------------------------------------------------------------------------------


@pytest.mark.parametrize("join,ahead,want", [
    (-5.0, 67.0, True),       # exactly on time: 62 = 60 + 2
    (-5.0, 66.999, False),
    (0.0, 62.0, True),
    (0.0, 61.999, False),
    (-70.0, 75.0, False),     # airs in 5 s
])
def test_reach_boundary(join, ahead, want):
    assert gq.reach(game(join_in_s=join, lookahead_s=ahead), FREE, NOW) is want


def test_reach_counts_busy_exactly_once():
    e = game(join_in_s=0.0, lookahead_s=72.0)
    assert gq.reach(e, Stick(busy_until=NOW + 10), NOW) is True    # 72 = 60 + 10 + 2
    assert gq.reach(e, Stick(busy_until=NOW + 10.001), NOW) is False


@pytest.mark.parametrize("bad", BAD + (None,))
def test_reach_bad_lookahead_fails_closed(bad):
    assert gq.reach(game(join_in_s=0.0, lookahead_s=bad), FREE, NOW) is False


@pytest.mark.parametrize("bad", BAD)
def test_reach_bad_join_fails_closed(bad):
    assert gq.reach(game(join_in_s=bad, lookahead_s=75.0), FREE, NOW) is False


def test_reach_negative_lookahead_and_overflow_fail_closed():
    assert gq.reach(game(join_in_s=500.0, lookahead_s=-1.0), FREE, NOW) is False
    assert gq.reach(game(join_in_s=1e308, lookahead_s=1e308), FREE, NOW) is False


@pytest.mark.parametrize("route,lead,want", [
    (YT, None, True), (ESPN, None, True),               # no deadline
    (PRIME, None, False), (PRIME, 119.999, False), (PRIME, 120.0, True), (PRIME, math.nan, False),
    (Route("x", "x", klass="???"), None, False),        # an unknown class is treated as long lead
])
def test_reach_without_a_deadline(route, lead, want):
    assert gq.reach(game(route=route, lead_s=lead), FREE, NOW) is want


def test_reach_no_route_or_bad_clock():
    assert gq.reach(game(route=None), FREE, NOW) is False
    assert gq.reach(game(), FREE, math.nan) is False
    assert gq.reach(game(), None, NOW) is False


# ---- the authority ladder -----------------------------------------------------------------------

KNOWN = {"join_in_s": -5.0, "lookahead_s": 75.0}


@pytest.mark.parametrize("kw,rung", [
    ({"user": "lock"}, Rung.LOCK),
    ({"user": "lock", "committed": True, "tier": 1, **KNOWN}, Rung.LOCK),
    ({"committed": True, "tier": 1, **KNOWN}, Rung.NEXT),
    ({"committed": True, "user": "pin"}, Rung.NEXT),
    ({"user": "pin", "tier": 1, **KNOWN}, Rung.TIER1),          # decision 12: a known Tier 1 beats a Pin
    ({"user": "pin", "tier": 2, **KNOWN}, Rung.PIN),
    ({"user": "pin"}, Rung.PIN),
    ({"tier": 2, "brain_rank": 1, **KNOWN}, Rung.TIER2),
    ({"tier": 1, "join_in_s": 0.0, "lookahead_s": 75.0}, Rung.TIER1),
    ({"tier": 1, "join_in_s": 0.1, "lookahead_s": 75.0}, Rung.NOBODY),      # outside the look-ahead
    ({"tier": 1, "join_in_s": -70.0, "lookahead_s": 75.0, "brain_rank": 1}, Rung.BRAIN),   # can't make it
    ({"tier": 3, "brain_rank": 1, **KNOWN}, Rung.BRAIN),
    ({"tier": True, "planner_rank": 1, **KNOWN}, Rung.PLANNER),
    ({"brain_rank": 2, "planner_rank": 1}, Rung.BRAIN),
    ({"brain_rank": math.nan, "planner_rank": 1}, Rung.PLANNER),
    ({"planner_rank": 1}, Rung.PLANNER),
    ({}, Rung.NOBODY),
    ({"user": "LOCK"}, Rung.NOBODY),
])
def test_authority_fast_route(kw, rung):
    assert gq.authority(game(**kw), FREE, NOW) == rung


@pytest.mark.parametrize("lead,rung", [(120.0, Rung.WARNING), (119.0, Rung.NOBODY), (None, Rung.NOBODY)])
def test_authority_warning_needs_the_lead_to_fit(lead, rung):
    assert gq.authority(game(route=PRIME, lvl=Level.R1, lead_s=lead), FREE, NOW) == rung


def test_authority_warning_is_only_for_long_lead_routes():
    assert gq.authority(game(lead_s=500.0), FREE, NOW) == Rung.NOBODY


@pytest.mark.parametrize("kw", [{"user": "lock"}, {"committed": True}, {"user": "pin"}, {"brain_rank": 1}])
def test_no_route_holds_no_rung(kw):
    assert gq.authority(game(route=None, **kw), FREE, NOW) == Rung.NOBODY


def names(plan):
    return [r.entry.game for r in plan.rows]


def test_tick_orders_the_whole_ladder_top_wins():
    entries = [
        game("planner", planner_rank=1), game("brain", brain_rank=9),
        game("warn", route=PRIME, lvl=Level.R1, lead_s=500.0), game("t2", tier=2, **KNOWN),
        game("pin", user="pin"), game("t1", tier=1, **KNOWN), game("next", committed=True),
        game("lock", user="lock"), game("idle"),
    ]
    plan = gq.tick(entries, None, None, FREE, NOW)
    assert names(plan) == ["lock", "next", "t1", "pin", "t2", "warn", "brain", "planner", "idle"]
    assert plan.want.entry.game == "lock"


def test_tick_within_a_rung():
    plan = gq.tick([game("a", brain_rank=3), game("b", brain_rank=1), game("c", brain_rank=2)], None, None, FREE, NOW)
    assert names(plan) == ["b", "c", "a"]
    moments = [game("late", tier=1, join_in_s=-1.0, lookahead_s=90.0), game("soon", tier=1, join_in_s=-5.0, lookahead_s=70.0)]
    assert names(gq.tick(moments, None, None, FREE, NOW)) == ["soon", "late"]
    assert names(gq.tick([game("z", planner_rank=1), game("y", planner_rank=1)], None, None, FREE, NOW)) == ["y", "z"]


def test_tick_never_queues_the_on_screen_game():
    plan = gq.tick([game("a", user="lock"), game("b", brain_rank=1)], "a", None, FREE, NOW)
    assert plan.want.entry.game == "b" and plan.on_screen.entry.game == "a"


def test_tick_skips_a_game_it_cannot_start():
    entries = [game("prime", route=PRIME, lvl=Level.R1, brain_rank=1), game("yt", brain_rank=2)]
    assert gq.tick(entries, None, None, FREE, NOW).want.entry.game == "yt"


def test_tick_top_game_already_hidden_stays():
    plan = gq.tick([game("a", lvl=Level.R4, user="lock"), game("b", brain_rank=1)], None, "a", FREE, NOW)
    assert plan.want.entry.game == "a" and plan.swap is False and plan.why == "already_hidden"


def test_tick_hidden_game_missing_from_the_list():
    assert gq.tick([game("b", brain_rank=1)], None, "ghost", FREE, NOW).swap is False
    assert gq.tick([game("b", user="lock")], None, "ghost", FREE, NOW).swap is True


def test_tick_nothing_to_queue():
    plan = gq.tick([game("a")], None, None, FREE, NOW)
    assert plan.want is None and plan.swap is False


def test_tick_duplicate_game_keeps_the_best_rank():
    plan = gq.tick([game("a", planner_rank=1), game("a", user="lock")], None, None, FREE, NOW)
    assert len(plan.rows) == 1 and plan.rows[0].rung == Rung.LOCK


# ---- replace ------------------------------------------------------------------------------------

R4 = {"lvl": Level.R4}


@pytest.mark.parametrize("hidden,want,result", [
    (game("h", user="lock", **R4), game("w", user="lock"), True),        # the latest tap wins
    (game("h", committed=True, **R4), game("w", user="lock"), True),
    (game("h", user="lock", **R4), game("w", committed=True), False),
    (game("h", committed=True, **R4), game("w", committed=True), True),
    (game("h", committed=True, **R4), game("w", user="pin"), False),
    (game("h", tier=1, **KNOWN, **R4), game("w", user="pin"), False),
    (game("h", user="pin", **R4), game("w", tier=1, value=0.0, **KNOWN), True),
    (game("h", tier=2, **KNOWN, **R4), game("w", user="pin"), True),
    (game("h", user="pin", **R4), game("w", tier=2, value=99.0, **KNOWN), False),
    (game("h", brain_rank=1, value=95.0, **R4), game("w", user="lock", value=0.0), True),  # owner ignores value
])
def test_replace_authority(hidden, want, result):
    assert gq.replace(hidden, want, FREE, NOW) is result


def test_replace_owner_ignores_reach():
    far = game("w", route=PRIME, lvl=Level.R1, user="pin")
    assert gq.reach(far, FREE, NOW) is False
    assert gq.replace(game("h", brain_rank=1, **R4), far, FREE, NOW) is True


@pytest.mark.parametrize("want", [
    game("w", route=None, user="lock"),
    game("w", route=dc.replace(YT, flagged=True), user="lock"),
    game("w", route=dc.replace(YT, app=""), committed=True),
    game("w", lvl=Level.R0, user="lock"),
])
def test_replace_never_takes_a_game_with_no_usable_route(want):
    assert gq.replace(game("h", **R4), want, FREE, NOW) is False
    assert gq.replace(None, want, FREE, NOW) is False


def test_replace_same_game_is_not_a_swap():
    assert gq.replace(game("a", **R4), game("a", user="lock"), FREE, NOW) is False


def test_replace_unreachable_moment_becomes_a_rewind():
    want = game("w", tier=1, value=100.0, join_in_s=-70.0, lookahead_s=75.0, brain_rank=1)
    assert gq.replace(game("h", planner_rank=1, value=0.0, **R4), want, FREE, NOW) is False


def test_replace_stick_free():
    assert gq.replace(None, game("w", brain_rank=1), FREE, NOW) is True
    assert gq.replace(None, game("w", route=PRIME, lvl=Level.R1, brain_rank=1), FREE, NOW) is False
    assert gq.replace(game("h", route=None, **R4), game("w", brain_rank=1), FREE, NOW) is True


NEED_R4 = P.evict_margin + P.evict_per_sunk_s * 60.0   # a YouTube TV R4 cost 4 + 56 s


@pytest.mark.parametrize("value,result", [(NEED_R4, False), (NEED_R4 + 0.001, True), (NEED_R4 - 0.001, False)])
def test_replace_value_boundary_includes_sunk_cost(value, result):
    assert gq.replace(game("h", brain_rank=1, value=0.0, **R4), game("w", brain_rank=2, value=value), FREE, NOW) is result


@pytest.mark.parametrize("lvl,sunk", [(Level.R1, 0.0), (Level.R2, 0.0), (Level.R3, 4.0), (Level.R4, 60.0), (Level.R5, 60.0)])
def test_replace_sunk_cost_follows_the_level(lvl, sunk):
    need = P.evict_margin + P.evict_per_sunk_s * sunk
    hidden = game("h", brain_rank=1, value=0.0, lvl=lvl)
    assert gq.replace(hidden, game("w", brain_rank=2, value=need), FREE, NOW) is False
    assert gq.replace(hidden, game("w", brain_rank=2, value=need + 0.01), FREE, NOW) is True


def test_replace_prime_sunk_cost_is_dearer():
    hidden = game("h", route=PRIME, brain_rank=1, value=0.0, **R4)   # 120 s went in
    assert gq.replace(hidden, game("w", brain_rank=2, value=16.9), FREE, NOW) is False
    assert gq.replace(hidden, game("w", brain_rank=2, value=17.1), FREE, NOW) is True


def test_replace_known_tier1_beats_a_ready_brain_game():
    want = game("w", tier=1, value=0.0, **KNOWN)
    assert gq.replace(game("h", brain_rank=1, value=80.0, **R4), want, FREE, NOW) is True


def test_replace_lower_rung_never_evicts_a_higher_one():
    assert gq.replace(game("h", brain_rank=1, value=0.0), game("w", planner_rank=1, value=100.0), FREE, NOW) is False


@pytest.mark.parametrize("bad", BAD + (None,))
def test_replace_bad_value_keeps_what_is_there(bad):
    assert gq.replace(game("h", brain_rank=1), game("w", brain_rank=2, value=bad), FREE, NOW) is False
    assert gq.replace(game("h", brain_rank=1, value=bad), game("w", brain_rank=2, value=50.0), FREE, NOW) is False


# ---- failure table (P-QUEUE section 9) ----------------------------------------------------------

TWO = Entry("A", (YT, Route("espn", "e2", klass=gq.LIVE_EDGE)), Level.R2)


def test_every_failure_has_a_rule():
    assert set(gq.FAILURE_RULES) == set(Failure)


@pytest.mark.parametrize("entry,kind,step,budget", [
    (TWO, Failure.STICK_HUNG, Step.WAIT_STICK, 0.0),
    (TWO, Failure.LINK_REFUSED, Step.NEXT_ROUTE, 0.0),
    (TWO, Failure.BLACKOUT, Step.NEXT_ROUTE, 0.0),
    (TWO, Failure.WALLED, Step.STOP, 0.0),
    (game(route=PRIME), Failure.NEVER_STARTED, Step.NEXT_GAME, 55.0),      # Prime has no retry, no other route
    (game(route=ESPN), Failure.NEVER_STARTED, Step.NEXT_GAME, 20.0),
    (game(route=Route("sling", "x")), Failure.NEVER_STARTED, Step.NEXT_GAME, 30.0),
    (TWO, Failure.NEVER_STARTED, Step.RETRY, 12.0),
    (game(route=SEARCH), Failure.NEVER_STARTED, Step.RETRY, 30.0),
    (TWO, Failure.SCRUB_BANNER_MISS, Step.STAY_LIVE, 45.0),
    (TWO, Failure.SCRUB_FAILED, Step.STAY_LIVE, 45.0),
    (TWO, Failure.HOLD_FAILED, Step.RETUNE, 4.0),
    (TWO, Failure.NO_ROUTE, Step.NEXT_GAME, 0.0),
    (TWO, Failure.UNKNOWN, Step.NEXT_GAME, 0.0),
])
def test_failure_table(entry, kind, step, budget):
    move = gq.on_failure(entry, kind)
    assert (move.step, move.budget_s) == (step, budget)


def test_stick_hung_does_not_blame_the_route():
    move = gq.on_failure(dc.replace(TWO, level=Level.R4), Failure.STICK_HUNG)
    assert move.entry.routes == TWO.routes and move.route_off == () and move.advance is False
    assert move.entry.level == Level.R2      # nothing on a hung stick counts as ready


def test_link_refused_goes_to_the_next_route_tonight():
    move = gq.on_failure(TWO, "link_refused")
    assert move.route_off == (YT,) and move.entry.routes == TWO.routes[1:] and move.entry.level == Level.R1


def test_blackout_is_one_read_and_off_for_the_night():
    move = gq.on_failure(TWO, Failure.BLACKOUT)
    assert move.reads == 1 and move.route_off == (YT,) and move.phone


def test_walled_never_presses_and_takes_the_whole_app_off():
    e = Entry("A", (Route("prime", "a"), Route("prime", "b"), YT), Level.R2)
    move = gq.on_failure(e, Failure.WALLED)
    assert move.step == Step.STOP and move.press_ok is False and move.phone
    assert [r.app for r in move.route_off] == ["prime", "prime"] and move.entry.routes == (YT,)


def test_walled_on_the_last_route_stays_a_stop():
    move = gq.on_failure(game(route=PRIME), Failure.WALLED)
    assert move.step == Step.STOP and move.press_ok is False and move.entry.level == Level.R0


def test_never_started_retries_youtube_once_then_moves_on():
    first = gq.on_failure(TWO, Failure.NEVER_STARTED, 0)
    assert first.step == Step.RETRY and first.entry == TWO and first.advance is False
    second = gq.on_failure(TWO, Failure.NEVER_STARTED, 1)
    assert second.step == Step.NEXT_ROUTE and second.backoff_s == 120.0
    assert second.route_off == () and second.entry.routes == TWO.routes[1:]


def test_never_started_with_no_route_left_does_not_claim_tonight():
    move = gq.on_failure(game(route=PRIME), Failure.NEVER_STARTED)
    assert move.step == Step.NEXT_GAME and move.phone == "" and move.backoff_s == 120.0


def test_refused_with_no_route_left_says_so():
    move = gq.on_failure(game(), Failure.LINK_REFUSED)
    assert move.step == Step.NEXT_GAME and move.phone and move.entry.level == Level.R0


def test_scrub_banner_miss_stays_live_and_retries_once():
    first = gq.on_failure(TWO, Failure.SCRUB_BANNER_MISS, 0)
    assert first.step == Step.STAY_LIVE and first.retry_after_s == 30.0 and first.advance is False
    assert first.entry.level == Level.R3 and first.entry.routes == TWO.routes
    second = gq.on_failure(TWO, Failure.SCRUB_BANNER_MISS, 1)
    assert second.failure == Failure.SCRUB_FAILED and second.advance is True and second.retry_after_s == 0.0


def test_scrub_failed_stays_live_and_moves_on():
    move = gq.on_failure(dc.replace(TWO, level=Level.R5), Failure.SCRUB_FAILED)
    assert move.entry.level == Level.R3 and move.advance is True


def test_hold_failed_retunes_once_then_stays_live():
    first = gq.on_failure(TWO, Failure.HOLD_FAILED, 0)
    assert first.step == Step.RETUNE and first.entry.level == Level.R3 and first.advance is False
    second = gq.on_failure(TWO, Failure.HOLD_FAILED, 1)
    assert second.step == Step.STAY_LIVE and second.advance is True


@pytest.mark.parametrize("bad", ["nope", None, 7, [], {}])
def test_unknown_failure_fails_closed(bad):
    move = gq.on_failure(TWO, bad)
    assert move.failure == Failure.UNKNOWN and move.step == Step.NEXT_GAME and move.press_ok is False
    assert move.backoff_s == 120.0 and move.entry.routes == TWO.routes


@pytest.mark.parametrize("bad", [-1, 1.5, "0", None, True])
def test_bad_attempt_means_no_retries_left(bad):
    assert gq.on_failure(TWO, Failure.NEVER_STARTED, bad).step == Step.NEXT_ROUTE
    assert gq.on_failure(TWO, Failure.HOLD_FAILED, bad).step == Step.STAY_LIVE


@pytest.mark.parametrize("kind", list(Failure))
def test_failure_on_a_bad_entry_does_not_raise(kind):
    assert gq.on_failure("not an entry", kind).step == Step.NEXT_GAME


@pytest.mark.parametrize("kind", [Failure.LINK_REFUSED, Failure.BLACKOUT, Failure.NEVER_STARTED, Failure.WALLED])
def test_routes_always_run_out(kind):
    e = Entry("A", (Route("a", "1"), Route("b", "2"), Route("c", "3")), Level.R2)
    for _ in range(4):
        e = gq.on_failure(e, kind, 5).entry
    assert e.routes == () and e.level == Level.R0


# ---- view ---------------------------------------------------------------------------------------


def test_view_shape():
    entries = [game("s", lvl=Level.R4), game("h", lvl=Level.R5, brain_rank=1)]
    entries += [game(f"n{i}", planner_rank=i) for i in range(7)]
    entries += [game("gone", route=None), game("walled", route=dc.replace(YT, flagged=True))]
    v = gq.view(gq.tick(entries, "s", "h", FREE, NOW))
    assert v["on_screen"]["game"] == "s" and v["partner"] == "s"
    assert v["hidden"]["game"] == "h" and v["hidden"]["flippable"] is True
    assert [c["game"] for c in v["up_next"]] == ["n0", "n1", "n2", "n3", "n4"]
    assert v["up_next"][0] == {"game": "n0", "level": "R2", "app": "youtubetv", "prep_s": 60.0,
                               "owner": "planner", "reason": "next in rotation", "flippable": False}
    assert sorted(v["cant_reach"]) == ["gone", "walled"]
    json.dumps(v, allow_nan=False)


def test_view_no_partner_when_the_screen_is_at_the_live_edge():
    assert gq.view(gq.tick([game("s", lvl=Level.R3)], "s", None, FREE, NOW))["partner"] is None


def test_view_is_spoiler_safe():
    plan = gq.tick([game("t1", tier=1, value=88.0, **KNOWN), game("t2", tier=2, value=77.0, **KNOWN)], None, None, FREE, NOW)
    text = json.dumps(gq.view(plan))
    for leak in ("tier", "join", "lookahead", "88", "77", "75"):
        assert leak not in text
    assert plan.rows[0].rung != plan.rows[1].rung and text.count('"owner": "moment"') == 2


# ---- config and purity --------------------------------------------------------------------------


def test_params_from_config_takes_good_values_only():
    cfg = {"queue.reach_margin_s": 3.5, "queue.evict_margin": -1, "queue.behind_s": "fast", "queue.r3_flippable": True,
           "queue.up_next": 3, "queue.tune_s": {"prime": 25.0, "espn": math.nan}, "queue.hold_apps": ["prime", "x"],
           "queue.retry_apps": "youtubetv"}
    p = gq.Params.from_config(lambda k, d=None: cfg.get(k, d))
    assert p.reach_margin_s == 3.5 and p.r3_flippable is True and p.up_next == 3 and p.hold_apps == ("prime", "x")
    assert p.evict_margin == P.evict_margin and p.behind_s == P.behind_s and p.retry_apps == P.retry_apps
    assert p.tune_s["prime"] == 25.0 and p.tune_s["espn"] == P.tune_s["espn"] and p.tune_s["youtubetv"] == P.tune_s["youtubetv"]


def test_params_from_an_empty_config_is_the_default():
    assert gq.Params.from_config(lambda k, d=None: d) == P


def test_r3_flippable_flag_defaults_off():
    assert P.r3_flippable is False


def test_module_is_pure():
    tree = ast.parse((ROOT / "redzone/noggin/gamequeue.py").read_text())
    roots = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            roots |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            assert n.level == 0
            roots.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            assert n.func.id not in ("open", "print", "exec", "eval")
    assert roots <= {"__future__", "dataclasses", "math", "collections", "enum", "typing"}


# ---- hostile fuzz -------------------------------------------------------------------------------

JUNK = [math.nan, math.inf, -math.inf, -1.0, 0.0, 0.1, 1e308, -1e308, 10**400, "x", "", None, True, False, [], {}, (), object()]


def junky(rng, good):
    return rng.choice(JUNK) if rng.random() < .45 else good


def hostile_route(rng):
    if rng.random() < .1:
        return rng.choice(JUNK)
    return Route(junky(rng, rng.choice(["youtubetv", "prime", "espn", "sling"])), junky(rng, "ref"),
                 junky(rng, rng.choice([gq.FAST, gq.LONG_LEAD, gq.LIVE_EDGE, "wat"])),
                 junky(rng, rng.random() < .3), junky(rng, False))


def hostile_entry(rng, name):
    rs = tuple(hostile_route(rng) for _ in range(rng.randint(0, 3)))
    num = lambda good: junky(rng, good)   # noqa: E731
    return Entry(name, junky(rng, rs), num(rng.randint(0, 5)), num(rng.choice(["", "pin", "lock"])), num(rng.random() < .2),
                 num(rng.choice([None, 1, 2, 3])), num(rng.uniform(-80, 20)), num(rng.uniform(0, 100)),
                 num(rng.uniform(0, 200)), num(rng.uniform(0, 9)), num(rng.uniform(0, 9)), num(rng.uniform(0, 100)))


def hostile_stick(rng):
    if rng.random() < .1:
        return rng.choice(JUNK)
    return Stick(junky(rng, NOW + rng.uniform(-5, 30)), junky(rng, rng.choice(["G0", "G1"])), junky(rng, rng.choice([3, 4, 5])),
                 junky(rng, {"youtubetv": rng.choice(JUNK)}), junky(rng, {"prime": rng.choice(JUNK)}))


def usable(e):
    return gq.level(e) != Level.R0 and gq._route(e) is not None


@pytest.mark.parametrize("seed", range(10))
def test_hostile_inputs_never_raise_and_fail_closed(seed):
    rng = random.Random(seed)
    for _ in range(400):
        entries = [hostile_entry(rng, f"G{i}") for i in range(rng.randint(0, 12))]
        junk_in = [rng.choice(JUNK)] if rng.random() < .2 else []
        stick, now = hostile_stick(rng), junky(rng, NOW)
        on_screen, hidden = junky(rng, "G0"), junky(rng, "G1")
        p = dc.replace(P, r3_flippable=rng.random() < .5)
        plan = gq.tick(entries + junk_in, on_screen, hidden, stick, now, p)
        view = gq.view(plan)
        json.dumps(view, allow_nan=False)                                 # JSON-safe, no NaN
        assert len(view["up_next"]) <= p.up_next

        games = [r.entry.game for r in plan.rows]
        assert len(games) == len(set(games)) and (not isinstance(on_screen, str) or on_screen not in games)
        if plan.want is None:
            assert plan.swap is False
        else:
            assert plan.want.rung != Rung.NOBODY and usable(plan.want.entry)
        if plan.swap:
            assert usable(plan.want.entry)
            assert plan.want.rung in gq._OWNERS or plan.want.reach

        for e in entries:
            cost = gq.prep_s(e, stick, now, p)
            assert cost == cost and cost >= 0                             # never NaN, never negative
            lv = gq.level(e)
            assert gq.flippable(e, p) is (lv >= Level.R4 or (lv == Level.R3 and p.r3_flippable))
            ok = gq.reach(e, stick, now, p)
            if lv == Level.R0:
                assert ok is False and cost == math.inf and gq.flippable(e, p) is False
            if e.join_in_s is not None and (gq._num(e.join_in_s) is None or gq._cost(e.lookahead_s) is None):
                assert ok is False
            for other in rng.sample(entries, min(2, len(entries))):
                swap = gq.replace(other, e, stick, now, p)
                assert swap in (True, False)
                if not usable(e):
                    assert swap is False
            step = gq.on_failure(e, junky(rng, rng.choice(list(Failure))), junky(rng, 0), p)
            assert step.step in Step and step.budget_s >= 0
            assert step.entry == e or len(gq._live_routes(step.entry)) <= len(gq._live_routes(e))
            if step.failure == Failure.WALLED:
                assert step.press_ok is False and not any(
                    r.app == gq._route(e).app for r in gq._live_routes(step.entry)) if gq._route(e) else True


@pytest.mark.parametrize("seed", range(5))
def test_input_order_does_not_change_the_answer(seed):
    rng = random.Random(1000 + seed)
    for _ in range(100):
        entries = [hostile_entry(rng, f"G{i}") for i in range(10)]
        stick = Stick(busy_until=NOW + 5)
        first = gq.view(gq.tick(entries, "G0", "G1", stick, NOW))
        rng.shuffle(entries)
        assert gq.view(gq.tick(entries, "G0", "G1", stick, NOW)) == first


# ---- exit check and the replay script -----------------------------------------------------------


def _replay_module():
    spec = importlib.util.spec_from_file_location("queue_replay", ROOT / "scripts/queue_replay.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["queue_replay"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_32_games_tick_and_view_under_1ms():
    games = _replay_module().slate()
    assert len(games) == 32
    stick, best = Stick(busy_until=NOW + 5, job_game="G03", job_to=3), math.inf
    for _ in range(300):   # the best run, so a loaded machine doesn't fail it
        t0 = time.perf_counter()
        gq.view(gq.tick(games, "G00", "G01", stick, NOW))
        best = min(best, time.perf_counter() - t0)
    assert best < 0.001


def test_replay_builtin_scenario():
    lines = _replay_module().replay(_replay_module().BUILTIN)
    assert [ln["queue"]["want"] for ln in lines][:3] == ["GB", "GB", "KC"]
    assert lines[3]["failure"]["step"] == "next_game" and "KC" in lines[3]["queue"]["cant_reach"]
    walled = lines[5]["failure"]
    assert walled["step"] == "stop" and "WSH" in lines[5]["queue"]["cant_reach"]
    json.dumps(lines, allow_nan=False)
