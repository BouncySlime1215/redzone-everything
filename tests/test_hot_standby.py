"""Hot standby with fake sticks: flip-back, cancelling stale preloads, parked replays, the sleep
guard, learned switch times, and the engine handing those times to the decider."""
import asyncio
import time

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.config import Config
from redzone.decider.decider import Candidate, Missed
from redzone.engine import Engine
from redzone.models import League
from redzone.scoring.engine import Scored
from redzone.store import Store
from redzone.tuners import tuners
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult


class FakeStick(Stick):
    """A stick that logs every command. playback() reports `state`; dumpsys power reports `power`."""

    last_input_host = None

    def __init__(self, host, cec_ok=True, state="playing", power="Awake"):
        super().__init__(host, name=host, os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": "yttv.pkg", "prime": "prime.pkg"}
        self.log = []
        self.cec_ok = cec_ok
        self.playing = state
        self.power = power

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
            await asyncio.sleep(0.03)   # a real adb round trip is never instant
            if self.cec_ok:
                FakeStick.last_input_host = self.host
            return RunResult(self.cec_ok, "" if self.cec_ok else "Error: unknown command", 0.01)
        if "mWakefulness" in cmd:
            return RunResult(True, f"  mWakefulness={self.power}", 0.01)
        return RunResult(True, "", 0.01)

    async def key(self, *codes):
        self.log.extend(codes)
        return RunResult(True, "", 0.01)

    async def playback(self, package=None):
        return {"state": self.playing, "pkg": package, "position": 0, "updated": 0}


class FakeTuner:
    """Tunes instantly (or after `slow[target]` seconds); reports a realistic 4s so it's a real sample."""

    def __init__(self, ok=True, took=4.0, slow=None):
        self.ok, self.took, self.slow, self.calls = ok, took, slow or {}, []

    async def tune(self, stick, ref, target, may_cut=None):
        self.calls.append((stick.host, target))
        if target in self.slow:
            await asyncio.sleep(self.slow[target])
        if may_cut is not None and not may_cut():
            return TuneResult(False, target, 0.0, [], "safe exit closed", screen_changed=False)
        if self.ok:
            stick.remember(target, await stick.playback(stick.packages.get('youtubetv')))
        return TuneResult(self.ok, target, self.took, ["playing"], "" if self.ok else "video never started")


@pytest.fixture
def two(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "explicit-test-display"
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    async def input_status():
        return c.hdmi.get(FakeStick.last_input_host)
    monkeypatch.setattr(c.tv, 'input_status', input_status)
    return c, tuner


async def refreshed(c):
    """Let the post-flip refresh of the game we left run to the end (preload included)."""
    if c._refresh_task:
        await asyncio.wait_for(asyncio.gather(c._refresh_task, return_exceptions=True), 2)
    if c._preload_task:
        await asyncio.wait_for(asyncio.gather(c._preload_task, return_exceptions=True), 2)


def flips(stick):
    return [x for x in stick.log if "onetouchplay" in x]


def rewind_presses(stick):
    return sum(x.count("MEDIA_REWIND") for x in stick.log)


# --- flip-back ------------------------------------------------------------------------------

async def test_flip_back_makes_going_back_a_flip_not_a_tune(two, monkeypatch):
    """The stick we leave pauses when it loses the TV input, so the game we left is reloaded live
    on it in the background; once that's done, going back is one flip."""
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, tuner = two
    c.on_target = "youtubetv:fox"                       # stick a is showing FOX live
    c.main.remember(c.on_target, await c.main.playback('yttv.pkg'))
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and out.path == "tuned" and c.main.host == "b:5555"
    assert c.standby == ("youtubetv:fox", None) and not c.hidden_ready   # a has FOX, paused
    await refreshed(c)
    assert c.hidden_ready and tuner.calls[-1] == ("a:5555", "youtubetv:fox")   # reloaded live
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)
    assert out.ok and out.path == "preloaded" and c.main.host == "a:5555"
    assert tuner.calls == [("b:5555", "youtubetv:espn"), ("a:5555", "youtubetv:fox")]   # no tune at the switch
    assert c.standby == ("youtubetv:espn", None)        # ...and ESPN is being reloaded on b


async def test_going_back_before_the_refresh_is_a_tune_not_a_flip_to_a_paused_stream(two):
    c, tuner = two
    c.on_target = "youtubetv:fox"
    c.main.remember(c.on_target, await c.main.playback('yttv.pkg'))
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)   # right away: a is still paused
    assert out.ok and out.path == "tuned" and tuner.calls[-1] == ("a:5555", "youtubetv:fox")


