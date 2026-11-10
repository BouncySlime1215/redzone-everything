"""[timing.dead_v2] The break class reaches the timeline beside the stoppage label (redzone/decider/screen.py Event.stop_class).

Why: an NFL timeout the feed calls official is a commercial break and "Timeout #n by TEAM" is short (research ledger L1); the
decider can only tell them apart if the class travels with the stoppage, without changing the label anything else reads.
"""
import pytest

from redzone.decider.screen import Event, Timeline
from redzone.models import League, Play, Stoppage
from tests.conftest import nfl


def _stopped(clock, league, stoppage, play):
    """The stop_start event of a game that was live one poll and stopped the next, `play` being the stoppage's own entry."""
    tl = Timeline(league, clock=clock)
    g = nfl()
    g.league = league
    g.recent_plays = [Play("p0", "run for 3", "rush", wallclock=clock.t - 30, type_text="Rush")]
    g.last_play = g.recent_plays[-1]
    tl.observe(g)
    clock.t += 10
    g = nfl(stoppage=stoppage)
    g.league = league
    g.recent_plays = [play] if play else []
    g.last_play = play
    tl.observe(g)
    return tl.last("stop_start")


@pytest.mark.parametrize("type_text,text,cls", [
    ("Official Timeout", "Official Timeout", "official"),
    ("Timeout", "Timeout #2 by WSH at 01:58.", "team"),
    ("Timeout", "Timeout at 07:12.", "other"),
])
def test_a_football_timeout_carries_its_class_beside_an_unchanged_label(clock, type_text, text, cls):
    start = _stopped(clock, League.NFL, Stoppage.TIMEOUT, Play("t1", text, "timeout", wallclock=clock.t + 5, type_text=type_text))
    assert (start.kind, start.label, start.stop_class, start.pid) == ("stop_start", "timeout", cls, "t1")


def test_every_other_stoppage_is_its_own_class(clock):
    tmw = _stopped(clock, League.NFL, Stoppage.TWO_MINUTE_WARNING,
                   Play("w", "Two-Minute Warning", "two_minute_warning", wallclock=clock.t + 5, type_text="Two-minute warning"))
    assert (tmw.label, tmw.stop_class) == ("two_minute_warning", "two_minute_warning")
    nba = _stopped(clock, League.NBA, Stoppage.TIMEOUT, Play("t", "Wizards Full timeout", "timeout", wallclock=clock.t + 5,
                                                              type_text="Full Timeout"))
    assert (nba.label, nba.stop_class) == ("timeout", "timeout")


def test_a_timeout_whose_play_has_no_timeout_type_reads_as_the_pooled_class(clock):
    """Another feed's copy of the play (no ESPN type text): nothing to read, so the class is the kind and the pooled cell answers."""
    start = _stopped(clock, League.NFL, Stoppage.TIMEOUT, Play("t1", "Timeout #1 by DAL", "timeout", wallclock=clock.t + 5))
    assert (start.label, start.stop_class) == ("timeout", "timeout")


def test_a_stoppage_with_no_play_and_a_hand_built_event_carry_no_class(clock):
    assert _stopped(clock, League.NFL, Stoppage.TIMEOUT, None).stop_class == ""
    assert Event(0.0, "stop_start", "timeout").stop_class == ""               # the default: every older caller is unchanged
