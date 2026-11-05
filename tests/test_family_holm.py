"""Tests for the family-wise correction proposal (EXP-722)."""
import importlib.util
import random
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "family_holm", Path(__file__).resolve().parent.parent / "scripts" / "family_holm.py")
fh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fh)


def draws(mu, sd=1.0, n=999, seed=0):
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


def test_holm_matches_hand_computation():
    adj = fh.holm({"a": 0.01, "b": 0.02, "c": 0.04})
    assert adj == {"a": 0.03, "b": 0.04, "c": 0.04}


def test_holm_is_monotone_and_capped():
    adj = fh.holm({"a": 0.5, "b": 0.6})
    assert adj["a"] == 1.0 and adj["b"] == 1.0


def test_lucky_arm_passes_alone_but_not_in_a_family():
    lucky = {"id": "x", "deltas": draws(1.8, seed=1)}            # one-sided p ~ .035
    alone = fh.grade({"arms": [lucky]})
    assert alone[0]["status"] == "PASS"
    fam = fh.grade({"arms": [lucky] + [{"id": f"n{i}", "deltas": draws(0.0, seed=10 + i)} for i in range(5)]})
    assert [r for r in fam if r["id"] == "x"][0]["status"] == "FAIL"


def test_strong_arm_survives_the_family():
    fam = fh.grade({"arms": [{"id": "s", "deltas": draws(4.0, seed=2)}]
                    + [{"id": f"n{i}", "deltas": draws(0.0, seed=20 + i)} for i in range(5)]})
    assert [r for r in fam if r["id"] == "s"][0]["status"] == "PASS"


def test_underpowered_arm_is_pending_and_not_counted():
    fam = fh.grade({"arms": [{"id": "small", "deltas": [1.0] * 50}, {"id": "s", "deltas": draws(4.0, seed=3)}]})
    st = {r["id"]: r for r in fam}
    assert st["small"]["status"] == "PENDING" and st["s"]["family_size"] == 1
