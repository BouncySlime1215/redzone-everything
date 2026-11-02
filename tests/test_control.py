"""The TV machine with fake sticks: single stick, two-stick hot standby, safe flips, fallbacks."""
import asyncio

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult


class FakeStick(Stick):
    def __init__(self, host, cec_ok=True):
        super().__init__(host, name=host, os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": "yttv.pkg", "prime": "prime.pkg"}
        self.log = []
        self.cec_ok = cec_ok

    async def shell(self, cmd, timeout=8.0):
        self.log.append(cmd)
        if "onetouchplay" in cmd:
            return RunResult(self.cec_ok, "" if self.cec_ok else "Error: unknown command", 0.01)
        return RunResult(True, "", 0.01)

    async def key(self, *codes):
        self.log.extend(codes)


class FakeTuner:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    async def tune(self, stick, ref, target):
        self.calls.append((stick.host, target))
        return TuneResult(self.ok, target, 0.01, ["playing"], "" if self.ok else "video never started")


@pytest.fixture
def two(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    return c, tuner


def run(coro):
    return asyncio.run(coro)


def test_dry_run_never_touches_sticks(cfg, tmp_path):
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = [FakeStick("a:5555")]
    out = run(c.execute("youtubetv:espn"))
    assert out.ok and out.dry_run and c.sticks[0].log == []


def test_two_stick_tunes_hidden_then_flips(two):
    c, tuner = two
    assert c.two_stick and c.main.host == "a:5555" and c.hidden.host == "b:5555"
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok and "flipped to HDMI2" in out.detail
    assert tuner.calls == [("b:5555", "youtubetv:espn")]      # the hidden stick did the work
    assert c.main.host == "b:5555"                              # roles swapped
    assert any("onetouchplay" in x for x in c.sticks[1].log)


def test_preloaded_standby_flips_instantly(two):
    c, tuner = two
    c.hidden_target, c.hidden_ready = "youtubetv:fox", True
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert out.ok and tuner.calls == []                         # no tuning needed


def test_flip_waits_for_safe_moment(two, monkeypatch):
    c, _ = two
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 1.0)
    calls = {"n": 0}

    def may_cut():
        calls["n"] += 1
        return calls["n"] > 2   # unsafe twice, then safe
    out = run(c.execute("youtubetv:espn", may_cut=may_cut))
    assert out.ok and calls["n"] >= 3


def test_never_flips_mid_play(two, monkeypatch):
    c, _ = two
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 1.0)
    out = run(c.execute("youtubetv:espn", may_cut=lambda: False))
    assert not out.ok and "no safe moment" in out.detail
    assert c.main.host == "a:5555"                              # still on the original stick


def test_failed_flip_falls_back_to_single_stick(two, monkeypatch):
    c, tuner = two
    for s in c.sticks:
        s.cec_ok = False

    async def no_tv(hdmi):
        return False
    monkeypatch.setattr(c.tv, "set_input", no_tv)
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok                                               # single-stick tune still happened
    assert not c.two_stick and "Two-stick mode off" in c.note
    assert tuner.calls[-1] == ("a:5555", "youtubetv:espn")


def test_unknown_channel_is_a_clear_error(two):
    c, _ = two
    out = run(c.execute("youtubetv:nbatv", may_cut=lambda: True))
    assert not out.ok and "not set up" in out.detail
