"""AUDIT-8: hockey dead windows end when the game clock has run, whether or not the faceoff was logged, and the
period change does the same for hockey as for basketball. ESPN's live feed hands over the last plays only, so a missed
faceoff is a live possibility: until the restart is known, live hockey looks dead."""
import pytest

from redzone.decider.cuts import DeadTracker
from redzone.models import League

T0 = 10_000.0


def _add(dt, pid, at, ttext, clock, period, kind="", text=""):
    dt.add(pid, at, at, kind or ttext.lower().replace(" ", "_"), text or ttext, ttext, clock, period)


def test_a_missed_faceoff_does_not_stretch_a_whistle_over_live_hockey():
    dt = DeadTracker(League.NHL)
    _add(dt, "w1", T0, "Stoppage", 1000.0, 1, text="Offside")
    # no faceoff logged; the first entry after is a shot with 40 s of game clock gone
    _add(dt, "shot", T0 + 80, "Shot", 960.0, 1, text="A wrist shot saved")
    w = dt.windows()[0]
    assert w.kind == "whistle" and w.exact and w.end == pytest.approx(T0 + 80 - 40 - 3)
    assert dt.landing(T0 + 60, T0 + 70).state != "dead"       # the middle of those 40 s of play


def test_a_second_whistle_after_a_missed_faceoff_opens_its_own_window():
    dt = DeadTracker(League.NHL)
    _add(dt, "w1", T0, "Stoppage", 1000.0, 1, text="Icing")
    _add(dt, "w2", T0 + 70, "Stoppage", 950.0, 1, text="Offside")
    ws = dt.windows()
    assert len(ws) == 2 and ws[0].end == pytest.approx(T0 + 70 - 50 - 3) and ws[1].start == T0 + 70
    assert dt.landing(T0 + 40, T0 + 50).state != "dead"


def test_a_logged_faceoff_still_ends_the_window_exactly():
    dt = DeadTracker(League.NHL)
    _add(dt, "w1", T0, "Stoppage", 1000.0, 1, text="Icing")
    _add(dt, "fo", T0 + 30, "Face Off", 1000.0, 1, text="A won faceoff")
    assert dt.windows()[0].end == T0 + 30


def test_a_penalty_at_the_same_clock_keeps_the_stoppage_open():
    dt = DeadTracker(League.NHL)
    _add(dt, "w1", T0, "Stoppage", 1000.0, 1, text="Icing")
    _add(dt, "pen", T0 + 10, "Hooking", 1000.0, 1, text="A minor penalty")
    assert dt.windows()[0].end is None


def test_the_next_period_dates_the_faceoff_from_its_clock_when_the_faceoff_is_missing():
    dt = DeadTracker(League.NHL)
    _add(dt, "end", T0, "Period End", 0.0, 1, text="End of 1st Period")
    _add(dt, "w2", T0 + 1100, "Stoppage", 1140.0, 2, text="Offside")     # 60 s into the 2nd
    ws = dt.windows()
    assert ws[0].end == pytest.approx(T0 + 1100 - 60 - 3) and ws[1].start == T0 + 1100


@pytest.mark.parametrize("league,period,clock,ttext,text", [
    (League.NBA, 2, 680.0, "Jump Shot", "A makes 24-foot jumper"),                     # 40 s of Q2 already gone
    (League.NHL, 2, 1160.0, "Shot", "A wrist shot saved"),
])
def test_a_live_action_first_in_the_next_period_dates_the_restart_from_its_clock(league, period, clock, ttext, text):
    """The break's end used to be read off that entry's stamp, 26 s back at most, however much clock had run."""
    dt = DeadTracker(league)
    _add(dt, "end", T0, "End Period" if league == League.NBA else "Period End", 0.0, period - 1)
    _add(dt, "act", T0 + 400, ttext, clock, period, text=text)
    assert dt.windows()[0].end == pytest.approx(T0 + 400 - 40 - 3)