async def test_retuning_the_hidden_stick_forgets_the_flip_back(two):
    c, tuner = two
    c.on_target = "youtubetv:fox"
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert c.preload("youtubetv:cbs")
    await c._preload_task
    assert c.standby == ("youtubetv:cbs", None)
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)
    assert out.path == "tuned" and tuner.calls[-1] == ("a:5555", "youtubetv:fox")


async def test_already_on_it_does_nothing(two):
    c, tuner = two
    c.on_target = "youtubetv:fox"
    c.main.remember(c.on_target, await c.main.playback("yttv.pkg"))
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)
    assert out.ok and out.path == "already" and tuner.calls == [] and not flips(c.sticks[1])


async def test_verify_drops_a_flip_back_that_stopped_playing(two, monkeypatch):
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, _ = two
    c.on_target = "youtubetv:fox"
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    await refreshed(c)
    assert c.standby == ("youtubetv:fox", None) and c.hidden_ready
    c.hidden.playing = "paused"                         # CEC paused the stick that lost the input
    c._verified_at = 0
    await c.verify_standby()
    assert c.standby == (None, None)


# --- preloads vs switches ---------------------------------------------------------------------

async def test_a_switch_cancels_a_preload_of_some_other_game(two):
    c, tuner = two
    tuner.slow = {"youtubetv:fox": 5.0}
    assert c.preload("youtubetv:fox")
    await asyncio.sleep(0.05)                           # mid-tune on the hidden stick
    done = asyncio.Event()
    outs = []
    t0 = time.monotonic()
    c.submit("youtubetv:espn", on_done=lambda o: (outs.append(o), done.set()), may_cut=lambda: True)
    await asyncio.wait_for(done.wait(), 2)
    assert outs[0].ok and time.monotonic() - t0 < 1.0   # didn't queue behind the 5s preload
    assert c._preload_task.cancelled() and c.main.host == "b:5555"


async def test_a_switch_picks_up_a_preload_of_the_same_game(two):
    c, tuner = two
    tuner.slow = {"youtubetv:fox": 0.2}
    assert c.preload("youtubetv:fox")
    await asyncio.sleep(0.05)
    done = asyncio.Event()
    outs = []
    c.submit("youtubetv:fox", on_done=lambda o: (outs.append(o), done.set()), may_cut=lambda: True)
    await asyncio.wait_for(done.wait(), 2)
    assert outs[0].ok and outs[0].path == "preloaded"
    assert tuner.calls == [("b:5555", "youtubetv:fox")]  # tuned once, by the preload


async def test_a_switch_cancelled_mid_tune_leaves_no_false_standby(two):
    c, tuner = two
    tuner.slow = {"youtubetv:fox": 5.0}
    c.submit("youtubetv:fox", may_cut=lambda: True)
    await asyncio.sleep(0.05)                           # hidden stick is mid-tune to FOX
    c._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await c._task
    assert c.standby == (None, None)                    # not "FOX, loading" forever


async def test_a_new_command_never_cancels_a_flip_in_progress(two, monkeypatch):
    c, _ = two
    started = asyncio.Event()

    async def slow_flip(stick):
        started.set()
        await asyncio.sleep(0.2)
        return True
    monkeypatch.setattr(c, "_flip_to", slow_flip)
    outs, done = [], asyncio.Event()
    c.submit("youtubetv:espn", on_done=outs.append, may_cut=lambda: True)
    await asyncio.wait_for(started.wait(), 1)
    c.submit("youtubetv:fox", on_done=lambda o: (outs.append(o), done.set()), may_cut=lambda: True)
    await asyncio.wait_for(done.wait(), 2)
    assert [(o.target, o.ok) for o in outs] == [("youtubetv:espn", True), ("youtubetv:fox", True)]
    assert c.main.host == "a:5555" and c.standby == ("youtubetv:espn", None)


