"""The cut-timing scorecard: where each switch left and where it landed (redzone/lab/cutgrade.py)."""
from redzone.lab import cutgrade
from redzone.lab.backtest import Replay, SwitchLog
from redzone.models import League


def _p(kind, clock="10:00", period=1, text="", **kw):
    return {"type": {"text": kind}, "clock": {"displayValue": clock}, "period": {"number": period},
            "text": text, **kw}


def _nfl():
    # snaps every 40 s (play ends at the logged wallclock), then a timeout and a long break
    plays = [_p("Rush"), _p("Pass Reception"), _p("Rush"), _p("Timeout", text="Timeout #1"), _p("Rush")]
    wall = [1000.0, 1040.0, 1080.0, 1085.0, 1200.0]
    return Replay(League.NFL, "g1", {}, plays, wall, "AAA @ BBB")


def test_nfl_entry_mid_play_and_wait():
    r = _nfl()
    lag = 40.0
    # Picture comes up (t + 5) at screen time 1037: inside the play that ends at 1040 -> mid-play.
    mid = cutgrade.grade([r], [SwitchLog(1037 - 5 + lag, None, "g1", "better", "")], lag, 5.0)
    assert mid.entries == 1 and mid.entries_mid_play == 1
    # Screen time 1050: between plays; the next snap starts at 1080 - 7 = 1073 -> 23 s of wait.
    ok = cutgrade.grade([r], [SwitchLog(1050 - 5 + lag, None, "g1", "better", "")], lag, 5.0)
    assert ok.entries_mid_play == 0 and round(ok.entry_wait_s) == 23 and ok.entries_in_break == 0
    # Screen time 1100: inside the timeout -> a break landing, wait until the 1193 snap.
    brk = cutgrade.grade([r], [SwitchLog(1100 - 5 + lag, None, "g1", "better", "")], lag, 5.0)
    assert brk.entries_in_break == 1 and round(brk.entry_wait_s) == 93


def test_leaves_inside_dead_windows():
    r = _nfl()
    lag = 40.0
    sw = [SwitchLog(1050 + lag, "g1", "x", "better", ""), SwitchLog(1100 + lag, "g1", "x", "better", "")]
    card = cutgrade.grade([r], sw, lag, 5.0)
    assert card.leaves == 2 and card.leaves_in_dead == 1 and card.leaves_between_plays == 1
    assert card.summary()["leaves_in_dead_pct"] == 50.0


def test_nba_resume_from_the_clock():
    # Timeout at 5:00 (wall 2000); first clock-moving event at 4:50 logged at wall 2200: play
    # restarted at 2190, so landing at 2100 waits 90 s and landing at 2195 is mid-play.
    plays = [_p("Jump Shot", "5:00", scoringPlay=True), _p("Full Timeout", "5:00"),
             _p("Substitution", "5:00"), _p("Jump Shot", "4:50")]
    wall = [1990.0, 2000.0, 2001.0, 2200.0]
    r = Replay(League.NBA, "n1", {}, plays, wall, "AAA @ BBB")
    assert cutgrade.resume_after(League.NBA, wall, plays, 1) == 2190.0
    a = cutgrade.grade([r], [SwitchLog(2100 - 5 + 30, None, "n1", "better", "")], 30.0, 5.0)
    assert a.entries_mid_play == 0 and round(a.entry_wait_s) == 90 and a.entries_in_break == 1
    b = cutgrade.grade([r], [SwitchLog(2195 - 5 + 30, None, "n1", "better", "")], 30.0, 5.0)
    assert b.entries_mid_play == 1


def test_plays_missed_during_the_tune():
    r = _nfl()
    lag = 40.0
    # Switch sent at screen time 1030 (black until 1035): the play 1033-1040 started in the dark.
    card = cutgrade.grade([r], [SwitchLog(1030 + lag, None, "g1", "better", "")], lag, 5.0)
    assert card.plays_missed_entry == 1
