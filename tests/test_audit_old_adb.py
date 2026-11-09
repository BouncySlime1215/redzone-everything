"""AUDIT-OLD, adb: a hung stick must fail fast everywhere, and a cancelled or refused command must leave no false state.

Why: the breaker guarded shell() only, adb's helper left processes behind on cancel, and a refused wake-up counted as awake.
"""
import asyncio

import pytest

from redzone.tuners import adb
from redzone.tuners.adb import RunResult, Stick


@pytest.fixture
def no_adb(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("an adb process was started for a stick whose breaker is open")
    monkeypatch.setattr(adb.asyncio, "create_subprocess_exec", boom)


def _hung() -> Stick:
    s = Stick("10.0.0.9:5555")
    s.breaker.hang()
    s.breaker.hang()
    assert s.breaker.is_open
    return s


async def test_screen_text_skips_a_hung_stick(no_adb):
    """Repro: screen_text runs its own adb process, around the breaker: 12 s of dump plus a screenshot on a dead stick."""
    assert await _hung().screen_text() == ""


async def test_screenshots_skip_a_hung_stick(no_adb):
    s = _hung()
    assert await s.screencap(timeout=15) == b""
    assert await s.screencap_band(0.7, timeout=15) == b""


class _HungProc:
    returncode = None

    def __init__(self):
        self.killed = False

    async def communicate(self):
        await asyncio.sleep(60)

    def kill(self):
        self.killed = True


async def test_a_cancelled_adb_call_leaves_no_process_behind(monkeypatch):
    """Repro: adb._adb cleaned up on timeout and OSError but not on cancel (Stick.run does), so a cancelled
    `adb connect` kept running with nobody waiting for it."""
    proc = _HungProc()

    async def spawn(*a, **k):
        return proc
    monkeypatch.setattr(adb.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.ensure_future(adb._adb("connect", "10.0.0.9:5555", timeout=30))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert proc.killed