async def test_single_stick_cancelled_tune_forgets_what_is_on(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555")]
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner(slow={"youtubetv:espn": 5.0}))
    c.on_target = "youtubetv:fox"
    c.submit("youtubetv:espn")
    await asyncio.sleep(0.05)
    c._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await c._task
    assert c.on_target is None                          # so "back to FOX" re-tunes, not "already on it"


async def test_a_failed_preload_backs_off(two):
    c, tuner = two
    tuner.ok = False
    assert c.preload("youtubetv:fox")
    await c._preload_task
    assert c.standby == (None, None)
    c._last_preload = 0
    assert not c.can_tune("youtubetv:fox") and not c.preload("youtubetv:fox")


async def test_a_sleeping_hidden_stick_is_never_woken_by_a_preload(two):
    c, tuner = two
    c.sticks[1].power = "Asleep"
    assert c.preload("youtubetv:fox")
    await c._preload_task
    assert tuner.calls == [] and "KEYCODE_WAKEUP" not in c.sticks[1].log
    c._last_preload = 0
    assert not c.preload("youtubetv:fox")               # and it doesn't keep asking


async def test_a_switch_wakes_a_sleeping_hidden_stick_only_at_a_safe_moment(two, monkeypatch):
    c, tuner = two
    monkeypatch.setattr(ctl, "SAFE_POLL_S", 0.01)
    c.sticks[1].power = "Asleep"
    asked = {"n": 0, "at_tune": None}

    def may_cut():
        asked["n"] += 1
        return asked["n"] > 3

    async def tune(stick, ref, target, may_cut=None):
        asked["at_tune"] = asked["n"]
        stick.remember(target, await stick.playback('yttv.pkg'))
        return TuneResult(True, target, 4.0, ["playing"])
    monkeypatch.setattr(tuner, "tune", tune)
    out = await c.execute("youtubetv:espn", may_cut=may_cut)
    assert out.ok and asked["at_tune"] == 4             # the wake/tune came only after the gate opened


# --- parked replays ---------------------------------------------------------------------------

async def test_a_parked_replay_starts_the_instant_we_flip(two):
    c, tuner = two
    c.sticks[1].playing = "paused"
    assert c.preload("youtubetv:espn", back_s=60, replay_id="p1")
    await c._preload_task
    hid = c.hidden
    assert c.standby == ("youtubetv:espn", "p1") and c.parked.paused
    assert rewind_presses(hid) >= 5 and hid.log[-1] == "KEYCODE_MEDIA_PAUSE"   # 60 s: 4 x 15 s + 1 wake press
    hid.log.clear()
    tuner.calls.clear()
    out = await c.execute("youtubetv:espn", replay_back_s=75, may_cut=lambda: True, replay_id="p1")
    assert out.ok and out.path == "parked replay" and tuner.calls == [] and rewind_presses(hid) == 0
    assert hid.log.index("KEYCODE_MEDIA_PLAY") < hid.log.index(flips(hid)[0])   # roll it, then show it
    assert c.on_target is None                          # replay footage isn't live


async def test_a_parked_replay_that_drifted_is_pulled_back(two):
    c, tuner = two
    c.sticks[1].playing = "playing"                     # PAUSE didn't take: it keeps rolling
    assert c.preload("youtubetv:espn", back_s=60, replay_id="p1")
    await c._preload_task
    assert not c.parked.paused
    c.parked.at -= 30                                   # it has been rolling for 30s
    hid = c.hidden
    hid.log.clear()
    out = await c.execute("youtubetv:espn", replay_back_s=95, may_cut=lambda: True, replay_id="p1")
    assert out.ok and out.path == "parked replay" and rewind_presses(hid) == 3   # 30s of drift, 10s presses


async def test_a_drifting_park_is_pulled_back_in_the_background(two):
    c, _ = two
    c.sticks[1].playing = "playing"
    assert c.preload("youtubetv:espn", back_s=60, replay_id="p1")
    await c._preload_task
    c.parked.at -= 35                                   # past 2 seek steps (2 x 15 s, measured Nov 4)
    hid = c.hidden
    hid.log.clear()
    c._maybe_reseek()                                   # keepalive does this every ~10s
    await c._preload_task
    assert rewind_presses(hid) == 4 and c.standby == ("youtubetv:espn", "p1") and time.time() - c.parked.at < 1


