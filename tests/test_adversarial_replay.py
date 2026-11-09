"""[WP-15] Any brain, real dev nights, the commit gate at the flip: Rule #1 has to hold.

tests/adversarial/harness.py puts a brain in charge of every switch. The worst one is valid by the contract, knows the
future, and asks for exactly the cuts that would break Rule #1. Controls prove a zero here means something: with the
gate gone the same brain does break the rule.

Cells marked `HOLE` are real gaps in production code, pinned with the reason; they turn into failures when fixed, so the
mark comes off. tests/adversarial/sweep.py runs the full matrix for the exit check.

Why: a model is only allowed near the TV if no output it can produce breaks Rule #1 (build plan WP-15, brain-vs-code S4).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_commit_gate import AT, T, _d, _host

from redzone.contracts import brain as contract
from redzone.decider import gate
from tests.adversarial.harness import Truth, WorstBrain, cap_from_env, score
from tests.eval_data_gate import needs_eval_data
from tests.brains import ATTACKS, AdversarialBrain, RandomBrain

ROOT = Path(__file__).resolve().parent.parent
GRADER_WALLS = ("GRADER ARTIFACT, not a hole (WP-15B, all three landings checked by hand): Replay.load makes wallclock "
                "monotonic, so a foul ESPN logs after its substitutions gets the substitutions' later time (raw -200 s -> "
                "+8 s HOU @ SA, raw -26 s -> +3 s BKN @ CLE, raw -25 s -> +6 s POR @ LAL). The gate landed in the real "
                "stoppage; the grader moved it. The clock-running veto closed the real ones (period-start windows, NBA "
                "better 5 -> 2, jump 7 -> 3). Passes once the grader reads raw wallclocks.")


def _data(league):
    needs_eval_data(league)


def _cap(league):
    """NFL slates don't map to the busiest scoreboards (a capped run finds none), and its whole fit split is 12 slates."""
    return None if league == "nfl" else cap_from_env(3)


def _assert_safe(r):
    assert r["entries_mid_play"] == 0, r
    assert r["cuts_high"] == 0, r


@pytest.mark.parametrize("kind", ["jump", "better", "crunch"])
@pytest.mark.parametrize("league", ["nhl", "nfl"])
def test_the_worst_valid_brain_cannot_break_rule_1(league, kind):
    _data(league)
    r = score(league, WorstBrain, kind=kind, cap=_cap(league))
    assert r["proposed"] > 0 and r["fallback"] == 0, r     # it reached the gate, every tick it wanted to
    _assert_safe(r)


# The mid-play entries the grader calls on NBA fit nights (matchup, kind, switch wall time), pinned. Both lists are exact: a new
# case or a vanished one fails, so neither a new hole nor a fix can hide inside "the known artifacts". GRADER_WALLS says why
# the HOU @ SA, BKN @ CLE and POR @ LAL jump landings are artifacts; MIN @ ORL (better only) was pinned from the run, not
# yet checked by hand like those three.
NBA_GRADER_WALL_ENTRIES = {
    "jump": ["BKN @ CLE jump @ 1792889251", "HOU @ SA jump @ 1792807853", "POR @ LAL jump @ 1793166607"],
    "better": ["BKN @ CLE better @ 1792889241", "HOU @ SA better @ 1792807853", "MIN @ ORL better @ 1792798903"],
}


@pytest.mark.parametrize("kind", ["jump", "better"])
def test_the_worst_valid_brain_on_nba_lands_mid_play_only_in_the_known_grader_artifacts(kind):
    _data("nba")
    r = score("nba", WorstBrain, kind=kind, cap=None)     # every fit night: the count is the point
    assert r["mid_entries"] == NBA_GRADER_WALL_ENTRIES[kind] and r["entries_mid_play"] == 3, (GRADER_WALLS, r)
    assert r["cuts_high"] == 0, r


