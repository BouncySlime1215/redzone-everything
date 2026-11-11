"""[WP-15] Mutate every check in the brain contract's `valid()`; a named check must notice each change.

The classes stay the real ones (`load`'s restore), so the same BrainOutput goes through each mutated `problem()`.

Why: `valid()` is the one place that decides a brain output is usable. A check nobody tests is a door a bad model walks
through (build plan WP-15 and WP-08).
"""
from __future__ import annotations

import dataclasses as dc
import math
from pathlib import Path

import pytest

from redzone.contracts import brain
from tests.adversarial import mutate as M

SOURCE = Path(brain.__file__)
NOW = 1000.0
REAL_CLASSES = {n: getattr(brain, n) for n in ("Option", "GameFacts", "Moment", "ScreenFacts", "StickFacts", "Preferences",
                                               "BrainInput", "GameValue", "BrainOutput", "Brain")}


def _inp(**kw):
    return brain.BrainInput(tick=7, now=NOW, games=(brain.GameFacts("g1"), brain.GameFacts("g2")),
                            options=(brain.Option("o1", "stay", "g1"), brain.Option("o2", "cut", "g2")), **kw)


def _ok(**kw):
    base = dict(tick=7, model_id="t", made_at=NOW - 1, ttl_s=5.0, scores={"o1": 10.0, "o2": 20.0},
                values=(brain.GameValue("g1", 50, 60, 0.3, "x"),), preload=("g2", "g1"), stay_one_more=0.4)
    return brain.BrainOutput(**{**base, **kw})


def _why(m, out, inp=None):
    return m.problem(out, inp or _inp())


def _val(**kw):
    base = dict(game_id="g1", value_now=50, value_next_3m=60, p_tier1_60s=0.3)
    return (brain.GameValue(**{**base, **kw}),)


def k_accepts_good(m):
    assert _why(m, _ok()) is None and m.valid(_ok(), _inp()) is True
    assert _why(m, _ok(scores={}, values=(), preload=(), stay_one_more=None)) is None


def k_valid_mirrors_problem(m):
    assert m.valid(_ok(tick=8), _inp()) is False


def k_schema(m):
    assert _why(m, "not an output") == "schema"
    assert _why(m, _ok(schema=99)) == "schema"
    assert _why(m, _ok(), dc.replace(_inp(), schema=99)) == "schema"


def k_tick(m):
    assert _why(m, _ok(tick=6)) == "tick" and _why(m, _ok(tick=8)) == "tick"


def k_clock(m):
    assert _why(m, _ok(made_at=math.nan)) == "nan" and _why(m, _ok(made_at=math.inf)) == "nan"
    assert _why(m, _ok(ttl_s=math.nan)) == "nan" and _why(m, _ok(ttl_s=0.0)) == "nan" and _why(m, _ok(ttl_s=-1.0)) == "nan"
    assert _why(m, _ok(ttl_s=math.inf)) == "nan"
    assert _why(m, _ok(ttl_s=0.25, made_at=NOW - 0.25)) is None   # a TTL under a second is still a TTL
    assert _why(m, _ok(made_at=NOW)) is None                       # made this instant
    assert _why(m, _ok(made_at=NOW + 0.01)) == "stale"             # from the future
    assert _why(m, _ok(made_at=NOW - 5.0, ttl_s=5.0)) is None      # exactly at its TTL
    assert _why(m, _ok(made_at=NOW - 5.01, ttl_s=5.0)) == "stale"


def k_scores(m):
    for good in (100, 100.0, -100, -100.0, 99.5, -99.5, 0):
        assert _why(m, _ok(scores={"o1": good})) is None, good
    for bad in (100.5, -100.5, 1e9, math.nan, math.inf, -math.inf, True, "5", None):
        assert _why(m, _ok(scores={"o1": bad})) == "nan", bad
    assert _why(m, _ok(scores={"ghost": 1.0})) == "unknown_id"
    assert _why(m, _ok(scores={"ghost": math.nan})) == "nan"       # the number is checked first


def k_values(m):
    for field, hi in (("value_now", 100), ("value_next_3m", 100), ("p_tier1_60s", 1)):
        assert _why(m, _ok(values=_val(**{field: 0}))) is None, field
        assert _why(m, _ok(values=_val(**{field: hi}))) is None, field
        assert _why(m, _ok(values=_val(**{field: -0.5}))) == "nan", field
        assert _why(m, _ok(values=_val(**{field: hi + 0.5}))) == "nan", field
        assert _why(m, _ok(values=_val(**{field: math.nan}))) == "nan", field
    assert _why(m, _ok(values=_val(p_tier1_60s=0.5))) is None
    assert _why(m, _ok(values=_val(game_id="ghost"))) == "unknown_id"
    assert _why(m, _ok(values=_val(game_id="ghost", value_now=math.nan))) == "nan"


def k_preload(m):
    assert _why(m, _ok(preload=("g1",))) is None
    assert _why(m, _ok(preload=("ghost",))) == "unknown_id"
    assert _why(m, _ok(preload=("g1", "ghost"))) == "unknown_id"
    assert _why(m, _ok(preload=("g1", "g1"))) == "unknown_id"


def k_stay(m):
    for good in (None, 0, 0.0, 1, 1.0, 0.5):
        assert _why(m, _ok(stay_one_more=good)) is None, good
    for bad in (1.5, -0.5, math.nan, math.inf, True):
        assert _why(m, _ok(stay_one_more=bad)) == "nan", bad


def k_num(m):
    assert m._num(5, 0, 10) is True and m._num(5.0, 0, 10) is True
    assert m._num(0, 0, 10) is True and m._num(10, 0, 10) is True      # the ends are in range
    assert m._num(-0.1, 0, 10) is False and m._num(10.1, 0, 10) is False
    for bad in (True, False, None, "1", math.nan, math.inf, -math.inf, [1]):
        assert m._num(bad, -math.inf, math.inf) is False, bad


CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("k_") and callable(v)]
EQUIVALENT = {}
MUTANTS = M.mutants(SOURCE, {"_num", "problem", "valid", "SCORE_MIN", "SCORE_MAX"}, rules={"_num", "problem", "valid"})


def _load(mutant):
    return M.load(mutant, REAL_CLASSES)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_the_checks_pass_on_the_real_contract(check):
    check(_load(M.Mutant("real", SOURCE.read_text())))


@pytest.mark.parametrize("mutant", [x for x in MUTANTS if x.id not in EQUIVALENT], ids=lambda x: x.id)
def test_every_contract_mutant_is_killed(mutant):
    assert M.killed_by(_load(mutant), CHECKS) is not None, f"survived: {mutant.id}"


@pytest.mark.parametrize("mid", sorted(EQUIVALENT))
def test_listed_equivalent_mutants_still_survive(mid):
    mutant = next(x for x in MUTANTS if x.id == mid)
    assert M.killed_by(_load(mutant), CHECKS) is None, f"{mid} is killable now: remove it from EQUIVALENT"


def test_mutation_score_is_reported_and_full():
    killed = sum(M.killed_by(_load(x), CHECKS) is not None for x in MUTANTS)
    total = len(MUTANTS)
    print(f"\nCONTRACT MUTATION SCORE: {killed}/{total} = {100 * killed / total:.1f}% ({len(EQUIVALENT)} listed equivalent)")
    assert killed == total - len(EQUIVALENT)
