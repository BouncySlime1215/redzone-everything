"""PARK hands in the controller (integration plan unit 7): tune, place, park, jump and finish on fake sticks and a fake
TV that behave like the real ones did on Nov 10 (ledger L13-L21, L41-L51), with the repo's real sound guard on top.

Why: the local show runner's device steps (tools/park_live RealRig) worked on the real sticks; the app's own controller
needs the same steps, behind park.enabled (off), with every step returning a small result and the TV kept muted.

The fakes: a stick whose media session says "playing" at position 0.0 for 2 s after a tune, then the stream's own
clock (tens of thousands of seconds); CENTER pauses and brings up the overlay, LEFT moves its knob exactly 15 s,
CENTER commits; the pause key freezes the frame 0.70 s (2023) or 0.60 s (Plus) past the key. A TV whose input
changes 2.7 s after a SmartThings command, with mute and volume read back. Time is fake: it moves only when
every task is waiting on it (`Clock`), so a busy machine cannot change what a step takes.
"""
from __future__ import annotations

import asyncio
import heapq
import math
from types import SimpleNamespace

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.platform.sound_guard import SoundGuard
from redzone.tuners import yttv_timing
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult
from redzone.tuners.yttv_label import LabelRead

IDLE_PASSES = 40          # event-loop passes with nothing new to do before the fake clock moves on
PKG = "yttv.pkg"
FORBIDDEN = ("VOLUME", "MUTE", "POWER", "HOME", "SLEEP", "WAKEUP")
STATIONS = {"espn": "ESPN", "fs1": "FS1", "nbc": "WRAL", "tnt": "TNT"}


