"""[switch timing] The e2e night's TV with the SmartThings input change, at the latency measured on
the user's TV (live lab Nov 4 2026: 64 of 64 confirmed, 0 retries, p50 1.89 s, p95 2.18 s, max 2.58 s,
command -> readback; ack 0.31 s. It replaces the Nov 3 10-sample 1.98 / 2.2 s).

    python -m redzone.lab.night --tv smartthings

The production SamsungTV runs unchanged (set_input, readback verify, backoff); only the two cloud
calls are faked: setInputSource makes the house's TV follow that stick after a seeded draw from the
measured distribution, and the status read reports what the house's TV shows. Nothing is pressed on
either stick, so both keep playing (the point of the TV path).

Why: first added in 28e7207f (2026-11-04) as "Switch timing: plan TV-input flips with the measured SmartThings latency; e2e night on that path"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import random

# Command -> the TV reporting the new input, as an inverse CDF through the measured quantiles. The
# readback loop (tv.VERIFY_GAP_S = 0.3) adds U(0, 0.3) on top, as it did live, so each point sits
# 0.15 s under the confirmed figure: confirmed p50 ~1.89, p95 ~2.18, max ~2.58 (tests/test_switch_timing).
MEASURED_P50_S, MEASURED_P95_S, MEASURED_MAX_S = 1.89, 2.18, 2.58
MEASURED_MEDIAN_S = MEASURED_P50_S
READBACK_S = 0.15
_Q = ((0.0, 1.60), (0.5, MEASURED_P50_S), (0.95, MEASURED_P95_S), (1.0, MEASURED_MAX_S))


def draw(rng: random.Random) -> float:
    """One input change, seconds. Read back one VERIFY_GAP_S later at most, like the live timing."""
    u = rng.random()
    for (q0, x0), (q1, x1) in zip(_Q, _Q[1:]):
        if u <= q1:
            return x0 + (x1 - x0) * (u - q0) / (q1 - q0) - READBACK_S
    return MEASURED_MAX_S - READBACK_S


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
            if house.faults_on("st_down"):
                self.last_error = "SmartThings 503 (fault st_down)"
                return False
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
            if house.faults_on("st_down"):
                return None
            s = house.tv.source
            return s.hdmi if s is not None else house.tv.other

        async def warm(self, probe: bool = False) -> bool:   # the keepalive passes probe=True (DEV-07)
            return True

    return NightSmartThingsTV
