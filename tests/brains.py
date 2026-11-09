"""Test brains for the brain contract: Null, Random, Adversarial. Tests only; nothing runs a model.

Why: WP-08. The arbiter and the fault drill need brains that behave (Null, Random) and brains that lie
(Adversarial) so `valid()` and the baseline fallback are proven before a real model exists.
All are pure: Random takes a seed, none reads a clock.
"""
from __future__ import annotations

import dataclasses as dc
import math
import random

from redzone.contracts import brain


def _base(inp: brain.BrainInput, **kw) -> brain.BrainOutput:
    return brain.BrainOutput(tick=inp.tick, model_id=kw.pop("model_id", "test"), made_at=inp.now,
                             ttl_s=kw.pop("ttl_s", 5.0), **kw)


class NullBrain:
    """No opinion: no scores, no values, no preload. Code must behave exactly as with no brain."""
    def think(self, inp: brain.BrainInput) -> brain.BrainOutput:
        return _base(inp, model_id="null")


class RandomBrain:
    """Valid but meaningless: random scores for real options, random preload of real games."""
    def __init__(self, seed: int = 0):
        self._rng = random.Random(seed)

    def think(self, inp: brain.BrainInput) -> brain.BrainOutput:
        r = self._rng
        scores = {o.id: r.uniform(-100, 100) for o in inp.options}
        ids = [g.game_id for g in inp.games]
        r.shuffle(ids)
        values = tuple(brain.GameValue(g, r.uniform(0, 100), r.uniform(0, 100), r.random(), "random")
                       for g in ids)
        return _base(inp, model_id="random", scores=scores, values=values, preload=tuple(ids),
                     stay_one_more=r.random())


ATTACKS = ("nan", "wrong_tick", "unknown_option", "unknown_game", "stale", "future", "bad_schema",
           "out_of_range", "dup_preload", "bad_stay")


class AdversarialBrain:
    """Starts from a valid output and breaks it one way. `attack` is one of ATTACKS."""
    def __init__(self, attack: str):
        if attack not in ATTACKS:
            raise ValueError(attack)
        self.attack = attack

    def think(self, inp: brain.BrainInput) -> brain.BrainOutput:
        good = RandomBrain(1).think(inp)
        a = self.attack
        if a == "nan":
            first = inp.options[0].id if inp.options else "x"
            return dc.replace(good, scores={**good.scores, first: math.nan})
        if a == "wrong_tick":
            return dc.replace(good, tick=inp.tick + 1)
        if a == "unknown_option":
            return dc.replace(good, scores={**good.scores, "no-such-option": 50.0})
        if a == "unknown_game":
            return dc.replace(good, preload=good.preload + ("no-such-game",))
        if a == "stale":
            return dc.replace(good, made_at=inp.now - good.ttl_s - 1)
        if a == "future":
            return dc.replace(good, made_at=inp.now + 60)
        if a == "bad_schema":
            return dc.replace(good, schema=brain.SCHEMA + 1)
        if a == "out_of_range":
            return dc.replace(good, scores={**good.scores, "x": 1e9} if not inp.options
                              else {**good.scores, inp.options[0].id: 1e9})
        if a == "dup_preload":
            return dc.replace(good, preload=good.preload + good.preload[:1])
        return dc.replace(good, stay_one_more=2.0)
