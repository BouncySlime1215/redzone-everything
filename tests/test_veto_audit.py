"""[VETO-AUDIT] The audit's bookkeeping, and the rule that explains a refusal can't disagree with the rule that refuses.

Why: `legal.refusals` and `arbiter.refused_by` name what said no to a brain's top pick. If the explanation drifted from
the real choice, the ranked table would blame the wrong rule. The replay itself is scripts/veto_audit.py's job.
"""
from __future__ import annotations

import dataclasses
import itertools
import sys
from collections import Counter

import pytest

from redzone.contracts import brain
from redzone.decider import gate
from redzone.noggin import arbiter, legal
from scripts import veto_audit as va


NOW = 1000.0


def _facts(**kw):
    rows = (legal.GameRow("a"), legal.GameRow("b"), legal.GameRow("c"))
    return legal.Facts(**{"now": NOW, "current": "a", "games": rows, **kw})


SITUATIONS = {
    "clear": {},
    "paused": {"paused": True},
    "manual": {"manual_until": NOW + 5},
    "locked": {"locked_game": "a"},
    "safe": {"safe_mode": True},
    "arriving": {"arrival_until": NOW + 1},
    "dwell": {"since": NOW - 1, "dwell_min_s": 30.0, "dwell_kinds": ("better",)},
    "too_soon": {"last_switch_at": NOW - 1, "min_gap_s": 30.0},
    "leave_gate": {"gate_ok": False},
    "moment_lock": {"moment_lock": "empty net"},
    "focus": {"focus_game": "c"},
    "dark": {"games": (legal.GameRow("a"), legal.GameRow("b", dark=True))},
    "no_landing": {"games": (legal.GameRow("a"), legal.GameRow("b", landable=False))},
    "not_tunable": {"games": (legal.GameRow("a"), legal.GameRow("b", tunable=False))},
    "banned": {"games": (legal.GameRow("a"), legal.GameRow("b", app="peacock"))},
    "off_screen": {"current": None},
}


@pytest.mark.parametrize("name", sorted(SITUATIONS))
def test_refusals_empty_exactly_when_the_cut_is_on_the_list(name):
    f = _facts(**SITUATIONS[name])
    on_list = {o.game_id for o in legal.legal_options(f) if o.verb == "cut"}
    for g in f.games:
        assert (legal.refusals(f, g.game_id) == ()) == (g.game_id in on_list), (name, g.game_id)


def test_refusals_name_each_rule_that_said_no():
    f = _facts(paused=True, gate_ok=False)
    assert legal.refusals(f, "b") == ("user:paused", "leave_gate")
    assert legal.refusals(_facts(), "zz") == ("unknown_game",)
    assert legal.refusals(_facts(), "a") == ("on_screen",)


def _inp(opts):
    return brain.BrainInput(tick=1, now=NOW, options=tuple(opts))


def _out(inp, **scores):
    return brain.BrainOutput(tick=1, model_id="t", made_at=NOW, ttl_s=5.0, scores={k.replace("__", ":"): v for k, v in scores.items()})


STAY, CUT_B, CUT_C = legal.STAY, brain.Option("cut:b", "cut", "b"), brain.Option("cut:c", "cut", "c")
ALL = (STAY, CUT_B, CUT_C)


@pytest.mark.parametrize("show,kw,expect", [
    (arbiter.Show(cuts_60s=3), {}, "ceiling"),
    (arbiter.Show(live_games=1), {}, "last_game"),
    (arbiter.Show(late=True, close=frozenset({"c"})), {}, "late_narrow"),
    (arbiter.Show(visits={"b": 9}), {}, "visit_budget"),
])
def test_refused_by_names_the_arbiter_rule(show, kw, expect):
    inp = _inp(ALL)
    out = _out(inp, stay=0.0, cut__b=50.0, cut__c=40.0)
    assert arbiter.refused_by(inp, out, "cut:b", show=show, **kw) == expect


def test_refused_by_margin_and_clean():
    inp = _inp(ALL)
    assert arbiter.refused_by(inp, _out(inp, stay=0.0, cut__b=1.0, cut__c=0.5), "cut:b") == "margin"
    assert arbiter.refused_by(inp, _out(inp, stay=0.0, cut__b=50.0, cut__c=40.0), "cut:b") == ""


def test_refused_by_never_disagrees_with_choose():
    shows = [arbiter.Show(), arbiter.Show(cuts_60s=3), arbiter.Show(live_games=1), arbiter.Show(late=True, close=frozenset({"c"})),
             arbiter.Show(visits={"b": 9})]
    margins = [(0.0, 50.0, 40.0), (0.0, 1.0, 0.5), (60.0, 50.0, 40.0)]
    for show, (s, b, c) in itertools.product(shows, margins):
        inp = _inp(ALL)
        out = _out(inp, stay=s, cut__b=b, cut__c=c)
        chosen = arbiter.choose(inp, out, show=show)
        why = arbiter.refused_by(inp, out, "cut:b", show=show)
        assert (why == "") == (chosen.option is not None and chosen.option.id == "cut:b")


