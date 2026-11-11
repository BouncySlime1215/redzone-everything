"""AUDIT-6: college basketball's silent stretch is a 30 s shot clock, not the NBA's 24 s."""
import pytest

from redzone.decider.cuts import DeadTracker
from redzone.models import League

T0 = 10_000.0


def _add(dt, pid, at, ttext, clock, period, kind="", text=""):
    dt.add(pid, at, at, kind or ttext.lower().replace(" ", "_"), text or ttext, ttext, clock, period)


@pytest.mark.parametrize("league,silent", [(League.NBA, 24.0), (League.CBB, 30.0), (League.WCBB, 30.0)])
def test_live_action_can_follow_a_full_shot_clock_of_silence(league, silent):
    dt = DeadTracker(league)
    _add(dt, "to", T0, "Timeout", None, 1, text="Timeout")
    _add(dt, "shot", T0 + 100, "Jump Shot", None, 1, text="A misses a jumper")
    assert dt.windows()[0].end == pytest.approx(T0 + 100 - silent - 2.0)


def test_the_two_shot_clock_tables_agree():
    from redzone.decider.cuts import SILENT_S
    from redzone.decider.screen import SILENT_LIVE_S
    for lg in (League.NBA, League.CBB, League.WCBB):
        assert SILENT_S.get(lg, 0.0) == SILENT_LIVE_S.get(lg, 0.0)


def test_covered_counts_the_college_shot_clock():
    dt = DeadTracker(League.CBB)
    dt.seen(T0, 10.0)
    assert dt._covered() == pytest.approx(T0 - 10.0 - 30.0 - 2.0)
