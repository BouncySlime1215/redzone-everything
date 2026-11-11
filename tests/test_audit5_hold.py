"""AUDIT-5: only a hold that really left the stick playing may call that standby ready.

Why: a ready standby is what a flip goes to. A hold that a switch displaced can leave the same target mid-tune, and a hold
that never resumed leaves a paused picture: either one called ready puts a stream nobody has confirmed on the TV.
"""
import time

import pytest

from tests.test_control import two  # noqa: F401  (fixture)

FOX = "youtubetv:fox"


@pytest.fixture
def setup(two, monkeypatch):  # noqa: F811
    c, _ = two
    monkeypatch.setattr(c, "_buffer_for", lambda *a: 60.0)
    monkeypatch.setattr(c, "_scrub_wanted", lambda *a: False)
    return c, monkeypatch


def fake_hold(c, mp, end, relinked=False, ok=False):
    async def hold(stick, target, behind_s, base_s=0.0):
        c._last_hold = {"host": stick.host, "target": target, "end": end, **({"relinked": True} if relinked else {})}
        c._set_hidden(target, False)   # as it stands when a switch has this same target mid-tune, or the hold never resumed
        return ok
    mp.setattr(c, "_hold", hold)


@pytest.mark.parametrize("end", ["displaced", "failed"])
async def test_a_preload_never_calls_a_displaced_or_unresumed_hold_ready(setup, end):
    """Repro: _hold came back displaced (a switch re-tuning this same target) or failed with the stick still paused, the
    target still matched, and the preload set it ready: a flip could go to a stream mid-tune or paused."""
    c, mp = setup
    fake_hold(c, mp, end)
    await c._preload(FOX, None, None, time.time())
    assert not c.hidden_ready


@pytest.mark.parametrize("end,relinked", [("held", False), ("refused", False), ("failed", True)])
async def test_a_hold_that_left_it_playing_is_still_ready(setup, end, relinked):
    c, mp = setup
    fake_hold(c, mp, end, relinked, ok=end == "held")
    await c._preload(FOX, None, None, time.time())
    assert c.hidden_ready and c.hidden_target == FOX


async def test_the_standby_hold_does_not_call_an_unresumed_stick_ready(setup):
    """Repro: the same hole in buffer_standby's pause-hold: 'the stick stays unready' in _hold, ready in the caller."""
    c, mp = setup
    fake_hold(c, mp, "failed")
    c._set_hidden(FOX, False)
    await c._hold_standby(c.hidden, FOX)
    assert not c.hidden_ready


async def test_a_rebuild_does_not_call_a_displaced_hold_ready(setup):
    """Repro: the STK-06 rebuild marked it ready whatever its hold had come back as."""
    c, mp = setup
    fake_hold(c, mp, "displaced")
    c._set_hidden(FOX, False)
    await c._rebuild_buffer(c.hidden, FOX, 30.0, -30.0)
    assert not c.hidden_ready


async def test_a_rebuild_with_no_hold_to_make_is_ready(setup):
    c, mp = setup
    mp.setattr(c, "_buffer_for", lambda *a: 0.0)
    c._set_hidden(FOX, False)
    await c._rebuild_buffer(c.hidden, FOX, 90.0, 30.0)
    assert c.hidden_ready and c.hidden_target == FOX


