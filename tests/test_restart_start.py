"""After a restart nothing is confirmed on screen: the first pick must keep trying until it lands.

Tonight's symptom: the engine sat with now=None, state watching, next=None for minutes until a
phone tap, because the first switch hung (up to the 150 s switch deadline) and a cancelled or
refused one was either retried every half second or left the target cooling down."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from redzone.control import Outcome
from redzone.decider.decider import START_RETRY_MAX_S, START_RETRY_S
from redzone.engine import Engine
from redzone.store import Store
from conftest import nfl


class FakeController:
    """Records submits; the test decides how each one ends."""

    def __init__(self, engine):
        self.calls = []
        engine.control.submit = lambda target, back, done, **kw: self.calls.append(SimpleNamespace(
            target=target, done=done, kw=kw))
        engine.control.cancel_pending = lambda: None

    def end(self, detail, ok=False, **kw):
        self.calls[-1].done(Outcome(ok, self.calls[-1].target, 1.0, detail, False,
                                    confirmed_at=time.time() if ok else None, **kw))


@pytest.fixture
async def eng(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    e = Engine(cfg, Store(tmp_path / "t.db"))
    g = nfl("g1", q=4, clock=200.0, hs=20, as_=17)
    e.merger.update("espn_scoreboard", g)
    e.decider.timeline(g.game_id, g.league).observe(g)
    e.discovery.choose = lambda game, base: SimpleNamespace(target="youtubetv:espn", tunable=True, label="ESPN")
    yield e
    await e.close()


def later(e, seconds):
    """Move the decider's clock (backoff) forward."""
    base = e.decider._clock
    e.decider._clock = lambda: base() + seconds


@pytest.mark.parametrize("detail,kw", [
    ("switch cancelled; playback not confirmed", {}),
    ("no safe moment to cut — kept the current game", {"screen_unchanged": True}),
    ("TV input is unknown; verify the visible stick before resuming", {}),
])
async def test_start_pick_that_did_not_land_is_retried_after_a_backoff(eng, detail, kw):
    fc = FakeController(eng)
    eng.decide()
    assert len(fc.calls) == 1 and eng.last_decision.kind == "start"
    fc.end(detail, **kw)
    assert eng.decider.current is None
    eng.decide()   # straight away: not hammered every tick...
    assert len(fc.calls) == 1
    d = eng.last_decision
    assert d.action == "stay" and d.want == "g1" and "trying again" in d.reason
    assert eng.snapshot()["next"]["game_id"] == "g1"   # ...and the phone says what it is waiting for
    later(eng, START_RETRY_S + 0.1)
    eng.decide()   # ...but never given up on
    assert len(fc.calls) == 2 and eng.last_decision.kind == "start"


async def test_start_backoff_doubles_up_to_a_cap_and_resets_once_it_lands(eng):
    fc = FakeController(eng)
    waits = []
    for _ in range(7):
        eng.decide()
        fc.end("switch cancelled; playback not confirmed")
        waits.append(round(eng.decider.start_retry_at - eng.decider._clock()))
        later(eng, START_RETRY_MAX_S + 1)
    assert waits == [3, 6, 12, 24, 48, 60, 60]
    eng.decide()
    fc.end("tuned", ok=True)
    assert eng.decider.current == "g1"
    assert eng.decider.start_fails == 0 and eng.decider.start_retry_at == 0.0


async def test_hung_start_pick_gives_up_sooner_than_a_normal_switch(eng):
    fc = FakeController(eng)
    eng.decide()
    assert eng._pending_decision is not None
    eng._pending_since = time.time() - 80   # 80 s: under the 150 s switch deadline, over the start one
    eng._pending_mono = time.monotonic() - 80   # ...on both clocks (a real 80 s, not a wall-clock jump)
    eng.decide()
    assert eng._pending_decision is None
    assert eng.decider.start_fails == 1 and eng.decider.start_retry_at > eng.decider._clock()


async def test_hung_normal_switch_keeps_the_long_deadline(eng):
    fc = FakeController(eng)
    eng.decider.current = "old"
    eng.decider._commit("old")
    eng.decider._arrival_until = 0.0
    eng.decide()
    if eng._pending_decision is None:
        pytest.skip("no switch away from the old game in this slate")
    eng._pending_since = time.time() - 80
    eng._pending_mono = time.monotonic() - 80
    eng.decide()
    assert eng._pending_decision is not None


async def test_real_controller_cancel_reaches_the_engine_and_is_retried(eng, monkeypatch):
    """The real submit -> cancelled task -> done callback path, not just a fake callback."""
    started = asyncio.Event()

    async def hang(*a, **kw):
        started.set()
        await asyncio.sleep(3600)
    monkeypatch.setattr(eng.control, "execute", hang)
    eng.decide()
    await started.wait()
    eng.control.cancel_pending()
    for _ in range(5):
        await asyncio.sleep(0)
    assert eng._pending_decision is None
    assert eng.decider.start_fails == 1
    later(eng, START_RETRY_S + 0.1)
    eng.decide()
    assert eng._pending_decision is not None and eng._pending_decision.kind == "start"
    eng.control.cancel_pending()
