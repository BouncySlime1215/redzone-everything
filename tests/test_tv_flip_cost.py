"""[switch timing] The decider plans a TV input change (SmartThings, ~2 s) with that path's own
numbers, not HOME's (2-6 s and a relaunch)."""
import statistics

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap


class _TV:
    def __init__(self, ready):
        self._ready = ready

    def ready(self):
        return self._ready

    def paths(self):
        return ["smartthings"] if self._ready else []


def _ctl(cfg, tmp_path, tv_ready):
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.tv = _TV(tv_ready)
    return c


def test_tv_path_plans_with_the_measured_smartthings_prior(cfg, tmp_path):
    c = _ctl(cfg, tmp_path, True)
    assert c.flip_path() == "tv_flip"
    assert c.flip_estimate() == pytest.approx(1.89 + 1.0)        # median + HDMI settle, before any of our own
    assert c._flip_upper() == pytest.approx(2.02 + 1.0)  # the measured max until we've timed 5


def test_tv_path_ignores_home_flips(cfg, tmp_path):
    c = _ctl(cfg, tmp_path, True)
    for s in (5.4, 6.0, 5.1, 4.2):
        c.latency.record("flip", s)
        c.latency.record("home_flip", s)
    assert c.flip_estimate() == pytest.approx(1.89 + 1.0)
    for s in (1.8, 1.9, 2.0, 2.1, 2.4):
        c.latency.record("tv_flip", s)
    assert c.flip_estimate() == 2.0 + 1.0
    assert c._flip_upper() == 2.4 + 1.0             # five of our own: their p90 takes over


def test_home_path_unchanged_and_reads_old_saves(cfg, tmp_path):
    c = _ctl(cfg, tmp_path, False)
    assert c.flip_path() == "home_flip"
    assert c.flip_estimate() == c._default_cost("hdmi_flip")
    for s in (2.2, 2.8, 3.0):
        c.latency.record("flip", s)                 # a save from before home_flip existed
    assert c.flip_estimate() == 2.8 + 1.0
    c.latency.record("tv_flip", 1.0)
    assert c.flip_estimate() == 2.8 + 1.0


def test_a_broken_tv_object_falls_back_to_home(cfg, tmp_path):
    c = _ctl(cfg, tmp_path, True)

    class Broken:
        def ready(self):
            raise RuntimeError("boom")
    c.tv = Broken()
    assert c.flip_path() == "home_flip"


def test_deadline_cost_uses_the_tv_bound_for_a_preloaded_target(cfg, tmp_path, monkeypatch):
    c = _ctl(cfg, tmp_path, True)
    monkeypatch.setattr(type(c), "two_stick", property(lambda self: True))
    monkeypatch.setattr(c, "_live_on_hidden", lambda t: True)
    monkeypatch.setattr(c, "_tile_path_cost", lambda t: None)
    monkeypatch.setattr(type(c), "prime_mv", property(lambda self: None))
    monkeypatch.setattr(type(c), "hidden", property(lambda self: None))
    assert c.deadline_cost("youtubetv:espn") == pytest.approx(2.02 + 1.0)


def test_tune_cost_is_per_stick_model_once_measured(cfg, tmp_path, monkeypatch):
    """Live lab Nov 4: hidden-stick tune to PLAYING p50 2.33 s on the 4K Plus, 3.74 s on the 4K 2023."""
    from types import SimpleNamespace
    c = _ctl(cfg, tmp_path, True)
    plus, slow = SimpleNamespace(model="AFTMA08C15", host="a"), SimpleNamespace(model="AFTKM", host="b")
    for _ in range(4):
        c.latency.record("youtubetv", 2.3)
        c.latency.record("youtubetv@AFTMA08C15", 2.3)
        c.latency.record("youtubetv", 3.8)
        c.latency.record("youtubetv@AFTKM", 3.8)
    assert c.tune_estimate("youtubetv:espn", plus) == 2.3 and c.tune_estimate("youtubetv:espn", slow) == 3.8
    assert c.tune_estimate("youtubetv:espn") == statistics.median([2.3] * 4 + [3.8] * 4)
    assert c.tune_estimate("youtubetv:espn", SimpleNamespace(model="AFTKA")) == c.tune_estimate("youtubetv:espn")
    # deadline_cost: the tuning stick's own p90 only after PER_STICK_DEADLINE_MIN tunes (rule #1)
    monkeypatch.setattr(type(c), "two_stick", property(lambda self: False))
    monkeypatch.setattr(type(c), "main", property(lambda self: slow))
    before = c.deadline_cost("youtubetv:espn")
    for _ in range(ctl.PER_STICK_DEADLINE_MIN):
        c.latency.record("youtubetv@AFTKM", 9.0)
    assert c.deadline_cost("youtubetv:espn") == 9.0 > before
