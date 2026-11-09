"""release_gate.league_promotion: Nick's per-league promotion rule (Nov 5 2026, 8:20 PM ET)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("rg", Path(__file__).resolve().parent.parent / "scripts" / "release_gate.py")
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)


def card(mid=0, big=60.0, crunch=80.0, dead=20.0, missed=2, cuts=0):
    # mid_play_cuts: grader v47 R10 requires the candidate to report leaves too (pass bar 0)
    return {"metrics": {"entries_mid_play": mid, "mid_play_cuts": cuts, "big_live_pct": big, "crunch_live_pct": crunch,
                        "dead_air_pct": dead, "plays_missed_entry": missed}}


def both(c):
    return {"dev": {"nba": c}, "locked": {"nba": c}}


def test_beats_live_with_zero_mid_play_promotes():
    assert rg.league_promotion(both(card(mid=5)), both(card(big=70.0)), "nba")[0] == rg.PASS


def test_any_mid_play_entry_blocks_even_if_better():
    live = both(card(mid=5))
    cand = {"dev": {"nba": card(big=90.0)}, "locked": {"nba": card(mid=1, big=90.0)}}
    assert rg.league_promotion(live, cand, "nba")[0] == rg.FAIL


def test_a_worse_line_blocks():
    assert rg.league_promotion(both(card()), both(card(big=70.0, dead=25.0)), "nba")[0] == rg.FAIL


def test_equal_is_not_a_win():
    assert rg.league_promotion(both(card()), both(card()), "nba")[0] == rg.FAIL


def test_missing_split_is_pending():
    assert rg.league_promotion(both(card()), {"dev": {"nba": card(big=70.0)}}, "nba")[0] == rg.PENDING


def test_big_seen_drop_blocks():
    """Lane 2's NFL catch (Nov 5): big seen 89 -> 78 must fail even when big live improves."""
    live = both({"metrics": {**card()["metrics"], "big_seen_pct": 89.0}})
    cand = both({"metrics": {**card(big=70.0)["metrics"], "big_seen_pct": 78.0}})
    assert rg.league_promotion(live, cand, "nba")[0] == rg.FAIL


def test_any_mid_play_leave_blocks_even_if_better():
    assert rg.league_promotion(both(card()), both(card(big=90.0, cuts=1)), "nba")[0] == rg.FAIL


def test_unmeasured_leaves_is_pending():
    c = card(big=90.0)
    del c["metrics"]["mid_play_cuts"]
    assert rg.league_promotion(both(card()), both(c), "nba")[0] == rg.PENDING
