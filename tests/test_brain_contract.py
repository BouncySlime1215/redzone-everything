"""WP-08: the brain contract. valid() must reject each bad case on its own; test brains behave.

Why: slow, missing or invalid brain output must fall back to the baseline in the same tick.
"""
from __future__ import annotations

import dataclasses as dc
import math

import pytest

from redzone.contracts import brain
from tests.brains import ATTACKS, AdversarialBrain, NullBrain, RandomBrain

NOW = 1000.0


def _inp(tick: int = 7) -> brain.BrainInput:
    return brain.BrainInput(
        tick=tick, now=NOW,
        games=(brain.GameFacts("g1"), brain.GameFacts("g2")),
        options=(brain.Option("o1", "stay", "g1"), brain.Option("o2", "cut", "g2")),
        lookahead_s={"g1": 60.0, "g2": 40.0})


def _good(inp: brain.BrainInput) -> brain.BrainOutput:
    return brain.BrainOutput(tick=inp.tick, model_id="t", made_at=inp.now - 1, ttl_s=5.0,
                             scores={"o1": 10.0, "o2": 20.0},
                             values=(brain.GameValue("g1", 50, 60, 0.3, "x"),), preload=("g2", "g1"),
                             stay_one_more=0.4)


def test_valid_accepts_a_good_output():
    inp = _inp()
    assert brain.valid(_good(inp), inp)
    assert brain.problem(_good(inp), inp) is None


def test_valid_rejects_nan_score():
    inp = _inp()
    out = dc.replace(_good(inp), scores={"o1": math.nan})
    assert not brain.valid(out, inp) and brain.problem(out, inp) == "nan"


def test_valid_rejects_nan_in_values_and_stay():
    inp = _inp()
    bad_v = dc.replace(_good(inp), values=(brain.GameValue("g1", math.inf, 1, 0.1),))
    assert not brain.valid(bad_v, inp)
    assert not brain.valid(dc.replace(_good(inp), stay_one_more=math.nan), inp)


def test_valid_rejects_wrong_tick():
    inp = _inp(7)
    out = dc.replace(_good(inp), tick=6)
    assert not brain.valid(out, inp) and brain.problem(out, inp) == "tick"


def test_valid_rejects_unknown_option_id():
    inp = _inp()
    out = dc.replace(_good(inp), scores={"o1": 1.0, "ghost": 2.0})
    assert not brain.valid(out, inp) and brain.problem(out, inp) == "unknown_id"


def test_valid_rejects_unknown_game_id_in_preload_and_values():
    inp = _inp()
    assert not brain.valid(dc.replace(_good(inp), preload=("ghost",)), inp)
    assert not brain.valid(dc.replace(_good(inp), values=(brain.GameValue("ghost", 1, 1, 0.1),)), inp)


def test_valid_rejects_stale_output():
    inp = _inp()
    out = dc.replace(_good(inp), made_at=NOW - 6, ttl_s=5.0)
    assert not brain.valid(out, inp) and brain.problem(out, inp) == "stale"


def test_valid_rejects_output_from_the_future():
    inp = _inp()
    assert brain.problem(dc.replace(_good(inp), made_at=NOW + 1), inp) == "stale"


def test_valid_rejects_wrong_schema():
    inp = _inp()
    assert brain.problem(dc.replace(_good(inp), schema=99), inp) == "schema"


def test_valid_rejects_out_of_range_values():
    inp = _inp()
    assert not brain.valid(dc.replace(_good(inp), scores={"o1": 1e9}), inp)
    assert not brain.valid(dc.replace(_good(inp), stay_one_more=1.5), inp)


def test_null_brain_changes_nothing():
    inp = _inp()
    out = NullBrain().think(inp)
    assert brain.valid(out, inp)
    assert not out.scores and not out.values and out.preload == () and out.stay_one_more is None


def test_random_brain_is_valid_and_seeded():
    inp = _inp()
    a, b = RandomBrain(3).think(inp), RandomBrain(3).think(inp)
    assert brain.valid(a, inp) and a == b
    assert a != RandomBrain(4).think(inp)


@pytest.mark.parametrize("attack", ATTACKS)
def test_every_adversarial_attack_is_rejected(attack):
    inp = _inp()
    assert not brain.valid(AdversarialBrain(attack).think(inp), inp)


def test_brains_satisfy_the_protocol():
    assert all(isinstance(b, brain.Brain) for b in (NullBrain(), RandomBrain(), AdversarialBrain("nan")))
