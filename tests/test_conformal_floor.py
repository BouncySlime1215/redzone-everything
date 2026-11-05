"""EXP-024 conformal floor and EXP-089 unknown landings (redzone/decider/cuts.py plan_entry)."""
from redzone.decider.cuts import landing_slack, plan_entry
from tests.test_cut_timing import nfl_tracker


def test_slack_is_the_widest_delay_error_that_still_lands_dead():
    dt = nfl_tracker()   # huddle 1003..1030
    assert abs(landing_slack(dt, 1015, 1015) - 12.0) <= 0.25
    assert landing_slack(dt, 1035, 1036) == 0.0   # mid-snap: no slack


def test_plans_carry_their_slack():
    p = plan_entry(nfl_tracker(), 40.0, 3.0, 1050.0, 5.0, can_rewind=True)
    assert p.ok and p.rewind_s == 0 and abs(p.slack - 12.0) <= 0.25


def test_floor_refuses_a_landing_with_less_slack():
    p = plan_entry(nfl_tracker(), 40.0, 3.0, 1050.0, 5.0, can_rewind=True, floor=13.0)
    assert not p.ok or p.slack >= 13.0 - 0.25


def test_unknown_landing_enters_by_default_and_waits_when_refused():
    dt = nfl_tracker()   # past the last logged play: the data can't call it
    assert plan_entry(dt, 40.0, 3.0, 1400.0, 5.0, can_rewind=False).ok
    p = plan_entry(dt, 40.0, 3.0, 1400.0, 5.0, can_rewind=False, unknown_ok=False)
    assert not p.ok and "waiting for a dead ball" in p.why
    assert not plan_entry(dt, 40.0, 3.0, 1400.0, 5.0, can_rewind=False, floor=1.0).ok


def test_floor_and_unknown_flags_ship_off():
    from redzone import config
    assert config.default("decider.entry.conformal.enabled") is False
    assert config.default("decider.entry.unknown_wait_leagues") == []
    assert all(v == 0 for v in config.default("decider.entry.conformal.tau").values())
