"""Nick, Nov 5 2026 12:28 AM: time to see each big play is a headline line, and the catch-up window is a per-league
ratchet (3 min -> 90 s -> 30 s -> live) that tightens after 2 releases holding the next window on locked, never loosens."""
import json

from redzone.lab.backtest import CATCHUP_LADDER, time_to_see
from scripts import catchup_ratchet as cr
from scripts import northstar as ns


def test_time_to_see_live_replay_never_unknown():
    assert time_to_see(True, {}, ("g", 1), 100.0) == 0.0
    assert time_to_see(False, {("g", 1): 160.0}, ("g", 1), 100.0) == 60.0
    assert time_to_see(False, {}, ("g", 1), 100.0) == float("inf")
    assert time_to_see(False, {("g", 1)}, ("g", 1), 100.0) is None
    assert CATCHUP_LADDER == (180.0, 90.0, 30.0, 0.0)


def _cards(see, n=9):
    per = [{"night": f"n{i}", "big_see_s": list(see), "entries": 1, "landings": 1} for i in range(n)]
    return {lg: {"metrics": {"per_night": per}} for lg in ns.LEAGUES}


def test_rows_report_median_p90_and_shares():
    rows = {r["key"]: r for r in ns.see_rows({lg: _cards([0, 0, 45, 120, -1])[lg]["metrics"]["per_night"]
                                              for lg in ns.LEAGUES})}
    r = rows["time_to_see_nba"]
    assert r["status"] == ns.INFO and r["value"] == 45.0 and "p90 never" in r["detail"]
    assert "within 3 min 80.0%, 90 s 60.0%, 30 s 40.0%, live 40.0%" in r["detail"]
    assert rows["catchup_nba"]["status"] == ns.FAIL               # 80% within 3 min < 99%


def test_old_scorecards_are_pending():
    rows = {r["key"]: r for r in ns.see_rows({lg: [{"night": "n"}] for lg in ns.LEAGUES})}
    assert rows["catchup_nfl"]["status"] == ns.PENDING and rows["time_to_see_nfl"]["status"] == ns.PENDING


def _state():
    return {"ladder_s": [180, 90, 30, 0], "window_s": {"nfl": 180, "nba": 180}, "releases": []}


def test_ratchet_steps_down_after_two_releases_and_never_back_up():
    good = _cards([0] * 30 + [60] * 30)          # everything within 90 s
    bad = _cards([0] * 30 + [150] * 30)          # within 3 min only
    st = cr.record(_state(), "RC7", good)
    assert st["window_s"] == {"nfl": 180, "nba": 180}            # one release isn't enough
    st = cr.record(st, "RC8", good)
    assert st["window_s"]["nba"] == 90 and st["window_s"]["nfl"] == 90
    st = cr.record(st, "RC9", bad)                                # a bad release never loosens it
    assert st["window_s"]["nba"] == 90
    st = cr.record(st, "RC10", good)                              # 30 s not held: stays at 90
    assert st["window_s"]["nba"] == 90


def test_the_repo_ratchet_starts_at_3_min():
    st = json.loads(cr.STATE.read_text())
    assert set(st["window_s"].values()) == {180} and st["ladder_s"] == [180, 90, 30, 0]
