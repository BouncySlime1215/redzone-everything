"""AUDIT-4: a standby called ready must be one a flip can use.

Why: every key on a stick drops its anchor (Stick.shell), and a flip needs that anchor: a ready standby without one is
refused by every jump, for as long as nothing else happens to re-tune it.
"""
import pytest

from redzone import control as ctl
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)

FOX = "youtubetv:fox"


class AnchorStick(FakeStick):
    """As on a real stick: a key clears the anchor."""

    async def key(self, *codes):
        self.tuned = None
        return await super().key(*codes)


@pytest.fixture
def hid(two):  # noqa: F811
    c, _ = two
    c.sticks[1] = AnchorStick("b:5555", display={"input": "HDMI1"}, hdmi="HDMI2")
    return c, c.hidden


async def test_a_refused_pause_leaves_the_stream_usable(hid):
    """Repro: the PAUSE key cleared the anchor, the stream never paused and played on, and it was marked ready with no
    anchor: 'standby not ready (stopped playing)' on every jump."""
    c, stick = hid
    stick.remember(FOX, await stick.playback("yttv.pkg"))
    c._set_hidden(FOX, False)
    assert await c._hold(stick, FOX, 0.01) is False and c._last_hold["end"] == "refused"
    assert (stick.tuned or {}).get("target") == FOX


async def test_a_jump_drops_a_ready_standby_that_has_lost_its_anchor(hid):
    """Repro: nothing could flip to it and nothing replaced it, so the standby slot was stuck."""
    c, stick = hid
    c._set_hidden(FOX, True)
    assert stick.tuned is None
    c._preloaded_only = True
    out = await c._execute_two(FOX, None, lambda: True, 0.0)
    assert not out.ok and out.detail.startswith(ctl.STANDBY_NOT_READY)
    assert c.hidden_target is None and not c.hidden_ready


async def test_a_jump_keeps_a_standby_that_only_blipped(hid, monkeypatch):
    """The other half: an anchored standby that isn't steady this moment stays; the next tick can use it."""
    c, stick = hid
    stick.remember(FOX, await stick.playback("yttv.pkg"))
    c._set_hidden(FOX, True)

    async def not_steady(*a):
        return None
    monkeypatch.setattr(c, "_steady_playing", not_steady)
    c._preloaded_only = True
    out = await c._execute_two(FOX, None, lambda: True, 0.0)
    assert not out.ok and c.hidden_target == FOX and c.hidden_ready
