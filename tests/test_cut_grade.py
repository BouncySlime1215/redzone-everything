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


# ---- [grader v49] the entry window after a made basket (Nick, Nov 9, decisions.md) ----

def _hoops(league=League.NBA, first=("Jump Shot", True), after=()):
    """A shot at wall 1000 (made or not), then `after` rows, with the clock running, so nothing is a dead ball."""
    kind, made = first
    plays = [_p("Substitution", "5:05"), _p(kind, "5:00", shootingPlay=True, scoringPlay=made), *(p for _, p in after),
             _p("Jump Shot", "3:50", shootingPlay=True)]     # the game goes on: an entry after the last row isn't graded
    wall = [900.0, 1000.0, *(w for w, _ in after), 1100.0]
    return Replay(league, "n1", {}, plays, wall, "AAA @ BBB")


def _enter(r, st, lag=30.0):
    return cutgrade.grade([r], [SwitchLog(st - 5 + lag, None, "n1", "better", "")], lag, 5.0)


def test_entry_inside_the_window_after_a_make_is_clean_but_stays_on_the_old_count():
    r = _hoops(after=[(1040.0, _p("Jump Shot", "4:30", shootingPlay=True))])
    c = _enter(r, 1006.0)     # past the old 4 s dead ball after a score
    assert c.entries == 1 and c.entries_mid_play == 0 and c.entries_mid_play_strict == 1 and c.mid_entries == []
    assert c.summary()["entries_mid_play_strict"] == 1 and c.summary()["entries_mid_play"] == 0


def test_window_length_is_8_s_nba_and_9_s_cbb():
    after = [(1040.0, _p("Jump Shot", "4:30", shootingPlay=True))]
    assert _enter(_hoops(after=after), 1008.0).entries_mid_play == 0
    assert _enter(_hoops(after=after), 1008.5).entries_mid_play == 1
    assert _enter(_hoops(League.CBB, after=after), 1008.5).entries_mid_play == 0
    assert _enter(_hoops(League.CBB, after=after), 1009.5).entries_mid_play == 1
    assert _enter(_hoops(League.WCBB, after=after), 1006.0).entries_mid_play == 1     # women's college is not in the approval


def test_entry_after_the_next_shot_is_not_clean():
    r = _hoops(after=[(1006.0, _p("Jump Shot", "4:54", shootingPlay=True))])
    c = _enter(r, 1007.0)     # inside 8 s of the make, but the next shot went up first
    assert c.entries_mid_play == 1 and c.entries_mid_play_strict == 1 and len(c.mid_entries) == 1


def test_entry_after_a_miss_or_a_rebound_is_not_clean():
    assert _enter(_hoops(first=("Jump Shot", False)), 1006.0).entries_mid_play == 1
    rebound = _hoops(first=("Jump Shot", False), after=[(1002.0, _p("Defensive Rebound", "4:58"))])
    assert _enter(rebound, 1006.0).entries_mid_play == 1


def test_a_made_free_throw_opens_no_window():
    # the approval is for made baskets; the 8 and 9 s were measured on field goals
    after = [(1040.0, _p("Jump Shot", "4:30", shootingPlay=True))]
    assert after_bucket_says(_hoops(first=("Free Throw - 1 of 1", True), after=after), 1006.0) is False
    assert after_bucket_says(_hoops(League.CBB, first=("MadeFreeThrow", True), after=after), 1006.0) is False     # college spelling


def after_bucket_says(r, st):
    return cutgrade.after_bucket(r.league, r.wall, r.plays, st)


def test_the_window_is_entries_only_and_leaves_no_other_metric_moved():
    r = _hoops(after=[(1040.0, _p("Jump Shot", "4:30", shootingPlay=True))])
    c = _enter(r, 1006.0).summary()
    assert c["landed_dead_pct"] == 0.0 and c["landings_in_dead_pct"] == 0.0 and c["entry_to_restart_hist"] == {"mid-play": 1}
    left = cutgrade.grade([r], [SwitchLog(1006.0 + 30.0, "n1", "x", "better", "")], 30.0, 5.0)
    assert left.leaves == 1 and left.leaves_in_dead == 0 and left.leaves_between_plays == 0


def test_nfl_is_unchanged_by_the_window():
    # a scoring play a second before the entry would open the window in basketball: NFL scores the same on both counts
    plays = [_p("Rush", shootingPlay=True, scoringPlay=True), _p("Pass Reception")]
    r = Replay(League.NFL, "g1", {}, plays, [1000.0, 1040.0], "AAA @ BBB")
    assert not cutgrade.after_bucket(League.NFL, r.wall, r.plays, 1001.0)
    c = cutgrade.grade([r], [SwitchLog(1037 - 5 + 40, None, "g1", "better", "")], 40.0, 5.0)
    assert c.entries_mid_play == 1 and c.entries_mid_play_strict == 1