async def test_a_live_switch_never_uses_parked_replay_footage(two):
    c, tuner = two
    assert c.preload("youtubetv:espn", back_s=60, replay_id="p1")
    await c._preload_task
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and out.path == "tuned" and tuner.calls[-1] == ("b:5555", "youtubetv:espn")


async def test_an_unparked_replay_holds_still_while_waiting_for_a_safe_moment(two, monkeypatch):
    c, _ = two
    monkeypatch.setattr(ctl, "SAFE_POLL_S", 0.01)
    gate = iter([False, False, True])
    out = await c.execute("youtubetv:espn", replay_back_s=30, may_cut=lambda: next(gate, True), replay_id="p9")
    hid = c.sticks[1]
    assert out.ok and out.path == "tuned" and out.waited > 0
    pause, last_play, flip = (hid.log.index("KEYCODE_MEDIA_PAUSE"), len(hid.log) - 1 - hid.log[::-1].index(
        "KEYCODE_MEDIA_PLAY"), hid.log.index(flips(hid)[0]))
    assert pause < last_play < flip


async def test_single_stick_replay_rewind_counts_the_tune_time(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}, "replay": {"seek_step_s": 0.5}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes["espn"] = "AAAAAAAAAAA"
    c = ctl.Controller(cfg, ch)
    c.sticks = [FakeStick("a:5555")]
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FakeTuner(slow={"youtubetv:espn": 0.6}))
    out = await c.execute("youtubetv:espn", replay_back_s=3.0)
    # 3.0s back + ~0.6s spent tuning (- 1s allowed late) = 6 presses of 0.5s; ignoring the tune time gave 4
    assert out.ok and out.path == "single" and rewind_presses(c.sticks[0]) >= 6


# --- learned switch times -----------------------------------------------------------------------

def test_latency_book_median_ignores_glitches_and_keeps_the_last_15():
    b = ctl.LatencyBook()
    for x in (5, 6, 7, 30):
        b.record("youtubetv", x)
    assert b.median("youtubetv") == 6.5                 # one stuck 30s tune barely moves it
    b.record("youtubetv", 0.01)                         # a fake/instant "tune" isn't a measurement
    b.record("youtubetv", 500)
    assert len(b.samples["youtubetv"]) == 4
    for _ in range(20):
        b.record("flip", 0.4)
    assert len(b.samples["flip"]) == 15 and b.median("flip") == 0.4
    assert b.median("prime") is None


def test_latency_book_survives_a_restart(tmp_path):
    b = ctl.LatencyBook()
    b.record("prime", 9.5)
    b.note_preloaded("youtubetv", True)
    b.save(tmp_path / "times.json")
    b2 = ctl.LatencyBook()
    b2.load(tmp_path / "times.json")
    assert b2.median("prime") == 9.5 and b2.miss_rate("youtubetv") == 1 / 3
    ctl.LatencyBook().load(tmp_path / "missing.json")   # nothing there: fine


async def test_every_real_tune_and_flip_is_timed(two):
    c, _ = two
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert c.latency.median("youtubetv") == 4.0 and c.latency.median("flip") is not None
    assert c.latency.preloaded["youtubetv"][-1] is False  # this one still needed a tune


def test_switch_costs_follow_the_path_we_would_use(two):
    c, _ = two
    for _ in range(3):
        c.latency.record("youtubetv", 6.0)
        c.latency.record("flip", 0.5)
    for hit in (True, True, True, False):
        c.latency.note_preloaded("youtubetv", hit)
    costs = c.switch_costs()
    # flip 0.5 + 1.0 TV settle = 1.5; a tune is needed (1+1)/(4+2) = 1/3 of the time: 1.5 + 6/3
    assert costs["hdmi_flip"] == 1.5 and costs["youtubetv"] == 3.5
    prime = c._default_cost("prime")                    # the shipped (measured) default
    assert costs["prime"] == 1.5 + 0.5 * prime          # nothing measured here: half of the default
    c.two_stick_ok = False                              # one stick: every switch is a full tune
    assert c.switch_costs()["youtubetv"] == 6.0 and c.switch_costs()["prime"] == prime


