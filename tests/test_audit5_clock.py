"""AUDIT-5: the screen-time clock is recorded only while a phone is open, so a gap in the tape is not a clock.

Why: broadcast() builds the snapshot only with a subscriber. Opening the phone after 20 minutes read the clock from
before the gap back as the clock on screen, marked "ok". The card promises a blank when it doesn't know.
"""
from types import SimpleNamespace as NS

from redzone import views
from redzone.models import Phase

LIVE = {"phase": Phase.LIVE.value, "clock": "live"}


def g(clock):
    return NS(period=2, clock_s=clock)


def test_a_gap_in_the_recording_is_not_read_back():
    """Repro: last recorded at t=0, the phone reopens at t=1200, delay 60: the 20-minute-old 9:00 came back as 'ok'."""
    t = views.ClockTape()
    t.record("a", g(540), now=0.0)
    t.record("a", g(120), now=1200.0)
    out = views.screen_item(LIVE, "a", enabled=True, tape=t, delay_s=60.0, now=1200.0)
    assert out["clock"] == "" and out["screen_clock"] == "unknown"


def test_a_steady_tape_still_reads_back():
    t = views.ClockTape()
    for i in range(0, 121, 5):
        t.record("a", g(600 - i), now=float(i))
    out = views.screen_item(LIVE, "a", enabled=True, tape=t, delay_s=60.0, now=120.0)
    assert out["clock"] == "9:00" and out["screen_clock"] == "ok"


def test_a_stopped_clock_on_a_steady_tape_still_reads_back():
    """Unchanged readings aren't stored: the gap rule must follow the recording, not the rows."""
    t = views.ClockTape()
    for i in range(0, 121, 5):
        t.record("a", g(600), now=float(i))
    assert t.at("a", 60.0) == (2, 600)
