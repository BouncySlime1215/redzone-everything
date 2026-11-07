"""[EXP-610] basketball flips on the scrub path need a clock-stopped dead ball; the sim's re-check uses the same rule."""
import time
from types import SimpleNamespace

import pytest

from redzone.config import Config
from redzone.decider import whistle
from redzone.engine import Engine
from redzone.lab import backtest as B
from redzone.models import League

# (wall, period, clock s left, type)
MAKE = [(100.0, 1, 600.0, "jump shot"), (112.0, 1, 588.0, "defensive rebound"), (130.0, 1, 570.0, "layup shot")]
FOUL = [(100.0, 1, 600.0, "shooting foul"), (130.0, 1, 600.0, "free throw - 1 of 2"), (150.0, 1, 600.0, "free throw - 2 of 2"),
        (160.0, 1, 592.0, "jump shot")]
TIMEOUT = [(100.0, 1, 500.0, "full timeout"), (230.0, 1, 500.0, "jump shot"), (240.0, 1, 490.0, "layup shot")]


def test_after_a_make_or_rebound_is_not_dead():
    for a in (101.0, 104.0, 113.0, 115.0):
        assert not whistle.clock_stopped(MAKE, a)


def test_foul_and_free_throws_are_dead():
    assert whistle.clock_stopped(FOUL, 105.0)
    assert whistle.clock_stopped(FOUL, 120.0)       # clock didn't move 100 -> 130
    assert not whistle.clock_stopped(FOUL, 158.0)   # band reaches the live jump shot


def test_long_timeout_is_dead_and_unknown_next_is_not():
    assert whistle.clock_stopped(TIMEOUT, 170.0)
    assert not whistle.clock_stopped(TIMEOUT[:1], 105.0)   # nothing after it known yet


def test_clock_secs():
    assert whistle.clock_secs("5:32") == 332.0 and whistle.clock_secs("42.1") == 42.1
    assert whistle.clock_secs(None) is None and whistle.clock_secs("x") is None


def _eng(scrub=True, league=League.NBA, events=FOUL, mu=0.0, hold_only=("nhl", "nba")):
    cfg = {"decider.entry.hold_only": list(hold_only), "decider.entry.whistle_band_s": 3}
    return SimpleNamespace(
        control=SimpleNamespace(_scrub_wanted=lambda t: scrub),
        merger=SimpleNamespace(games={"g": SimpleNamespace(league=league)}),
        cfg=SimpleNamespace(get=lambda k, d=None: cfg.get(k, d)),
        decider=SimpleNamespace(timelines={"g": SimpleNamespace(clock_events=list(events))}),
        delays=SimpleNamespace(get=lambda t: SimpleNamespace(mu=mu)))


def _d(kind="switch"):
    return SimpleNamespace(action=kind, target="g")


def test_live_gate_only_on_scrub_path_and_basketball():
    now = time.time()
    foul = [(now - 95 + w - 100, p, c, t) for w, p, c, t in FOUL]   # the foul aired 95 s ago in arena time
    make = [(now - 95 + w - 100, p, c, t) for w, p, c, t in MAKE]
    assert Engine._flip_lands_whistle(_eng(events=foul, mu=90.0), _d(), "youtubetv:g")
    assert not Engine._flip_lands_whistle(_eng(events=make, mu=90.0), _d(), "youtubetv:g")
    assert Engine._flip_lands_whistle(_eng(events=make, mu=90.0, scrub=False), _d(), "youtubetv:g")
    assert Engine._flip_lands_whistle(_eng(events=make, mu=90.0, league=League.NHL), _d(), "youtubetv:g")
    assert Engine._flip_lands_whistle(_eng(events=make, mu=90.0, hold_only=()), _d(), "youtubetv:g")
    assert Engine._flip_lands_whistle(_eng(events=make, mu=90.0), _d("replay"), "youtubetv:g")


class _Clock:
    t = 0.0


def test_sim_rule_and_90s_cost():
    s = B.HiddenStick("wait", 90.0, 1.0, Config(), _Clock(), 0, {}, entry="scrub_90_measured")
    assert s.recheck_rule == "dev_dead" and "recheck_rule" not in s.card()
    assert s.hold_of("youtubetv:x") == pytest.approx(37.6 + 7.5)
    with pytest.raises(ValueError):
        B.run([], Config(), set(), hidden_stick="wait", hs_recheck_rule="whistle")
    with pytest.raises(ValueError):
        B.run([], Config(), set(), hidden_stick="wait", hs_recheck=True, hs_recheck_rule="nope")
