"""Nov 5 MIA@GS (RC6.2): two ESPN corrections of plays from 32 minutes earlier arrived during
halftime. Timeline.observe read them as play resuming, so leave_gate shut the rest of halftime
("play is live"). With decider.stale_correction_guard on, an entry timed before the stoppage began
can't end it; a real play after the stoppage still does (Rule #1: 0 mid-play exits)."""
import pytest

from redzone.decider.screen import Delay, Timeline, leave_gate, leave_window
from redzone.models import GameState, League, Phase, Play, Stoppage, Team

MU, SIG = 42.0, 15.0   # the night's NBA TV estimate after the delay_doubt reset


def nba(stoppage, plays):
    return GameState(league=League.NBA, game_id="g", home=Team("GS", score=53), away=Team("MIA", score=53),
                     phase=Phase.LIVE, period=2, clock_s=0.0, stoppage=stoppage,
                     recent_plays=list(plays), last_play=plays[-1] if plays else None)


def P(pid, kind, w):
    return Play(pid, kind, kind, False, w)


def halftime(clock, guard):
    tl = Timeline(League.NBA, clock)
    tl.stale_guard = guard
    t0 = clock.t
    shot = P("1", "jump_shot", t0 - 10)
    tl.observe(nba(Stoppage.NONE, [shot]))
    clock.t += 9
    tl.observe(nba(Stoppage.NONE, [shot, P("2", "defensive_rebound", t0 - 2)]))
    clock.t += 9
    end = P("3", "end_period", t0)                      # End of the 2nd Quarter
    tl.observe(nba(Stoppage.HALFTIME, [end]))
    clock.t = t0 + MU + 2 * SIG + 30                    # the break is on screen
    tl.observe(nba(Stoppage.HALFTIME, [end]))
    return tl, t0, end


@pytest.mark.parametrize("guard", [False, True])
def test_halftime_opens_the_gate(clock, guard):
    tl, _, _ = halftime(clock, guard)
    g = leave_gate(tl, Delay(MU, SIG), clock.t, 8.0)
    assert g.safe and g.on_screen_break


def test_stale_correction_closed_halftime_without_the_guard(clock):
    """The bug as it ran on Nov 5 (guard off): pins what the flag changes."""
    tl, t0, end = halftime(clock, guard=False)
    clock.t += 360
    tl.observe(nba(Stoppage.HALFTIME, [end, P("old", "lost_ball_turnover", t0 - 1350)]))
    assert not leave_gate(tl, Delay(MU, SIG), clock.t, 8.0).safe


def test_stale_correction_keeps_halftime_open_with_the_guard(clock):
    tl, t0, end = halftime(clock, guard=True)
    for dt, pid, age in ((360, "old1", 1350), (51, "old2", 1370)):
        clock.t += dt
        tl.observe(nba(Stoppage.HALFTIME, [end, P(pid, "lost_ball_turnover", t0 - age)]))
        g = leave_window(tl, Delay(MU, SIG), clock.t, 8.0, 8.0)
        assert g.safe and g.on_screen_break, g.why


def test_real_play_after_the_stoppage_still_ends_it_with_the_guard(clock):
    """Rule #1: a play timed after the stoppage began is a resume, guard or not."""
    tl, t0, end = halftime(clock, guard=True)
    clock.t += 700
    tl.observe(nba(Stoppage.NONE, [end, P("tip", "jump_ball", clock.t - 5)]))
    assert not leave_gate(tl, Delay(MU, SIG), clock.t, 8.0).safe


def test_untimed_entry_after_the_stoppage_still_ends_it_with_the_guard(clock):
    """No wallclock: it could be new, so it still counts as a resume."""
    tl, t0, end = halftime(clock, guard=True)
    clock.t += 700
    tl.observe(nba(Stoppage.HALFTIME, [end, P("x", "jump_shot", None)]))
    assert not leave_gate(tl, Delay(MU, SIG), clock.t, 8.0).safe
