"""Tests for the gate power proposal (EXP-726)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "gate_power", Path(__file__).resolve().parent.parent / "scripts" / "gate_power.py")
gp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gp)


def line(pairs, name="big_live_nba"):
    return {"line": name, "league": "nba", "nights": [{"night": str(i), "hits": h, "n": n} for i, (h, n) in enumerate(pairs)]}


def test_nba_like_set_is_underpowered():
    # ~71 graded plays over 9 nights at ~79%, uneven nights
    pairs = [(6, 8), (5, 9), (8, 9), (4, 7), (7, 8), (6, 7), (5, 8), (9, 10), (4, 5)]
    r = gp.line_power(line(pairs), target_hw=3.0)
    assert r["decisions"] == 71 and r["status"] == "UNDERPOWERED"
    assert r["nights_needed"] > 9 and r["more_nights"] == r["nights_needed"] - 9


def test_decision_floor_drives_nights_when_ci_is_tight():
    pairs = [(5, 5)] * 4          # zero variance, but only 20 decisions
    r = gp.line_power(line(pairs), target_hw=3.0, min_decisions=60)
    assert r["half_width"] == 0 and r["nights_for_min_decisions"] == 12 and r["nights_needed"] == 12


def test_ready_when_both_bars_met():
    pairs = [(18, 20), (19, 20), (18, 20), (19, 20)] * 5
    r = gp.line_power(line(pairs), target_hw=3.0)
    assert r["status"] == "READY" and r["more_nights"] == 0


def test_one_night_is_pending():
    assert gp.line_power(line([(5, 8)]), 3.0)["status"] == "PENDING"


def test_bootstrap_is_deterministic():
    pairs = [(6, 8), (5, 9), (8, 9), (4, 7)]
    assert gp.boot_ci(pairs) == gp.boot_ci(pairs)
