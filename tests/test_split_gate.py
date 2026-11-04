"""[V7, B11] the split reasoner gate: rule conformance, calibration, non-inferiority vs OFF, abstain."""
from agent.distill import evaluate as E


def _row(i, lock, **kw):
    return {"id": f"r{i}", "league": kw.pop("league", "nba"), "brief": kw.pop("brief", "Q4 2:00"),
            "target": {"lock_in": lock, "safe_to_leave": "no"}, **kw}


def test_rule_conformance_counts_only_rows_the_brief_decides():
    rows = [_row(0, "none", garbage=True), _row(1, "must_see", mine=True, late=True),
            _row(2, "watch", league="nhl", brief="power play WSH 1:40"), _row(3, "watch")]
    preds = {"r0": {"lock_in": "none"}, "r1": {"lock_in": "watch"}, "r2": {"lock_in": "must_see"},
             "r3": {"lock_in": "none"}}
    assert [E.rule_expect(r) for r in rows] == ["none", "must_see", "watch+", None]
    assert E.conformance(rows, preds) == (66.7, 3)       # r1 broke "your team late = must_see"


def test_calibration_is_zero_when_confidence_matches_accuracy():
    rows = [_row(i, "watch") for i in range(10)]
    right = {f"r{i}": {"lock_in": "watch" if i < 8 else "none", "confidence": 0.8} for i in range(10)}
    assert E.calibration(rows, right) == 0.0
    over = {f"r{i}": {"lock_in": "watch" if i < 5 else "none", "confidence": 0.95} for i in range(10)}
    assert abs(E.calibration(rows, over) - 0.45) < 1e-9
    assert E.calibration(rows, {}) is None


def test_gate_split_lines():
    res = {"today": {"all": {"unsafe_leave_pct": 8.9, "must_see_recall": 70.0, "lock_acc": 70.5}},
           "student": {"all": {"usable_json_pct": 100.0, "rule_conformance_pct": 99.5, "confidence_ece": 0.06,
                               "unsafe_leave_pct": 6.5, "must_see_recall": 75.7, "lock_acc": 69.0, "n": 1000}}}
    dk = {"cut": 0.6, "test_coverage_pct": 55.0, "test_answered": {"unsafe_leave_pct": 0.4}}
    g = E.gate_split(res, "student", dk)
    assert all(v[2] for v in g.values()), g                # lock_acc 69.0 is within 2 points of OFF's 70.5
    res["student"]["all"]["lock_acc"] = 68.0
    dk["test_answered"]["unsafe_leave_pct"] = 0.9
    g = E.gate_split(res, "student", dk)
    assert not g["vs_off_lock_acc"][2] and not g["abstain_coverage_pct"][2] and g["usable_json_pct"][2]