async def test_cec_that_failed_goes_straight_to_the_tv_next_time(two, monkeypatch):
    c, _ = two
    for s in c.sticks:
        s.cec_ok = False

    async def tv_ok(hdmi):
        return True
    monkeypatch.setattr(c.tv, "set_input", tv_ok)
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    await c.execute("youtubetv:fox", may_cut=lambda: True)
    await c.execute("youtubetv:cbs", may_cut=lambda: True)
    assert len(flips(c.sticks[1])) <= 1                 # HOME first; onetouchplay tried at most once
    assert c.two_stick                                  # the TV remote worked: still two sticks


# --- engine: policy wiring and cost sync ------------------------------------------------------------

def sc(gid, score, pre=None, is_break=False):
    return Scored(gid, League.NFL, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break, None, pre, 1.0)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}})
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    e.channels = ChannelMap(tmp_path / "channels.json")
    e.channels.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC",
                             "nbc": "DDDDDDDDDDD"})
    e.control.channels = e.channels
    e.control.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    e.control.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    e.control.tv.st_device = 'explicit-test-display'
    calls = []
    monkeypatch.setattr(e.control, "preload",
                        lambda target, back_s=None, replay_id=None: calls.append((target, back_s, replay_id)) or True)
    return e, calls


def test_engine_keeps_your_game_on_the_hidden_stick_during_a_break_visit(eng):
    e, calls = eng
    e.decider.current, e.decider.home_break_visit = "visit", "home"
    cands = {"home": Candidate(sc("home", 40, is_break=True), "youtubetv:espn"),
             "visit": Candidate(sc("visit", 60), "youtubetv:fox"),
             "hot": Candidate(sc("hot", 95, pre="4th & goal"), "youtubetv:cbs")}
    e._preload_standby(cands)
    assert calls == [("youtubetv:espn", None, None)]
    view = e._standby_view()
    assert view is None                                  # (preload is stubbed: nothing loaded yet)


async def test_break_visit_end_to_end_the_way_home_is_one_flip(tmp_path, monkeypatch):
    """Your game breaks, we visit another, the hidden stick keeps YOUR game live through every
    decision tick (the old policy re-tuned it to the best other game after 30s), and going home
    when play resumes is a flip. Leaving your game pauses its stick, so it's reloaded live there
    once in the background (the refresh); the switch home itself costs no tune."""
    from conftest import FakeClock
    from redzone import standby
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}})
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    e.channels = ChannelMap(tmp_path / "channels.json")
    e.channels.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC"})
    c = e.control
    c.channels = e.channels
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = 'explicit-test-display'
    async def input_status():
        return c.hdmi.get(FakeStick.last_input_host)
    monkeypatch.setattr(c.tv, 'input_status', input_status)
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    clock = FakeClock()
    e.planner = standby.Planner(cfg, clock=clock)
    c.on_target = "youtubetv:espn"                       # watching your game on stick a
    c.main.remember(c.on_target, await c.main.playback('yttv.pkg'))
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)   # its break: visit FOX
    assert out.ok and c.standby == ("youtubetv:espn", None)
    e.decider.current, e.decider.home_break_visit = "visit", "home"
    cands = {"home": Candidate(sc("home", 45, is_break=True), "youtubetv:espn"),
             "visit": Candidate(sc("visit", 60), "youtubetv:fox"),
             "hot": Candidate(sc("hot", 92, pre="4th & goal"), "youtubetv:cbs")}
    for _ in range(150):                                 # 150 decision ticks across the break
        clock.t += 1
        e._preload_standby(cands)
        await asyncio.sleep(0.002)
    await refreshed(c)
    assert c.standby == ("youtubetv:espn", None) and c.hidden_ready
    assert tuner.calls == [("b:5555", "youtubetv:fox"), ("a:5555", "youtubetv:espn")]   # the refresh
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)  # play resumes: home
    assert out.ok and out.path == "preloaded" and c.main.host == "a:5555"
    assert len(tuner.calls) == 2                         # no tune at either switch


