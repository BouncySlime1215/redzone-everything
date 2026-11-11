"""[P1] arbiter.class_bonus: the L1 class as points, not a hard filter, on the brain's pick only.

Why: the hard filter kept a much stronger moment off the screen just because a better class was on offer (378 must-see value
lost in the veto audit). With points a big enough gap can win, but the north star must hold on ties and near-ties: your team,
then crunch, then league order. The must-show floor keeps its filter.
"""
from __future__ import annotations

from dataclasses import replace

from redzone.contracts import brain
from redzone.noggin import arbiter as A

NOW = 1000.0
ON = replace(A.DEFAULT_PARAMS, class_bonus=True, late_narrow=False, visit_budget=0)
OFF = replace(ON, class_bonus=False)


def cut(g, cls):
    return brain.Option(f"cut:{g}", "cut", g, l1_class=cls)


def pick(opts, scores, show=A.Show(), p=ON, musts=()):
    inp = brain.BrainInput(tick=1, now=NOW, options=(A.legal.STAY, *opts))
    out = brain.BrainOutput(tick=1, model_id="t", made_at=NOW, ttl_s=5.0, scores=scores)
    return A.choose(inp, out, musts=musts, show=show, p=p)


def test_flag_off_the_class_is_still_a_hard_filter():
    ch = pick([cut("open", 2)], {"stay": 0.0, "cut:open": 100.0}, A.Show(current="mine", current_class=0), OFF)
    assert ch.option.id == "stay"


def test_a_much_stronger_moment_beats_crunch_on_screen():
    ch = pick([cut("open", 2)], {"stay": 10.0, "cut:open": 60.0}, A.Show(current="crunch", current_class=1))
    assert ch.option.id == "cut:open" and ch.why == "brain"


def test_a_much_stronger_moment_can_beat_your_team_on_screen():
    ch = pick([cut("open", 2)], {"stay": 0.0, "cut:open": 60.0}, A.Show(current="mine", current_class=0))
    assert ch.option.id == "cut:open"


def test_your_team_wins_a_close_call_on_screen():
    ch = pick([cut("open", 2)], {"stay": 20.0, "cut:open": 55.0}, A.Show(current="mine", current_class=0))
    assert ch.option.id == "stay"


def test_your_team_wins_a_close_call_on_offer():
    ch = pick([cut("mine", 0), cut("open", 2)], {"stay": 0.0, "cut:mine": 30.0, "cut:open": 65.0})
    assert ch.option.id == "cut:mine"


def test_an_exact_tie_goes_to_the_better_class():
    ch = pick([cut("crunch", 1), cut("open", 2)], {"stay": 0.0, "cut:crunch": 40.0, "cut:open": 60.0})
    assert ch.option.id == "cut:crunch"


def test_a_near_tie_goes_to_your_team_even_a_point_behind():
    ch = pick([cut("mine", 0), cut("open", 2)], {"stay": 0.0, "cut:mine": 48.0, "cut:open": 89.0})
    assert ch.option.id == "cut:mine"


def test_past_the_tie_band_the_stronger_moment_wins():
    ch = pick([cut("mine", 0), cut("open", 2)], {"stay": 0.0, "cut:mine": 40.0, "cut:open": 83.0})
    assert ch.option.id == "cut:open"


def test_inside_one_class_a_near_tie_follows_league_order():
    show = A.Show(league_rank={"nfl": 0, "mlb": 7})
    ch = pick([cut("nfl", 2), cut("mlb", 2)], {"stay": 0.0, "cut:nfl": 49.0, "cut:mlb": 50.0}, show)
    assert ch.option.id == "cut:nfl"


def test_league_order_does_not_override_a_clear_score_gap():
    show = A.Show(league_rank={"nfl": 0, "mlb": 7})
    ch = pick([cut("nfl", 2), cut("mlb", 2)], {"stay": 0.0, "cut:nfl": 30.0, "cut:mlb": 50.0}, show)
    assert ch.option.id == "cut:mlb"


def test_the_margin_reads_the_bonus_scores():
    ch = pick([cut("open", 2)], {"stay": 10.0, "cut:open": 32.0}, A.Show(current="crunch", current_class=1))
    assert ch.option.id == "stay"          # 32 vs 10 + 20: inside the 3-point margin (and the tie band)


def test_the_floor_is_untouched_by_the_flag():
    opts = [cut("mine", 0), cut("open", 2)]
    must = A.must_of("nfl", "td", "mine", NOW + 60.0)
    other = A.must_of("nfl", "td", "open", NOW + 60.0)
    scores = {"stay": 0.0, "cut:mine": 1.0, "cut:open": 100.0}
    for p in (ON, OFF):
        ch = pick(opts, scores, p=p, musts=(must, other))
        assert ch.option.id == "cut:mine" and ch.why == "floor:td"


def test_from_config_reads_the_flag_and_defaults_off():
    assert A.DEFAULT_PARAMS.class_bonus is False
    p = A.Params.from_config(lambda k, d=None: {"arbiter.class_bonus": True, "arbiter.class_bonus_pts": 30}.get(k, d))
    assert p.class_bonus is True and p.class_bonus_pts == 30.0
