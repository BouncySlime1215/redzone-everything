"""The supervisor's explicit state machines (redzone/machines/platform.py) and the bugs they fixed."""
import asyncio
from types import SimpleNamespace

import pytest

from redzone import supervisor as sup
from test_supervisor import fail_row, where, world  # noqa: F401  (world is a fixture)


async def test_unstick_forgives_failures_so_the_back_off_does_not_flap(world):  # noqa: F811
    """Bug: idle_with_live cleared the back-off, but the same two failures were still in the window, so
    the very next repeat_failures tick backed the target off again (and the two rules took turns)."""
    w = world                                            # g1 is watchable on youtubetv:espn; fs1 keeps failing
    w.e.store.rows += [fail_row(w.clock() + 1, "youtubetv:fs1"), fail_row(w.clock() + 2, "youtubetv:fs1")]
    w.clock.t += 5
    await w.s.rule_failures(w.clock())
    assert w.s.targets.state("youtubetv:fs1") == "backed_off"
    w.dec.current = None
    await w.s.rule_idle(w.clock())
    w.clock.t += 181
    await w.s.rule_idle(w.clock())                       # unstick: cool-downs cleared
    assert "youtubetv:fs1" not in w.e._fail_cool
    assert w.s.targets.state("youtubetv:fs1") == "available"
    w.clock.t += 700                                     # past the back-off rate limit too
    await w.s.rule_failures(w.clock())
    assert "youtubetv:fs1" not in w.e._fail_cool         # the forgiven failures don't back it off again
    w.e.store.rows += [fail_row(w.clock() + 1, "youtubetv:fs1"), fail_row(w.clock() + 2, "youtubetv:fs1")]
    w.clock.t += 5
    await w.s.rule_failures(w.clock())
    assert w.s.targets.state("youtubetv:fs1") == "backed_off"   # new failures still count


async def test_a_muted_app_covers_targets_that_show_up_later_and_survives_engine_overwrites(world):  # noqa: F811
    """Bug: muting an app only cooled the targets in e.where at that moment; a later target of the app
    (or one whose cool-down the engine overwrote with its 90 s one) was tried again."""
    w = world
    for t in ("prime:a", "prime:b"):
        w.s.muted_targets[t] = w.clock() + 9999
    await w.s.rule_failures(w.clock())
    assert w.s.apps.state("prime") == "muted_night"
    w.e.where["g9"] = where("prime:later")               # a new game on the muted app
    w.e._fail_cool["prime:a"] = w.clock() + 90           # the engine's own short cool-down after a failure
    w.clock.t += 10
    await w.s.rule_failures(w.clock())
    end = sup.night_end(w.clock())
    assert w.e._fail_cool["prime:later"] == pytest.approx(end)
    assert w.e._fail_cool["prime:a"] >= end - 1


async def test_mutes_expire_at_night_end(world):  # noqa: F811
    w = world
    w.s.muted_targets["prime:x"] = w.clock() + 100
    assert w.s.muted("prime:x")
    w.clock.t += 101
    assert not w.s.muted("prime:x")
    await w.s.rule_failures(w.clock())
    assert w.s.targets.state("prime:x") == "available" and "prime:x" not in w.s.muted_targets


async def test_running_goes_false_when_the_loop_ends(world):  # noqa: F811
    """Bug: Supervisor.running was set True when run() started and never set back."""
    w = world
    stop = asyncio.Event()

    async def sleep(s):
        await stop.wait()
    w.s.sleep = sleep
    task = asyncio.create_task(w.s.run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert w.s.running and w.s.loop.state == "running"
    w.e.recorded = SimpleNamespace(active=True)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not w.s.running and w.s.status()["running"] is False
    assert w.s.loop.state == "stopped"


async def test_loop_shows_disabled_and_yielded(world):  # noqa: F811
    w = world
    w.s.loop.to("running", "test")
    w.e.recorded = SimpleNamespace(active=True)
    await w.s.step()
    assert w.s.loop.state == "yielded"
    w.e.cfg.update({"supervisor": {"enabled": False}}, record=False)
    await w.s.step()
    assert w.s.loop.state == "disabled"


async def test_screen_check_moves_suspect_then_restoring_then_clear(world):  # noqa: F811
    w = world
    w.stick.pb = {"state": "idle", "read_ok": True}
    w.stick.caps = [b"same"]
    w.stick.fg = "com.amazon.tv.launcher/.Home"
    await w.s.rule_screen(w.clock())
    assert w.s.screen.state == "suspect" and w.s._screen_obs[:2] == ("home", "youtubetv:espn")
    w.clock.t += 25
    await w.s.rule_screen(w.clock())
    assert w.s.screen.state == "clear" and w.control.restored == [("youtubetv:espn", True)]
    assert [h[2] for h in w.s.screen.history] == ["suspect", "restoring", "clear"]


async def test_conditions_and_safe_episodes_are_machines(world):  # noqa: F811
    w = world
    assert w.s._held("x", True, w.clock()) == 0
    w.clock.t += 7
    assert w.s._held("x", True, w.clock()) == 7 and w.s.since == {"x": w.clock() - 7}
    w.s._held("x", False, w.clock())
    assert w.s.since == {}
    await w.s.track_episodes(w.clock())
    assert w.s._safe_on is False
    w.dec.safe_mode = True
    await w.s.track_episodes(w.clock())
    assert w.s._safe_on is True and w.s.safe_episode.state == "on"
