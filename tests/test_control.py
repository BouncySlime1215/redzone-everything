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
        if "mIsActiveSource" in cmd:   # the HOME flip's verification read
            on = getattr(type(self), "last_input_host", None) == self.host or getattr(self, "_active", False)
            return RunResult(True, "1" if on else "0", 0.01)
        if "Active Source> " in cmd:
            return RunResult(True, "0", 0.01)
        if "KEYCODE_HOME" in cmd:      # HOME makes a Fire TV announce itself as the active source
            if not self.cec_ok:
                return RunResult(False, "", 0.01)   # CEC off on this TV: HOME switches nothing
            self.log.append("onetouchplay(home)")
            cmd = "onetouchplay"
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


def test_fire_os_no_shell_implementation_is_explicit_noop(two, monkeypatch):
    c, _ = two
    fallbacks = []
    async def unsupported(command, timeout):
        return RunResult(True, 'No shell command implementation.', 0.01)
    async def verified_input(hdmi):
        fallbacks.append(hdmi)
        return True
    async def must_not_read_cec_ack():
        raise AssertionError('there was no CEC command to confirm')
    monkeypatch.setattr(c.hidden, 'shell', unsupported)
    monkeypatch.setattr(c.tv, 'set_input', verified_input)
    monkeypatch.setattr(c.tv, 'input_status', must_not_read_cec_ack)
    assert run(c._flip_to(c.hidden))
    assert fallbacks == ['HDMI2'] and c.hidden.host in c._cec_bad


def test_generic_cec_timeout_error_is_not_a_definite_noop(two, monkeypatch):
    c, _ = two
    fallbacks = []
    async def timed_out(command, timeout):
        return RunResult(False, 'Error: HDMI request timed out', 5)
    async def unreadable():
        return None
    async def must_not_retry(hdmi):
        fallbacks.append(hdmi)
        return True
    monkeypatch.setattr(c.hidden, 'shell', timed_out)
    monkeypatch.setattr(c.tv, 'input_status', unreadable)
    monkeypatch.setattr(c.tv, 'set_input', must_not_retry)
    assert not run(c._flip_to(c.hidden))
    assert not fallbacks


def test_cached_ready_flag_cannot_flip_buffering_standby(two):
    c, tuner = two
    c.hidden_target, c.hidden_ready = "youtubetv:fox", True
    c.hidden.remember("youtubetv:fox", run(c.hidden.playback("yttv.pkg")))
    c.hidden.playback_state = "buffering"
    hidden = c.hidden.host
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    # Not flipped to as-is: the stalled standby is tuned again first (a stick that lost the TV
    # input can pause its app; failing the switch there left the TV on the old game).
    assert out.path == "tuned" and tuner.calls == [(hidden, "youtubetv:fox")]


def test_no_readback_and_cec_flip_off_keeps_single_stick_mode(two):
    c, tuner = two
    c.tv.st_device = None
    c.cfg.update({"two_stick": {"cec_flip": False}})
    assert not c.two_stick and c.hidden is None
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok and out.path == "single"
    assert tuner.calls == [("a:5555", "youtubetv:espn")]
    assert not any("onetouchplay" in cmd for stick in c.sticks for cmd in stick.log)


def test_unknown_channel_is_a_clear_error(two):
    c, _ = two
    out = run(c.execute("youtubetv:nbatv", may_cut=lambda: True))
    assert not out.ok and "not set up" in out.detail


def test_cec_flip_cannot_enable_two_stick_mode_without_display_readback(two):
    """A stick claiming Active Source does not prove the TV accepted the input."""
    c, _ = two
    c.tv.st_device = None
    assert not c.two_stick and c.hidden is None