def test_engine_cues_a_replay_when_the_game_on_screen_is_about_to_break(eng):
    e, calls = eng
    e.decider.current = "cur"
    e.scored["cur"] = sc("cur", 50, is_break=True)       # the data says break; your screen is ~45s behind
    e.decider.queue = [Missed("x", "p1", "TD pass", "td", time.time() - 60, 80.0)]
    cands = {"cur": Candidate(sc("cur", 50, is_break=True), "youtubetv:espn"),
             "x": Candidate(sc("x", 40), "youtubetv:fox"), "y": Candidate(sc("y", 70), "youtubetv:cbs")}
    e._preload_standby(cands)
    target, back, rid = calls[0]
    # rewound to 3s before the snap: 60s ago + 8s play (league unknown) + 3s lead, minus the screen's delay
    assert (target, rid) == ("youtubetv:fox", "p1")
    assert back == pytest.approx(71 - e.delays.get("youtubetv:fox").mu, abs=1)


def test_engine_hands_measured_times_to_the_decider_through_settings(eng):
    e, _ = eng
    e.control.two_stick_ok = False                      # one stick: costs are plain tune times
    for _ in range(5):
        e.control.latency.record("youtubetv", 7.2)      # clear of the shipped 5.5 (a move is >= 10 %)
        e.control.latency.record("prime", 11.0)
    e._sync_switch_costs()
    assert e.cfg.get("decider.switch_cost.youtubetv") == 7.0 and e.cfg.get("decider.switch_cost.prime") == 11.0
    assert e.decider._cost("youtubetv:espn") == 7.0     # the decider plans with it now
    assert not list((e.cfg.history_dir).glob("*.yaml"))[1:]   # learned values stay out of Undo
    for _ in range(15):
        e.control.latency.record("youtubetv", 9.0)
    e._sync_switch_costs()
    assert e.cfg.get("decider.switch_cost.youtubetv") == 7.0   # at most every few minutes...
    for hit in (True, True, True, True):
        e.control.latency.record("flip", 0.5)
        e.control.latency.note_preloaded("youtubetv", hit)
    e.control.two_stick_ok = True                       # ...unless the path changes: two sticks = flips
    e._sync_switch_costs()
    # flip 0.5 + 1.0 settle, plus a 9s tune 1/6 of the time (4 of 4 preloaded, smoothed)
    assert e.cfg.get("decider.switch_cost.youtubetv") == 3.0
    assert (e.cfg.user_path.parent / "switch_times.json").exists()


def test_snapshot_shows_switch_times(eng):
    e, _ = eng
    e.control.latency.record("youtubetv", 4.4)
    h = e.snapshot()["health"]["switch_times"]
    assert h["measured"]["youtubetv"]["median_s"] == 4.4 and h["path"] == "flip" and "youtubetv" in h["cost"]


# --- tuner: confirm loop speed ----------------------------------------------------------------

class ConfirmStick(Stick):
    def __init__(self, states):
        super().__init__("t:5555", name="t")
        self.packages = {"youtubetv": "yttv.pkg"}
        self.states, self.texts, self.keys = list(states), 0, []

    async def playback(self, package=None):
        return {"state": self.states.pop(0) if len(self.states) > 1 else self.states[0], "pkg": package}

    async def shell(self, cmd, timeout=8):
        return RunResult(True, "mWakefulness=Awake", 0.01)

    async def screen_text(self):
        self.texts += 1
        return "Are you still watching? | Continue"

    async def key(self, *codes):
        self.keys.extend(codes)

    async def view(self, url, package, wait=False):
        return RunResult(True, "", 0.01)


@pytest.fixture
def fast_confirm(monkeypatch):
    monkeypatch.setattr(tuners.Tuner, "poll_s", 0.001)
    monkeypatch.setattr(tuners.Tuner, "prompt_grace_s", 0.0)
    monkeypatch.setattr(tuners.Tuner, "loading_grace_s", 60.0)


async def test_tuner_doesnt_stop_to_read_the_screen_while_the_stream_loads(fast_confirm):
    st = ConfirmStick(["buffering"] * 5 + ["playing"])
    res = await tuners.YouTubeTVTuner().tune(st, "AAAAAAAAAAA", "youtubetv:espn")
    assert res.ok and st.texts == 0


async def test_tuner_still_clears_a_prompt_when_nothing_is_loading(fast_confirm):
    st = ConfirmStick(["none"] * 3 + ["playing"])
    res = await tuners.YouTubeTVTuner().tune(st, "AAAAAAAAAAA", "youtubetv:espn")
    assert res.ok and st.texts == 1 and "KEYCODE_DPAD_CENTER" in st.keys


