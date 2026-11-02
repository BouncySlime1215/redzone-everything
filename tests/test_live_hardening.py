"""Hardening for the live switch path (Nov 2, before the 7pm games):
- a cold YouTube TV tune confirms on the channel name in the app's metadata (was ~20 s);
- sticks reconnect within one keepalive tick, even after a flaky `adb version` at startup;
- the hidden stick is never woken by a failed power read; a lost TV input recovers from CEC."""
import asyncio
import time

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners import adb
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import NewStream, YouTubeTVTuner, channel_matcher
from test_tuners import YT, ScriptedStick, quick


def yt(state=3, pos=0, upd=0, speed=1.0, meta="Show, Channel, null"):
    return (f"    package={YT}\n"
            f"    state=PlaybackState {{state={state}, position={pos}, buffered position=0, speed={speed}, "
            f"updated={upd}, actions=822, custom actions=[], active item id=-1, error=null}}\n"
            f"    metadata: size=4, description={meta}\n")


# --- 1. cold tune speed -------------------------------------------------------------------

def test_channel_matcher_reads_the_yttv_subtitle():
    m = channel_matcher("youtubetv:espn")
    assert m({"subtitle": "ESPN"}) is True
    assert m({"subtitle": "ESPN2"}) is False            # whole words: ESPN2 is not ESPN
    assert m({"subtitle": ""}) is None and m({}) is None
    assert channel_matcher("youtubetv:nbc")({"subtitle": "NBC 5"}) is True
    assert channel_matcher("youtubetv:st:abcdefghijk") is None   # Sunday Ticket: generic names
    assert channel_matcher("youtubetv:st1") is None
    assert channel_matcher("prime:amzn1.x") is None


async def test_cold_yttv_tune_confirms_on_channel_name_not_after_a_whole_attempt():
    """The 17:30 ESPN switch: the hidden stick's old live stream reports no usable position, so the
    playback-clock rules could never tell old from new and attempt 1 timed out (15 s)."""
    old = yt(3, 0, 5000, meta="Pardon the Interruption, ESPN2, null")
    st = ScriptedStick(now=old, after=[old, yt(3, 0, 6000, speed=0.0, meta="SportsCenter, ESPN, null"),
                                       yt(3, 0, 7000, meta="SportsCenter, ESPN, null")])
    t = quick(confirm_timeout=5.0)
    t0 = time.monotonic()
    res = await t.tune(st, "CODE", "youtubetv:espn")
    assert res.ok and time.monotonic() - t0 < 1.0
    assert len(st.links()) == 1                          # no second attempt
    assert st.tuned["report"]["updated"] == 7000         # the moving report, not the speed-0 one
    assert any("ESPN" in s and "metadata" in s for s in res.steps)


async def test_old_stream_on_another_channel_never_confirms_even_after_a_gap():
    old = yt(3, 0, 5000, meta="Pardon the Interruption, ESPN2, null")
    st = ScriptedStick(now=old, after=[yt(6, 0, 5500, meta="Pardon the Interruption, ESPN2, null"), old])
    res = await quick(confirm_timeout=0.1).tune(st, "CODE", "youtubetv:espn")
    assert not res.ok and st.tuned is None


async def test_old_stream_on_the_same_channel_does_not_confirm_by_name():
    w = NewStream({"state": "playing", "speed": 1.0, "position": 0, "updated": 1, "subtitle": "ESPN",
                   "title": "A"}, expect=channel_matcher("youtubetv:espn"))
    assert not w.check({"state": "playing", "speed": 1.0, "position": 0, "updated": 1, "subtitle": "ESPN",
                        "title": "A"})
    assert not w.by_name


def test_name_confirmation_needs_moving_playback():
    w = NewStream({"state": "none"}, expect=channel_matcher("youtubetv:espn"))
    assert not w.check({"state": "playing", "speed": 0.0, "subtitle": "ESPN"})
    assert w.check({"state": "playing", "speed": 1.0, "subtitle": "ESPN"})


# --- 2. reconnect -------------------------------------------------------------------------

class FlakyStick(Stick):
    def __init__(self):
        super().__init__("10.0.0.9:5555", name="s", os="Fire OS 8")
        self.packages = {"youtubetv": YT}
        self.reachable = False
        self.attempts = 0

    async def _shell(self, cmd, timeout=8.0):
        if "hdmi_control" in cmd:
            return RunResult(False, "0", 0.0)
        return RunResult(self.reachable, "ok\n" if self.reachable else "error: device offline", 0.0)


