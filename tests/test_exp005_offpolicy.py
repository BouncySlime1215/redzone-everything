"""[EXP-005] The doubly robust estimator (redzone/lab/offpolicy.py) on synthetic logged decisions with a
known truth: right with a right reward model, still right with a WRONG one when the propensities are
right (the IPS correction), and the plain direct method when nothing was explored."""
import math
import random

import pytest

from redzone.lab import offpolicy as O

MEANS = [0.2, 0.5, 0.8]           # true reward of options 0, 1, 2 (same every decision)


def _logs(n=4000, explore=0.3, nights=10, seed=1):
    rng = random.Random(seed)
    base = [1 - explore, explore / 2, explore / 2]          # live: mostly option 0
    rows = []
    for i in range(n):
        a = rng.choices(range(3), base)[0]
        r = MEANS[a] + rng.gauss(0, 0.1)
        opts = tuple({"a": "switch", "g": f"g{k}", "p": base[k]} for k in range(3))
        rows.append(O.Logged(f"n{i % nights}", opts, a, r))
    return rows


def new_pi(row):            # the new policy: always option 2
    return [0.0, 0.0, 1.0]


def good_q(row, i):
    return MEANS[i]


def bad_q(row, i):           # a reward model that's badly wrong
    return 0.0


def test_dr_is_right_with_a_right_model():
    e = O.estimate(_logs(), new_pi, good_q)
    assert abs(e.dr - 0.8) < 0.01 and e.lo <= 0.8 <= e.hi


def test_dr_recovers_the_truth_from_a_wrong_model_through_the_propensities():
    e = O.estimate(_logs(), new_pi, bad_q)
    assert e.dm == 0.0 and abs(e.dr - 0.8) < 0.06 and e.lo <= 0.8 <= e.hi


def test_without_exploration_dr_is_the_direct_method():
    rows = [O.Logged("n0", tuple({"a": "stay", "g": f"g{k}", "p": 1.0 if k == 0 else 0.0} for k in range(3)), 0, 0.2)
            for _ in range(50)]
    e = O.estimate(rows, new_pi, good_q)
    assert e.dr == pytest.approx(e.dm) == pytest.approx(0.8) and e.ips == 0.0


def test_same_policy_as_live_reads_the_logged_mean():
    rows = _logs()
    live = [r.reward for r in rows]
    e = O.estimate(rows, lambda r: [o["p"] for o in r.options], bad_q)
    assert abs(e.dr - sum(live) / len(live)) < 1e-9


def test_weights_are_clipped_and_counted():
    rows = _logs(n=200, explore=0.01)
    e = O.estimate(rows, new_pi, good_q, w_max=5.0)
    assert e.clipped == sum(r.taken == 2 for r in rows)


def test_reads_a_decision_record():
    rec = {"action": "switch", "target": "g2", "options": [{"a": "stay", "g": "g0", "p": 0.95},
                                                          {"a": "switch", "g": "g2", "p": 0.05}]}
    row = O.from_decision_log(rec, 1.0, "20261104")
    assert row.taken == 1 and row.p == 0.05
    assert O.from_decision_log({"action": "stay", "target": "g0"}, 0.0, "x") is None


def test_bad_inputs_raise():
    row = O.Logged("n", ({"p": 0.0}, {"p": 1.0}), 0, 1.0)
    with pytest.raises(ValueError):
        O.row_terms(row, lambda r: [1, 0], good_q)
    with pytest.raises(ValueError):
        O.row_terms(O.Logged("n", ({"p": 1.0},), 0, 1.0), lambda r: [0.5, 0.5], good_q)
    assert math.isnan(O.night_mean_ci({})[0])


def test_cli_reads_a_log(tmp_path, capsys):
    import json as J
    from scripts import offpolicy_score as S
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(J.dumps({"night": f"n{i % 2}", "action": "switch", "target": "g1", "value": v,
                                     "options": [{"a": "stay", "g": "g0", "p": 0.5}, {"a": "switch", "g": "g1",
                                                                                       "p": 0.5}]})
                           for i, v in enumerate([1.0, 0.0, 1.0, 1.0])) + "\n")
    assert S.main([str(p)]) == 0
    out = J.loads(capsys.readouterr().out)
    assert out["n"] == 4 and abs(out["dr"] - 0.75) < 1e-9
