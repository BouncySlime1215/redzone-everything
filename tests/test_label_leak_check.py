"""Grader freeze v17: the context-only label leak check and the CBB lock exclusion."""
import random

from agent.distill import evaluate
from scripts import label_leak_check as L


def _rows(n=1200, seed=1, rule=True):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        f = {"period": rng.randint(1, 4), "clock_s": rng.uniform(0, 720), "abs_diff": rng.randint(0, 25),
             "mine": int(rng.random() < 0.2), "late": 0, "crunch": 0, "garbage": 0}
        f["late"] = int(f["period"] == 4 and f["clock_s"] < 300)
        f["crunch"] = int(f["late"] and f["abs_diff"] <= 5)
        # rule label: what the clock/crunch rule says (leaks); outcome label: a coin with a small tilt
        y = (f["crunch"] or (f["mine"] and f["late"])) if rule else int(rng.random() < 0.3 + 0.1 * f["crunch"])
        out.append({"night": f"n{i % 30}", "league": "cbb", "facts": f,
                    "target": {"lock_in": "must_see" if y else "none"}})
    return out


def test_rule_label_fails():
    rep = {"name": "t", "league": "nba", "kind": "lock", **L.context_auc(_rows(), L.label_fn("target.lock_in=must_see"))}
    assert rep["max_auc"] >= L.AUC_BAR
    assert L.judge(rep, excluded={})[0] == "FAIL"


def test_outcome_label_passes():
    rep = {"name": "t", "league": "nba", **L.context_auc(_rows(rule=False), L.label_fn("target.lock_in=must_see"))}
    assert rep["max_auc"] < L.AUC_BAR
    assert L.judge(rep, excluded={})[0] == "PASS"


def test_excluded_lock_reads_excluded_not_pass():
    rep = {"name": "t", "league": "cbb", "kind": "lock", **L.context_auc(_rows(), L.label_fn("target.lock_in=must_see"))}
    assert L.judge(rep, excluded={"cbb": "leak"})[0] == "EXCLUDED"
    rep["kind"] = ""                       # a non-lock label in an excluded league still fails
    assert L.judge(rep, excluded={"cbb": "leak"})[0] == "FAIL"


def test_too_few_rows_pending():  # a weak score on thin data is not a PASS
    rep = {"name": "t", "league": "nba", **L.context_auc(_rows(n=50, rule=False), L.label_fn("target.lock_in=must_see"))}
    assert L.judge(rep, excluded={})[0] == "PENDING"
    leak = {"name": "t", "league": "nba", **L.context_auc(_rows(n=50), L.label_fn("target.lock_in=must_see"))}
    assert L.judge(leak, excluded={})[0] == "FAIL"     # a leak on thin data is still a leak


def test_cross_fit_by_night():
    # every row its own night label with no signal: the joint table must not memorise rows
    rows = _rows(rule=False)
    for i, r in enumerate(rows):
        r["night"] = f"n{i % 3}"
    rep = L.context_auc(rows, L.label_fn("target.lock_in=must_see"))
    assert rep["joint_auc"] < 0.8


def test_committed_reports_pass_ci():
    assert all(st != "FAIL" for st, _ in L.judge_reports())
    assert L.main([]) == 0


def test_cbb_lock_acc_excluded():
    assert "cbb" in evaluate.LOCK_EXCLUDED
    tgt = {"lock_in": "must_see", "safe_to_leave": "no"}
    rows = [{"id": f"c{i}", "league": "cbb", "y": 1, "target": tgt} for i in range(5)]
    rows += [{"id": f"n{i}", "league": "nba", "y": 1, "target": tgt} for i in range(5)]
    preds = {r["id"]: {"lock_in": "none" if r["league"] == "nba" else "must_see", "safe_to_leave": "no"} for r in rows}
    s = evaluate.score(rows, preds)
    assert s["lock_acc"] == 0.0 and s["lock_excluded_rows"] == 5     # CBB's perfect calls don't count
    assert evaluate.score(rows[:5], preds)["lock_acc"] is None        # a CBB-only card has no lock number
