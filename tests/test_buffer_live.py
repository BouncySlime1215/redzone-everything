"""[time-shift buffer] The hidden stick holds a fresh live tune B s behind live (pause, wait, play),
without holding the control lock, and the delay book follows the held offset."""
import asyncio

import pytest

from redzone import control as ctl
from redzone.decider.screen import DelayBook
from redzone.tuners.adb import RunResult
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)


class PausingStick(FakeStick):
    """A player that really pauses and resumes on the media keys."""
    async def key(self, *codes):
        self.log.extend(codes)
        for c in codes:
            if c == "KEYCODE_MEDIA_PAUSE":
                self.playback_state = "paused"
            elif c == "KEYCODE_MEDIA_PLAY":
                self.playback_state = "playing"
        return RunResult(True, "", 0.01)


@pytest.fixture
def held(two, monkeypatch):   # noqa: F811
    c, tuner = two
    c.cfg.update({"timing": {"behind_live_s": {"enabled": True, "max_s": 90, "apps": ["youtubetv"]}}})
    stick = PausingStick("b:5555", display={"input": "HDMI1"}, hdmi="HDMI2")
    c.sticks[1] = stick
    calls = []
    c.behind_of = lambda target: 0.2
    c.on_behind = lambda t, new, old: calls.append((t, new, old))
    return c, tuner, stick, calls


async def _preload_and_wait(c, target):
    assert c.preload(target)
    await c._preload_task


def test_hold_pauses_waits_and_plays_then_reports_the_offset(held):
    c, tuner, stick, calls = held
    asyncio.run(_preload_and_wait(c, "youtubetv:fox"))
    assert tuner.calls == [("b:5555", "youtubetv:fox")]
    assert stick.log[-2:] == ["KEYCODE_MEDIA_PAUSE", "KEYCODE_MEDIA_PLAY"]
    assert c.hidden_target == "youtubetv:fox" and c.hidden_ready
    assert 0.15 <= c.held["youtubetv:fox"] <= 1.0 and calls[-1][0] == "youtubetv:fox"
    # [SYNC 2] Re-anchored after the hold: the held stream still counts as the preloaded standby,
    # so the next flip uses it instead of re-tuning it at the live edge (which threw the hold away).
    assert stick.tuned["report"]["state"] == "playing"
    assert stick.still_on("youtubetv:fox", asyncio.run(stick.playback("yttv.pkg")))


def test_not_flippable_while_held_and_lock_is_free(held):
    c, tuner, stick, calls = held
    c.behind_of = lambda target: 0.5

    async def go():
        assert c.preload("youtubetv:fox")
        await asyncio.sleep(0.2)
        assert not c.hidden_ready                 # mid-hold: nothing may flip to it
        assert not c._lock.locked()               # ...and switches aren't blocked meanwhile
        await c._preload_task
        assert c.hidden_ready
    asyncio.run(go())


def test_off_by_default_and_a_fresh_tune_clears_the_hold(held):
    c, tuner, stick, calls = held
    c.cfg.update({"timing": {"behind_live_s": {"enabled": False}}})
    asyncio.run(_preload_and_wait(c, "youtubetv:fox"))
    assert "KEYCODE_MEDIA_PAUSE" not in stick.log and not c.held
    c.held["youtubetv:espn"] = 90.0
    asyncio.run(c._tune(stick, "youtubetv:espn"))
    assert "youtubetv:espn" not in c.held and calls[-1] == ("youtubetv:espn", 0.0, 90.0)


def test_a_player_that_wont_pause_stays_at_the_live_edge(held):
    c, tuner, stick, calls = held

    async def stubborn(*codes):
        stick.log.extend(codes)
        return RunResult(True, "", 0.01)
    stick.key = stubborn
    asyncio.run(_preload_and_wait(c, "youtubetv:fox"))
    assert not c.held and c.hidden_ready


def test_landing_keeps_held_offsets(cfg):
    from redzone.decider.decider import Decider
    book = DelayBook({"default": 40.0}, 4.0)
    dec = Decider(cfg, book)
    book.held["youtubetv:a"] = 90.0
    book.set_behind("youtubetv:a", 90.0)
    book.set_behind("youtubetv:b", 20.0)          # an old entry rewind on another tune
    dec.target_of = lambda gid: "youtubetv:a" if gid == "A" else None
    dec._land_behind("A", 10.0)
    assert book.get("youtubetv:a").behind == 100.0 and book.get("youtubetv:b").behind == 0.0
