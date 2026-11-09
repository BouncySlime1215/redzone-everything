"""AUDIT-4: a stick that keys moved behind live stays marked behind, whatever stopped the work.

Why: a same-channel deep link doesn't bring a YouTube TV stick back to live (#391), so the mark is the only thing that
stops the next tune from booking a picture still behind live as the live edge.
"""
import asyncio
import json
import time

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners import replay_park, yttv_seek
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)

FOX = "youtubetv:fox"


async def _cancel_when(task, started):
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _hang(started):
    async def hang(*a, **k):
        started.set()
        await asyncio.Event().wait()
    return hang


async def test_a_cancelled_scrub_leaves_the_stick_marked_behind(two, monkeypatch):  # noqa: F811
    """Repro: any switch cancels a preload mid-scrub. Keys had gone out, but the cancel skipped the line that sets
    behind_on, so the next tune of that channel booked a stick up to 90 s behind as the live edge."""
    c, tuner = two
    hid, started = c.hidden, asyncio.Event()
    c.cfg.update({"timing": {"behind_live_s": {"scrub_after_tune_s": 0}}})
    monkeypatch.setattr(yttv_seek, "enter_buffer", _hang(started))
    c._set_hidden(FOX, False)
    await _cancel_when(asyncio.create_task(c._scrub_into(hid, FOX, 60.0)), started)
    assert hid.behind_on == FOX
    await c._tune(hid, FOX)
    assert [t for _, t in tuner.calls] == ["youtubetv:espn", FOX]   # through another channel first


async def test_a_scrub_cancelled_before_its_first_key_marks_nothing(two):  # noqa: F811
    c, _ = two
    hid = c.hidden
    c.cfg.update({"timing": {"behind_live_s": {"scrub_after_tune_s": 30}}})
    c._set_hidden(FOX, False)
    c._played_at[hid.host] = time.time()
    task = asyncio.create_task(c._scrub_into(hid, FOX, 60.0))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hid.behind_on is None


async def test_a_cancelled_replay_scrub_leaves_the_stick_marked_behind(two, monkeypatch):  # noqa: F811
    c, _ = two
    hid, started = c.hidden, asyncio.Event()
    c.label_probe = object()
    monkeypatch.setattr(replay_park, "park", _hang(started))
    await _cancel_when(asyncio.create_task(c._scrub_park(hid, FOX, 60.0, "r1")), started)
    assert hid.behind_on == FOX


async def test_a_cancelled_youtube_tv_pause_hold_leaves_the_stick_marked_behind(two):  # noqa: F811
    c, _ = two
    hid = c.hidden
    hid.playback_state = "paused"
    c._set_hidden(FOX, False)
    task = asyncio.create_task(c._hold(hid, FOX, 30.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hid.behind_on == FOX


async def test_a_cancelled_prime_hold_marks_nothing(two):  # noqa: F811
    """Prime has no other channel to reset through: a mark there would fail every later tune."""
    c, _ = two
    hid = c.hidden
    hid.playback_state = "paused"
    c._set_hidden("prime:gti", False)
    task = asyncio.create_task(c._hold(hid, "prime:gti", 30.0))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hid.behind_on is None


async def test_a_hold_that_did_not_resume_reloads_through_another_channel(two):  # noqa: F811
    """Repro: 'reloading at the live edge' was a same-channel link on a stick paused behind live."""
    c, tuner = two
    hid = c.hidden
    hid.playback_state = "paused"
    c.cfg.update({"timing": {"behind_live_s": {"resume_settle_s": 0, "resume_poll_s": 0.01}}})
    c._set_hidden(FOX, False)
    assert await c._hold(hid, FOX, 0.01) is False
    assert [t for _, t in tuner.calls] == ["youtubetv:espn", FOX]


def test_a_restart_keeps_behind_on_even_when_the_offsets_are_too_old(cfg, tmp_path):
    """Repro: restore_held returned early on an old file, dropping behind_on with it. The offsets age out; a stick that
    is behind live stays behind until something re-tunes it, however long the app was down."""
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    c.sticks = [FakeStick("a:5555"), FakeStick("b:5555")]
    (tmp_path / "held.json").write_text(json.dumps({"ts": time.time() - 7200, "held": {FOX: 75.0},
                                                    "behind_on": {"b:5555": FOX}}))
    assert c.restore_held() == {}                      # the offsets are still too old to trust
    assert c.sticks[1].behind_on == FOX and c.sticks[0].behind_on is None
