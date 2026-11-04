"""[switch timing] The e2e night's TV with the SmartThings input change, at the latency measured on
the user's TV (Nov 3 2026: 10 of 10 confirmed, median 1.98 s, max 2.2 s, command -> readback).

    python -m redzone.lab.night --tv smartthings

The production SamsungTV runs unchanged (set_input, readback verify, backoff); only the two cloud
calls are faked: setInputSource makes the house's TV follow that stick after a seeded draw from the
measured distribution, and the status read reports what the house's TV shows. Nothing is pressed on
either stick, so both keep playing (the point of the TV path).
"""
from __future__ import annotations

import asyncio
import random

# Command -> the TV reporting the new input. Triangular around the measured median, inside the
# measured range: the readback loop (tv.VERIFY_GAP_S) adds up to one gap on top, as it did live.
MEASURED_MEDIAN_S = 1.98
MEASURED_MAX_S = 2.2
LOW_S = 1.6


def draw(rng: random.Random) -> float:
    """One input change, seconds. Read back one VERIFY_GAP_S later at most, like the live timing."""
    return rng.triangular(LOW_S, MEASURED_MAX_S - 0.15, MEASURED_MEDIAN_S - 0.1)


def make_tv_class(tv_mod, house, rng: random.Random):
    """A SamsungTV whose SmartThings calls drive the fake house. tv_mod is redzone.tv (imported by the
    night after its clock is installed)."""

    class NightSmartThingsTV(tv_mod.SamsungTV):
        def __post_init__(self):
            super().__post_init__()
            self.st_device = self.st_device or "night-tv"
            self.st_capability = "samsungvd.mediaInputSource"
            self.st_inputs = [s.hdmi for s in house.sticks.values()]
            self.method = "smartthings"
            self.draws: list[float] = []

        @property
        def cloud_ready(self) -> bool:
            return not self._backed_off("smartthings")

        @property
        def has_readback(self) -> bool:
            return True

        async def st_discover(self) -> bool:
            return True

        async def st_input(self, source: str) -> bool:
            stick = next((s for s in house.sticks.values() if s.hdmi == source), None)
            if stick is None:
                self.last_error = f"TV doesn't list input {source}"
                return False
            d = draw(rng)
            self.draws.append(round(d, 3))
            house.event("smartthings_input", stick.host, f"{source} in {d:.2f}s")
            asyncio.get_running_loop().call_later(d, house.tv_input, stick)
            return True

        async def input_status(self) -> str | None:
            s = house.tv.source
            return s.hdmi if s is not None else None

        async def warm(self) -> bool:
            return True

    return NightSmartThingsTV
