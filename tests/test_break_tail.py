"""[EXP-728/736] break tail: the restart table lookup and the flag's default."""
from redzone import config
from redzone.decider import break_tail as bt


def test_lengths_by_network_then_league_row():
    assert bt.network_of("youtubetv:nbatv") == "NBATV" and bt.network_of(None) == ""
    assert bt.length("nba", "end_period", "youtubetv:nbatv", "q25") == 164.0     # the network's own row (n >= 8)
    assert bt.length("nba", "end_period", "youtubetv:nope", "q25") == 157.0      # else the league x kind row
    assert bt.length("nba", "nonsense", "youtubetv:espn", "q25") is None
    assert bt.WINDOW_KIND["timeout_long"] == "timeout" and "timeout_short" not in bt.WINDOW_KIND and "shooting_foul" not in bt.WINDOW_KIND


def test_ships_off():
    assert config.default("decider.entry.break_tail") == []
    assert config.default("decider.entry.break_tail_s") == 10 and config.default("decider.entry.break_tail_q") == "q25"


def test_exp803_arms_ship_off():
    """[EXP-803 A1/A3] the arms' flags ship off; the A3 length is an early-side prior (Data F's cdn q10)."""
    import dataclasses

    from redzone.decider.decider import Decision
    assert config.default("decider.entry.break_tail_defer") is False
    assert config.default("decider.entry.break_tail_tv_split") is False
    assert 150 <= config.default("decider.entry.break_tail_tv_timeout_s") <= 180
    assert {f.name: f.default for f in dataclasses.fields(Decision)}["defer_s"] == 0.0


def test_defer_keeps_the_old_game_on_screen_until_the_cut():
    """[EXP-803 A1, integrator review] during a deferred cut the TV still shows the OLD game for every line:
    a big play there is on screen (live), the destination's break is not (no dead air credited to it)."""
    from types import SimpleNamespace

    from redzone.grading import SwitchLog
    from redzone.lab import backtest as bt_

    t, tune = 1000.0, 5.0
    watching = [(0.0, "old")]
    switches = [SwitchLog(t, "old", "new", "ended", "x", 0.0)]
    watching.append((t, "new"))
    bt_._apply_defer(SimpleNamespace(defer_s=6.0), switches, watching, 0, step=10.0)
    assert watching[-1] == (t + 6.0, "new")
    assert switches[-1].leave_dt == 6.0 and switches[-1].arrive_dt == 6.0
    assert bt_._on_screen_game(watching, t + 5.5, tune) == "old"     # the old game's play during the defer: seen
    assert bt_._on_screen_game(watching, t + 8.0, tune) is None      # the tune after the cut: black, not "new"
    assert bt_._on_screen_game(watching, t + 11.5, tune) == "new"
    # never more than one step, so the next decision sorts after the cut
    w2 = [(0.0, "old"), (t, "new")]
    s2 = [SwitchLog(t, "old", "new", "ended", "x", 0.0)]
    bt_._apply_defer(SimpleNamespace(defer_s=25.0), s2, w2, 0, step=10.0)
    assert w2[-1][0] == t + 10.0
    # no defer: untouched
    w3 = [(t, "new")]
    s3 = [SwitchLog(t, "old", "new", "ended", "x", 0.0)]
    bt_._apply_defer(SimpleNamespace(defer_s=0.0), s3, w3, 0, step=10.0)
    assert w3 == [(t, "new")] and s3[0].leave_dt == 0.0


def test_exp739_start_timed_ships_off():
    assert config.default("decider.entry.start_timed") is False


def test_lag_floor_off_by_default_and_narrows_coverage():
    """[EXP-211] decider.entry.lag_floor_s ships 0 (off); when set, an open stoppage counts as covered
    only as far as a feed at least that slow would show the restart."""
    from redzone.config import default
    from redzone.decider.cuts import DeadTracker
    from redzone.models import League
    assert default("decider.entry.lag_floor_s") == 0
    t = DeadTracker(League.NHL)
    t.seen(1000.0, 10.0)
    base = t._covered()
    t.lag_floor = 91.0
    assert t._covered() == base - 81.0
