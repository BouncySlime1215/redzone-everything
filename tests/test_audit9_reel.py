"""AUDIT-9: the reel prepares only clips a reel could still use."""
import asyncio

from redzone.reel import Clip, Reeler


class FakeCache:
    def __init__(self):
        self.ready, self._busy, self.root, self.prepared = {}, set(), None, []

    async def prepare(self, c):
        self.prepared.append(c.id)
        return False


def test_aged_out_clips_do_not_take_a_preparation_slot(cfg):
    now = 100_000.0
    cache = FakeCache()
    r = Reeler(cfg, cache=cache, clock=lambda: now)
    for i in range(12):   # old, high value, still held by ingest for 10 more minutes (age 2000 s > 1800 s)
        r.clips[f"old{i}"] = Clip(f"old{i}", f"g{i}", "nfl", "Touchdown", "u", now - 2000, 20.0, 30.0)
    r.clips["fresh"] = Clip("fresh", "gf", "nfl", "Field goal", "u", now - 60, 20.0, 12.0)

    async def go():
        await r.refresh()
        await asyncio.gather(*list(r._tasks))
    asyncio.run(go())
    assert "fresh" in cache.prepared
    assert not any(c.startswith("old") for c in cache.prepared)
