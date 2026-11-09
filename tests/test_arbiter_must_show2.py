"""MUST-SHOW-2: the order of clips a free hidden stick takes, hurry-up plays as one clip, and the sim's arbiter policy.

Why: a must-show counts only if the play aired in full; the picks may change what airs, never what counts as seen."""
import random

import pytest

from redzone.models import League
from redzone.noggin import arbiter as A
from scripts import buffered_jump_sim as sim
from tests.test_maxrate_sim import plays, slate


def test_without_catchup_the_key_is_the_old_trigger_order_key():
    assert A.clip_key(3, False, 10.0, 20.0) == (3, False, 10.0, 20.0)


def test_with_catchup_every_live_must_show_goes_before_any_catchup_one():
    live_gain = A.clip_key(A.trigger_rank("nfl", "gain10"), False, 50.0, 60.0, "rank", True)
    old_td = A.clip_key(0, True, 1.0, 2.0, "rank", True)
    assert live_gain < old_td


def test_a_non_floor_clip_never_jumps_a_catchup_must_show():
    assert A.clip_key(0, True, 900.0, 950.0, "rank", True) < A.clip_key(A.NOT_FLOOR, False, 1.0, 2.0, "rank", True)


def cand(i, rank, show, dur=27.0, late=False, deadline=1e9, last=1e9):
    return (i, rank, late, show, deadline, dur, last)


def test_pick_clip_takes_the_best_clip_when_it_can_air_now():
    assert A.pick_clip([cand("td", 0, 5.0), cand("gain", 14, 1.0)], 0.0, 2.5, 30.0) == "td"


def test_pick_clip_fills_in_front_of_a_clip_still_waiting_for_its_window():
    td = cand("td", 0, 80.0, last=200.0)
    assert A.pick_clip([td, cand("gain", 14, 2.0)], 0.0, 2.5, 30.0) == "gain"


def test_pick_clip_never_fills_when_the_best_would_lose_its_window():
    td = cand("td", 0, 35.0, last=40.0)
    assert A.pick_clip([td, cand("gain", 14, 12.0)], 0.0, 2.5, 30.0) == "td"


def test_nothing_goes_in_front_of_a_protected_rank():
    td = cand("td", 0, 80.0, last=200.0)
    gain = cand("gain", 14, 2.0)
    assert A.pick_clip([td, gain], 0.0, 2.5, 30.0, protect_rank=0) == "td"
    assert A.pick_clip([td, gain], 0.0, 2.5, 30.0, protect_rank=-1) == "gain"


def test_a_non_floor_clip_is_never_the_filler_for_a_must_show():
    td = cand("td", 0, 80.0, last=200.0)
    assert A.pick_clip([td, cand("x", A.NOT_FLOOR, 2.0)], 0.0, 2.5, 30.0) == "td"


def item(land, leave, start=None, end=None):
    return sim.MRItem("g", land, leave, land + 7 if start is None else start, leave - 7 if end is None else end, leave + 20)


def test_merge_joins_a_hurry_up_run_with_clean_outer_ends():
    its = [item(0.0, 27.0), item(26.0, 53.0, 34.0, 46.0), item(200.0, 227.0)]   # the middle pair cannot be landed between
    sim.mr_merge(its, 6.75, 120.0)
    assert [(i.land, i.leave) for i in its] == [(0.0, 53.0), (200.0, 227.0)] and its[0].clean


def test_merge_leaves_a_run_too_long_or_not_clean_on_the_outside():
    long_run = [item(0.0, 27.0), item(26.0, 300.0, 34.0, 290.0)]
    sim.mr_merge(long_run, 6.75, 120.0)
    assert len(long_run) == 2     # past the cap: untouched, so each stays unclean and is never a job
    run = [item(0.0, 27.0, 7.0, 25.0), item(24.0, 53.0, 30.0, 46.0), item(52.0, 79.0, 58.0, 72.0)]
    sim.mr_merge(run, 6.75, 60.0)
    assert len(run) == 3          # the whole run does not fit in 60 s


def test_the_arbiter_policy_needs_facts():
    with pytest.raises(ValueError):
        sim.run_maxrate(slate(k=3), sim.MR(policy="arbiter"), random.Random(1), League.NFL)


def facts_for(games, every_nth=3):
    """Must-shows on every third play of each game: a heavy slate, so catch-up has work to do."""
    out = {}
    for g, q in games:
        floor = sorted((t, A.trigger_rank("nfl", "gain10"), "gain10") for i, (t, *_rest) in enumerate(q) if i % every_nth == 0)
        out[g] = (floor, [(t, False) for t, *_ in q])
    return out


def run_arb(games, arb):
    return sim.run_maxrate(games, sim.MR(games=4, seek_s=8.0, prep_s=15.0, policy="arbiter", arb=arb), random.Random(5),
                           League.NFL, facts_for(games))


def test_catchup_airs_must_shows_the_live_window_had_to_drop_and_rule_one_holds():
    games = [(str(9000 + i), plays(str(9000 + i), 1_700_000_000.0 + 3.0 * i, every=33.0)) for i in range(4)]
    live = run_arb(games, sim.Arb(fill=True))
    caught = run_arb(games, sim.Arb(fill=True, catchup_s=600.0))
    missed = lambda r: [m for m in r["misses"] if m[1] != "seen_catchup"]   # noqa: E731
    assert caught["catchup_clips"] > 0 and len(missed(caught)) < len(missed(live))
    for r in (live, caught):
        assert r["joins_mid_play"] == 0 and r["leaves_high"] == 0


def test_a_missed_must_show_names_one_reason_from_the_list():
    games = [(str(9000 + i), plays(str(9000 + i), 1_700_000_000.0 + 3.0 * i, every=33.0)) for i in range(4)]
    r = run_arb(games, sim.Arb())
    assert r["misses"] and {w for _, w in r["misses"]} <= set(sim.MISS_WHYS) | {"seen_catchup"}
