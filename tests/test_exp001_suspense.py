"""[EXP-001] Suspense at entry: S = E[(dWP)^2] over the plays after each entry, graded beside mid-play entries."""
import sys
from pathlib import Path
from types import SimpleNamespace

from redzone.lab import cutgrade

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import northstar as ns  # noqa: E402


def _replay(wps):
    plays = [{"id": str(i)} for i in range(len(wps))]
    data = {"winprobability": [{"playId": str(i), "homeWinPercentage": w} for i, w in enumerate(wps) if w is not None]}
    return SimpleNamespace(plays=plays, wall=[100.0 + 10 * i for i in range(len(wps))], data=data, _cache={})


def test_suspense_is_the_mean_squared_wp_swing_in_points():
    r = _replay([0.50, 0.60, 0.40, 0.40] + [0.40] * 10)
    # from play 0: swings +10, -20, 0, 0, 0, 0, 0, 0 points over 8 plays -> (100 + 400) / 8
    assert abs(cutgrade.suspense_at(r, 100.0) - 500 / 8) < 1e-9
    assert cutgrade.suspense_at(r, 135.0) == 0.0                     # flat from play 3 on


def test_no_win_probability_reads_none():
    assert cutgrade.suspense_at(_replay([None] * 6), 100.0) is None
    assert cutgrade.suspense_at(_replay([0.5] + [None] * 5), 100.0) is None   # one point is no swing


def test_northstar_line_is_info_and_weighted_by_entries():
    per = {lg: [] for lg in ns.LEAGUES}
    per["nfl"] = [{"suspense_sum": 300.0, "suspense_n": 3}]
    per["nba"] = [{"suspense_sum": 10.0, "suspense_n": 1}]
    row = ns.suspense_row(per)
    assert row["status"] == ns.INFO and row["value"] == 77.5 and "NFL 100.0 (3)" in row["detail"]
    assert ns.suspense_row({lg: [] for lg in ns.LEAGUES})["value"] is None
    assert ns._fmt("suspense_entry", 77.5) == "77.5 pts²"
