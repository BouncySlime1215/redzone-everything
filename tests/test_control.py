"""The TV machine: confirmed input changes, safe exits, and uncertain-command recovery."""
import asyncio
import time

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


def test_live_mode_with_no_stick_is_not_a_success(cfg, tmp_path):
    """[Nov 7 overseer] The Mac awake in live mode with every stick offline: the switch is refused, not
    logged as a 0 s success, and on_target stays where the TV really is."""
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = []
    c.on_target = "youtubetv:espn"
    out = run(c.execute("youtubetv:fs1"))
    assert not out.ok and not out.dry_run and out.screen_unchanged and out.detail == ctl.NO_STICK
    assert c.on_target == "youtubetv:espn"


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


def follow_display(stick):
    """This stick claims HDMI-CEC active source exactly when the TV shows its input."""
    original = stick.shell

    async def shell(cmd, timeout=8.0):
        if "mIsActiveSource" in cmd:
            on = stick.display is not None and stick.display["input"] == stick.hdmi
            stick.log.append(cmd)
            return RunResult(True, "1" if on else "0", 0.01)
        return await original(cmd, timeout)
    stick.shell = shell


def no_tv_network(c, monkeypatch):
    """The TV isn't reachable on the network: any SmartThings/remote call is a test failure."""
    async def unreachable(*_):
        raise AssertionError("the TV is not reachable over the network")
    monkeypatch.setattr(c.tv, "input_status", unreachable)
    monkeypatch.setattr(c.tv, "set_input", unreachable)


def homes(stick):
    return [cmd for cmd in stick.log if "KEYCODE_HOME" in cmd]


def test_cec_flip_runs_two_stick_mode_without_tv_network(two, monkeypatch):
    """No SmartThings: the HOME flip, verified on the stick's own CEC state, is the readback."""
    c, tuner = two
    c.tv.st_device = None
    no_tv_network(c, monkeypatch)
    for s in c.sticks:
        follow_display(s)
    assert c.two_stick and c.hidden.host == "b:5555"
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert out.ok and out.path == "tuned" and out.flip_s is not None
    assert c.main.host == "b:5555" and tuner.calls == [("b:5555", "youtubetv:espn")]
    a, b = c.sticks
    assert homes(b) and any("mIsActiveSource" in cmd for cmd in b.log)
    assert not homes(a)                    # the stick that went hidden never gets HOME


def test_unconfirmed_home_flip_is_not_success(two, monkeypatch):
    """HOME sent but the stick never claims the TV: no success without a readback."""
    c, _ = two
    c.tv.st_device = None
    monkeypatch.setattr(ctl, "FLIP_DEADLINE_S", 0.2)
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    for s in c.sticks:
        s.display = None                   # HOME never moves the TV
        follow_display(s)

    async def no_tv(*_):
        return False
    monkeypatch.setattr(c.tv, "set_input", no_tv)
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and c.input_unknown and c.main.host == "a:5555"
    assert not homes(c.sticks[0])


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
        self.active_source = "1"
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
            return RunResult(True, self.active_source, 0.0)
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


def test_home_flip_is_verified_by_the_sticks_active_source(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    no_tv_network(c, monkeypatch)
    assert run(c._home_flip(stick, "youtubetv:espn"))
    assert stick.home_sent and stick.restore_sent      # the backgrounded app is relaunched
    assert c._flip_s is not None


def test_home_flip_without_active_source_claim_fails_but_restores_app(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.active_source = "0"
    no_tv_network(c, monkeypatch)
    monkeypatch.setattr(ctl, "FLIP_DEADLINE_S", 0.2)
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    assert not run(c._home_flip(stick, "youtubetv:espn"))
    assert stick.home_sent and stick.restore_sent


def test_flip_refuses_before_any_key_when_safe_window_closed(two):
    c, _ = two
    stick = prepare_home_trial(c)
    c._exit_check = lambda: False
    assert not run(c._flip_to(stick))
    assert not stick.commands


def test_uncertain_home_is_not_followed_by_restore_or_fallback(two):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.home_result = RunResult(False, "timeout", 8)
    assert not run(c._home_flip(stick))
    assert stick.commands == ["input keyevent KEYCODE_HOME"]
    assert stick.tuned is None


def test_paused_player_after_home_gets_play_then_relink_not_trust(two, monkeypatch):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.home_sent = True                 # HOME left the player paused
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.0)
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner(ok=False))
    assert run(c._resume_playback(stick, "youtubetv:espn", 0.0)) is None
    assert "input keyevent KEYCODE_MEDIA_PLAY" in stick.commands


