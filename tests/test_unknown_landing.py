"""EXP-089: an entry never takes a landing the data can't call when its league is listed (cuts.plan_entry)."""
from redzone.decider.cuts import plan_entry
from tests.test_cut_timing import nfl_tracker


def test_unknown_landing_enters_by_default_and_waits_when_refused():
    dt = nfl_tracker()   # past the last logged play: the data can't call it
    assert plan_entry(dt, 40.0, 3.0, 1400.0, 5.0, can_rewind=False).ok
    p = plan_entry(dt, 40.0, 3.0, 1400.0, 5.0, can_rewind=False, unknown_ok=False)
    assert not p.ok and "waiting for a dead ball" in p.why


def test_a_known_dead_ball_still_enters():
    assert plan_entry(nfl_tracker(), 40.0, 3.0, 1050.0, 5.0, can_rewind=True, unknown_ok=False).ok


def test_ships_off():
    from redzone import config
    assert config.default("decider.entry.unknown_wait_leagues") == []