@pytest.fixture
def two(tmp_path, monkeypatch, cfg):
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    a, b = FlakyStick(), FlakyStick()
    b.host = "10.0.0.10:5555"
    c.sticks = [a, b]
    c.hdmi = {a.host: "HDMI1", b.host: "HDMI2"}
    return c


async def test_flaky_adb_version_at_startup_does_not_disable_sticks_forever(two, monkeypatch):
    """17:27 restart: `adb version` came back with returncode 255 (asyncio: "Unknown child process"),
    adb_ok latched False and keepalive returned early for good."""
    async def fake_adb(*args, timeout=8.0):
        if args[0] == "version":
            return RunResult(False, "", 0.0)                       # the flaky first check
        st = next(s for s in two.sticks if s.host == args[1])
        return RunResult(True, f"connected to {st.host}" if st.reachable else "failed to connect", 0.0)
    monkeypatch.setattr(adb, "_adb", fake_adb)
    monkeypatch.setattr(ctl, "adb_available", adb.adb_available)
    monkeypatch.setattr(ctl.Controller, "load_devices", lambda self: None)
    await two.start()
    assert not two.adb_ok
    for s in two.sticks:
        s.reachable = True

    async def version_ok(*args, timeout=8.0):
        if args[0] == "version":
            return RunResult(True, "Android Debug Bridge version 1.0.41", 0.0)
        return await fake_adb(*args, timeout=timeout)
    monkeypatch.setattr(adb, "_adb", version_ok)
    await two.keepalive()                                  # one tick later
    assert two.adb_ok and all(s.state == "connected" for s in two.sticks)


async def test_adb_version_output_counts_even_with_a_lost_exit_code(monkeypatch):
    async def fake_adb(*args, timeout=8.0):
        return RunResult(False, "Android Debug Bridge version 1.0.41\nVersion 35.0.2", 0.0)
    monkeypatch.setattr(adb, "_adb", fake_adb)
    assert await adb.adb_available()


async def test_reachable_stick_reconnects_within_one_tick_after_many_failures(monkeypatch):
    s = FlakyStick()

    async def fake_adb(*args, timeout=8.0):
        return RunResult(True, f"connected to {s.host}" if s.reachable else "failed to connect", 0.0)
    monkeypatch.setattr(adb, "_adb", fake_adb)
    for _ in range(8):                                     # a long outage
        assert not await s.connect()
        s._next_retry = min(s._next_retry, time.time() + adb.CONNECT_RETRY_MAX_S)
    assert s._next_retry - time.time() <= adb.CONNECT_RETRY_MAX_S
    s.reachable = True
    s._next_retry = time.time() - 0.01                   # one keepalive tick later
    assert await s.connect() and s.state == "connected"


async def test_keepalive_survives_one_stick_raising(two, monkeypatch):
    two.adb_ok = True
    a, b = two.sticks
    b.reachable = True

    async def boom():
        raise RuntimeError("adb exploded")
    a.connect = boom

    async def ok():
        b.state = "connected"
        return True
    b.connect = ok
    await two.keepalive()
    assert b.state == "connected"


# --- 3. live path -------------------------------------------------------------------------

async def test_unreadable_power_state_never_wakes_the_hidden_stick(two):
    hid = two.sticks[1]
    hid.state = "connected"
    sent = []

    async def shell(cmd, timeout=8.0):
        sent.append(cmd)
        return RunResult(False, "timeout/error", 0.0)
    hid._shell = shell
    assert await two._sleeping(hid)                      # unreadable: treated as asleep (no preload)
    assert not any("WAKEUP" in c for c in sent)


async def test_awake_reading_is_shared_with_the_stick(two):
    hid = two.sticks[1]

    async def shell(cmd, timeout=8.0):
        return RunResult(True, "  mWakefulness=Awake\n", 0.0)
    hid._shell = shell
    assert not await two._sleeping(hid)
    assert not await hid.ensure_awake()                  # trusts the controller's fresh reading


