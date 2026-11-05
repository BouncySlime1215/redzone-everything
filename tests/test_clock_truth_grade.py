"""Tests for the clock-truth re-grade proposal (EXP-744)."""
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "clock_truth_grade", Path(__file__).resolve().parent.parent / "scripts" / "clock_truth_grade.py")
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)

T0 = 1_800_000_000.0


def _ev(*xs):
    # (wall offset s, period, clock "m:ss", type)
    return [(T0 + w, p, ct._secs(c), t) for w, p, c, t in xs]


def test_running_clock_is_live_and_stopped_clock_is_dead():
    ev = _ev((0, 1, "10:00", "jump shot"), (20, 1, "9:40", "layup shot"), (80, 1, "9:35", "foul"))
    assert ct.label_at(ev, T0 + 10) == "live"          # 20 s wall, 20 s clock
    assert ct.label_at(ev, T0 + 50) == "dead"          # 60 s wall, 5 s clock
    assert ct.label_at(ev, T0 + 85) == "ambiguous"     # nothing after it


def test_whistle_and_period_change_are_dead():
    ev = _ev((0, 1, "5:00", "shooting foul"), (40, 1, "4:20", "jump shot"),
             (60, 1, "0:00", "end of 1st quarter"), (200, 2, "12:00", "jump ball"))
    assert ct.label_at(ev, T0 + 5) == "dead"           # within 10 s of a foul
    assert ct.label_at(ev, T0 + 100) == "dead"         # straddles the period change


def test_band_disagreement_is_ambiguous():
    ev = _ev((0, 1, "10:00", "jump shot"), (20, 1, "9:40", "layup shot"), (80, 1, "9:35", "rebound"))
    assert ct.label(ev, T0 + 19, band=0) == "live"
    assert ct.label(ev, T0 + 19, band=5) == "ambiguous"


def test_grade_counts_and_skips_other_leagues(tmp_path):
    (tmp_path / "nba").mkdir()
    plays = [{"wallclock": "2027-01-15T08:00:00Z", "period": {"number": 1}, "clock": {"displayValue": "10:00"},
              "type": {"text": "Jump Shot"}},
             {"wallclock": "2027-01-15T08:00:20Z", "period": {"number": 1}, "clock": {"displayValue": "9:40"},
              "type": {"text": "Layup Shot"}}]
    (tmp_path / "nba" / "summary_g1.json").write_text(json.dumps({"plays": plays}))
    a = ct.events(tmp_path, "nba", "g1")[0][0] + 10
    rows = [{"gid": "g1", "league": "nba", "screen_wall": a, "mid_play": False, "night_et": "n"},
            {"gid": "x", "league": "nfl", "screen_wall": a, "mid_play": False}]
    r = ct.grade(rows, tmp_path)
    assert r["entries"] == 1 and r["truth_live"] == 1 and r["grader_dead_but_truth_live"] == 1
