"""[from the start] grader line for the time-shift buffer (Nick, Nov 5 9:10 AM): a big play counts from the
start only if the screen had its game from the previous dead ball through the end of the play."""
from types import SimpleNamespace

from redzone.grading import restart_before
from redzone.lab.backtest import from_start, watched_whole
from redzone.models import League


def _p(kind, clock, text="", score=False):
    return {"type": {"text": kind}, "text": text or kind, "period": {"number": 4},
            "clock": {"displayValue": clock}, "scoringPlay": score, "homeScore": 100, "awayScore": 99}


def _nba():
    plays = [_p("Shooting Foul", "1:30"),            # whistle at wall 100
             _p("Jump Shot", "1:20", "misses jumper"),   # clock ran 10 s: ball live again at ~130
             _p("Defensive Rebound", "1:19"),
             _p("Jump Shot", "1:05", "makes jumper", score=True)]   # the big play, live run from ~130
    return SimpleNamespace(league=League.NBA, gid="g1", wall=[100.0, 140.0, 141.0, 155.0], plays=plays)


def test_restart_is_the_previous_dead_ball():
    r = _nba()
    s = restart_before(r.league, r.wall, r.plays, 3)
    assert 100.0 <= s <= 140.0 and s < 155.0


def test_football_restart_is_the_snap():
    plays = [{"type": {"text": "Rush"}, "text": "run"}, {"type": {"text": "Pass Reception"}, "text": "TD",
                                                         "scoringPlay": True}]
    assert restart_before(League.NFL, [10.0, 50.0], plays, 1) == 50.0 - 7.0


def test_joined_mid_run_is_live_but_not_from_start():
    r, lag, tune = _nba(), 40.0, 5.0
    late = [(150.0 + lag - 10, "g1")]          # tuned in 5 s before the shot, after the restart
    assert watched_whole(late, "g1", 155.0 + lag, 155.0 + lag, tune, 1e9)
    assert not from_start(late, r, 3, lag, tune, 1e9)
    early = [(100.0 + lag, "g1")]              # tuned in during the whistle
    assert from_start(early, r, 3, lag, tune, 1e9)


def test_a_switch_away_inside_the_run_breaks_it():
    r, lag, tune = _nba(), 40.0, 5.0
    w = [(100.0 + lag, "g1"), (145.0 + lag, "g2"), (148.0 + lag, "g1")]
    assert not from_start(w, r, 3, lag, tune, 1e9)
