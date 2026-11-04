"""The release gate: pass/fail/pending logic (scripts/release_gate.py)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_gate", Path(__file__).parent.parent / "scripts/release_gate.py")
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)


def _card(**kw):
    m = {"eval_data": "eval-data-v1", "mid_play_cuts": 0, "entries": 100, "entries_mid_play": 2,
         "big_seen_pct": 99.0, "your_team_crunch_live_pct": 100.0, "crunch_live_pct": 96.0}
    m.update(kw)
    return {"metrics": m}


def _status(rows, gate, scope="nhl"):
    return next(r[2] for r in rows if r[0] == gate and r[1] == scope)


def test_clean_card_passes_and_system_gates_stay_pending():
    rows = rg.gates({"nhl": _card()})
    assert all(r[2] == rg.PASS for r in rows if r[1] == "nhl")
    assert all(r[2] == rg.PENDING for r in rows if r[1] == "system")


def test_cuts_dead_ball_and_rise_above_today_fail():
    base = {"cards": {"nhl": _card(entries_mid_play=2)}}
    rows = rg.gates({"nhl": _card(mid_play_cuts=1, entries_mid_play=6)}, base)
    assert _status(rows, "mid-play cuts = 0") == rg.FAIL
    assert _status(rows, "entries at a dead ball >= 95%") == rg.FAIL
    assert _status(rows, "entries mid-play <= 5%") == rg.FAIL
    assert _status(rows, "entries mid-play never above today") == rg.FAIL


def test_crunch_under_95_is_pending_not_pass_and_zero_of_zero_is_pending():
    rows = rg.gates({"nhl": _card(crunch_live_pct=90.0, your_team_crunch_live_pct=0.0)})
    assert _status(rows, "crunch live >= 95% or feasible ceiling") == rg.PENDING
    assert _status(rows, "your-team crunch live = 100%") == rg.PENDING
    assert _status(rg.gates({"nhl": _card(your_team_crunch_live_pct=93.0)}), "your-team crunch live = 100%") == rg.FAIL


def test_unpinned_data_and_crashed_scorecard_fail():
    rows = rg.gates({"nhl": _card(eval_data="UNPINNED: x"), "nba": {"error": "exit 1"}})
    assert _status(rows, "pinned data") == rg.FAIL
    assert _status(rows, "scorecard ran", "nba") == rg.FAIL


def test_demo_card_reads_its_own_shapes():
    demo = {"metrics": {"eval_data": "eval-data-v1", "mid_play_cuts": 0,
                        "cut_timing": {"entries": 200, "entries_mid_play": 4},
                        "nfl_big_plays": {"seen": "137/137 (100%)"}, "nhl_goals": {"seen": "35/36 (97%)"},
                        "crunch_live": "104/137 (76%)"}}
    rows = rg.gates({"demo_20261101": demo})
    assert _status(rows, "entries at a dead ball >= 95%", "demo_20261101") == rg.PASS
    assert _status(rows, "big plays seen >= 95% (NHL)", "demo_20261101") == rg.PASS
    assert _status(rows, "crunch live >= 95% or feasible ceiling", "demo_20261101") == rg.PENDING


def test_reel_cards_get_their_own_gate_rows():
    rows = rg.gates({"nhl_reels": _card(reels=12, reel_s=600, reels_in_dead_pct=97.5, reel_home_big_missed=0,
                                        reel_big_missed_anywhere=2)})
    assert _status(rows, "reels >= 95% in dead time", "nhl_reels") == rg.PASS
    assert _status(rows, "0 live big plays missed on a reel", "nhl_reels") == rg.PASS
    assert not any(r[0].startswith("reels >= 95% in dead time, 0") for r in rows)   # no pending system row
    rows = rg.gates({"nfl_reels": _card(reels=5, reel_s=300, reels_in_dead_pct=90.0, reel_home_big_missed=1)})
    assert _status(rows, "reels >= 95% in dead time", "nfl_reels") == rg.FAIL
    assert _status(rows, "0 live big plays missed on a reel", "nfl_reels") == rg.FAIL
    rows = rg.gates({"nhl": _card()})
    assert any(r[0].startswith("reels >= 95% in dead time, 0") and r[2] == rg.PENDING for r in rows)
