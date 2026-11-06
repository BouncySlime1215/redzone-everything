"""[EXP-212, autopsy Nov 5] NBA breaks with the measured feed lag: halftime is a break, a quarter break is one
only with the measured floor, and a play after the restart closes it (0 mid-play entries).

Numbers from Thu Nov 5 (RC6.2, MIA@GS on NBA TV): delay 42 +- 15 s, feed lag ~40 s. With those, `covered` is
never true for basketball (42 - 2*15 - 24 silent-live - 40 lag < 0), so only a floor can open the gate."""
from redzone.decider.decider import Decider
from redzone.decider.screen import Delay, DelayBook, Event, Timeline, leave_gate
from redzone.models import League, Stoppage

NIGHT = Delay(42.0, 15.0)   # NBA TV, Nov 5 (supervisor delay_doubt kept resetting it to about this)
SIG_LIMIT = 8.0             # decider.uncalibrated_sigma_s


def _nba(clock, kind: Stoppage, floors=None) -> Timeline:
    tl = Timeline(League.NBA, clock)
    tl.lag_prior = 40.0
    tl.fresh_at = clock.t
    tl.events.append(Event(clock.t - 2, "play", "jumper", pid="p1"))
    tl.events.append(Event(clock.t, "stop_start", kind.value))
    if floors:
        tl.extra_floors = floors
    return tl


def test_halftime_on_screen_with_a_wide_delay_may_be_left(clock):
    tl = _nba(clock, Stoppage.HALFTIME)
    T = clock.t
    assert not leave_gate(tl, NIGHT, T + 30, SIG_LIMIT).safe              # the last play may still be airing
    g = leave_gate(tl, NIGHT, T + 42 + 30 + 1, SIG_LIMIT)
    assert g.safe and g.on_screen_break                                    # halftime is on screen
    assert leave_gate(tl, NIGHT, T + 600, SIG_LIMIT).safe                  # still halftime 10 min in


def test_halftime_closes_the_moment_a_restart_play_arrives(clock):
    """Rule #1: the first play of the 3rd quarter in the data ends the break for the screen too."""
    tl = _nba(clock, Stoppage.HALFTIME)
    T = clock.t
    assert leave_gate(tl, NIGHT, T + 300, SIG_LIMIT).safe
    tl.events.append(Event(T + 290, "play", "tip-off", pid="p2"))         # restart at T+290 in real time
    tl.fresh_at = T + 300
    # The restart reaches the screen at T+290+42, and the gate shuts 2 sigma before that.
    assert not leave_gate(tl, NIGHT, T + 300, SIG_LIMIT).safe


def test_quarter_break_without_a_floor_reads_live_and_with_it_opens(clock):
    T = clock.t
    off = _nba(clock, Stoppage.END_PERIOD)
    assert leave_gate(off, NIGHT, T + 80, SIG_LIMIT).why == "play is live (only leave at a stoppage)"
    on = _nba(clock, Stoppage.END_PERIOD, {Stoppage.END_PERIOD: 120.0})
    g = leave_gate(on, NIGHT, T + 80, SIG_LIMIT)
    assert g.safe and g.on_screen_break
    # floor 120 + mu 42 - margin 30 - tune 6 = 126 s after the stamp: no later, whatever the delay says
    assert leave_gate(on, NIGHT, T + 126, SIG_LIMIT).safe
    assert not leave_gate(on, NIGHT, T + 127, SIG_LIMIT).safe


def test_a_floor_never_opens_a_timeout_or_live_play(clock):
    """The floor is for its own stoppage kind only: NBA timeout stamps are often the restart (docs/timing.md A3)."""
    T = clock.t
    tl = _nba(clock, Stoppage.TIMEOUT, {Stoppage.END_PERIOD: 120.0})
    assert not leave_gate(tl, NIGHT, T + 80, SIG_LIMIT).safe


def test_decider_sets_the_floor_from_config_per_league(cfg, clock):
    cfg.data.setdefault("league_overrides", {}).setdefault("nba", {})["decider.leave.break_floor_s"] = {
        "end_period": 120}
    dec = Decider(cfg, DelayBook({"youtubetv": 42.0}, sigma_default=15.0, clock=clock), clock=clock)
    assert dec.timeline("g1", League.NBA).extra_floors == {Stoppage.END_PERIOD: 120.0}
    assert dec.timeline("g2", League.NHL).extra_floors == {}


def test_default_config_ships_the_floor_off(cfg, clock):
    dec = Decider(cfg, DelayBook({"youtubetv": 42.0}, sigma_default=15.0, clock=clock), clock=clock)
    assert dec.timeline("g1", League.NBA).extra_floors == {}
