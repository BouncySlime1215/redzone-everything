"""grading.held_stop (grader v35 proposal, EXP-746/747): review batches and NHL period starts keep the stoppage open."""
from pathlib import Path

import pytest

from redzone import grading
from redzone.models import League

ROOT = Path(__file__).resolve().parents[1]


def _p(t, per=4, clk="8:17", text=""):
    return {"type": {"text": t}, "period": {"number": per}, "clock": {"displayValue": clk}, "text": text}


def _nba_review(relogged_clk="8:17", batch_dt=0.0):
    # challenge at 0, overturned + subs + the re-logged turnover at 40, next shot 200 s later at 8:03
    plays = [_p("Jump Shot", clk="8:20"), _p("Challenge"), _p("Coach's Challenge (Overturned)"), _p("Substitution"),
             _p("Lost Ball Turnover", clk=relogged_clk), _p("Hook Shot", clk="8:03")]
    wall = [-10.0, 0.0, 40.0, 40.0, 40.0 + batch_dt, 240.0]
    return plays, wall


def test_relogged_play_in_review_batch_keeps_it_dead():
    plays, wall = _nba_review()
    assert grading.dead_at(League.NBA, wall, plays, 60.0)[0]
    assert grading.dead_at(League.NBA, wall, plays, 200.0)[0]       # clock didn't move until 8:03


def test_clock_moved_or_new_second_ends_the_batch():
    plays, wall = _nba_review(relogged_clk="8:15")
    assert grading.held_stop(League.NBA, wall, plays, 4) == grading.Stoppage.NONE
    plays, wall = _nba_review(batch_dt=5.0)
    assert grading.held_stop(League.NBA, wall, plays, 4) == grading.Stoppage.NONE


def test_action_without_review_batch_stays_live():
    plays = [_p("Jump Shot", clk="8:20"), _p("Defensive Rebound", clk="8:18"), _p("Jump Shot", clk="8:01")]
    wall = [0.0, 2.0, 21.0]
    assert grading.held_stop(League.NBA, wall, plays, 1) == grading.Stoppage.NONE
    assert not grading.dead_at(League.NBA, wall, plays, 5.0)[0]


def test_nhl_period_start_is_dead_until_face_off():
    plays = [_p("Period End", per=4, clk="5:00"), _p("Period Start", per=5, clk="0:00"),
             _p("Face Off", per=5, clk="0:00"), _p("Shot", per=5, clk="0:20")]
    wall = [0.0, 100.0, 160.0, 180.0]
    assert grading.dead_at(League.NHL, wall, plays, 130.0)[0]
    assert not grading.dead_at(League.NHL, wall, plays, 170.0)[0]


def test_hou_sa_oct23_challenge_turnover_is_dead():
    p = ROOT / "data" / "archive" / "nba" / "summary_401909096.json"
    if not p.exists():
        pytest.skip("eval-data-v1 not present")
    from redzone.lab.backtest import Replay
    r = Replay.load(League.NBA, p)
    i = next(k for k, x in enumerate(r.plays) if x["period"]["number"] == 4 and x["clock"]["displayValue"] == "8:17"
             and x["type"]["text"] == "Lost Ball Turnover")
    assert grading.dead_at(r.league, r.wall, r.plays, r.wall[i] + 60.0)[0]


@pytest.mark.parametrize("order", [("Substitution", "Challenge"), ("Challenge", "Substitution")])
def test_nhl_shaped_batch_with_sub_and_review(order):
    # NHL subs classify NONE in stoppage_for; the batch must still hold the stoppage whichever row comes first
    plays = [_p("Shot", per=2, clk="12:10"), _p(order[0], per=2, clk="12:14"), _p(order[1], per=2, clk="12:14"),
             _p("Takeaway", per=2, clk="12:14"), _p("Face Off", per=2, clk="12:14"), _p("Shot", per=2, clk="12:30")]
    wall = [0.0, 30.0, 30.0, 30.0, 150.0, 166.0]
    assert grading.held_stop(League.NHL, wall, plays, 3) in (grading.Stoppage.REVIEW, grading.Stoppage.WHISTLE)
    assert grading.dead_at(League.NHL, wall, plays, 90.0)[0]
