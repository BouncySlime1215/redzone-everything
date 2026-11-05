"""[EXP-302] The real-outcome lock label: built from what happened next, and not readable off the clock."""
import math
import random

from redzone.lab import outcome_label as O
from redzone.models import League


def play(per, clock, home, away):
    return {"period": {"number": per}, "clock": {"displayValue": clock}, "homeScore": home, "awayScore": away}


def test_a_lead_change_inside_the_window_is_a_flip_and_a_tie_from_level_is_not():
    plays = [play(1, "10:00", 0, 0), play(1, "9:40", 2, 0), play(1, "9:20", 2, 3), play(1, "6:00", 5, 3)]
    wall = [0.0, 20.0, 40.0, 400.0]
    lab = O.label_game(League.NBA, plays, wall)
    assert lab[1]["flip"] == 1           # home led 2-0, away went ahead 3-2 within 180 s
    assert lab[0]["flip"] == 0           # 0-0 -> 2-0 is not a lead change
    assert lab[2]["flip"] == 0           # the next lead change is 360 s later: outside the window


def test_a_late_swing_is_a_wp_swing_and_an_early_one_is_not():
    late = [play(4, "0:30", 100, 101), play(4, "0:05", 103, 101)]
    assert O.label_game(League.NBA, late, [0.0, 30.0])[0]["wp_swing"] == 1
    early = [play(1, "11:00", 0, 1), play(1, "10:30", 3, 1)]
    assert O.label_game(League.NBA, early, [0.0, 30.0])[0]["wp_swing"] == 0


def test_a_game_ending_close_is_reported_but_not_in_the_label():
    plays = [play(4, "0:20", 99, 98), play(4, "0:00", 100, 98)]
    lab = O.label_game(League.NBA, plays, [0.0, 25.0])
    assert lab[0]["close_finish"] == 1 and lab[0]["y_out"] == lab[0]["flip"] | lab[0]["wp_swing"]


def test_context_auc_catches_a_label_that_is_our_own_rule():
    rng = random.Random(1)
    rows = [{"period": rng.randint(1, 4), "clock_s": rng.uniform(0, 720), "abs_diff": rng.randint(0, 20)}
            for _ in range(4000)]
    for r in rows:
        r["rule"] = int(r["period"] == 4 and r["clock_s"] <= 120 and r["abs_diff"] <= 5)
        r["coin"] = int(rng.random() < 0.3)
    tr, te = rows[:3000], rows[3000:]
    assert O.context_auc(tr, te, "rule") >= O.CONTEXT_AUC_MAX
    assert abs(O.context_auc(tr, te, "coin") - 0.5) < 0.08


def test_lock_grade_is_balanced_and_bootstrapped_over_nights():
    rows = [{"id": f"{n}-{k}", "night": f"2026100{n}", "y_out": int(k < 2)} for n in range(1, 8) for k in range(10)]
    always_no = {r["id"]: "none" for r in rows}
    g = O.lock_grade(rows, always_no)
    assert g["balanced_acc"] == 0.5 and not g["powered"]     # 80% plain accuracy, but no skill
    perfect = {r["id"]: "must_see" if r["y_out"] else "watch" for r in rows}
    g = O.lock_grade(rows, perfect)
    assert g["balanced_acc"] == 1.0 and g["ci95"] == (1.0, 1.0) and g["n"] == 70


def test_win_prob_is_even_when_tied_and_settles_at_the_end():
    assert math.isclose(O.win_prob(League.NBA, 0, 600), 0.5)
    assert O.win_prob(League.NBA, 1, 0) == 1.0 and O.win_prob(League.MLB, 1, 100) is None
