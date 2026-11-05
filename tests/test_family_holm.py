"""Tests for the family-wise correction proposal (EXP-722)."""
import importlib.util
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("family_holm", ROOT / "scripts" / "family_holm.py")
fh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fh)


def draws(mu, sd=1.0, n=999, seed=0):
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


def fams(*arms, name="f"):
    return {name: {"name": name, "declared": "2026-11-05", "arms": list(arms)}}


def by_id(res):
    return {r["id"]: r for r in res}


def test_holm_matches_hand_computation():
    assert fh.holm({"a": 0.01, "b": 0.02, "c": 0.04}) == {"a": 0.03, "b": 0.04, "c": 0.04}


def test_holm_is_monotone_and_capped():
    adj = fh.holm({"a": 0.5, "b": 0.6})
    assert adj["a"] == 1.0 and adj["b"] == 1.0


def test_lucky_arm_passes_alone_but_not_in_a_family():
    lucky = {"id": "x", "deltas": draws(1.8, seed=1)}            # one-sided p ~ .035
    assert by_id(fh.grade({"family": "f", "arms": [lucky]}, fams("x")))["x"]["status"] == "PASS"
    others = [{"id": f"n{i}", "deltas": draws(0.0, seed=10 + i)} for i in range(5)]
    res = fh.grade({"family": "f", "arms": [lucky] + others}, fams("x", *[o["id"] for o in others]))
    assert by_id(res)["x"]["status"] == "FAIL"


def test_strong_arm_survives_the_family():
    others = [{"id": f"n{i}", "deltas": draws(0.0, seed=20 + i)} for i in range(5)]
    res = fh.grade({"family": "f", "arms": [{"id": "s", "deltas": draws(4.0, seed=2)}] + others},
                   fams("s", *[o["id"] for o in others]))
    assert by_id(res)["s"]["status"] == "PASS"


def test_dropping_an_arm_does_not_shrink_the_family():
    lucky = {"id": "x", "deltas": draws(1.8, seed=1)}
    res = by_id(fh.grade({"family": "f", "arms": [lucky]}, fams("x", "y", "z", "w", "v", "u")))
    assert res["x"]["status"] == "FAIL" and res["x"]["family_size"] == 6
    assert res["y"]["status"] == "PENDING"


def test_undeclared_arm_and_unknown_family_fail():
    res = by_id(fh.grade({"family": "f", "arms": [{"id": "late", "deltas": draws(4.0)}]}, fams("x")))
    assert res["late"]["status"] == "FAIL"
    assert fh.grade({"family": "nope", "arms": []}, fams("x"))[0]["status"] == "FAIL"


def test_declared_families_file_is_well_formed():
    f = fh.load_families(ROOT / "docs" / "families.json")
    assert f and all(v["declared"] and v["arms"] and len(set(v["arms"])) == len(v["arms"]) for v in f.values())
