"""The HDMI flip on two Fire TV sticks, with fake sticks (no adb, no TV):
- HOME -> active source is polled fast and stops early; flip_s and resume_s are booked separately;
- the relaunched app must be PLAYING the game before the switch reports (MEDIA_PLAY, then one deep link);
- neither / both sticks claiming the TV: a bounded HOME re-assert on the intended stick only;
- the stick we left never gets HOME or WAKEUP;
- the visible-stick watchdog restarts a screen that stopped playing (and leaves a playing one alone)."""
import asyncio
import time

import pytest

from redzone import control as ctl
from redzone.tuners.adb import RunResult
from test_hot_standby import FakeStick, two  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ctl, "FLIP_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.2)
    FakeStick.last_input_host = "a:5555"     # the TV starts on stick a (HDMI1)
    yield
    FakeStick.last_input_host = None


def keys(stick, *names):
    return [x for x in stick.log if any(n in x for n in names)]


def live(c):
    """Ready for keepalive without real adb."""
    c.adb_ok = True

    async def no_reconnect(s):
        return None
    c._reconnect = no_reconnect
    c._watchdog_applied = True
    return c


class SlowCecStick(FakeStick):
    """HOME is accepted, but the stick only reports active source after `polls` reads (or never)."""

    def __init__(self, host, polls=3, **kw):
        super().__init__(host, **kw)
        self.polls, self.reads, self.homed = polls, 0, False

    async def shell(self, cmd, timeout=8.0):
        if "mIsActiveSource" in cmd:
            self.log.append(cmd)
            if self.homed:
                self.reads += 1
                if self.polls is not None and self.reads >= self.polls:
                    FakeStick.last_input_host = self.host
            return RunResult(True, "1" if FakeStick.last_input_host == self.host else "0", 0.01)
        if "KEYCODE_HOME" in cmd:
            self.log.append(cmd)
            self.homed = True
            return RunResult(True, "", 0.01)
        return await super().shell(cmd, timeout)


# --- 1. flip verification -----------------------------------------------------------------

async def test_flip_polls_fast_stops_early_and_books_flip_and_resume_separately(two):
    c, tuner = two
    b = SlowCecStick("b:5555", polls=3)
    c.sticks[1] = b
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and out.resumed is True and c.main is b
    assert b.reads == 3                                   # stopped at the first "yes"
    assert out.flip_s is not None and out.flip_s < 1.0 and out.resume_s is not None
    st = c.switch_times()
    assert st["last_flip"]["flip_s"] == out.flip_s and st["last_flip"]["resume_s"] == out.resume_s
    assert st["last_flip"]["how"] == "auto" and st["flip_s"] is not None and st["resume_s"] is not None
    assert "resume" in c.latency.view() and "flip" in c.latency.view()


async def test_flip_that_never_takes_the_tv_fails_without_waiting_forever(two, monkeypatch):
    monkeypatch.setattr(ctl, "FLIP_DEADLINE_S", 0.15)
    c, _ = two
    b = SlowCecStick("b:5555", polls=None, cec_ok=False)   # never becomes active source
    c.sticks[1] = b

    async def no(*a):
        return False
    monkeypatch.setattr(c.tv, "set_input", no)
    c.on_target = "youtubetv:fox"
    t0 = time.monotonic()
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert not out.ok and time.monotonic() - t0 < 2.0
    assert c.input_unknown and not c.two_stick_ok and c._unknown_target == "youtubetv:fox"
    assert not keys(c.sticks[0], "KEYCODE_HOME", "WAKEUP")  # the stick on screen was never touched


async def test_paused_after_the_flip_gets_media_play_not_a_retune(two, monkeypatch):
    c, tuner = two
    b = c.sticks[1]
    c.on_target = "youtubetv:fox"
    real = b.shell

    async def shell(cmd, timeout=8.0):
        if "KEYCODE_HOME" in cmd:
            b.playing = "paused"                          # the relaunch came back paused
        return await real(cmd, timeout)
    b.shell = shell

    async def key(*codes):
        b.log.extend(codes)
        if "KEYCODE_MEDIA_PLAY" in codes:
            b.playing = "playing"
        return RunResult(True, "", 0.01)
    b.key = key
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and out.resumed is True and "media play" in out.detail
    assert "KEYCODE_MEDIA_PLAY" in b.log and len(tuner.calls) == 1
    assert c.last_flip["how"] == "media_play"


async def test_an_autoplaying_hero_preview_is_not_the_game(two, monkeypatch):
    """Same app, playing, but a different title than the stream we confirmed: re-deep-link."""
    c, tuner = two
    b = c.sticks[1]
    c.on_target = "youtubetv:fox"
    titles = {"now": "Wizards at Magic"}
    real_pb = b.playback

    async def playback(package=None):
        r = await real_pb(package)
        r["title"] = titles["now"]
        return r
    b.playback = playback
    real = b.shell

    async def shell(cmd, timeout=8.0):
        if "KEYCODE_HOME" in cmd:
            titles["now"] = "Coming soon: a trailer"     # the app's home page hero autoplays
        return await real(cmd, timeout)
    b.shell = shell
    orig = tuner.tune

    async def tune(stick, ref, target, may_cut=None):
        titles["now"] = "Wizards at Magic"
        return await orig(stick, ref, target, may_cut)
    monkeypatch.setattr(tuner, "tune", tune)
    monkeypatch.setattr(ctl, "channel_matcher", lambda t: None)   # Prime-like: no channel name to go on
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and out.resumed is True and c.last_flip["how"] == "relink"
    assert len(tuner.calls) == 2