async def test_lost_input_recovers_from_unambiguous_cec(two):
    a, b = two.sticks
    for s in (a, b):
        s.state = "connected"
    two.adb_ok = True
    two.input_unknown, two.two_stick_ok, two.on_target = True, False, "youtubetv:espn"

    def cec(active):
        async def shell(cmd, timeout=8.0):
            if "mIsActiveSource" in cmd:
                return RunResult(active, "1\n" if active else "0\n", 0.0)
            return RunResult(True, "ok\n", 0.0)
        return shell
    a._shell, b._shell = cec(False), cec(True)
    assert await two.sync_active_from_cec()
    assert two.active == 1 and not two.input_unknown
    assert two.on_target is None and two.hidden_target is None


async def test_two_sticks_both_claiming_active_source_changes_nothing(two):
    a, b = two.sticks
    for s in (a, b):
        s.state = "connected"

        async def shell(cmd, timeout=8.0):
            return RunResult(True, "1\n", 0.0)
        s._shell = shell
    two.active, two.input_unknown = 0, True
    assert not await two.sync_active_from_cec()
    assert two.active == 0 and two.input_unknown


# --- after a HOME flip the relaunched app must really be playing --------------------------

from test_hot_standby import two as hot_two  # noqa: E402,F401  (fixture)


async def test_home_flip_that_resumes_is_left_alone(hot_two, monkeypatch):
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.3)
    c, tuner = hot_two
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c._resume_task is not None
    await c._resume_task
    assert tuner.calls == [("b:5555", "youtubetv:espn")]      # no re-tune
    assert c.on_target == "youtubetv:espn" and c.main.tuned["target"] == "youtubetv:espn"


async def test_home_flip_that_lands_on_the_app_home_reloads_the_game(hot_two, monkeypatch):
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.3)
    c, tuner = hot_two
    c.on_target = "youtubetv:fox"
    c.sticks[1].playing = "none"                              # relaunch opened the app's home page
    orig = tuner.tune

    async def tune(stick, ref, target, may_cut=None):
        if len(tuner.calls) == 0:
            res = await orig(stick, ref, target, may_cut)        # the hidden-stick tune before the flip
            return res
        stick.playing = "playing"
        return await orig(stick, ref, target, may_cut)
    monkeypatch.setattr(tuner, "tune", tune)
    c.sticks[1].playing = "playing"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok
    c.sticks[1].playing = "none"
    await c._resume_task
    assert tuner.calls[-1] == ("b:5555", "youtubetv:espn") and len(tuner.calls) == 2
    assert c.on_target == "youtubetv:espn"


async def test_a_new_switch_cancels_the_resume_check(hot_two, monkeypatch):
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 5.0)
    c, _ = hot_two
    c.on_target = "youtubetv:fox"
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    c.sticks[1].playing = "none"
    task = c._resume_task
    c.submit("youtubetv:fox", may_cut=lambda: True)
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    await c._task


async def test_event_link_confirms_when_the_channel_in_the_metadata_changes():
    """ev:/st: targets have no channel name to expect. YouTube TV's live position runs on with the
    clock across channels, so only a change of channel (the subtitle) shows the new stream fast."""
    old = yt(3, 46_801_268, 860_547, meta="SportsCenter, ESPN, null")
    new = yt(3, 46_803_300, 862_600, meta="Monday Night Football, ABC 11, null")
    st = ScriptedStick(now=old, after=[old, new])
    res = await quick(confirm_timeout=5.0).tune(st, "CODE", "youtubetv:ev:CODEcodeCOD")
    assert res.ok and len(st.links()) == 1


async def test_without_metadata_rules_the_clock_alone_cannot_tell_yttv_channels_apart():
    """Why the 17:30 ESPN switch took 20.8 s: same wall-clock-like position on both channels."""
    old = yt(3, 46_801_268, 860_547, meta="SportsCenter, ESPN2, null")
    new = yt(3, 46_803_300, 862_600, meta="SportsCenter, ESPN, null")
    w = NewStream(adb.parse_playback(old))
    assert not w.check(adb.parse_playback(new))
    w = NewStream(adb.parse_playback(old), expect=channel_matcher("youtubetv:espn"))
    assert w.check(adb.parse_playback(new))


def test_top_of_the_hour_title_change_on_the_old_channel_is_not_a_new_stream():
    old = {"state": "playing", "speed": 1.0, "position": 46_801_268, "updated": 860_547,
           "title": "SportsCenter", "subtitle": "ESPN"}
    w = NewStream(old, channel_change=True)
    assert not w.check(dict(old, title="NFL Countdown", position=46_803_300, updated=862_579))
