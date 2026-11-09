"""AUDIT-6: a dead window that spans a period change ends at the tip, whatever the next period logs first."""
import pytest

from redzone.decider.cuts import DeadTracker
from redzone.models import League

T0 = 10_000.0


def _add(dt, pid, at, ttext, clock, period, kind="", text=""):
    dt.add(pid, at, at, kind or ttext.lower().replace(" ", "_"), text or ttext, ttext, clock, period)


# ---- a window across a period change
def test_a_foul_early_in_the_next_quarter_ends_the_quarter_break():
    dt = DeadTracker(League.NBA)
    _add(dt, "end", T0, "End Period", 0.0, 1)
    # Q2 tipped off about T0 + 130 with nothing logged until a foul 40 s of game clock in
    _add(dt, "foul", T0 + 170, "Personal Foul", 680.0, 2, text="Foul on A")
    first = dt.windows()[0]
    assert first.kind == "end_period" and first.end == pytest.approx(T0 + 170 - 40 - 3)
    assert dt.dead_until(T0 + 150) is None or dt.dead_until(T0 + 150) < T0 + 170   # Q2 is live here
    assert dt.landing(T0 + 150, T0 + 155).state != "dead"


@pytest.mark.parametrize("league,period,clock,ran", [
    (League.CBB, 2, 1160.0, 40.0),   # a college half is 20:00
    (League.WCBB, 2, 560.0, 40.0),   # women's quarters are 10:00
    (League.NBA, 5, 260.0, 40.0),    # overtime is 5:00
])
def test_the_period_length_is_the_leagues(league, period, clock, ran):
    dt = DeadTracker(league)
    _add(dt, "end", T0, "End Period", 0.0, period - 1)
    _add(dt, "foul", T0 + 400, "Personal Foul", clock, period, text="Foul on A")
    assert dt.windows()[0].end == pytest.approx(T0 + 400 - ran - 3)


def test_a_substitution_before_the_tip_keeps_the_break_open():
    dt = DeadTracker(League.NBA)
    _add(dt, "end", T0, "End Period", 0.0, 1)
    _add(dt, "sub", T0 + 100, "Substitution", 720.0, 2, text="A enters for B")
    assert [w.end for w in dt.windows()] == [None]
