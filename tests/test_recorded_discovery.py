import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from redzone.discovery.runtime import DiscoveryRuntime


def runtime():
    value = DiscoveryRuntime.__new__(DiscoveryRuntime)
    value.engine = NS(cfg=NS(get=lambda key, default=None: False if key == "mode.dry_run" else default),
                      control=NS(usable=[]), recorded=NS(active=False))
    value.book = NS(refresh_sticks=AsyncMock())
    value.autonomy = NS(step=AsyncMock(), close=AsyncMock())
    value._device_suspended = False
    value._device_iteration = None
    return value


async def test_recorded_entry_cancels_and_awaits_inflight_discovery_read_before_device_job():
    value = runtime()
    entered, released = asyncio.Event(), asyncio.Event()

    async def read(_):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            released.set()

    value.book.refresh_sticks = read
    value._device_iteration = asyncio.create_task(value._step())
    await entered.wait()
    await value.suspend_device_work()
    assert released.is_set() and value._device_iteration.done()
    value.autonomy.step.assert_not_awaited()
    value.autonomy.close.assert_awaited_once()


async def test_recorded_entry_during_package_read_prevents_following_maintenance():
    value = runtime()

    async def read(_):
        value.engine.recorded.active = True

    value.book.refresh_sticks = read
    await value._step()
    value.autonomy.step.assert_not_awaited()
    assert value.device_suspended


async def test_suspended_runtime_does_not_scan_or_launch_jobs():
    value = runtime()
    await value.suspend_device_work()
    await value._step()
    value.book.refresh_sticks.assert_not_awaited()
    value.autonomy.step.assert_not_awaited()
    value.resume_device_work()
    await value._step()
    value.book.refresh_sticks.assert_awaited_once()
    value.autonomy.step.assert_awaited_once()