class HomeTrialStick(Stick):
    """Keep production shell invalidation; only the transport is replaced."""
    def __init__(self):
        super().__init__("trial:5555", os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": "yttv.pkg"}
        self.commands = []
        self.home_result = RunResult(True, "", 0.0)
        self.home_sent = False
        self.restore_sent = False
        self.after = "paused"
        self.updated = 5000

    async def _shell(self, command, timeout=8):
        self.commands.append(command)
        if "KEYCODE_HOME" in command:
            self.home_sent = True
            return self.home_result
        if command.startswith("am start"):
            self.restore_sent = True
        if "mIsActiveSource" in command:
            return RunResult(True, "1", 0.0)
        if "onetouchplay" in command:
            return RunResult(True, "No shell command implementation.", 0.0)
        return RunResult(True, "", 0.0)

    async def playback(self, package=None):
        state = self.after if self.home_sent else "playing"
        self.updated += 1
        return {"pkg": package, "state": state, "speed": 1 if state == "playing" else 0,
                "position": self.updated - 4000, "updated": self.updated,
                "read_ok": True, "read_started": ctl.time.monotonic()}

    async def foreground(self):
        return "yttv.pkg/.Player"


def prepare_home_trial(c):
    stick = HomeTrialStick()
    c.sticks = [stick]
    c.hdmi = {stick.host: "HDMI2"}
    stick.remember("youtubetv:espn", run(stick.playback("yttv.pkg")))
    return stick


def test_automatic_flip_never_uses_destructive_home(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    selected = []

    async def set_input(hdmi):
        selected.append(hdmi)
        return True

    monkeypatch.setattr(c.tv, "set_input", set_input)
    assert run(c._flip_to(stick))
    assert selected == ["HDMI2"]
    assert not stick.home_sent


def test_home_trial_does_not_trust_stick_active_source(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.after = "playing"
    assert not run(c._home_flip(stick))  # the selected TV continues reporting HDMI1
    assert stick.home_sent and stick.restore_sent
    assert stick.tuned is None


def test_home_trial_refuses_unknown_input_before_any_key(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)

    async def unknown():
        return None

    monkeypatch.setattr(c.tv, "input_status", unknown)
    assert not run(c._home_flip(stick))
    assert not stick.commands


def test_home_trial_rechecks_safe_exit_after_slow_readback(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    gap = {"open": True}

    async def closing_read():
        gap["open"] = False
        return "HDMI1"

    c._exit_check = lambda: gap["open"]
    monkeypatch.setattr(c.tv, "input_status", closing_read)
    assert not run(c._home_flip(stick))
    assert not stick.commands


def test_uncertain_home_is_not_followed_by_restore_or_fallback(two):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.home_result = RunResult(False, "timeout", 8)
    assert not run(c._home_flip(stick))
    assert stick.commands == ["input keyevent KEYCODE_HOME"]
    assert stick.tuned is None


def test_home_trial_requires_restored_transport_not_just_tv_input(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)

    async def input_status():
        return "HDMI2" if stick.home_sent else "HDMI1"

    async def no_wait(_):
        pass

    monkeypatch.setattr(c.tv, "input_status", input_status)
    monkeypatch.setattr(ctl.asyncio, "sleep", no_wait)
    assert not run(c._home_flip(stick))  # Home left the player paused
    assert stick.tuned is None


def test_home_trial_restores_memory_only_after_fresh_transport_and_tv_readback(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.after = "playing"

    async def input_status():
        return "HDMI2" if stick.home_sent else "HDMI1"

    monkeypatch.setattr(c.tv, "input_status", input_status)
    assert run(c._home_flip(stick))
    assert stick.tuned["target"] == "youtubetv:espn"
    assert stick.tuned["report"]["updated"] > 5002


def test_startup_input_uses_selected_tv_not_stick_claim(two, monkeypatch):
    c, _ = two
    c.sticks[0]._active = True

    async def selected_tv():
        return "HDMI2"

    monkeypatch.setattr(c.tv, "input_status", selected_tv)
    run(c.sync_active_from_display())
    assert c.active == 1 and not c.input_unknown
    assert not any("mIsActiveSource" in cmd for stick in c.sticks for cmd in stick.log)


def test_startup_unreadable_tv_does_not_guess_visible_stick(two, monkeypatch):
    c, _ = two
    c.sticks[0]._active = True

    async def unknown():
        return None

    monkeypatch.setattr(c.tv, "input_status", unknown)
    run(c.sync_active_from_display())
    assert c.input_unknown
    out = run(c.execute("youtubetv:espn"))
    assert not out.ok and "input is unknown" in out.detail
    assert not any("input keyevent" in cmd for stick in c.sticks for cmd in stick.log)


@pytest.mark.parametrize("state,speed", [("playing", 0.0), ("playing", 1.0)])
def test_home_trial_cannot_reuse_stationary_or_stale_playback(two, monkeypatch, state, speed):
    c, _ = two
    stick = prepare_home_trial(c)
    original = stick.playback
    before = run(original("yttv.pkg"))

    async def playback(package=None):
        if not stick.home_sent:
            return before
        # First case: fresh but zero-speed reload. Second: ready-looking stale session.
        return dict(before, state=state, speed=speed,
                    updated=before["updated"] + (1 if speed == 0 else 0),
                    read_started=ctl.time.monotonic())

    async def selected_tv():
        return "HDMI2" if stick.home_sent else "HDMI1"

    async def no_wait(_):
        pass

    monkeypatch.setattr(stick, "playback", playback)
    monkeypatch.setattr(c.tv, "input_status", selected_tv)
    monkeypatch.setattr(ctl.asyncio, "sleep", no_wait)
    assert not run(c._home_flip(stick))
    assert stick.tuned is None


def test_dry_run_start_preserves_unknown_input_until_live_readback(two, monkeypatch):
    c, _ = two
    c.cfg.update({"mode": {"dry_run": True}})

    async def adb_available():
        return True

    monkeypatch.setattr(ctl, "adb_available", adb_available)
    monkeypatch.setattr(c, "load_devices", lambda: None)
    run(c.start())
    assert c.input_unknown
    c.cfg.update({"mode": {"dry_run": False}})
    for stick in c.sticks:
        stick.last_ok = ctl.time.time()
    run(c.keepalive())
    assert not c.input_unknown and c.active == 0



def test_unknown_input_blocks_preload_and_sunday_ticket_scan(two):
    c, tuner = two
    c.input_unknown = True
    assert not c.preload("youtubetv:espn")
    assert run(c.scan_sunday_ticket(["AAAAAAAAAAA"], [])) == {}
    assert tuner.calls == []
    assert not c.sticks[1].log