# --- refreshing the game we left after a flip ----------------------------------------------

async def flipped_away(c, monkeypatch, poll=0.01):
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", poll)
    c.on_target = "youtubetv:fox"
    c.main.remember(c.on_target, await c.main.playback('yttv.pkg'))
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c._refresh_task is not None and not c._refresh_task.done()


async def test_close_cancels_the_refresh(two, monkeypatch):
    c, tuner = two
    await flipped_away(c, monkeypatch, poll=5.0)
    task = c._refresh_task
    await c.close()
    assert task.cancelled() and tuner.calls == [("b:5555", "youtubetv:espn")]


async def test_a_new_switch_cancels_the_refresh(two, monkeypatch):
    c, tuner = two
    await flipped_away(c, monkeypatch, poll=0.2)
    task = c._refresh_task
    done = asyncio.Event()
    c.submit("youtubetv:cbs", on_done=lambda o: done.set(), may_cut=lambda: True)
    await asyncio.wait_for(done.wait(), 2)
    await asyncio.sleep(0.3)
    assert task.cancelled()
    assert ("a:5555", "youtubetv:fox") not in tuner.calls    # the switch decided; no stale reload


async def test_the_planners_pick_wins_over_a_refresh_mid_tune(two, monkeypatch):
    c, tuner = two
    tuner.slow = {"youtubetv:fox": 5.0}
    await flipped_away(c, monkeypatch)
    for _ in range(100):
        if c._refreshing:
            break
        await asyncio.sleep(0.01)
    assert c._refreshing == "youtubetv:fox"             # the refresh is tuning FOX on a (slowly)
    refresh = c._preload_task
    assert c.preload("youtubetv:cbs")                   # the planner wants CBS there instead
    await asyncio.wait_for(c._preload_task, 2)
    assert refresh.cancelled()
    assert c.standby == ("youtubetv:cbs", None) and c.hidden_ready and c._refreshing is None


async def test_the_planners_preload_cancels_a_waiting_refresh(two, monkeypatch):
    c, tuner = two
    await flipped_away(c, monkeypatch, poll=0.2)
    task = c._refresh_task
    assert c.preload("youtubetv:cbs")                   # no gap used up by the refresh
    await c._preload_task
    await asyncio.sleep(0.3)
    assert task.cancelled() and c.standby == ("youtubetv:cbs", None)
    assert ("a:5555", "youtubetv:fox") not in tuner.calls


async def test_a_refresh_doesnt_use_up_the_planners_preload_gap(two, monkeypatch):
    c, tuner = two
    await flipped_away(c, monkeypatch)
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", "youtubetv:fox")
    assert c.preload("youtubetv:cbs")                   # the planner can change it straight away


async def test_a_refresh_never_wakes_a_sleeping_stick_or_presses_home(two, monkeypatch):
    c, tuner = two
    await flipped_away(c, monkeypatch)
    left = c.hidden
    left.power = "Asleep"                               # the stick we left dozed off
    c._awake_at.pop(left.host, None)
    before = len(left.log)
    await refreshed(c)
    new = left.log[before:]
    assert tuner.calls == [("b:5555", "youtubetv:espn")]
    assert not any("WAKEUP" in x or "HOME" in x or "onetouchplay" in x for x in new)
    assert c.main.host == "b:5555" and FakeStick.last_input_host == "b:5555"


# --- phone controls: a game opened on the phone, "go now" on a preloaded game ------------------

async def test_a_game_opened_on_the_phone_skips_the_preload_gap_and_displaces_the_planner(two):
    """preload(user=True) (engine._peek): what you opened wins over the planner's guess still loading,
    and the planner's MIN_PRELOAD_GAP_S doesn't make you wait."""
    c, tuner = two
    tuner.slow = {"youtubetv:fox": 5.0}
    assert c.preload("youtubetv:fox")                   # the planner's guess, slow to load
    await asyncio.sleep(0.05)
    guess = c._preload_task
    assert not c.preload("youtubetv:cbs")               # a second planner preload waits out the gap
    assert c.preload("youtubetv:cbs", user=True)        # your pick doesn't
    await asyncio.wait_for(c._preload_task, 2)
    assert guess.cancelled() and c.standby == ("youtubetv:cbs", None) and c.hidden_ready


