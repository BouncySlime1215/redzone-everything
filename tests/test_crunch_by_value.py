"""[crunch by value, TREE-6 Mac half] North Star: crunch live + big plays live (Nick, Nov 4 4:15 PM).

Crunch vs crunch is decided by value with league order and your teams as priors, not walls. Nick's case:
we're on the Bills with 3 minutes left; the Bears start a 1-minute drive worth clearly more. Jump to the
Bears at the Bills' safe moment, and come back to the Bills when that drive is done (the Bills' plays
missed meanwhile stay catch-up candidates). Flag decider.crunch.by_value, off by default."""
from redzone.models import League
from redzone.scoring.engine import Scored
from tests.test_decider import calibrate, cands, csc, dec, on_break_now, start_on  # noqa: F401 (dec: fixture)


def _on_bills(dec, clock, cfg):
    cfg.data.setdefault("decider", {}).setdefault("crunch", {})["by_value"] = True
    for g in ("bills", "bears"):
        calibrate(dec, f"youtubetv:{g}")
    dec.timeline("bears", League.NFL)
    start_on(dec, clock, "bills", {})
    on_break_now(dec, clock, "bills")     # the Bills' timeout: a safe moment to leave


def test_bills_to_the_bears_drive_and_back(dec, clock, cfg):
    _on_bills(dec, clock, cfg)
    d = dec.decide(cands(bills=csc("bills", 60, is_break=True), bears=csc("bears", 85)))
    assert d.action == "switch" and d.target == "bears" and d.kind == "crunch", d.reason
    assert dec.overlap_why["value"] == 1
    # The drive ends (the Bears' moment is spent): back to the Bills at the Bears' dead ball
    clock.t += 100
    on_break_now(dec, clock, "bears")
    d = dec.decide(cands(bills=csc("bills", 60), bears=csc("bears", 30, is_break=True)))
    assert d.action == "switch" and d.target == "bills", d.reason


def test_a_small_edge_is_not_worth_the_jump(dec, clock, cfg):
    """Within decider.crunch.value_margin, only the old stoppage hop applies (here the Bears are also
    stopped, so we stay)."""
    _on_bills(dec, clock, cfg)
    d = dec.decide(cands(bills=csc("bills", 60, is_break=True), bears=csc("bears", 65, is_break=True)))
    assert not (d.action == "switch" and d.target == "bears"), d.reason


def test_league_and_your_team_are_priors_not_walls(dec, clock, cfg):
    _on_bills(dec, clock, cfg)
    nba = Scored("bears", League.NBA, 99.0, 99.0, 99.0, 1.0, 0.0, "reason", [], False, None, None, 1.0, crunch=True)
    # an NBA crunch worth far more still wins over a dull NFL crunch...
    assert dec._crunch_value(nba) > dec._crunch_value(csc("bills", 40)) + 10
    # ...but your team's crunch carries its prior
    assert dec._crunch_value(csc("bills", 40, personal=22.5)) > dec._crunch_value(csc("x", 60))


def test_off_by_default(dec, clock, cfg):
    assert not cfg.get("decider.crunch.by_value", False)
    for g in ("bills", "bears"):
        calibrate(dec, f"youtubetv:{g}")
    dec.timeline("bears", League.NFL)
    start_on(dec, clock, "bills", {})
    on_break_now(dec, clock, "bills")
    d = dec.decide(cands(bills=csc("bills", 60, is_break=True), bears=csc("bears", 85, is_break=True)))
    assert dec.overlap_why["value"] == 0, d.reason
