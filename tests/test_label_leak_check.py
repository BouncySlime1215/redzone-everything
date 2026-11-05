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


def test_lock_acc_outcome_truth_and_exclusions(monkeypatch):
    assert {"cbb", "cfb", "mlb"} <= set(evaluate.LOCK_EXCLUDED)
    monkeypatch.setattr(evaluate, "LOCK_EXCLUDED", {"cbb": "leak"})
    tgt = {"lock_in": "must_see", "safe_to_leave": "no"}
    rows = [{"id": f"c{i}", "league": "cbb", "y": 1, "y_out": 1, "target": tgt} for i in range(5)]
    rows += [{"id": f"n{i}", "league": "nba", "y": 1, "y_out": 0, "y_out2": 1, "target": tgt} for i in range(5)]
    rows += [{"id": f"o{i}", "league": "nba", "y": 1, "target": tgt} for i in range(3)]   # pre-#278 row: no y_out
    assert evaluate.truth_lock(rows[5]) == "watch"                    # outcome truth, not the crunch-rule target
    preds = {r["id"]: {"lock_in": "watch" if r["league"] == "nba" else "must_see", "safe_to_leave": "no"} for r in rows}
    s = evaluate.score(rows, preds)
    assert s["lock_acc"] == 100.0 and s["lock_excluded_rows"] == 8    # 5 CBB (excluded) + 3 without y_out
    assert evaluate.score(rows[:5], preds)["lock_acc"] is None        # a CBB-only card has no lock number


def test_every_graded_league_has_passing_label_reports():
    """A league leaves LOCK_EXCLUDED only with PASSing lock and watch reports (never by hand)."""
    import json
    for lg in ("nfl", "cfb", "nba", "cbb", "nhl", "mlb"):
        if lg in evaluate.LOCK_EXCLUDED:
            continue
        for name in ("reasoner_lock", "reasoner_watch"):
            p = L.REPORTS / f"{name}_{lg}.json"
            assert p.exists(), p
            assert L.judge(json.loads(p.read_text()), excluded={})[0] == "PASS", p