def test_resume_remembers_a_fresh_moving_report(two):
    c, _ = two
    stick = prepare_home_trial(c)
    stick.home_sent, stick.after = True, "playing"
    assert run(c._resume_playback(stick, "youtubetv:espn", 0.0)) == "auto"
    assert stick.tuned["target"] == "youtubetv:espn"
    assert stick.tuned["report"]["updated"] > 5001


def test_resume_does_not_count_a_stationary_player(two):
    c, _ = two
    stick = prepare_home_trial(c)
    moving = run(stick.playback("yttv.pkg"))
    assert c._plays_target(stick, "youtubetv:espn", moving, "yttv.pkg")
    # Fire OS reports PLAYING with speed 0 while a player reloads.
    assert not c._plays_target(stick, "youtubetv:espn", dict(moving, speed=0.0), "yttv.pkg")


def test_startup_input_comes_from_the_sticks_cec_claim(two, monkeypatch):
    c, _ = two
    no_tv_network(c, monkeypatch)
    c.sticks[1]._active = True
    assert run(c.sync_active_from_cec())
    assert c.active == 1 and not c.input_unknown and c.cec_view == "one"


@pytest.mark.parametrize("claims,view", [((True, True), "both"), ((False, False), "none")])
def test_ambiguous_cec_claim_never_guesses_the_visible_stick(two, monkeypatch, claims, view):
    c, tuner = two
    no_tv_network(c, monkeypatch)
    c.input_unknown = True
    for s, on in zip(c.sticks, claims):
        s._active = on
    assert not run(c.sync_active_from_cec())
    assert c.input_unknown and c.cec_view == view
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and "input is unknown" in out.detail and tuner.calls == []
    assert not any("input keyevent" in cmd for stick in c.sticks for cmd in stick.log)


def test_dry_run_start_sends_no_keys_and_live_keepalive_reads_cec(two, monkeypatch):
    c, _ = two
    c.cfg.update({"mode": {"dry_run": True}})

    async def adb_available():
        return True

    monkeypatch.setattr(ctl, "adb_available", adb_available)
    monkeypatch.setattr(c, "load_devices", lambda: None)
    run(c.start())
    assert c.active == 0
    assert not any("input keyevent" in cmd for stick in c.sticks for cmd in stick.log)
    c.cfg.update({"mode": {"dry_run": False}})
    c.sticks[1]._active = True             # someone used the TV remote
    for stick in c.sticks:
        stick.last_ok = ctl.time.time()
    run(c.keepalive())
    assert not c.input_unknown and c.active == 1


def test_unknown_input_blocks_preload_reseek_and_sunday_ticket_scan(two):
    c, tuner = two
    c.input_unknown = True
    assert not c.preload("youtubetv:espn")
    c._set_hidden("youtubetv:espn", True, ctl.Parked("r1", "youtubetv:espn", ctl.time.time() - 60, False))
    c._maybe_reseek()
    assert c._preload_task is None
    assert run(c.scan_sunday_ticket(["AAAAAAAAAAA"], [])) == {}
    assert tuner.calls == []
    assert not c.sticks[1].log


def test_entry_rewind_lands_the_live_switch_behind_on_purpose(two):
    """[cut timing] A live switch planned to come up on the dead ball before the current play:
    flip, then whole rewind presses; the game stays the live target (only its delay grows).
    [seek model] the planner's 18 s is one 15 s step + the 3 s mid stall: ONE press that counts,
    plus the first press YouTube TV swallows (live lab, Nov 4)."""
    c, tuner = two
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True, entry_rewind_s=18.0))
    assert out.ok and "rewound 18s to the dead ball" in out.detail
    presses = sum(cmd.count("KEYCODE_MEDIA_REWIND") for cmd in c.main.log if isinstance(cmd, str))
    assert presses == 2 and "KEYCODE_MEDIA_PLAY" in c.main.log
    assert c.on_target == "youtubetv:espn"


