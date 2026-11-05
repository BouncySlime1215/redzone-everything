"""[buffer.hold_left] playbook STK-07: the game we leave stays paused on the stick we left, at the dead
ball we left it on, so going back is a flip plus a catch-up from there (not a reload at the live edge).
Guarded like a time-shift hold (STK-06): one that resumed by itself or reached the behind-live cap is
reloaded at the live edge. Serves: STK-07. Owner: lane 3."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners.adb import RunResult
from tests.test_hot_standby import FakeStick, FakeTuner, refreshed


class PausingStick(FakeStick):
    """MEDIA_PAUSE / MEDIA_PLAY change what playback() reports, like a real player."""

    async def key(self, *codes):
        self.log.extend(codes)
        for k in codes:
            if k == "KEYCODE_MEDIA_PAUSE" and self.playing == "playing":
                self.playing = "paused"
            elif k == "KEYCODE_MEDIA_PLAY" and self.playing == "paused":
                self.playing = "playing"
        return RunResult(True, "", 0.01)


@pytest.fixture
def held(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}, "buffer": {"hold_left": True}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC"})
    c = ctl.Controller(cfg, ch)
    c.sticks = [PausingStick("a:5555"), PausingStick("b:5555")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "explicit-test-display"
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)

    async def input_status():
        return c.hdmi.get(FakeStick.last_input_host)
    monkeypatch.setattr(c.tv, "input_status", input_status)
    return c, tuner


def rows(c):
    if not c.buffer_log_path.exists():
        return []
    return [json.loads(x)["kind"] for x in c.buffer_log_path.read_text().splitlines()]


async def leave_fox(c):
    c.on_target = "youtubetv:fox"                       # stick a is showing FOX live
    c.main.remember(c.on_target, await c.main.playback("yttv.pkg"))
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c.main.host == "b:5555"
    await refreshed(c)
    return c.sticks[0]


async def test_the_left_game_is_held_paused_where_we_left_it_not_reloaded(held):
    c, tuner = held
    a = await leave_fox(c)
    assert a.playing == "paused" and "KEYCODE_MEDIA_PAUSE" in a.log
    assert c.standby == ("youtubetv:fox", None) and c.hidden_ready
    assert c._left is not None and c._left.target == "youtubetv:fox"
    assert tuner.calls == [("b:5555", "youtubetv:espn")]   # no reload of FOX at the live edge
    assert rows(c) == ["left_held"]


async def test_going_back_is_a_flip_that_plays_on_from_the_dead_ball(held):
    c, tuner = held
    a = await leave_fox(c)
    c._left.at -= 30.0                                  # we've been away 30 s
    t0 = time.time()
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)
    took = time.time() - t0                             # the flip and its resume ran behind live too
    assert out.ok and out.path == "left hold" and c.main is a and a.playing == "playing"
    assert tuner.calls == [("b:5555", "youtubetv:espn")]   # still no tune: one flip, then PLAY
    assert 30.0 <= c.held["youtubetv:fox"] <= 30.0 + took + 0.5   # that far behind live now
    assert "resumed where we left it" in out.detail and c._left.target == "youtubetv:espn"
    assert sorted(rows(c)[-2:]) == ["left_held", "left_resumed"]   # ...and ESPN is now held on b


async def test_the_flag_off_reloads_the_left_game_at_the_live_edge_as_before(held):
    c, tuner = held
    c.cfg.update({"buffer": {"hold_left": False}})
    a = await leave_fox(c)
    assert c._left is None and "KEYCODE_MEDIA_PAUSE" not in a.log
    assert tuner.calls[-1] == ("a:5555", "youtubetv:fox")


async def test_a_hold_at_the_cap_is_reloaded_at_the_live_edge(held):
    """STK-06 guard: never further behind live than timing.behind_live_s.max_s."""
    c, tuner = held
    await leave_fox(c)
    c._left.at -= float(c.cfg.get("timing.behind_live_s.max_s")) + 1
    c._verified_at = 0
    await c.verify_standby()
    await refreshed(c)
    assert c._left is None and tuner.calls[-1] == ("a:5555", "youtubetv:fox")
    assert "left_expired" in rows(c)


async def test_a_hold_that_resumed_by_itself_is_reloaded_not_kept(held):
    c, tuner = held
    a = await leave_fox(c)
    a.playing = "playing"                               # it played on, behind live by an unknown amount
    c._verified_at = 0
    await c.verify_standby()
    await refreshed(c)
    assert c._left is None and tuner.calls[-1] == ("a:5555", "youtubetv:fox")
    assert "left_lost" in rows(c)


async def test_a_paused_hold_is_kept_by_the_standby_check_not_dropped_as_stk05(held):
    c, tuner = held
    await leave_fox(c)
    c._verified_at = 0
    await c.verify_standby()
    assert c.hidden_ready and c._left is not None and len(tuner.calls) == 1
    # ...and it looks again by the time it reaches the cap, not a whole verify period later
    cap = float(c.cfg.get("timing.behind_live_s.max_s"))
    c._left.at = time.time() - (cap - 3)
    c._verified_at = 0
    await c.verify_standby()
    assert time.time() - c._verified_at >= c._verify_every() - 3.5


async def test_a_late_pause_is_not_a_hold_at_the_dead_ball(held):
    """A pause that lands after hold_left_slop_s isn't the dead ball we left: reload it as before."""
    c, tuner = held
    c.on_target = "youtubetv:fox"
    c.main.remember(c.on_target, await c.main.playback("yttv.pkg"))
    c.cfg.update({"buffer": {"hold_left": False}})
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    c._cancel_refresh()
    c.cfg.update({"buffer": {"hold_left": True}})
    late = time.time() - float(c.cfg.get("buffer.hold_left_slop_s")) - 1
    await asyncio.wait_for(c._hold_left("youtubetv:fox", c.sticks[0], late), 2)
    await refreshed(c)
    assert c._left is None and tuner.calls[-1] == ("a:5555", "youtubetv:fox")


async def test_a_retune_of_that_stick_forgets_the_hold(held):
    c, tuner = held
    await leave_fox(c)
    assert c.preload("youtubetv:cbs")
    await c._preload_task
    assert c._left is None and c.standby == ("youtubetv:cbs", None)
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)
    assert out.path == "tuned"


async def test_a_hold_that_resumed_right_before_the_flip_is_not_flipped_to(held):
    """SAFE-08 for a hold: between the decision and the flip it played on by itself."""
    c, tuner = held
    a = await leave_fox(c)
    calls = 0
    orig = c._left_paused

    async def then_gone(stick, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            a.playing = "playing"
        return await orig(stick, target)
    c._left_paused = then_gone
    out = await c.execute("youtubetv:fox", may_cut=lambda: True)
    assert not out.ok and out.path == "left hold" and c.main.host == "b:5555"   # the TV stayed on ESPN
    assert c._left is None and not c.hidden_ready
