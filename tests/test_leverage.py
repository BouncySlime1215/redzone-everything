"""redzone/lab/leverage.py: the rule floor for a mid-play leave (Nick, Nov 4 3:15 PM ET)."""
from redzone.lab.leverage import high_leverage
from redzone.models import League


def play(per, clock, home, away):
    return {"period": {"number": per}, "clock": {"displayValue": clock}, "homeScore": home, "awayScore": away}


def test_close_and_late_is_high_leverage():
    assert high_leverage(League.NFL, play(4, "2:00", 20, 17))
    assert high_leverage(League.NBA, play(4, "5:00", 100, 96))


def test_early_or_not_close_is_low_leverage():
    assert not high_leverage(League.NFL, play(2, "2:00", 20, 17))
    assert not high_leverage(League.NFL, play(4, "2:00", 30, 10))
    assert not high_leverage(League.NBA, play(4, "9:00", 100, 98))


def test_overtime_is_always_high_leverage():
    assert high_leverage(League.NFL, play(5, "8:00", 20, 20))
    assert high_leverage(League.NHL, play(4, "1:00", 2, 2))


def test_nhl_clock_counts_up():
    assert high_leverage(League.NHL, play(3, "17:00", 2, 1))       # 3:00 left
    assert not high_leverage(League.NHL, play(3, "5:00", 2, 1))    # 15:00 left


def test_mlb_late_and_close():
    assert high_leverage(League.MLB, play(8, "", 3, 2))
    assert not high_leverage(League.MLB, play(3, "", 3, 2))


def test_missing_play_or_score_is_not_high():
    assert not high_leverage(League.NFL, None)
    assert not high_leverage(League.NFL, {"period": {"number": 4}})