def test_refused_by_names_a_higher_class():
    mine = dataclasses.replace(CUT_C, l1_class=0)
    inp = _inp((STAY, CUT_B, mine))
    out = _out(inp, stay=0.0, cut__b=50.0, cut__c=40.0)
    assert arbiter.refused_by(inp, out, "cut:b") == "l1_class"


def test_every_gate_and_legal_rule_has_a_class():
    for name, _ in gate.GATE_RULES:
        assert f"gate:{name}" in va.CLASSES, name
    for rule in ("arriving", "dwell", "too_soon", "leave_gate", "moment_lock", "not_tunable", "dark", "no_landing"):
        assert f"legal:{rule}" in va.CLASSES, rule


@pytest.mark.parametrize("rule", ["gate:landing", "gate:landing_unproven", "gate:landing_whistle", "gate:leave", "gate:hold",
                                  "gate:ending_airing", "gate:replay_exit", "legal:leave_gate", "legal:moment_lock",
                                  "legal:dark", "legal:no_landing"])
def test_a_rule1_rule_is_filed_as_rule1(rule):
    assert va.CLASSES[rule] == va.RULE1


def test_value_orders_score_then_setup_then_the_rest():
    assert va.value_of("td") > va.value_of("rz_trip") > va.value_of("gain10") > 0


def test_seen_needs_the_game_up_before_the_play_started():
    air = va.Air(100.0, "b", "td", 5.0)
    assert va.seen(air, [(10.0, "a"), (90.0, "b")])
    assert not va.seen(air, [(10.0, "a"), (95.0, "b")])      # came up mid-play
    assert not va.seen(air, [(10.0, "b"), (90.0, "a")])      # left before it
    assert not va.seen(air, [])


def _truth(moments):
    t = va.Truth()
    t.tune_s, t.moments = 5.0, moments
    return t


def test_an_unseen_moment_charges_its_last_refusal_in_the_run_up():
    air = va.Air(1000.0, "b", "td", 5.0)
    tally = va.Tally(log={"b": [(900.0, "legal:too_soon", True), (950.0, "gate:leave", False), (995.0, "legal:dwell", True)]})
    row = va.new_row()
    va.charge_night(tally, _truth({"b": [air]}), row)
    assert row["lost"] == Counter({"gate:leave": 5.0})           # 995 is too late to have reached the play; 900 is out of the run-up
    assert row["lost_alone"] == Counter() and row["must_total"] == 5.0


def test_a_moment_nobody_asked_for_is_not_charged_to_a_rule():
    air = va.Air(1000.0, "b", "td", 5.0)
    row = va.new_row()
    va.charge_night(va.Tally(), _truth({"b": [air]}), row)
    assert not row["lost"] and row["not_asked"] == 5.0


def test_a_seen_moment_is_never_charged():
    air = va.Air(1000.0, "b", "td", 5.0)
    tally = va.Tally(screen=[(900.0, "b")], log={"b": [(950.0, "gate:leave", True)]})
    row = va.new_row()
    va.charge_night(tally, _truth({"b": [air]}), row)
    assert row["must_seen"] == 5.0 and not row["lost"]


def test_the_audit_refuses_every_split_but_fit():
    argv, sys.argv = sys.argv, ["veto_audit", "--split", "test"]
    try:
        with pytest.raises(SystemExit):
            va.main()
    finally:
        sys.argv = argv


def _last(pool, **kw):
    inp = _inp(pool)
    out = _out(inp, stay=0.0, **{f"cut__{o.game_id}": 50.0 for o in pool if o.verb != "stay"})
    return arbiter.choose(inp, out, show=arbiter.Show(current="done", live_games=1), p=arbiter.Params(**kw))


def test_flag_off_the_last_game_rule_is_todays():
    assert _last((STAY, CUT_B)).why == "last_game"


def test_flag_on_a_finished_game_may_cut_to_the_one_still_live():
    ch = _last((STAY, CUT_B), last_game_to_live=True)
    assert ch.option.id == "cut:b" and ch.why == "brain"


@pytest.mark.parametrize("gid", sorted(arbiter.NOT_GAMES))
def test_flag_on_a_channel_that_is_not_a_game_still_holds(gid):
    assert _last((STAY, brain.Option(f"cut:{gid}", "cut", gid)), last_game_to_live=True).why == "last_game"


def test_the_screens_own_class_is_named_as_l1():
    inp = _inp(ALL)
    out = _out(inp, stay=0.0, cut__b=50.0, cut__c=40.0)
    assert arbiter.refused_by(inp, out, "cut:b", show=arbiter.Show(current_class=0)) == "l1_class"
