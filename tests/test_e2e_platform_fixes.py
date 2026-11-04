"""Fixes for three bugs the e2e night test (redzone/lab/night) found:
1. Controller.boundary_reads crashed when a switch cleared a stick's tuned target mid-loop;
2. a "Still watching?" prompt stayed on screen while switches kept waiting for a safe moment
   (each wait held the lock that the visible-stick watchdog needs);
3. Engine.decide submitted a switch every tick while the TV input was unknown."""
import asyncio

import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap


class FakeStick:
    def __init__(self, host, target="youtubetv:espn"):
        self.host = host
        self.tuned = {"target": target}
        self.state = "paused"
        self.reads = 0

    async def playback(self, pkg=None):
        self.reads += 1
        await asyncio.sleep(0)
        return {"state": self.state, "read_ok": True}


@pytest.fixture
def c(tmp_path, monkeypatch, cfg):
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    a, b = FakeStick("a:5555"), FakeStick("b:5555")
    monkeypatch.setattr(ctl.Controller, "main", property(lambda self: a))
    monkeypatch.setattr(ctl.Controller, "usable", property(lambda self: [a, b]))
    monkeypatch.setattr(ctl.Controller, "live_control", property(lambda self: True))
    monkeypatch.setattr(c, "_package", lambda s, t: "pkg")
    c.a, c.b = a, b
    return c


async def test_boundary_reads_survive_a_switch_clearing_a_target(c):
    async def read_then_clear(pkg=None):
        c.b.tuned = None                     # a switch lands while we await stick a
        return {"state": "playing"}
    c.a.playback = read_then_clear
    assert await c.boundary_reads() == 2


async def test_a_switch_waiting_for_a_safe_moment_clears_a_stalled_screen(c, monkeypatch):
    monkeypatch.setattr(ctl, "SAFE_POLL_S", 0.0)
    monkeypatch.setattr(ctl, "WAIT_SCREEN_LOOK_S", 0.0)
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 0.2)
    c.on_target = "youtubetv:espn"
    fixed = []

    async def resume(stick, target, first_wait_s):
        fixed.append((stick.host, target))
        stick.state = "playing"
        return "media_play"
    monkeypatch.setattr(c, "_resume_playback", resume)

    async def switch():
        c._task = asyncio.current_task()
        return await c._wait_safe(lambda: False)
    assert await asyncio.create_task(switch()) is None      # still no safe moment: the switch gives up...
    assert fixed == [("a:5555", "youtubetv:espn")]          # ...but the prompt was cleared, once


async def test_a_prompt_during_a_replay_is_cleared_but_never_relinked_to_live(c, monkeypatch):
    """e2e seed 2: the prompt came up on a catch-up replay (on_target None by design)."""
    monkeypatch.setattr(ctl, "SAFE_POLL_S", 0.0)
    monkeypatch.setattr(ctl, "WAIT_SCREEN_LOOK_S", 0.0)
    monkeypatch.setattr(ctl, "VISIBLE_STALL_S", 0.0)
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 0.2)
    c.on_target = None
    c._replay_shown = ("youtubetv:espn", c.a, c._flip_gen)    # the replay was flipped onto stick a
    cleared = []

    async def relink(*a):
        raise AssertionError("a replay must never be re-linked to live")

    async def clear(stick, target, pkg):
        cleared.append((stick.host, target))
        stick.state = "playing"
        return True
    monkeypatch.setattr(c, "_resume_playback", relink)
    monkeypatch.setattr(c, "_clear_screen_prompt", clear)

    async def switch():
        c._task = asyncio.current_task()
        return await c._wait_safe(lambda: False)
    assert await asyncio.create_task(switch()) is None
    assert cleared == [("a:5555", "youtubetv:espn")]


async def test_a_playing_screen_is_never_touched_while_waiting(c, monkeypatch):
    monkeypatch.setattr(ctl, "SAFE_POLL_S", 0.0)
    monkeypatch.setattr(ctl, "WAIT_SCREEN_LOOK_S", 0.0)
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 0.1)
    c.on_target = "youtubetv:espn"
    c.a.state = "playing"

    async def resume(*a):
        raise AssertionError("pressed a key on a playing screen")
    monkeypatch.setattr(c, "_resume_playback", resume)

    async def switch():
        c._task = asyncio.current_task()
        return await c._wait_safe(lambda: False)
    assert await asyncio.create_task(switch()) is None
    assert c.a.reads > 0


async def test_a_preload_waiting_never_looks_at_the_screen(c, monkeypatch):
    monkeypatch.setattr(ctl, "SAFE_POLL_S", 0.0)
    monkeypatch.setattr(ctl, "WAIT_SCREEN_LOOK_S", 0.0)
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 0.05)
    c.on_target = "youtubetv:espn"
    assert await c._wait_safe(lambda: False) is None        # not the switch task
    assert c.a.reads == 0


async def test_decide_submits_nothing_while_the_tv_input_is_unknown(cfg, tmp_path, monkeypatch):
    from redzone.engine import Engine
    from redzone.models import GameState, League, Phase, Team
    from redzone.store import Store
    cfg.update({"mode": {"dry_run": False}})
    e = Engine(cfg, Store(tmp_path / "t.db"))
    monkeypatch.setattr(type(e.control), "live_control", property(lambda self: True))
    e.control.input_unknown = True
    submitted = []
    monkeypatch.setattr(e.control, "submit", lambda *a, **k: submitted.append(a))
    asked = []
    real = e.decider.decide
    monkeypatch.setattr(e.decider, "decide", lambda *a, **k: (asked.append(1), real(*a, **k))[1])
    e.merger.update("espn_summary", GameState(league=League.NFL, game_id="1", home=Team("A", score=0),
                                              away=Team("B", score=0), phase=Phase.LIVE, period=1, clock_s=900.0))
    for _ in range(5):
        e.decide()
    assert submitted == [] and asked == []
    e.control.input_unknown = False
    e.decide()
    assert asked                                            # decides again once the input is known
    await e.close()


def test_a_return_confirmed_as_already_on_it_leaves_the_stack(cfg):
    """e2e seed 1: the game on the return stack was also the current game; the "return" came back
    "already on it" and was proposed again every tick (348 times)."""
    from redzone.decider.decider import Decider
    from redzone.decider.screen import DelayBook
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, 3.0))
    d.current, d.stack = "g1", ["g1"]
    d._commit("g1", kind="return")
    assert d.stack == [] and d.current == "g1"
    d.stack = ["g0"]
    d._commit("g2", kind="better")                          # an ordinary switch still pushes
    assert d.stack == ["g0", "g1"]
