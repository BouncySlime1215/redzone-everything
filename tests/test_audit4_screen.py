"""AUDIT-4: a refused tune must not forget what's on screen.

Why: on_target is what the visible-stick watchdog and the left-game refresh work from; a tune that sent no key changed nothing.
"""
from redzone import control as ctl
from redzone.tuners.tuners import TuneResult
from tests.test_control import FakeTuner, two  # noqa: F401  (fixture)

FOX = "youtubetv:fox"
ESPN = "youtubetv:espn"


async def test_a_visible_tune_that_sent_nothing_keeps_the_screen_known(two, monkeypatch):  # noqa: F811
    """Repro: _execute_one cleared on_target before the tune; the cut window closed (no key sent), the old game played on
    with on_target None, and the visible-stick watchdog went blind until the next switch."""
    c, _ = two

    class Closed(FakeTuner):
        async def tune(self, stick, ref, target, may_cut=None):
            return TuneResult(False, target, 0.0, [], "safe switching window closed", screen_changed=False)
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", Closed())
    c.on_target = ESPN
    out = await c._execute_one(FOX, 0.0)
    assert not out.ok and out.screen_unchanged and c.on_target == ESPN


async def test_a_visible_tune_that_sent_keys_leaves_the_screen_unknown(two, monkeypatch):  # noqa: F811
    c, _ = two

    class Sent(FakeTuner):
        async def tune(self, stick, ref, target, may_cut=None):
            return TuneResult(False, target, 0.0, [], "video never started", screen_changed=True)
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", Sent())
    c.on_target = ESPN
    await c._execute_one(FOX, 0.0)
    assert c.on_target is None


async def test_a_tile_switch_that_sent_nothing_keeps_the_screen_known(two):  # noqa: F811
    c, _ = two

    class Mv:
        def is_main(self, host, target):
            return False

        async def switch(self, stick, target, may_cut=None):
            return TuneResult(False, target, 0.0, [], "cut window closed", False, "tile")
    c.on_target = ESPN
    assert await c._execute_tile(Mv(), "prime:x", lambda: True, 0.0) is None
    assert c.on_target == ESPN
