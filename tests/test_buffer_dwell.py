"""[EXP-213, RC8 switch count] decider.dwell: a minimum stay before an OPTIONAL switch, and a score floor on
halftime visits. Both only ever turn a switch into a stay (Rule #1 untouched) and ship off."""
from redzone.decider.decider import Decider, Decision, State
from redzone.decider.screen import DelayBook
from tests.test_decider import cands


def _dec(cfg, clock, **dwell):
    for k, v in dwell.items():
        cfg.data.setdefault("decider", {}).setdefault("dwell", {})[k] = v
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=3.0, clock=clock), clock=clock)
    d.current, d.since = "a", clock.t
    return d


def _sw(clock, kind):
    return Decision(clock.t, "switch", "b", "go", State.WATCHING, kind=kind)


def test_off_by_default_changes_nothing(cfg, clock):
    dec = _dec(cfg, clock)
    d = _sw(clock, "break_visit")
    assert dec._dwell(d, cands(a=50, b=60), clock.t, (None, None)) is d


def test_an_optional_switch_waits_out_the_dwell_then_goes(cfg, clock):
    dec = _dec(cfg, clock, min_s=30)
    clock.t += 10
    out = dec._dwell(_sw(clock, "break_visit"), cands(a=50, b=60), clock.t, (None, None))
    assert out.action == "stay" and out.target == "a" and out.want == "b"
    clock.t += 25
    d = _sw(clock, "break_visit")
    assert dec._dwell(d, cands(a=50, b=60), clock.t, (None, None)) is d


def test_crunch_ended_home_return_and_user_never_wait(cfg, clock):
    dec = _dec(cfg, clock, min_s=300)
    for kind in ("crunch", "ended", "home", "return", "user", "start", "must_see"):
        d = _sw(clock, kind)
        assert dec._dwell(d, cands(a=50, b=60), clock.t, (None, None)) is d, kind


def test_a_held_visit_restores_the_way_home(cfg, clock):
    dec = _dec(cfg, clock, min_s=30)
    dec.home_break_visit, dec.home_return_at = "x", 123.0      # set by the visit being proposed
    dec._dwell(_sw(clock, "break_visit"), cands(a=50, b=60), clock.t, (None, None))
    assert (dec.home_break_visit, dec.home_return_at) == (None, None)


def test_default_config_ships_both_off(cfg):
    assert cfg.get("decider.dwell.min_s") == 0 and cfg.get("decider.dwell.long_visit_min_score") == 0
