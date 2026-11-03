"""Fault injection: fake sticks, TV and feeds that fail on cue, for drills and end-to-end tests.

Nothing here touches real hardware or the network. Every fake records what it was told to do, and
a shared `World` records every moment the picture changed and whether it was safe to cut then, so a
test can count mid-play cuts directly instead of trusting the code's own report.

Faults (names used by scripts/fault_drill.py and any end-to-end test that wants them):
  adb_drop       the stick answers "error: device offline" to everything
  adb_hang       every adb command hangs until its own timeout, then fails
  app_crash      after a tune the app's media session is gone (state none)
  prompt_screen  the app sits on an "Are you still watching?" page instead of playing
  feed_5xx       the score feed answers 503
  tv_flip_fail   HOME never makes the stick the active source and the TV remote fails too
  clock_skew     the wall clock jumps (NTP fix, laptop waking from sleep) while time.monotonic doesn't

Usage:
    world = World(safe=lambda t: t < 3 or t > 9)   # a play runs from t=3 to t=9
    a, b = FaultStick("a:5555", world), FaultStick("b:5555", world)
    b.fault = "adb_hang"
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult

FAULTS = ("adb_drop", "adb_hang", "app_crash", "prompt_screen", "feed_5xx", "tv_flip_fail", "clock_skew")


@dataclass
class Cut:
    at: float          # seconds since the world started
    stick: str
    safe: bool         # was it a dead ball when the picture changed


@dataclass
class World:
    """Shared timeline. `safe(t)` says whether the visible game is at a dead ball t seconds in."""
    safe: Callable[[float], bool] = lambda t: True
    t0: float = field(default_factory=time.monotonic)
    on_input: str | None = None
    cuts: list[Cut] = field(default_factory=list)

    def now(self) -> float:
        return time.monotonic() - self.t0

    def is_safe(self) -> bool:
        return bool(self.safe(self.now()))

    def input_to(self, host: str) -> None:
        if host != self.on_input:
            self.on_input = host
            self.cuts.append(Cut(round(self.now(), 2), host, self.is_safe()))

    @property
    def mid_play_cuts(self) -> int:
        return sum(not c.safe for c in self.cuts)


class FaultStick(Stick):
    """A Fire TV stick that behaves like the fakes in tests/test_hot_standby.py until `fault` is set."""

    def __init__(self, host: str, world: World, playing: str = "playing", power: str = "Awake"):
        super().__init__(host, name=host, os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": "yttv.pkg", "prime": "prime.pkg"}
        self.world = world
        self.fault: str | None = None
        self.playing = playing
        self.power = power
        self.log: list[str] = []
        self.timeouts = 0           # commands that hung to their timeout

    async def _shell(self, cmd: str, timeout: float = 8.0) -> RunResult:
        self.log.append(cmd)
        if self.fault == "adb_drop":
            self.state = "offline"
            return RunResult(False, "error: device offline", 0.0)
        if self.fault == "adb_hang":
            self.timeouts += 1
            await asyncio.sleep(timeout)
            return RunResult(False, f"timeout/error: no reply in {timeout:g}s", timeout)
        if "mIsActiveSource" in cmd:
            return RunResult(True, "1" if self.world.on_input == self.host else "0", 0.01)
        if "Active Source> " in cmd:
            return RunResult(True, "0", 0.01)
        if "KEYCODE_HOME" in cmd:
            if self.fault != "tv_flip_fail":
                await asyncio.sleep(0.03)
                self.world.input_to(self.host)
            return RunResult(True, "", 0.01)
        if "onetouchplay" in cmd:
            return RunResult(True, "Error: unknown command", 0.01)
        if "mWakefulness" in cmd:
            return RunResult(True, f"  mWakefulness={self.power}", 0.01)
        return RunResult(True, "", 0.01)

    async def key(self, *codes: str) -> RunResult:
        r = await self.shell("input keyevent " + " ".join(codes))
        return r

    async def playback(self, package: str | None = None) -> dict:
        if self.fault in ("adb_drop", "adb_hang"):
            r = await self.shell("dumpsys media_session", timeout=4)
            return {"state": "unknown", "pkg": package, "read_ok": False, "error": r.out}
        if self.fault in ("app_crash", "prompt_screen"):
            return {"state": "none", "pkg": package, "position": 0, "updated": 0}
        return {"state": self.playing, "pkg": package, "position": 0, "updated": 0}


class FaultTuner:
    """A tuner that sends one deep link down the stick's own shell, so stick faults reach it."""

    def __init__(self, took: float = 0.05):
        self.took = took
        self.calls: list[tuple[str, str]] = []

    async def tune(self, stick: Stick, ref, target: str, may_cut=None) -> TuneResult:
        self.calls.append((stick.host, target))
        t0 = time.monotonic()
        r = await stick.shell(f"am start -a android.intent.action.VIEW -d {target}", timeout=8)
        if not r.ok:
            return TuneResult(False, target, time.monotonic() - t0, ["launch"], "launch failed: " + r.out[:60])
        await asyncio.sleep(self.took)
        pb = await stick.playback(stick.packages.get("youtubetv"))
        if pb.get("state") != "playing":
            what = "prompt on screen" if getattr(stick, "fault", None) == "prompt_screen" else "video never started"
            return TuneResult(False, target, time.monotonic() - t0, ["launch", pb.get("state", "?")], what)
        stick.remember(target, pb)
        return TuneResult(True, target, max(0.3, time.monotonic() - t0), ["launch", "playing"], "")


class FaultTV:
    """The TV's own remote / SmartThings. `fail` makes every input change fail."""

    def __init__(self, world: World, hdmi: dict[str, str]):
        self.world = world
        self.hdmi = hdmi
        self.fail = False
        self.calls = 0

    async def set_input(self, hdmi: str) -> bool:
        self.calls += 1
        if self.fail:
            return False
        for host, port in self.hdmi.items():
            if port == hdmi:
                self.world.input_to(host)
        return True

    async def input_status(self) -> str | None:
        return self.hdmi.get(self.world.on_input or "")


def failing_transport(status: Callable[[], int], body: bytes = b'{"events": []}') -> httpx.MockTransport:
    """An httpx transport that answers every request with status() (e.g. 503 while a fault is on)."""
    def handler(request: httpx.Request) -> httpx.Response:
        code = status()
        return httpx.Response(code, content=body if code == 200 else b"Service Unavailable",
                              headers={"content-type": "application/json"})
    return httpx.MockTransport(handler)


class SkewClock:
    """A wall clock that can jump. Pass it where code takes `clock=` or patch time.time with it."""

    def __init__(self, base: Callable[[], float] = time.time):
        self.base = base
        self.offset = 0.0

    def jump(self, seconds: float) -> None:
        self.offset += seconds

    def __call__(self) -> float:
        return self.base() + self.offset