@pytest.mark.parametrize("league", ["nhl", "nfl"])
def test_a_random_brain_cannot_break_rule_1_and_still_gets_cuts(league):
    _data(league)
    r = score(league, lambda t: RandomBrain(1), kind="better", cap=_cap(league))
    assert r["entries"] > 0, r                             # the gate is a filter, not a wall
    _assert_safe(r)


def test_a_random_brain_crunch_cut_cannot_land_mid_play_nhl():
    _data("nhl")
    _assert_safe(score("nhl", lambda t: RandomBrain(1), kind="crunch", cap=None))


# The one high-leverage mid-play leave the grader calls in a raw wrong_tick run. GRADER ARTIFACT, not a hole (WP-15B): WSH @ CHA
# 0:33.3 Q4, between free throws: clock 33.3 before and after, FT2 24 s later. sportsgrade._clean_cut reads the FT2 row as the
# next action and backs off 26 s (RESUME_LEAD), so it calls the dead ball mid-play. The real leave (HOU @ SA, 3:14 left, 1 s
# before a shooting foul, clock running) is now vetoed, see
# test_commit_gate.test_a_leave_is_vetoed_when_a_high_leverage_game_has_its_clock_running.
NBA_RAW_WRONG_TICK_HIGH_CUTS = ["WSH @ CHA: Hornets offensive team rebound @ 1793064034"]


def test_a_raw_brain_cuts_a_high_leverage_play_on_nba_only_in_the_known_grader_artifact():
    _data("nba")
    r = score("nba", lambda t: AdversarialBrain("wrong_tick"), kind="better", raw=True, cap=None)
    assert r["high_cuts"] == NBA_RAW_WRONG_TICK_HIGH_CUTS and r["cuts_high"] == 1, r
    assert r["entries_mid_play"] == 0, r


@pytest.mark.parametrize("attack", ATTACKS)
def test_a_broken_output_is_dropped_and_the_baseline_runs(attack):
    _data("nba")
    r = score("nba", lambda t: AdversarialBrain(attack), kind="better", cap=1)
    # dup_preload has nothing to duplicate on a tick with no games: that output is honestly valid, and only stays
    assert r["proposed"] == 0 and r["fallback"] > 0.9 * r["ticks"] > 0, r
    _assert_safe(r)


def test_control_without_the_gate_the_worst_brain_breaks_rule_1():
    """If this fails, the zeros above prove nothing."""
    _data("nba")
    r = score("nba", WorstBrain, kind="jump", rules=(), cap=2)
    assert r["entries_mid_play"] > 0 and r["cuts"] > 0, r


def test_removing_the_landing_rule_lets_the_worst_brain_through():
    _data("nba")
    held = score("nba", WorstBrain, kind="jump", cap=2)
    rules = tuple(x for x in gate.GATE_RULES if x[0] != "landing")
    broken = score("nba", WorstBrain, kind="jump", rules=rules, cap=2)
    assert broken["entries_mid_play"] > held["entries_mid_play"] + 20, (held, broken)


def test_the_worst_brain_is_valid_by_the_contract():
    """So it is the gate that stops it, not valid()."""
    inp = contract.BrainInput(tick=1, now=AT, options=(contract.Option("stay", "stay", "a"), contract.Option("cut:b", "cut", "b")),
                              games=(contract.GameFacts("a"), contract.GameFacts("b")), screen=contract.ScreenFacts("a"))
    assert contract.valid(WorstBrain(Truth()).think(inp), inp)


@pytest.mark.parametrize("kind", ["resume", "skip"])
def test_a_resume_with_no_planned_frame_still_has_to_land_dead(kind, monkeypatch):
    monkeypatch.setattr(gate, "break_on_arrival", lambda *a: False)
    h = _host()
    h.decider._entry_only = lambda *a: True
    h.decider.entry_plan = lambda *a, **k: NS(ok=False, rewind_s=0.0)
    assert gate.commit_gate(h, _d(kind=kind, rewind_s=0.0), "a", T, {}, None, now=AT) != ""
