"""Platform hardening, measured by redzone/platform/faults.py: a hung stick fails fast after two
hangs (circuit breaker), a wall-clock jump never ages a healthy switch, and no fault ever cuts mid-play."""
import asyncio
import time

import pytest

from redzone import control as ctl
from redzone.decider.decider import Decision, State
from redzone.engine import Engine
from redzone.platform.breaker import Breaker
from redzone.platform.faults import FaultStick, FaultTuner, SkewClock, World
from redzone.store import Store
from redzone.tuners.adb import STICK_HUNG, RunResult, Stick


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


# --- the breaker itself ---------------------------------------------------------------------

def test_two_hangs_open_it_and_it_fails_fast_until_the_backoff_ends():
    c = Clock()
    b = Breaker("x", clock=c)
    b.hang()
    assert b.allow() and b.state == "closed"          # one hang is a blip
    b.hang()
    assert b.state == "open" and not b.allow()
    c.t = 10.0
    assert b.allow() and not b.allow()                # half-open: exactly one probe at a time
    b.success()
    assert b.state == "closed" and b.allow()


def test_a_failed_probe_doubles_the_backoff_up_to_the_cap():
    c = Clock()
    b = Breaker("x", clock=c, max_backoff_s=30.0)
    b.hang(); b.hang()
    waits = []
    for _ in range(4):
        waits.append(b.retry_in())
        c.t = b.until
        assert b.allow()
        b.hang()
    assert waits == [10.0, 20.0, 30.0, 30.0]


def test_a_cancelled_probe_frees_the_slot():
    c = Clock()
    b = Breaker("x", clock=c)
    b.hang(); b.hang()
    c.t = 10.0
    assert b.allow()
    b.release()                                       # the probe was cancelled mid-command
    assert b.allow()                                  # ...so the next caller may probe


def test_a_fast_clear_failure_is_an_answer_not_a_hang():
    b = Breaker("x", clock=Clock())
    b.hang()
    b.success()                                       # "error: device offline" came back at once
    b.hang()
    assert b.state == "closed"


# --- on a stick -----------------------------------------------------------------------------

class HangStick(Stick):
    def __init__(self):
        super().__init__("h:5555")
        self.state = "connected"
        self.sent = []
        self.hung = True

    async def _shell(self, cmd, timeout=8.0):
        self.sent.append(cmd)
        if self.hung:
            await asyncio.sleep(0.01)
            return RunResult(False, f"timeout/error: no reply in {timeout:g}s", timeout)
        return RunResult(True, "ok\n", 0.01)


async def test_a_hung_stick_stops_being_sent_commands():
    s = HangStick()
    await s.shell("dumpsys power")
    await s.shell("am start x")
    r = await s.shell("input keyevent KEYCODE_HOME")
    assert not r.ok and r.out.startswith(STICK_HUNG) and r.took == 0.0
    assert s.sent == ["dumpsys power", "am start x"]  # the HOME was never sent


async def test_a_stick_that_answers_again_closes_after_one_probe():
    s = HangStick()
    await s.shell("a"); await s.shell("b")
    s.breaker.until = 0.0                             # backoff over
    s.hung = False
    assert (await s.shell("echo ok")).ok
    assert s.breaker.state == "closed" and (await s.shell("c")).ok


async def test_a_command_not_sent_keeps_what_we_know_is_on_screen():
    s = HangStick()
    s.tuned = {"target": "yt:espn"}
    await s.shell("a"); await s.shell("b")
    s.tuned = {"target": "yt:espn"}
    await s.shell("input keyevent KEYCODE_HOME")      # refused by the breaker: nothing changed
    assert s.tuned == {"target": "yt:espn"}


async def test_cancelling_a_command_never_leaves_the_breaker_stuck():
    s = HangStick()
    await s.shell("a"); await s.shell("b")
    s.breaker.until = 0.0
    s.hung = False

    async def slow(cmd, timeout=8.0):
        await asyncio.sleep(10)
    s._shell = slow
    t = asyncio.create_task(s.shell("probe"))
    await asyncio.sleep(0.01)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    del s._shell
    assert (await s.shell("next")).ok


# --- on the controller (two sticks) ---------------------------------------------------------

