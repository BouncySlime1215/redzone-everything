"""The release gate judges rate gates by NIGHTS (research 05 §2.2): cluster bootstrap, PASS needs
>= 8 nights, >= 60 decisions and the lower bound at the bar; a slice clearly under the bar FAILS."""
from scripts import release_gate as rg


def card(per):
    n = sum(x["entries"] for x in per)
    mid = sum(x["entries_mid_play"] for x in per)
    return {"metrics": {"eval_data": rg.HELDOUT["eval_data"], "mid_play_cuts": 0, "entries": n,
                        "entries_mid_play": mid, "per_night": per}}


def row(rows, gate):
    return next(r for r in rows if r[0] == gate)


def test_four_perfect_nights_are_pending_not_pass():
    per = [{"night": str(i), "entries": 25, "entries_mid_play": 0} for i in range(4)]
    r = row(rg.gates({"nhl": card(per)}), "entries at a dead ball >= 95%")
    assert r[2] == rg.PENDING and "4/8 nights" in r[3]


def test_ten_strong_nights_pass():
    per = [{"night": str(i), "entries": 30, "entries_mid_play": i % 2} for i in range(10)]   # 98.3 %
    r = row(rg.gates({"nfl": card(per)}), "entries at a dead ball >= 95%")
    assert r[2] == rg.PASS, r


def test_a_slice_clearly_under_the_bar_fails():
    per = [{"night": str(i), "entries": 5, "entries_mid_play": 2 + i % 2} for i in range(4)]  # NBA 10/20-like
    r = row(rg.gates({"nba": card(per)}), "entries at a dead ball >= 95%")
    assert r[2] == rg.FAIL


def test_borderline_point_estimate_is_pending():
    # 97.8 % pooled (NHL 133/136 today) has a lower bound under 95 %: not a PASS
    per = [{"night": str(i), "entries": 34, "entries_mid_play": m} for i, m in enumerate((0, 1, 2, 0))]
    r = row(rg.gates({"nhl": card(per)}), "entries at a dead ball >= 95%")
    assert r[2] == rg.PENDING


def test_zero_cuts_print_the_rule_of_three_bound():
    per = [{"night": str(i), "entries": 100, "entries_mid_play": 0} for i in range(3)]
    r = row(rg.gates({"nfl": card(per)}), "mid-play cuts = 0")
    assert r[2] == rg.PASS and "rule of three" in r[3] and "1.00%" in r[3]


def test_wilson_and_bootstrap_are_sane():
    lo, hi = rg.wilson(133, 136)
    assert 93 < lo < 95 < hi
    p, lo, hi = rg.night_ci([(30, 30)] * 8)
    assert p == lo == hi == 100.0


def test_a_visible_miss_fails_even_with_few_nights():
    from scripts.release_gate import FAIL, rate_gate
    st, d = rate_gate([(10, 10), (2, 3), (6, 6)], 95)   # CI reaches 100, point 94.7
    assert st == FAIL and "point estimate" in d
