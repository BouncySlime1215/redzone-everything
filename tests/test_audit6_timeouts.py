"""AUDIT-6: only a period seen from its start can call a timeout one of its first two."""
from redzone.decider.cuts import DeadTracker
from redzone.models import League

T0 = 10_000.0


def _add(dt, pid, at, ttext, clock, period, kind="", text=""):
    dt.add(pid, at, at, kind or ttext.lower().replace(" ", "_"), text or ttext, ttext, clock, period)


def _timeout(dt, pid, at, clock, period=2):
    _add(dt, pid, at, "Timeout", clock, period, text="Timeout")
    return dt.marks[pid].after


def test_a_timeout_after_joining_mid_quarter_is_not_called_a_long_one():
    dt = DeadTracker(League.NBA)
    _add(dt, "p", T0, "Jump Shot", 200.0, 2, text="A misses")      # first entry the tracker ever sees: 3:20 left
    assert _timeout(dt, "t", T0 + 10, 190.0) == "timeout_short"


def test_the_first_two_timeouts_of_a_period_seen_from_its_start_are_long():
    dt = DeadTracker(League.NBA)
    _add(dt, "p", T0, "Jump Shot", 700.0, 2, text="A misses")
    assert _timeout(dt, "t1", T0 + 100, 500.0) == "timeout_long"
    assert _timeout(dt, "t2", T0 + 200, 300.0) == "timeout_long"
    assert _timeout(dt, "t3", T0 + 300, 100.0) == "timeout_short"
