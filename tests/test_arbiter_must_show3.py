"""MUST-SHOW-3: at the real 95 s rewind, the order of the must-shows is a scheduling question. plan_clips answers it."""
from __future__ import annotations

import random

from redzone.models import League
from redzone.noggin import arbiter as A
from scripts import buffered_jump_sim as sim
from tests.test_arbiter_must_show2 import facts_for, plays

# (id, weight, window open, last legal flip, seconds, wall time this stick is ready)
def clip(i, w, opens, close=None, secs=27.0, ready=0.0):
    return (i, w, opens, opens + 35.0 if close is None else close, secs, ready)


FLIP, PREP = 2.5, 22.5


def plan(cands, free_at=0.0, **kw):
    return A.plan_clips(cands, free_at, FLIP, PREP, **kw)


def test_clip_weight_counts_owed_moments_and_a_touchdown_outweighs_any_run():
    assert A.clip_weight([]) < 1.0 <= A.clip_weight([7])
    assert A.clip_weight([14, 7, 12]) == 3.0
    assert A.clip_weight([0]) > 50.0


def test_nothing_to_plan_is_none():
    assert plan([]) is None


def test_a_clip_past_its_last_flip_is_not_planned():
    assert plan([clip("late", 1.0, 0.0, close=5.0, ready=20.0)]) is None


def test_the_run_airs_both_when_the_earlier_window_goes_first():
    # Rank alone airs the better clip first and the other one dies. Earliest window first airs both.
    better_but_later, cheap_but_first = clip("b", 2.0, 30.0), clip("c", 1.0, 0.0, close=8.0)
    assert plan([better_but_later, cheap_but_first]) == "c"


def test_when_only_one_fits_the_more_valuable_wins():
    assert plan([clip("one", 1.0, 0.0, close=4.0), clip("two", 2.0, 1.0, close=4.0)]) == "two"


def test_a_touchdown_beats_a_run_of_other_clips():
    td = clip("td", A.clip_weight([0]), 0.0, close=3.0)
    assert plan([td, clip("a", 3.0, 0.0, close=3.0), clip("b", 3.0, 0.5, close=3.5)]) == "td"


def test_a_gap_filler_goes_only_where_nothing_owed_needs_the_screen():
    owed = clip("owed", 1.0, 40.0)
    assert plan([owed, clip("filler", A.clip_weight([]), 0.0, secs=20.0)]) == "filler"           # fits in front
    assert plan([owed, clip("filler", A.clip_weight([]), 20.0, close=30.0, secs=60.0)]) == "owed"     # would cost it


def test_the_stick_keeps_its_clip_on_a_tie():
    kept, other = clip("kept", 1.0, 0.0, close=5.0), clip("other", 1.0, 1.0, close=6.0)
    assert plan([kept, other], keep="kept") == "kept"


def test_a_touchdown_takes_the_stick_from_a_clip_that_would_cost_it():
    kept = clip("kept", 1.0, 0.0, close=5.0, secs=30.0)
    td = clip("td", A.clip_weight([0]), 2.0, close=10.0, ready=4.0)
    assert plan([kept, td], keep="kept") == "td"


def test_a_touchdown_behind_another_clip_gets_the_worst_case_prep():
    first = clip("a", 1.0, 0.0, close=5.0, secs=5.0)
    td = clip("td", A.clip_weight([0]), 0.0, close=26.0)
    assert A.plan_clips([first, td], 0.0, FLIP, 10.0, sure_prep_s=30.0) == "td"      # the worst case doesn't fit behind "a"
    assert A.plan_clips([first, td], 0.0, FLIP, 10.0) == "a"                          # the average would


# --- the sim, on a heavy synthetic slate ---------------------------------------------------------------------------------
GAMES = [(str(9000 + i), plays(str(9000 + i), 1_700_000_000.0 + 3.0 * i, every=33.0)) for i in range(4)]


def run_sim(arb, high=False):
    f = facts_for(GAMES)
    if high:
        f = {g: (fl, [(t, True) for t, _ in lev]) for g, (fl, lev) in f.items()}
    return sim.run_maxrate(GAMES, sim.MR(games=4, seek_s=8.0, prep_s=15.0, policy="arbiter", arb=arb), random.Random(5), League.NFL, f)


def missed(r):
    return [m for m in r["misses"] if m[1] != "seen_catchup"]


def test_the_plan_misses_fewer_must_shows_than_the_trigger_order_and_rule_one_holds():
    off = run_sim(sim.Arb(fill=True, protect=0, catchup_s=95.0, plan=False, save_leave=False))
    on = run_sim(sim.Arb(fill=True, protect=0, catchup_s=95.0))
    assert len(missed(on)) < len(missed(off))
    assert on["joins_mid_play"] == 0 and on["leaves_high"] == 0


def test_save_leave_needs_the_plan():
    a = run_sim(sim.Arb(fill=True, plan=False, save_leave=True))
    b = run_sim(sim.Arb(fill=True, plan=False, save_leave=False))
    assert a == b


def test_never_a_save_leave_from_a_high_leverage_game():
    r = run_sim(sim.Arb(fill=True, protect=0, catchup_s=95.0), high=True)
    assert r["save_leaves"] == 0
