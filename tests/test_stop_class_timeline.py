"""[timing.dead_v2] The break class reaches the timeline beside the stoppage label (redzone/decider/screen.py Event.stop_class).

Why: an NFL timeout the feed calls official is a commercial break and "Timeout #n by TEAM" is short (research ledger L1); the
decider can only tell them apart if the class travels with the stoppage, without changing the label anything else reads.
"""
from pathlib import Path

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


# ----- [DEAD-V2] flag-off identity on a recorded dev night ---------------------------------------------------------------------------

ARCH = Path(__file__).resolve().parents[1] / "data" / "archive"
NIGHT = ("nfl", "20251123")       # a valid (dev) night under the eval-data-v1 split; never a locked one


def _replay(monkeypatch, cls_fn):
    from redzone.config import Config
    from redzone.decider import screen
    from redzone.lab import splits
    from redzone.lab.backtest import Replay, run
    monkeypatch.setattr(screen, "stoppage_class", cls_fn)
    lg, night = NIGHT
    arch = ARCH / lg
    assert night not in splits.league_split(lg, splits.archive_nights(arch)).test
    reps = [r for g in splits.archive_nights(arch)[night]
            if (f := arch / f"summary_{g}.json").exists() and (r := Replay.load(League(lg), f))]
    rep = run(reps, Config(user_path=ARCH / "no_user_config.yaml"), {"WSH"})
    return [repr(s) for s in rep.switches], rep.mid_play_cuts, rep.dead_air_s, repr(rep.decisions)


@pytest.mark.skipif(not (ARCH / NIGHT[0]).exists(), reason="needs the pinned eval data (scripts/fetch_eval_data.py)")
def test_the_class_changes_no_decision_on_a_recorded_night(monkeypatch):
    """Nothing reads Event.stop_class yet (timing.dead_v2 off): a whole recorded NFL night decides exactly the same whether the
    class is read or every event carries "" as before the salvage."""
    from redzone.scoring.dead_left import stoppage_class
    seen = []

    def real(*a, **k):
        seen.append(stoppage_class(*a, **k))
        return seen[-1]

    with_class = _replay(monkeypatch, real)
    without = _replay(monkeypatch, lambda *a, **k: "")
    assert {"official", "team"} <= set(seen)                         # the class was really read on this night
    assert len(with_class[0]) > 100 and with_class == without
