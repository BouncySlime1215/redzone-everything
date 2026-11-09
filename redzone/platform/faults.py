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
  app_update     the app is being updated: its deep link fails `launch_fail_left` times, then it plays
  clock_skew     the wall clock jumps (NTP fix, laptop waking from sleep) while time.monotonic doesn't

Queue faults (WP-10, Q14): `QueueTuner` fails one route at a time with a named mode (never_started,
link_refused, walled, blackout, hung) and `drive_queue` follows redzone/noggin/gamequeue.on_failure's
answer to each, then flips only at a dead ball. A drill built on it proves the typed policy never
presses a walled route again, never flips a stick that is not ready, and never cuts mid-play.

Why: WP-10 / Q14 and major-considerations step 10: the drill had hidden-stick and reboot/buffering faults
only; a "still watching" prompt, an app crash or update on the stick on screen, and a failed tune, scrub or
route on the hidden stick were never exercised, and a real fault is when a mid-play cut would happen.

Usage:
    world = World(safe=lambda t: t < 3 or t > 9)   # a play runs from t=3 to t=9
    a, b = FaultStick("a:5555", world), FaultStick("b:5555", world)
    b.fault = "adb_hang"
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from dataclasses import dataclass, field
from typing import Callable

import httpx

from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import TuneResult

FAULTS = ("adb_drop", "adb_hang", "app_crash", "app_update", "prompt_screen", "feed_5xx", "tv_flip_fail",
          "clock_skew")
QUEUE_MODES = ("ok", "never_started", "link_refused", "walled", "blackout", "hung")


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
        self.launch_fail_left = 0   # app_update: this many more deep links fail

    async def _shell(self, cmd: str, timeout: float = 8.0) -> RunResult:
        self.log.append(cmd)
        if self.fault == "adb_drop":
            self.state = "offline"
            return RunResult(False, "error: device offline", 0.0)
        if self.fault == "adb_hang":
            self.timeouts += 1
            await asyncio.sleep(timeout)
            return RunResult(False, f"timeout/error: no reply in {timeout:g}s", timeout)
        if "am start" in cmd and self.launch_fail_left > 0:
            self.launch_fail_left -= 1
            return RunResult(False, "Error: the app is being updated", 0.05)
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
        if self.fault in ("app_crash", "prompt_screen", "app_update"):
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


# ---- queue faults (WP-10 / Q14) -------------------------------------------------------------------

class QueueTuner:
    """Tunes the hidden stick for a queue drill. `modes` maps a route ref to how it fails; a ref not in it
    plays. The result's `error` names the failure the way a typed failure would (WP-13 wires the real
    classifier). It never sends a key: a walled route must not get one."""

    FAIL_TEXT = {"never_started": "video never started", "link_refused": "launch failed: link refused",
                 "walled": "purchase or sign-in screen", "blackout": "blacked out in your area",
                 "hung": "stick not responding"}

    def __init__(self, modes: dict[str, str] | None = None):
        self.modes = dict(modes or {})
        self.calls: list[tuple[str, str]] = []

    async def tune(self, stick: Stick, ref, target: str, may_cut=None) -> TuneResult:
        self.calls.append((stick.host, target))
        mode = self.modes.get(target, "ok")
        if mode == "ok":
            stick.remember(target, {"state": "playing", "pkg": "yttv.pkg", "position": 0, "updated": 0})
            return TuneResult(True, target, 0.05, ["launch", "playing"], "")
        return TuneResult(False, target, 0.05, ["launch"], self.FAIL_TEXT.get(mode, "unknown"))


def failure_of(r: TuneResult):
    """The typed gamequeue.Failure a failed TuneResult stands for (UNKNOWN when it says nothing)."""
    from redzone.noggin.gamequeue import Failure
    text = (r.error or "").lower()
    for word, kind in (("not responding", Failure.STICK_HUNG), ("link refused", Failure.LINK_REFUSED),
                       ("purchase", Failure.WALLED), ("blacked out", Failure.BLACKOUT),
                       ("never started", Failure.NEVER_STARTED)):
        if word in text:
            return kind
    return Failure.UNKNOWN


@dataclass
class QueueRun:
    """What one queue drill did. `flipped` is the hidden stick's host when the TV was moved to it."""
    steps: list[str] = field(default_factory=list)       # one word per move, e.g. next_route, stay_live
    tuned: list[str] = field(default_factory=list)       # targets sent to the hidden stick, in order
    level: int = 0                                       # the entry's level at the end
    flipped: str | None = None
    keys_after_wall: int = 0


async def drive_queue(entry, tuner: QueueTuner, stick: Stick, world: World, *, scrub: str | None = None,
                      max_moves: int = 8, wait_safe_s: float = 3.0) -> QueueRun:
    """Bring `entry` up on the hidden `stick`, following gamequeue.on_failure after every failure, then
    flip to it only if it is flippable and only at a dead ball (waiting up to wait_safe_s for one).
    `scrub` injects a scrub failure ("scrub_failed" or "scrub_banner_miss") after the first good tune.
    No key is sent to a route the queue marked press_ok=False."""
    from redzone.noggin import gamequeue as gq
    run = QueueRun()
    walled: set[str] = set()
    keys_at_wall = None
    attempt = 0
    scrub_left = {"scrub_failed": 2, "scrub_banner_miss": 1}.get(scrub or "", 0)
    for _ in range(max_moves):
        route = next((r for r in entry.routes if r.ref not in walled and not r.flagged), None)
        if route is None:
            run.steps.append("no_route")
            entry = gq.on_failure(entry, gq.Failure.NO_ROUTE).entry or entry
            break
        target = route.ref
        res = await tuner.tune(stick, route, target)
        run.tuned.append(target)
        failure = None if res.ok else failure_of(res)
        if res.ok and scrub_left:
            failure = gq.Failure.SCRUB_BANNER_MISS if scrub == "scrub_banner_miss" and scrub_left == 1 and attempt == 0 \
                else gq.Failure.SCRUB_FAILED
            scrub_left = 0 if failure == gq.Failure.SCRUB_FAILED else scrub_left - 1
        if failure is None:
            entry = dataclasses.replace(entry, level=gq.Level.R4)
            run.steps.append("ready")
            break
        move = gq.on_failure(entry, failure, attempt)
        run.steps.append(move.step.value)
        if move.entry is not None:
            entry = move.entry
        if not move.press_ok:
            walled.add(target)
            keys_at_wall = sum("keyevent" in c for c in stick.log)
        attempt = attempt + 1 if move.step in (gq.Step.RETRY, gq.Step.RETUNE) or failure == gq.Failure.SCRUB_BANNER_MISS else 0
        if move.step in (gq.Step.STAY_LIVE, gq.Step.NEXT_GAME, gq.Step.WAIT_STICK, gq.Step.STOP):
            break
    run.level = int(gq.level(entry))
    if keys_at_wall is not None:
        run.keys_after_wall = sum("keyevent" in c for c in stick.log) - keys_at_wall
    if gq.flippable(entry):
        waited, tick = 0.0, 0.05
        while not world.is_safe() and waited < wait_safe_s:
            await asyncio.sleep(tick)
            waited += tick
        if world.is_safe():
            world.input_to(stick.host)
            run.flipped = stick.host
    return run
