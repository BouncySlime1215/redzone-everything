"""[EXP-029] decider.break_fill (off): during a long home break any live game beats the ad. North Star: dead air.
Owner: lane 2 (noggin logic). Pre-registered arm B; failed its bar, ships off."""
from tests.test_decider import dec  # noqa: F401


def test_off_by_default_keeps_the_visit_bar(dec):
    dec._break_len = lambda gid: 900.0
    assert dec._fill_knobs("a") is dec.core.k


def test_long_break_drops_the_visit_bar_short_break_keeps_it(dec):
    dec.cfg.update({"decider": {"break_fill": {"enabled": True}}}, record=False)
    dec._break_len = lambda gid: 180.0
    assert dec._fill_knobs("a").visit_min == 0.0
    dec._break_len = lambda gid: 60.0
    assert dec._fill_knobs("a") is dec.core.k
    dec._break_len = lambda gid: None
    assert dec._fill_knobs("a") is dec.core.k
    assert dec._fill_knobs(None) is dec.core.k
