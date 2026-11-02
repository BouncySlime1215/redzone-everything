"""The TV machine: confirmed input changes, safe exits, and uncertain-command recovery."""
import asyncio

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult


class FakeStick(Stick):
    def __init__(self, host, cec_ok=True, display=None, hdmi=None):
        super().__init__(host, name=host, os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": "yttv.pkg", "prime": "prime.pkg"}
        self.log = []
        self.cec_ok = cec_ok
        self.display, self.hdmi = display, hdmi
        self.playback_state = "playing"

    async def shell(self, cmd, timeout=8.0):
        self.log.append(cmd)
        if "onetouchplay" in cmd:
            if self.cec_ok and self.display is not None:
                self.display["input"] = self.hdmi
            return RunResult(self.cec_ok, "" if self.cec_ok else "Error: unknown command", 0.01)
        if "mWakefulness" in cmd:
            return RunResult(True, "mWakefulness=Awake", 0.01)
        return RunResult(True, "", 0.01)

    async def key(self, *codes):
        self.log.extend(codes)
        return RunResult(True, "", 0.01)

    async def playback(self, package=None):
        return {"state": self.playback_state, "pkg": package, "position": 1000, "updated": 1000}


class FakeTuner:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    async def tune(self, stick, ref, target, may_cut=None):
        if may_cut is not None and not may_cut():
            return TuneResult(False, target, 0.0, [], "safe switching window closed", screen_changed=False)
        self.calls.append((stick.host, target))
        if self.ok:
            stick.remember(target, await stick.playback(stick.packages[target.split(":")[0]]))
        return TuneResult(self.ok, target, 0.01, ["playing"], "" if self.ok else "video never started")


@pytest.fixture
def two(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    display = {"input": "HDMI1"}
    c.sticks = [FakeStick("a:5555", display=display, hdmi="HDMI1"),
                FakeStick("b:5555", display=display, hdmi="HDMI2")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "selected-test-display"

    async def input_status():
        return display["input"]
    monkeypatch.setattr(c.tv, "input_status", input_status)
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
    c.hidden.remember("youtubetv:fox", run(c.hidden.playback("yttv.pkg")))
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


def test_unconfirmed_flip_stops_without_single_stick_fallback(two, monkeypatch):
    c, tuner = two
    for s in c.sticks:
        s.cec_ok = False

    async def no_tv(hdmi):
        return False
    monkeypatch.setattr(c.tv, "set_input", no_tv)
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and c.input_unknown
    assert not c.two_stick and "Two-stick mode off" in c.note
    assert tuner.calls == [("b:5555", "youtubetv:espn")]
    assert c.main.host == "a:5555" and c.on_target is None


def test_cec_ack_without_input_readback_never_claims_success_or_retries(two, monkeypatch):
    c, tuner = two
    fallback_calls = []

    async def unreadable():
        return None

    async def no_retry(hdmi):
        fallback_calls.append(hdmi)
        return True

    monkeypatch.setattr(c.tv, "input_status", unreadable)
    monkeypatch.setattr(c.tv, "set_input", no_retry)
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and out.confirmed_at is None
    assert c.input_unknown and c.on_target is None and not fallback_calls
    assert tuner.calls == [("b:5555", "youtubetv:espn")]
    # A later command also cannot tune an assumed visible stick while input is unknown.
    again = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert not again.ok and "input is unknown" in again.detail
    assert len(tuner.calls) == 1


def test_cached_ready_flag_cannot_flip_buffering_standby(two):
    c, tuner = two
    c.hidden_target, c.hidden_ready = "youtubetv:fox", True
    c.hidden.remember("youtubetv:fox", run(c.hidden.playback("yttv.pkg")))
    c.hidden.playback_state = "buffering"
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert not out.ok and out.screen_unchanged
    assert c.main.host == "a:5555" and not tuner.calls
    assert not any("onetouchplay" in cmd for stick in c.sticks for cmd in stick.log)


def test_no_readback_capable_display_keeps_single_stick_mode(two):
    c, tuner = two
    c.tv.st_device = None
    assert not c.two_stick and c.hidden is None
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok and out.path == "single"
    assert tuner.calls == [("a:5555", "youtubetv:espn")]
    assert not any("onetouchplay" in cmd for stick in c.sticks for cmd in stick.log)


def test_unknown_channel_is_a_clear_error(two):
    c, _ = two
    out = run(c.execute("youtubetv:nbatv", may_cut=lambda: True))
    assert not out.ok and "not set up" in out.detail