def test_a_hidden_cue_rewinds_the_hidden_stick_before_the_flip(two):
    """[seek model] entry_cue "hidden": the seek (and its 2-4 s stall) happens on the hidden stick before
    the flip, so the TV never shows the live edge mid-play; the visible stick gets no presses after."""
    c, tuner = two
    hid = c.hidden
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True, entry_rewind_s=18.0, entry_cue="hidden"))
    assert out.ok and "before the flip" in out.detail
    presses = sum(cmd.count("KEYCODE_MEDIA_REWIND") for cmd in hid.log if isinstance(cmd, str))
    assert presses == 2 and c.main is hid                       # 1 that counts + 1 swallowed, then flipped
    names = [n for n, _ in out.marks]
    assert names.index("rewind_done") < names.index("flip_start")
    assert c.on_target == "youtubetv:espn"


def test_rewind_wake_press_is_configurable(two):
    c, tuner = two
    c.cfg.update({"replay": {"seek_wake_presses": 0, "seek_step_s": 10, "seek_stall_min_s": 0, "seek_stall_max_s": 0}})
    assert run(c._rewind(c.main, 30.0))
    presses = sum(cmd.count("KEYCODE_MEDIA_REWIND") for cmd in c.main.log if isinstance(cmd, str))
    assert presses == 3


def _flaky_standby(c, monkeypatch, bad):
    """The preloaded standby's playback; read #2 (the flip-time check) and later go through `bad`."""
    monkeypatch.setattr(ctl, "STANDBY_REREAD_S", 0.0)
    c.hidden_target, c.hidden_ready = "youtubetv:fox", True
    hid = c.hidden
    hid.remember("youtubetv:fox", run(hid.playback("yttv.pkg")))
    real, reads = hid.playback, {"n": 0}

    async def playback(package=None):
        reads["n"] += 1
        return bad(reads["n"], await real(package))
    hid.playback = playback
    return reads


def test_one_missing_session_read_at_flip_time_is_asked_again(two, monkeypatch):
    """Soak, Nov 2: a fresh Prime standby read "none" once right at the flip and the whole switch
    failed. A missing session is re-read before the switch is given up."""
    c, tuner = two
    _flaky_standby(c, monkeypatch, lambda n, pb: {"state": "none", "pkg": None, "position": None,
                                                  "updated": None} if n == 2 else pb)
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert out.ok and out.path == "preloaded" and tuner.calls == []


@pytest.mark.playbook("SAFE-08")
def test_paused_standby_at_flip_time_is_not_reread(two, monkeypatch, caplog):
    """SAFE-08: a standby that isn't steady playing right before the flip: the flip is skipped and
    the TV stays on the current game (the next decision re-tunes it)."""
    c, tuner = two
    on_screen = c.main
    reads = _flaky_standby(c, monkeypatch, lambda n, pb: dict(pb, state="paused") if n >= 2 else pb)
    with caplog.at_level("INFO", logger="redzone.control"):
        out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert not out.ok and out.detail == "standby stream is no longer confirmed" and reads["n"] == 2
    assert out.screen_unchanged and c.main is on_screen and not c.hidden_ready
    assert "playbook=SAFE-08" in caplog.text


def test_a_jump_never_retunes_a_standby_that_stopped(two):
    """[jump mode, overseer Nov 7 20:01:50] A jump decided on a ready standby went out "tuned" (30 s) because the
    stream wasn't steady-playing at the flip. preloaded_only refuses instead: the screen is untouched."""
    c, tuner = two
    c.hidden_target, c.hidden_ready = "youtubetv:fox", True
    c.hidden.remember("youtubetv:fox", run(c.hidden.playback("yttv.pkg")))
    c.hidden.playback_state = "buffering"
    on_screen = c.main
    c._preloaded_only = True
    out = run(c.execute("youtubetv:fox", may_cut=lambda: True))
    assert not out.ok and out.detail.startswith(ctl.STANDBY_NOT_READY) and out.path == "refused"
    assert out.screen_unchanged and tuner.calls == [] and c.main is on_screen
    # nothing on the hidden stick at all: refused before any key
    c.hidden_target, c.hidden_ready = None, False
    out = run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok and out.detail == ctl.STANDBY_NOT_READY and tuner.calls == []


def test_a_refill_skips_the_preload_gap_and_says_why_one_is_refused(two):
    c, tuner = two

    async def go():
        c._last_preload = time.time()
        assert not c.preload("youtubetv:fox") and c.preload_why == "preload gap"
        assert c.preload("youtubetv:fox", urgent=True) and c.preload_why == ""
        assert not c.preload("youtubetv:espn", urgent=True) and c.preload_why == "a switch or preload is running"
        await c._preload_task
    run(go())