async def test_a_user_preload_never_displaces_a_switch(two):
    c, tuner = two
    tuner.slow = {"youtubetv:espn": 0.3}
    done = asyncio.Event()
    c.submit("youtubetv:espn", on_done=lambda o: done.set(), may_cut=lambda: True)
    await asyncio.sleep(0.05)
    assert not c.preload("youtubetv:cbs", user=True)    # the switch owns the sticks
    await asyncio.wait_for(done.wait(), 2)


async def test_go_now_on_a_fresh_preload_flips_without_a_second_steady_wait(two, monkeypatch):
    """The tune's confirming read counts as the first of the flip's two steady reads, so "go now"
    right after a preload costs one read, not FLIP_STEADY_S more."""
    monkeypatch.setattr(ctl, "FLIP_STEADY_S", 1.5)
    c, tuner = two
    assert c.preload("youtubetv:fox")
    await asyncio.wait_for(c._preload_task, 2)
    assert c._steady.get(c.hidden.host, (None,))[0] == ("youtubetv:fox", 0)
    await asyncio.sleep(1.55)
    done = asyncio.Event()
    outs = []
    t0 = time.monotonic()
    c.submit("youtubetv:fox", on_done=lambda o: (outs.append(o), done.set()), may_cut=lambda: True)
    await asyncio.wait_for(done.wait(), 4)
    assert outs[0].ok and outs[0].path == "preloaded"
    assert time.monotonic() - t0 < 1.2                  # the flip itself (~0.6 s here), no 1.5 s steady wait


async def test_verify_standby_keeps_the_steady_evidence_fresh(two):
    c, tuner = two
    assert c.preload("youtubetv:fox")
    await asyncio.wait_for(c._preload_task, 2)
    c._steady.pop(c.hidden.host, None)
    c._verified_at = 0
    await c.verify_standby()
    assert c._steady.get(c.hidden.host, (None,))[0] == ("youtubetv:fox", 0)


# --- playbook=TV-04: a stick that rebooted woke the TV onto itself ------------------------------

@pytest.fixture
def readback(two, monkeypatch):
    c, tuner = two
    monkeypatch.setattr(type(c.tv), "has_readback", property(lambda s: True))
    return c, tuner


async def _stolen(c):
    """Stick a shows ESPN; then stick b (rebooted, on its home screen) takes the TV by HDMI-CEC."""
    c.on_target = "youtubetv:espn"
    c.main.remember(c.on_target, await c.main.playback('yttv.pkg'))
    FakeStick.last_input_host = "a:5555"
    await c._sync_from_tv()
    c.sticks[1].playing, c.sticks[1].tuned = "none", None
    FakeStick.last_input_host = "b:5555"
    assert await c._sync_from_tv() and c.main.host == "b:5555"


@pytest.mark.playbook("TV-04")
async def test_a_cec_steal_is_undone_and_nothing_preloads_over_the_game_meanwhile(readback):
    c, tuner = readback
    c.cfg.update({"hardening": {"tv": True}})
    await _stolen(c)
    assert c._steal_task is not None
    assert not c.preload("youtubetv:fox")       # the "hidden" stick is the game the TV jumped off
    await asyncio.wait_for(c._steal_task, 5)
    assert c.main.host == "a:5555" and c.on_target == "youtubetv:espn"
    assert FakeStick.last_input_host == "a:5555" and tuner.calls == []


async def test_a_cec_steal_is_left_alone_with_hardening_off(readback):
    c, _ = readback
    await _stolen(c)
    assert c._steal_task is None and c.main.host == "b:5555"


@pytest.mark.playbook("TV-04")
async def test_a_jump_to_a_stick_playing_something_is_a_person_and_stays(readback):
    c, _ = readback
    c.cfg.update({"hardening": {"tv": True}})
    c.on_target = "youtubetv:espn"
    c.main.remember(c.on_target, await c.main.playback('yttv.pkg'))
    FakeStick.last_input_host = "a:5555"
    await c._sync_from_tv()
    c.sticks[1].tuned = None                    # b plays something we never put there
    FakeStick.last_input_host = "b:5555"
    await c._sync_from_tv()
    await asyncio.wait_for(c._steal_task, 5)
    assert c.main.host == "b:5555"