async def test_a_flip_whose_game_never_comes_back_reports_the_cut_and_hands_off_to_the_watchdog(two, monkeypatch):
    c, tuner = two
    b = c.sticks[1]
    c.on_target = "youtubetv:fox"
    real = b.shell

    async def shell(cmd, timeout=8.0):
        if "KEYCODE_HOME" in cmd:
            b.playing = "none"
        return await real(cmd, timeout)
    b.shell = shell
    orig = tuner.tune

    async def tune(stick, ref, target, may_cut=None):
        if stick is b and stick.playing == "none":
            tuner.calls.append((stick.host, target))
            return ctl.TuneResult(False, target, 1.0, [], "video never started")
        return await orig(stick, ref, target, may_cut)
    monkeypatch.setattr(tuner, "tune", tune)
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    # The input changed (the decider must know the cut happened), the picture is not confirmed.
    assert out.ok and out.resumed is False and "watchdog" in out.detail
    assert c._visible_bad_since is not None and c.last_flip["resumed"] is False


# --- 2. neither / both sticks claim the TV ---------------------------------------------------

async def test_neither_stick_on_screen_reasserts_home_on_the_intended_stick_only(two, monkeypatch):
    monkeypatch.setattr(ctl, "REASSERT_FIRST_S", 0.0)
    c, tuner = live(two[0]), two[1]
    a, b = c.sticks
    c.on_target = "youtubetv:fox"
    c._mark_input_unknown("youtubetv:fox")
    c.two_stick_ok = False
    c._two_stick_retry_at = 0
    FakeStick.last_input_host = None                    # TV on another input (or off)
    await c.keepalive()
    assert keys(a, "KEYCODE_HOME") and not keys(b, "KEYCODE_HOME", "WAKEUP")
    assert not c.input_unknown and c.active == 0 and c.two_stick_ok
    assert c.on_target == "youtubetv:fox" and c.last_flip["stick"] == "a:5555"


async def test_reasserts_are_capped_so_a_tv_turned_off_stays_off(two, monkeypatch):
    monkeypatch.setattr(ctl, "REASSERT_FIRST_S", 0.0)
    monkeypatch.setattr(ctl, "REASSERT_GAP_S", 0.0)
    monkeypatch.setattr(ctl, "FLIP_DEADLINE_S", 0.03)
    c = live(two[0])
    a = SlowCecStick("a:5555", polls=None)               # the TV never answers
    c.sticks[0] = a
    FakeStick.last_input_host = None
    c._mark_input_unknown("youtubetv:fox")
    for _ in range(6):
        await c.keepalive()
    assert len(keys(a, "KEYCODE_HOME")) == ctl.REASSERT_MAX and c.input_unknown
    assert not keys(c.sticks[1], "KEYCODE_HOME", "WAKEUP")


async def test_both_claiming_waits_for_a_safe_moment(two, monkeypatch):
    monkeypatch.setattr(ctl, "REASSERT_FIRST_S", 0.0)
    c = live(two[0])
    a, b = c.sticks
    a._active = b._active = True                          # both say they're on screen
    c._mark_input_unknown("youtubetv:fox")
    c.safe_moment = lambda: False                         # a play is live on screen
    await c.keepalive()
    assert c.cec_view == "both" and not keys(a, "KEYCODE_HOME") and c.input_unknown
    c.safe_moment = lambda: True
    await c.keepalive()
    assert keys(a, "KEYCODE_HOME") and not keys(b, "KEYCODE_HOME") and not c.input_unknown


async def test_both_claiming_without_an_engine_gate_waits_two_minutes(two, monkeypatch):
    monkeypatch.setattr(ctl, "REASSERT_FIRST_S", 0.0)
    c = live(two[0])
    a, b = c.sticks
    a._active = b._active = True
    c._mark_input_unknown(None)
    await c.keepalive()
    assert not keys(a, "KEYCODE_HOME")
    c._unknown_since -= ctl.REASSERT_BOTH_S
    await c.keepalive()
    assert keys(a, "KEYCODE_HOME") and not c.input_unknown


async def test_unknown_input_is_retried_well_within_two_minutes(two, monkeypatch):
    c = live(two[0])
    FakeStick.last_input_host = None
    c._mark_input_unknown("youtubetv:fox")
    assert c._reassert_at - time.time() <= ctl.REASSERT_FIRST_S + 0.1
    assert ctl.TWO_STICK_RETRY_S <= 120


