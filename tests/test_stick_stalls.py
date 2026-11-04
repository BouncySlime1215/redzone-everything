"""hardening.sticks: a player stuck "buffering" is a stalled player (fakes only, no sticks):
- the visible stick buffering 20 s+ is repaired like a crashed app (re-link), never sooner;
- a standby stuck buffering is dropped so the planner reloads it, instead of being flipped to;
- reads too far apart restart the clock (it may have played in between);
- off (the default): buffering always counts as playing, as before."""
import asyncio

import pytest

from redzone import control as ctl
from test_hdmi_flip import fast, live  # noqa: F401  (fixture)
from test_hot_standby import FakeStick, two  # noqa: F401  (fixture)


class Clock:
    def __init__(self):
        self.t = 10_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(ctl.time, "time", c)
    return c


def harden(c, on=True):
    c.cfg.update({"hardening": {"sticks": on}})


async def test_visible_stick_buffering_20s_is_relinked(two, clock, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    c, tuner = two
    live(c)
    harden(c)
    c.on_target = "youtubetv:fox"
    c.main.playing = "buffering"
    orig = tuner.tune

    async def tune(stick, ref, target, may_cut=None):
        stick.playing = "playing"
        return await orig(stick, ref, target, may_cut)
    monkeypatch.setattr(tuner, "tune", tune)
    for _ in range(3):                       # 0, 10, 19 s: still a load
        await c._watch_visible()
        clock.t += 9.5
    assert c._visible_bad_since is None and tuner.calls == []
    clock.t += 1.0                           # 20.0 s of buffering
    await c._watch_visible()                 # stalled: first look
    await c._watch_visible()                 # repair runs as its own task
    await asyncio.wait_for(asyncio.gather(c._resume_task, return_exceptions=True), 2)
    assert tuner.calls == [("a:5555", "youtubetv:fox")]
    assert not [x for x in c.sticks[1].log if "KEYCODE_HOME" in x]   # never touched the other stick


async def test_buffering_reads_far_apart_restart_the_clock(two, clock, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    c, tuner = two
    live(c)
    harden(c)
    c.on_target = "youtubetv:fox"
    c.main.playing = "buffering"
    await c._watch_visible()
    clock.t += ctl.STUCK_GAP_S + 5           # we didn't look for a while: it may have played
    await c._watch_visible()
    assert c._visible_bad_since is None and tuner.calls == []


async def test_flag_off_buffering_is_always_playing(two, clock, monkeypatch):
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    c, tuner = two
    live(c)
    harden(c, on=False)
    c.on_target = "youtubetv:fox"
    c.main.playing = "buffering"
    for _ in range(10):
        await c._watch_visible()
        clock.t += 10
    assert c._visible_bad_since is None and tuner.calls == [] and c._loading_since == {}


async def test_standby_stuck_buffering_is_dropped_for_a_reload(two, clock):
    c, tuner = two
    live(c)
    harden(c)
    hid = c.hidden
    hid.playing = "buffering"
    c._set_hidden("youtubetv:espn", True)
    c._verified_at = 0.0
    await c.verify_standby()
    assert c.hidden_ready and c.hidden_target == "youtubetv:espn"     # a load: keep it
    clock.t += 15
    c._verified_at = 0.0
    await c.verify_standby()
    assert c.hidden_ready
    clock.t += 15                                                     # 30 s buffering
    c._verified_at = 0.0
    await c.verify_standby()
    assert not c.hidden_ready and c.hidden_target is None


async def test_standby_buffering_kept_when_off(two, clock):
    c, tuner = two
    live(c)
    hid = c.hidden
    hid.playing = "buffering"
    c._set_hidden("youtubetv:espn", True)
    for _ in range(5):
        c._verified_at = 0.0
        await c.verify_standby()
        clock.t += 15
    assert c.hidden_ready and c.hidden_target == "youtubetv:espn"