class Clock:
    """Fake wall time that moves ONLY when everything is waiting on it: once every task has run until it waits, the
    earliest sleeper is woken and the time jumps to its moment (concurrent waits overlap as they would). So a step
    takes the same fake time however busy the real machine is. (The first clock ran 25 times faster than real time:
    0.4 s of real stall was 10 s of test time, and a test with a 9 s margin failed once on a loaded Mac.)"""

    def __init__(self):
        self.t = 1_794_000_000.0
        self._q, self._n, self._pump = [], 0, None

    def now(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        if s <= 0:
            await asyncio.sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        self._n += 1
        heapq.heappush(self._q, (self.t + s, self._n, fut))
        if self._pump is None or self._pump.done():
            self._pump = asyncio.ensure_future(self._run())
        await fut

    async def _run(self) -> None:
        while self._q:
            for _ in range(IDLE_PASSES):
                await asyncio.sleep(0)                         # let everything run until it waits
            while self._q and self._q[0][2].cancelled():
                heapq.heappop(self._q)                         # a waiter that was cancelled moves no time
            if not self._q:
                break
            when, _, fut = heapq.heappop(self._q)
            self.t = max(self.t, when)
            if not fut.done():
                fut.set_result(None)


class YTStick(Stick):
    """A Fire TV stick running YouTube TV, from the outside: media session, keys, foreground. Truth kept apart."""

    def __init__(self, host, clock, model, lead):
        super().__init__(host, name=model, model=model, os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": PKG}
        self.clock, self.lead, self.boot = clock, lead, clock.now() - 5000.0
        self.channel, self.origin, self.loaded_at = None, 0.0, -1e18
        self.ps, self.pos0, self.wall0, self.frozen, self.event = "none", 0.0, 0.0, 0.0, clock.now()
        self.overlay, self.lefts = False, 0
        self.keys: list[str] = []
        self.hold_fails = self.ignore_pause = False
        self.fg = PKG

    # truth -------------------------------------------------------------------------------------------------
    def pos(self, w=None) -> float:
        w = self.clock.now() if w is None else w
        return self.pos0 + (w - self.wall0) if self.ps == "playing" else self.frozen

    def content(self, pos) -> float:
        return self.origin + pos

    def behind(self) -> float:
        """How far the picture is behind YouTube's clock now."""
        return self.clock.now() - self.content(self.pos())

    def load(self, channel, base_lag=20.0, behind=0.0):
        now = self.clock.now()
        self.channel, self.loaded_at, self.ps = channel, now, "playing"
        self.pos0, self.wall0, self.event = 41_000.0 + (hash(channel) % 997), now, now
        self.origin = (now - base_lag - behind) - self.pos0
        self.overlay, self.lefts = False, 0

    def _pause(self, lead):
        now = self.clock.now()
        self.frozen, self.ps, self.event = self.pos(now + lead), "paused", now + lead

    def _play(self, pos):
        now = self.clock.now()
        self.pos0, self.wall0, self.ps, self.event = pos, now, "playing", now

    # the stick's outside ------------------------------------------------------------------------------------
    def _media(self) -> str:
        now = self.clock.now()
        if self.channel is None:
            return ""
        up = lambda w: int(round((w - self.boot) * 1000))                  # noqa: E731
        if self.ps == "playing" and now - self.loaded_at < 2.0:           # the first 2 s after a tune: position 0.0
            code, pos, upd, speed = 3, 0, up(self.loaded_at), 1.0
        elif self.ps == "playing":
            code, pos, upd, speed = 3, int(self.pos0 * 1000), up(self.wall0), 1.0
        else:
            code, pos, upd, speed = 2, int(self.frozen * 1000), up(self.event), 0.0
        return (f"package={PKG}\nactive=true\nstate=PlaybackState {{state={code}, position={pos}, buffered position=0, "
                f"speed={speed}, updated={upd}, actions=0}}\n"
                f"metadata: size=3, description=Basketball, {STATIONS.get(self.channel, self.channel.upper())}, null\n")

    async def _shell(self, cmd, timeout):
        now = self.clock.now()
        if cmd.startswith("cat /proc/uptime"):
            return RunResult(True, f"{now - self.boot:.2f} 1000.00\n" + self._media(), 0.01)
        if cmd.startswith("dumpsys media_session"):
            return RunResult(True, self._media(), 0.01)
        if cmd.startswith("dumpsys activity"):
            return RunResult(True, f"mResumedActivity: ActivityRecord{{1 u0 {self.fg}/.Main t1}}", 0.01)
        if cmd.startswith("input keyevent"):
            k = cmd.split()[-1].replace("KEYCODE_", "")
            self.keys.append(k)
            if k == "MEDIA_PLAY_PAUSE":
                if self.ps == "playing" and not self.ignore_pause:
                    self._pause(self.lead)
                elif self.ps == "paused":
                    self.overlay = False
                    self._play(self.frozen)
            elif k == "DPAD_CENTER":
                if self.ps == "playing" and not self.overlay:
                    self.overlay, self.lefts = True, 0
                    self._pause(0.70)
                elif self.overlay:
                    self.overlay = False
                    self._play(self.frozen - 15.0 * self.lefts)
            elif k == "DPAD_LEFT" and self.overlay:
                self.lefts += 1
            elif k == "BACK":
                self.overlay = False
                if self.ps == "paused" and self.hold_fails:
                    self._play(self.frozen)
            return RunResult(True, "", 0.01)
        return RunResult(True, "", 0.01)

    def forbidden(self) -> list[str]:
        return [k for k in self.keys if any(f in k for f in FORBIDDEN)]


class Labels:
    """The overlay's time label as the repo reader returns it. mode: ok | nostart (no programme start) | fail |
    fail_once."""

    def __init__(self, clock, mode="ok"):
        self.clock, self.mode, self.calls = clock, mode, 0

    async def __call__(self, stick):
        self.calls += 1
        await self.clock.sleep(4.0)                           # a screenshot and the on-Mac text reading
        if self.mode == "fail" or (self.mode == "fail_once" and self.calls == 1) or not stick.overlay:
            return LabelRead("no_overlay", self.clock.now())
        knob = stick.content(stick.frozen - 15.0 * stick.lefts)
        start = math.floor(knob / 300.0) * 300.0
        return LabelRead("ok", self.clock.now(), label_s=float(math.floor(knob - start)),
                         prog_start=None if self.mode == "nostart" else start)


class Tuner:
    """The YouTube TV tuner: 2.7 s; `fail` lists refs that fail; `fail_but_on` ones that fail although the stream
    came up (the media session then names the game)."""

    def __init__(self, clock, lags=None):
        self.clock, self.lags, self.fail, self.fail_but_on, self.calls = clock, lags or {}, set(), set(), []

    async def tune(self, stick, ref, target, may_cut=None):
        self.calls.append(ref)
        await self.clock.sleep(2.7)
        ch = target.split(":", 1)[1]
        if ref in self.fail:
            return TuneResult(False, target, 2.7, [], "video never started")
        stick.load(ch, base_lag=self.lags.get(ch, 20.0))
        if ref in self.fail_but_on:
            return TuneResult(False, target, 2.7, [], "no confirmation")
        return TuneResult(True, target, 2.7, ["playing"], "")


class TV:
    """The Samsung TV from the controller's side: SmartThings input change (2.7 s), mute and volume read back."""

    def __init__(self, clock, marker):
        self.clock, self.input, self.mute, self.volume, self.power = clock, "HDMI1", "muted", 0, "on"
        self.flip_s, self.flip_fails, self.unmute_on_flip = 2.7, False, False
        self.st_device, self.last_error, self.cloud_disabled, self.sets, self.commands = "test-display", "", False, [], []
        self.settings = {"sound_guard.enabled": True, "sound_guard.must_be_silent": True}
        self.sound_guard = SoundGuard(self, settings=self.sound_settings, clock=clock.now, sleep=clock.sleep, marker=marker)

    def sound_settings(self, key, fallback=None):
        return self.settings.get(key, fallback)

    def must_be_silent(self):
        return bool(self.settings.get("sound_guard.must_be_silent"))

    def sound_blocked(self):
        return self.sound_guard.blocked_why() if self.sound_guard is not None else ""

    def ready(self):
        return True

    def paths(self):
        return ["smartthings"]

    async def sound_state(self, input_timeout_s=10.0):
        return {"power": self.power, "mute": self.mute, "volume": self.volume, "input": self.input}

    async def input_status(self):
        return self.input

    async def st_command(self, capability, command, args=None):
        self.commands.append((capability, command))
        if capability == "switch" and command == "off":
            self.power = "off"
        return True

    async def set_input(self, hdmi, methods=None):
        self.sets.append((hdmi, list(methods or [])))
        g = self.sound_guard
        if g is not None and not await g.before_switch():
            return False
        await self.clock.sleep(self.flip_s)
        if self.unmute_on_flip:
            self.mute, self.volume = "unmuted", 12
        if not self.flip_fails:
            self.input = hdmi
        return not self.flip_fails


@pytest.fixture
def world(cfg, tmp_path, monkeypatch):
    clk = Clock()
    monkeypatch.setattr(yttv_timing, "time", SimpleNamespace(time=clk.now))
    cfg.update({"mode": {"dry_run": False}, "park": {"enabled": True}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fs1": "BBBBBBBBBBB", "tnt": "CCCCCCCCCCC"})   # nbc: by search only
    c = ctl.Controller(cfg, ch)
    a = YTStick("a:5555", clk, "Fire TV Stick 4K (2023)", 0.70)
    b = YTStick("b:5555", clk, "Fire TV Stick 4K Plus", 0.60)
    c.sticks, c.hdmi = [a, b], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv = TV(clk, tmp_path / "sound_incident.json")
    tuner = Tuner(clk, lags={"nbc": 58.8})
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    labels = Labels(clk)
    c._park_hands = ctl.ParkHands(c, now=clk.now, sleep=clk.sleep, label_reader=labels)
    a.load("tnt")                                             # the picture on screen: stick a, playing live
    a.loaded_at -= 10
    yield SimpleNamespace(c=c, a=a, b=b, tv=c.tv, tuner=tuner, labels=labels, clk=clk, hands=c._park_hands)
    assert a.forbidden() == [] and b.forbidden() == [], "PARK never sends volume, mute, power or HOME"


def near(x, y, tol):
    return x is not None and abs(x - y) <= tol


# --- flag off: nothing runs, nothing changes ---------------------------------------------------------------------

async def test_flag_off_every_step_is_refused_and_nothing_is_made_or_sent(world):
    w = world
    w.c.cfg.update({"park": {"enabled": False}})
    w.c._park_hands = None
    for out in (await w.c.park_tune("youtubetv:espn"), await w.c.park_pause("youtubetv:espn", None),
                await w.c.park_jump("youtubetv:espn")):
        assert out.outcome == "refused" and out.why == "park.enabled is off"
    assert (await w.c.park_finish()).outcome == "refused"
    assert w.c._park_hands is None and w.c.park_hands() is None and w.c.park_stale() == ""
    assert w.a.keys == [] and w.b.keys == [] and w.tuner.calls == [] and w.tv.sets == []
    assert "park" not in w.c.machines_view()


async def test_flag_off_an_ordinary_switch_reads_no_park_key_and_makes_no_hands(cfg, tmp_path, monkeypatch):
    """The existing two-stick switch, as tests/test_hot_standby.py runs it, with park.enabled off: no park.* key is
    read and ParkHands is never made."""
    from redzone.config import Config
    from test_hot_standby import FakeStick, FakeTuner
    real = Config.get

    def get(self, key, default=None):
        assert not str(key).startswith("park."), f"{key} read with park.enabled off"
        return real(self, key, default)
    monkeypatch.setattr(Config, "get", get)
    monkeypatch.setattr(ctl.ParkHands, "__init__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("made")))
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    c.sticks, c.hdmi = [FakeStick("a:5555"), FakeStick("b:5555")], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "explicit-test-display"
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner())
    FakeStick.last_input_host = "a:5555"

    async def input_status():
        return c.hdmi.get(FakeStick.last_input_host)
    monkeypatch.setattr(c.tv, "input_status", input_status)
    try:
        c.on_target = "youtubetv:fox"
        out = await c.execute("youtubetv:espn", may_cut=lambda: True)
        assert out.ok and c.main is c.sticks[1] and c._park_hands is None
    finally:
        FakeStick.last_input_host = None
        if c._refresh_task:
            c._refresh_task.cancel()


def test_flag_off_a_recorded_night_decides_identically(monkeypatch):
    """The recorded NBA night tests/test_timing_truth.py replays: the same decisions and switches with park.enabled
    on or off (nothing in the decision path calls the PARK steps yet), and with ParkHands unbuildable."""
    from test_timing_truth import _night_decisions
    monkeypatch.setattr(ctl.ParkHands, "__init__", lambda *a, **k: (_ for _ in ()).throw(AssertionError("made")))
    base = _night_decisions()
    assert len(base[0]) > 500 and base[1]
    assert _night_decisions(**{"park.enabled": True}) == base


async def test_without_the_sound_guard_or_silence_required_park_refuses(world):
    w = world
    w.tv.settings["sound_guard.must_be_silent"] = False
    assert (await w.c.park_tune("youtubetv:espn")).why.startswith("sound_guard.must_be_silent is off")
    w.tv.sound_guard = None
    assert (await w.c.park_tune("youtubetv:espn")).why.startswith("sound_guard.enabled is off")
    assert w.b.keys == [] and w.tuner.calls == []


# --- 1 and 2. TUNE and PLACE --------------------------------------------------------------------------------------

async def test_fresh_tune_waits_out_position_zero_then_reads_its_place_exactly(world):
    w = world
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok, out.why
    assert out.way.startswith("code") and out.way.endswith(", read") and w.tuner.calls == ["AAAAAAAAAAA"]
    assert out.numbers["ref_pos_s"] >= 60                     # never the 0.0 of the first 2 s
    assert out.numbers["read"] is True and out.numbers["padding"] == "read" and near(out.numbers["seek_s"], -30.0, 0.05)
    assert w.b.keys == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]
    place = w.hands.place_of(w.b)
    assert place.read and near(place.content(w.b.pos()), w.b.content(w.b.pos()), 1.01)   # the label is a whole-second floor
    assert near(out.numbers["behind_youtube_s"], w.b.behind(), 1.5)
    from redzone.noggin.park_timing import Tune                # the PARK timing model takes the same two numbers (S8)
    assert Tune(place.anchor, place.read).content(10.0) == place.content(10.0)
    u = w.hands.offsets.known("youtubetv:espn")                # ... and decider/tune_offset gets the tune's lag
    assert u is not None and u.kind == "label" and near(u.u_s, 20.0, 1.5)
    assert w.c.standby_m.is_("park_held") and w.c.hidden_target == "youtubetv:espn" and not w.c.hidden_ready
    assert w.hands.m.state("Stick on HDMI2") == "playing"
    assert "5555" not in str(out.view()) and "b:" not in str(out.view())


async def test_a_label_with_no_programme_start_is_placed_on_the_five_minute_grid(world):
    w = world
    w.labels.mode = "nostart"
    out = await w.c.park_tune("youtubetv:nbc")                 # nbc: the local station, 58.8 s behind after a fresh tune
    assert out.ok and out.numbers["read"] and out.numbers["start_inferred"] and w.hands.counts["inferred_start"] == 1
    assert out.way.startswith("local search") and w.tuner.calls == ["search:local:nbc"]
    assert near(w.hands.place_of(w.b).content(w.b.pos()), w.b.content(w.b.pos()), 1.01)


async def test_a_read_that_fails_is_retried_once_then_the_place_is_unread_and_says_so(world):
    w = world
    w.labels.mode = "fail"
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and out.numbers["read"] is False and out.numbers["padding"] == "unread"
    assert "unread padding" in out.why and "label not read" in out.numbers["read_why"]
    assert w.labels.calls == 2 and w.hands.counts["reads_tried"] == 2 and w.hands.counts["reads_ok"] == 0
    assert w.hands.offsets.known("youtubetv:espn") is None
    w2 = world
    w2.labels.mode, w2.labels.calls = "fail_once", 0
    out = await w2.c.park_tune("youtubetv:fs1")
    assert out.ok and out.numbers["read"] is True and w2.labels.calls == 2


async def test_a_stream_already_on_the_game_120_s_behind_is_never_taken_as_live(world):
    w = world
    w.b.load("espn", behind=120.0)                             # left there by an earlier run, 120 s behind live
    w.b.loaded_at -= 10
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and out.way == "kept: already on it, place read" and w.tuner.calls == []
    assert out.numbers["read"] and near(out.numbers["behind_youtube_s"], w.b.behind(), 1.5)
    assert out.numbers["behind_youtube_s"] > 150                 # 120 + 20 + the read's own 30 s and its pause


async def test_a_stream_already_on_the_game_whose_place_is_not_read_is_tuned_fresh(world):
    w = world
    w.labels.mode = "fail"
    w.b.load("espn", behind=120.0)
    w.b.loaded_at -= 10
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and out.way.startswith("already on it but its place was not read: tuned fresh; code")
    assert w.tuner.calls == ["AAAAAAAAAAA"] and w.b.behind() < 70  # live edge (20) + the read's 30 s and its pause


async def test_ways_in_order_and_a_failed_tune_that_named_the_game_counts(world):
    w = world
    w.tuner.fail.add("search:local:nbc")
    out = await w.c.park_tune("youtubetv:nbc")
    assert out.ok and w.tuner.calls == ["search:local:nbc", "search:nbc"] and out.way.startswith("local search failed; search")
    w.tuner.calls.clear()
    w.tuner.fail_but_on.add("AAAAAAAAAAA")
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and out.way.startswith("code, confirmed by what its media session names") and w.tuner.calls == ["AAAAAAAAAAA"]


async def test_a_tune_where_every_way_fails_says_why_and_holds_nothing(world):
    w = world
    w.tuner.fail.update({"AAAAAAAAAAA"})
    out = await w.c.park_tune("youtubetv:espn")
    assert out.outcome == "failed" and out.why == "video never started" and w.hands.place_of(w.b) is None
    assert w.c.standby_m.is_("empty")


# --- 3. PARK ------------------------------------------------------------------------------------------------------

async def test_park_freezes_the_wanted_frame_clears_the_banner_and_reads_it_back(world):
    w = world
    assert (await w.c.park_tune("youtubetv:espn")).ok
    w.b.keys.clear()
    at = w.clk.now() + 3.0
    out = await w.c.park_pause("youtubetv:espn", at)
    assert out.ok, out.why
    assert w.b.keys == ["MEDIA_PLAY_PAUSE", "BACK"]
    assert near(w.b.event, at, 0.5) and near(out.numbers["key_late_s"], 0.0, 0.5) and out.numbers["pause_lead_s"] == 0.60
    place = w.hands.place_of(w.b)
    assert place.paused and near(place.frozen_pos, w.b.frozen, 0.002) and w.hands.m.state("Stick on HDMI2") == "parked"
    assert w.c.standby_m.is_("park_held") and w.c.park_stale() == ""


async def test_a_pause_that_does_not_hold_is_reported_and_the_stick_still_plays(world):
    w = world
    assert (await w.c.park_tune("youtubetv:espn")).ok
    w.b.hold_fails = True                                      # BACK resumes it
    out = await w.c.park_pause("youtubetv:espn", None)
    assert out.outcome == "failed" and "after BACK it reports playing" in out.why
    assert not w.hands.place_of(w.b).paused and w.hands.m.state("Stick on HDMI2") == "playing"


async def test_a_pause_key_that_does_nothing_fails_without_more_keys(world):
    w = world
    assert (await w.c.park_tune("youtubetv:espn")).ok
    w.b.ignore_pause, w.b.keys = True, []
    out = await w.c.park_pause("youtubetv:espn", None)
    assert out.outcome == "failed" and "no paused report" in out.why and w.b.keys == ["MEDIA_PLAY_PAUSE"]


async def test_a_park_held_past_max_park_s_is_stale(world):
    w = world
    assert (await w.c.park_tune("youtubetv:espn")).ok and (await w.c.park_pause("youtubetv:espn", None)).ok
    w.c.cfg.update({"park": {"max_park_s": 1}})
    await w.clk.sleep(2.0)
    assert "past park.max_park_s" in w.c.park_stale()


# --- 4. JUMP ------------------------------------------------------------------------------------------------------

async def parked(w, target="youtubetv:espn"):
    assert (await w.c.park_tune(target)).ok and (await w.c.park_pause(target, None)).ok
    w.a.keys.clear()
    w.b.keys.clear()


async def test_jump_resumes_flips_pauses_the_stick_left_and_reads_input_and_silence_back(world):
    w = world
    await parked(w)
    w.c.on_target = "youtubetv:tnt"
    w.hands.places[w.a.host] = ctl.ParkPlace("youtubetv:tnt", w.a.origin, True, w.clk.now(), w.clk.now(), w.a.pos())
    w.hands._to(w.a, "reading", "test: stick a's place is known (as after an earlier jump)")
    w.hands._to(w.a, "playing", "test: on screen")
    frozen = w.b.frozen
    assert await w.tv.sound_guard.check("session start") == "ok"   # the first switch of a session silences first
    out = await w.c.park_jump("youtubetv:espn")
    assert out.ok, out.why
    assert w.b.keys == ["MEDIA_PLAY_PAUSE"] and w.a.keys == ["MEDIA_PLAY_PAUSE", "BACK"]
    assert w.tv.sets == [("HDMI2", ["smartthings"])] and w.tv.input == "HDMI2"
    assert w.b.ps == "playing" and near(w.b.pos0, frozen, 0.01)        # it played on from the frozen frame
    assert w.a.ps == "paused" and out.numbers["left_paused"] is True
    assert 2.6 <= out.numbers["flip_s"] <= 3.6 and out.numbers["input_ok"] and out.numbers["land_pos_s"] == round(frozen, 2)
    assert w.c.main is w.b and w.c.on_target == "youtubetv:espn" and w.c.hidden is w.a
    assert w.c.standby_m.is_("park_held") and w.c.hidden_target == "youtubetv:tnt"
    assert w.hands.m.state("Stick on HDMI1") == "parked" and w.hands.m.state("Stick on HDMI2") == "playing"
    assert w.tv.sound_guard.m.is_("ok") and w.tv.commands == []


async def test_the_pause_to_the_stick_left_goes_out_1_35_s_after_the_flip_command(world, monkeypatch):
    w = world
    await parked(w)
    stamps = []
    real_key = ctl.ParkHands._key

    async def key(self, stick, code):
        stamps.append((stick.host, code, w.clk.now()))
        return await real_key(self, stick, code)
    monkeypatch.setattr(ctl.ParkHands, "_key", key)
    real_set = w.tv.set_input

    async def set_input(hdmi, methods=None):
        stamps.append(("tv", hdmi, w.clk.now()))
        return await real_set(hdmi, methods)
    w.tv.set_input = set_input
    assert (await w.c.park_jump("youtubetv:espn")).ok
    t_res, t_flip, t_hold = stamps[0][2], stamps[1][2], stamps[2][2]
    assert stamps[0][:2] == ("b:5555", "KEYCODE_MEDIA_PLAY_PAUSE") and stamps[1][0] == "tv"
    assert stamps[2][:2] == ("a:5555", "KEYCODE_MEDIA_PLAY_PAUSE")
    assert near(t_flip - t_res, 0.3, 0.2) and near(t_hold - t_flip, 1.35, 0.25)


async def test_a_failed_flip_keeps_the_picture_up_playing_and_degrades_nothing(world):
    w = world
    await parked(w)
    w.c.on_target = "youtubetv:tnt"
    w.tv.flip_fails = True
    out = await w.c.park_jump("youtubetv:espn")
    assert out.outcome == "kept" and out.why == "the flip did not confirm"
    assert w.tv.input == "HDMI1" and w.a.ps == "playing" and w.a.keys == ["MEDIA_PLAY_PAUSE", "MEDIA_PLAY_PAUSE"]
    assert w.c.main is w.a and w.c.on_target == "youtubetv:tnt"
    assert w.c.two_stick_m.is_("on") and not w.c.input_unknown and w.c.park_why() == ""


async def test_a_sound_incident_mid_jump_stops_everything(world):
    w = world
    await parked(w)
    w.tv.unmute_on_flip = True
    out = await w.c.park_jump("youtubetv:espn")
    assert out.outcome == "stop" and "sound incident" in out.why
    assert w.tv.sound_guard.blocked and ("switch", "off") in w.tv.commands      # the guard turned the TV off
    assert {w.hands.m.state("Stick on HDMI1"), w.hands.m.state("Stick on HDMI2")} == {"stopped"}
    n_a, n_b, n_tv = len(w.a.keys), len(w.b.keys), len(w.tv.sets)
    assert "sound" in w.c.park_why()
    assert (await w.c.park_tune("youtubetv:fs1")).outcome == "refused"
    assert (await w.c.park_finish()).outcome == "stop"
    assert not await w.hands._key(w.a, "KEYCODE_MEDIA_PLAY_PAUSE")
    assert (len(w.a.keys), len(w.b.keys), len(w.tv.sets)) == (n_a, n_b, n_tv)    # nothing more was sent


async def test_an_open_incident_before_a_jump_sends_nothing(world):
    w = world
    await parked(w)
    await w.tv.sound_guard.incident({"mute": "unmuted"}, "test", "not silent")
    out = await w.c.park_jump("youtubetv:espn")
    assert out.outcome == "refused" and "sound incident open" in out.why
    assert w.a.keys == [] and w.b.keys == [] and w.tv.sets == []


# --- keys, finish ---------------------------------------------------------------------------------------------------

async def test_only_parks_own_keys_go_out(world):
    w = world
    for code in ("KEYCODE_HOME", "KEYCODE_VOLUME_UP", "KEYCODE_MUTE", "KEYCODE_POWER", "KEYCODE_WAKEUP"):
        assert not await w.hands._key(w.b, code)
    assert w.b.keys == []
    assert await w.hands._key(w.b, "KEYCODE_BACK") and w.b.keys == ["BACK"]


async def test_finish_never_leaves_a_stick_paused(world):
    w = world
    await parked(w)
    out = await w.c.park_finish()
    assert out.ok and w.b.ps == "playing" and w.b.keys == ["MEDIA_PLAY_PAUSE"] and w.a.keys == []
    assert w.hands.place_of(w.b) is None and w.c.standby_m.is_("empty")
    view = w.hands.view()
    assert set(view["sticks"]) == {"Stick on HDMI1", "Stick on HDMI2"} and view["last"]["step"] == "finish"


# --- S10: the controller's own switch and tune under the sound guard (sound_guard.enabled only) -------------------

class Guard:
    """What SamsungTV asks of a sound guard: an incident open (`why`), or a first-switch silence that fails."""

    def __init__(self, why="", first_fails=False):
        self.why, self.first_fails = why, first_fails

    def blocked_why(self):
        return self.why

    def ensure_running(self):
        pass

    async def before_switch(self):
        if self.first_fails:
            self.why = "silence could not be read back"         # the guard raises its incident and refuses
            return False
        return not self.why

    async def after_switch(self):
        pass

    def view(self):
        return {}


@pytest.fixture
def two_stick(cfg, tmp_path, monkeypatch):
    from test_hot_standby import FakeStick, FakeTuner
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    c.sticks, c.hdmi = [FakeStick("a:5555"), FakeStick("b:5555")], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "explicit-test-display"
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    FakeStick.last_input_host = "a:5555"

    async def input_status():
        return c.hdmi.get(FakeStick.last_input_host)
    monkeypatch.setattr(c.tv, "input_status", input_status)
    monkeypatch.setattr(type(c.tv), "paths", lambda self: ["smartthings"])
    monkeypatch.setattr(type(c.tv), "ready", lambda self: True)
    yield c, tuner
    FakeStick.last_input_host = None
    if c._refresh_task:
        c._refresh_task.cancel()


def homes(stick):
    return [x for x in stick.log if "KEYCODE_HOME" in x or "onetouchplay" in x or "WAKEUP" in x]


async def test_a_switch_the_guard_refuses_at_the_first_silence_check_sends_no_home_and_degrades_nothing(two_stick):
    c, _ = two_stick
    c.tv.sound_guard = Guard(first_fails=True)
    a, b = c.sticks
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert not out.ok and out.screen_unchanged and "sound guard" in out.detail
    assert homes(a) == [] and homes(b) == [] and c.main is a
    assert c.two_stick_m.is_("on") and not c.input_unknown and c.on_target == "youtubetv:fox"


async def test_no_tune_goes_out_while_a_sound_incident_is_open(two_stick):
    c, tuner = two_stick
    c.tv.sound_guard = Guard("not silent")
    res = await c._tune(c.sticks[1], "youtubetv:espn")
    assert not res.ok and res.error.startswith("sound incident open") and tuner.calls == [] and c.sticks[1].log == []
    c.tv.sound_guard = None                                   # no guard (sound_guard.enabled off): as before
    assert (await c._tune(c.sticks[1], "youtubetv:espn")).ok and tuner.calls == [("b:5555", "youtubetv:espn")]