# --- 3. the stick we left -------------------------------------------------------------------

async def test_the_stick_we_left_never_gets_home_or_wakeup(two, monkeypatch):
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, tuner = two
    live(c)
    a, b = c.sticks
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c.main is b
    a.log.clear()
    a.playing = "paused"                                  # it lost the TV input
    if c._refresh_task:
        await asyncio.gather(c._refresh_task, return_exceptions=True)
    if c._preload_task:
        await asyncio.gather(c._preload_task, return_exceptions=True)
    c._verified_at = 0
    for _ in range(3):
        await c.keepalive()
    assert not keys(a, "KEYCODE_HOME", "KEYCODE_WAKEUP")


async def test_sunday_ticket_scan_never_wakes_a_sleeping_hidden_stick(two):
    c, tuner = two
    hid = c.hidden
    hid.power = "Asleep"
    c._awake_at.clear()
    hid._awake_at = 0.0
    found = await c.scan_sunday_ticket(["CODE1"], [])
    assert found == {} and tuner.calls == [] and not keys(hid, "WAKEUP", "KEYCODE_HOME")


# --- 5. visible-stick watchdog ---------------------------------------------------------------

async def test_watchdog_leaves_a_playing_screen_alone(two):
    c, tuner = two
    live(c)
    c.on_target = "youtubetv:fox"
    await c._watch_visible()
    assert c._visible_bad_since is None and not keys(c.main, "MEDIA_PLAY") and tuner.calls == []


async def test_watchdog_presses_play_after_8s_paused(two, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.05)
    c, tuner = two
    live(c)
    a = c.main
    c.on_target = "youtubetv:fox"
    a.playing = "paused"                                  # "Are you still watching?"

    async def key(*codes):
        a.log.extend(codes)
        if "KEYCODE_MEDIA_PLAY" in codes:
            a.playing = "playing"
        return RunResult(True, "", 0.01)
    a.key = key
    await c._watch_visible()
    assert c._visible_bad_since is not None and "KEYCODE_MEDIA_PLAY" not in a.log   # first look: wait
    await asyncio.wait_for(c._resume_task, 2)             # the 8 s follow-up
    assert "KEYCODE_MEDIA_PLAY" in a.log and tuner.calls == [] and c._visible_bad_since is None


async def test_watchdog_relinks_an_app_that_crashed_to_the_fire_tv_home(two, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    c, tuner = two
    live(c)
    a = c.main
    c.on_target = "youtubetv:fox"
    a.playing = "none"
    orig = tuner.tune

    async def tune(stick, ref, target, may_cut=None):
        stick.playing = "playing"
        return await orig(stick, ref, target, may_cut)
    monkeypatch.setattr(tuner, "tune", tune)
    await c._watch_visible()                              # first look
    await c._watch_visible()                              # stalled: the repair runs as its own task
    await asyncio.wait_for(asyncio.gather(c._resume_task, return_exceptions=True), 2)
    assert tuner.calls == [("a:5555", "youtubetv:fox")] and c._visible_bad_since is None
    assert not keys(c.sticks[1], "KEYCODE_HOME", "WAKEUP")


async def test_watchdog_stays_out_of_a_switch_and_can_be_switched_off(two, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    c, tuner = two
    live(c)
    c.on_target = "youtubetv:fox"
    c.main.playing = "paused"
    c._flipping = True
    await c._watch_visible()
    assert c._visible_bad_since is None
    c._flipping = False
    c.visible_watch_ok = lambda: False                    # e.g. a recorded session
    await c._watch_visible()
    await c._watch_visible()
    if c._resume_task:
        await asyncio.gather(c._resume_task, return_exceptions=True)
    assert tuner.calls == [] and not keys(c.main, "MEDIA_PLAY")


async def test_a_switch_cancels_a_running_watchdog_repair(two, monkeypatch):
    """A repair (deep link, up to ~30 s) must never make a real switch queue behind it."""
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    c, tuner = two
    live(c)
    c.on_target = "youtubetv:fox"
    c.main.playing = "none"
    tuner.slow = {"youtubetv:fox": 5.0}
    await c._watch_visible()
    await c._watch_visible()
    repair = c._resume_task
    await asyncio.sleep(0.05)                             # mid deep link
    outs = []
    t0 = time.monotonic()
    c.submit("youtubetv:espn", on_done=outs.append, may_cut=lambda: True)
    await asyncio.wait_for(asyncio.gather(c._task, return_exceptions=True), 2)
    assert repair.cancelled() and outs and time.monotonic() - t0 < 1.5


async def test_a_switch_cancels_the_watchdog_follow_up(two, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 5.0)
    c, _ = two
    live(c)
    c.on_target = "youtubetv:fox"
    c.main.playing = "paused"
    await c._watch_visible()
    follow = c._resume_task
    c.main.playing = "playing"
    c.submit("youtubetv:espn", may_cut=lambda: True)
    await asyncio.sleep(0)
    assert follow.cancelled() or follow.done()
    await c._task