@pytest.fixture
def hung_hidden(cfg, tmp_path, monkeypatch):
    from redzone.channels import ChannelMap
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB", "cbs": "CCCCCCCCCCC"})
    c = ctl.Controller(cfg, ch)
    world = World()
    a, b = FaultStick("a:5555", world), FaultStick("b:5555", world)
    c.sticks, c.hdmi = [a, b], {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "test"
    world.input_to("a:5555")
    c.on_target = "youtubetv:fox"
    a.remember(c.on_target, {"state": "playing", "pkg": "yttv.pkg", "position": 0, "updated": 0})
    b.fault = "adb_hang"
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", FaultTuner())

    async def short_hang(cmd, timeout=8.0, _orig=b._shell):
        return await _orig(cmd, min(timeout, 0.05))   # test-sized hang, same shape
    b._shell = short_hang
    return c, a, b, world


async def test_second_switch_to_a_hung_hidden_stick_fails_at_once(hung_hidden):
    c, a, b, world = hung_hidden
    first = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert not first.ok and first.detail.startswith(STICK_HUNG)
    t0 = time.monotonic()
    second = await c.execute("youtubetv:cbs", may_cut=lambda: True)
    assert not second.ok and second.detail.startswith(STICK_HUNG) and second.screen_unchanged
    assert time.monotonic() - t0 < 0.05
    assert world.mid_play_cuts == 0 and world.on_input == "a:5555" and c.on_target == "youtubetv:fox"


async def test_a_hung_stick_preload_doesnt_blame_the_channel(hung_hidden):
    c, a, b, world = hung_hidden
    taught = []
    c.on_preload_fail = lambda *a, **k: taught.append(a)
    b.breaker.hang(); b.breaker.hang()
    await c._preload("youtubetv:espn", None, None, time.time())
    assert not taught and "youtubetv:espn" not in c._failed


async def test_engine_doesnt_cool_down_a_target_for_a_hung_stick(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    engine = Engine(cfg, Store(tmp_path / "t.db"))
    engine.decider.current = "old"
    cbs = []
    engine.control.submit = lambda *a, **k: cbs.append(a[2])
    engine.decider.target_of = lambda gid: "youtubetv:new"
    engine._act(Decision(1, "switch", "new", "better game", State.WATCHING), "old", {})
    cbs[0](ctl.Outcome(False, "youtubetv:new", 0.0, f"{STICK_HUNG}: b:5555, retry in 10s", False,
                       screen_unchanged=True))
    assert "youtubetv:new" not in engine._fail_cool
    await engine.close()


# --- clock skew -----------------------------------------------------------------------------

@pytest.mark.parametrize("jump", [180.0, -180.0])
async def test_a_wall_clock_jump_never_ages_a_healthy_switch(cfg, tmp_path, monkeypatch, jump):
    cfg.update({"mode": {"dry_run": False}})
    engine = Engine(cfg, Store(tmp_path / "t.db"))
    engine.control.submit = lambda *a, **k: None
    cancelled = []
    engine.control.cancel_pending = lambda: cancelled.append(True)
    engine._act(Decision(1, "switch", "new", "better game", State.WATCHING), "old", {})
    skew = SkewClock(time.time)
    monkeypatch.setattr(time, "time", skew)
    skew.jump(jump)
    engine._switch_deadline(time.time())
    assert not cancelled and engine._pending_decision is not None
    assert engine.pending_age(time.time()) < 5
    monkeypatch.undo()
    await engine.close()


async def test_a_switch_that_really_hangs_is_still_given_up(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    engine = Engine(cfg, Store(tmp_path / "t.db"))
    engine.control.submit = lambda *a, **k: None
    cancelled = []
    engine.control.cancel_pending = lambda: cancelled.append(True)
    engine._act(Decision(1, "switch", "new", "better game", State.WATCHING), "old", {})
    engine._pending_since -= 200          # both clocks agree 200 s passed
    engine._pending_mono -= 200
    engine._switch_deadline(time.time())
    assert cancelled and engine._pending_decision is None
    await engine.close()


async def test_adb_connect_succeeding_lets_the_next_probe_through_at_once(monkeypatch):
    from redzone.tuners import adb
    s = HangStick()
    await s.shell("a"); await s.shell("b")
    s.breaker.until = 0.0
    assert s.breaker.allow()
    s.breaker.hang()                                  # a failed probe: backoff now 20 s
    assert s.breaker.retry_in() > 10
    s.hung = False

    async def fake_adb(*args, timeout=8.0):
        return RunResult(True, "already connected to h:5555", 0.01)
    monkeypatch.setattr(adb, "_adb", fake_adb)
    assert await s.connect() and s.state == "connected" and s.breaker.state == "closed"


def test_a_burst_of_hangs_from_one_outage_counts_once():
    c = Clock()
    b = Breaker("x", clock=c)
    for _ in range(5):                                # five reads in flight when the stick froze
        b.hang()
    assert b.retry_in() == 10.0 and b.trips == 1
