"""[T7] An offline Fire TV stick for the SEEK_TO helper's tests: it answers the same shell commands the
real stick does (tools/rzseek `list` / `seek` / `seekrel`, `input keyevent` chains, the dumpsys playback read)
and moves a picture the way the live lab measured it, so seek_to's landing, timeout, retry and key fallback
paths can be driven without a stick. Not a test module (no test_ prefix): tests/test_t7_*.py import it.

Time is real (time.monotonic) but tiny: stalls are tens of ms, so "how far behind" stays exact on the
stick's own clock (uptime - position), the same arithmetic Controller._seek_sample and rzseek use.

Fault knobs (all default off) cover what the hidden-stick run can hit:
  seek_actions      the session's PlaybackState actions; without bit 256 rzseek prints "no SEEK_TO"
  dex_missing       app_process can't find the class (dex not pushed)
  no_session        rzseek finds no session for the package
  timeouts          the next N rzseek seek calls hang past the caller's timeout, without moving
  hang_forever      every rzseek call ignores the timeout and blocks (the helper must guard itself)
  noop_seeks        the next N seekTo calls are accepted but the picture does not move
  seek_error_s      a list of landing errors (s) for successive seekTo calls (+ = lands further back)
  partial_then_fail first seekTo moves only this fraction of the ask; every later rzseek call fails
  raise_on_shell    shell raises OSError (a dead adb session)
  key_fail          `input keyevent` returns ok=False
  ad_on_seek        after a seekTo, the app plays a forced ad pod: a new media item, position restarts
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, field

from redzone.tuners.adb import RunResult

YTTV_PKG = "com.amazon.firetv.youtube.tv"
PRIME_PKG = "com.amazon.firebat"
SEEK_TO_BIT = 256
PLAYING, PAUSED, BUFFERING = 3, 2, 6


def _j(obj) -> str:
    """JSON the way RzSeek.java prints it: no spaces."""
    return json.dumps(obj, separators=(",", ":"))


@dataclass(frozen=True)
class AppProfile:
    """How an app's picture answers keys and seekTo (live lab Nov 4 for YTTV; Prime per docs/timing.md)."""
    pkg: str
    key_step_s: float          # one REWIND press
    key_swallowed: int         # presses of each batch that only show the seek bar
    stall_s: tuple[float, float]   # REAL seconds the picture freezes after a seek (scaled down for tests)
    actions: int               # PlaybackState actions the app advertises
    epoch_position: bool       # Prime: position is the frame's wall clock in ms


YTTV = AppProfile(YTTV_PKG, 15.0, 1, (0.02, 0.04), 383, False)
PRIME = AppProfile(PRIME_PKG, 10.0, 0, (0.02, 0.04), 1049455, True)
PROFILES = {"youtubetv": YTTV, "yttv": YTTV, "prime": PRIME}


@dataclass
class SimStick:
    app: AppProfile = YTTV
    host: str = "stickB"          # never an address: the repo is public
    model: str = "AFTKM"
    seed: int = 0
    behind_s: float = 0.0          # where the picture starts, seconds behind the live edge
    # faults
    seek_actions: int | None = None
    dex_missing: bool = False
    no_session: bool = False
    timeouts: int = 0
    hang_forever: bool = False
    noop_seeks: int = 0
    seek_error_s: list[float] = field(default_factory=list)
    partial_then_fail: float | None = None
    raise_on_shell: bool = False
    key_fail: bool = False
    ad_on_seek: bool = False
    # what happened
    calls: list[str] = field(default_factory=list)
    seeks: int = 0
    key_presses: int = 0
    ad_pods: int = 0
    tuned: dict | None = None

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._boot = time.monotonic() - 50_000.0          # the stick has been up ~14 h
        self._live0 = self._up()
        self._state = PLAYING
        self._stall_until = 0.0
        self._title = "Game"
        # the stream's position at anchor: Prime an epoch in ms, YTTV a per-tune counter (~46,790 s per tune)
        base = time.time() * 1000.0 if self.app.epoch_position else 46_790_000.0
        self._anchor_up = self._live0
        self._anchor_pos = base - self.behind_s * 1000.0
        self._p0 = base                  # the live edge's position at _live0
        self._partial_used = False

    # ---- the picture ----------------------------------------------------------------------------
    def _up(self) -> float:
        return (time.monotonic() - self._boot) * 1000.0

    def _settle(self) -> None:
        if self._state == BUFFERING and time.monotonic() >= self._stall_until:
            # the stall ends: position resumes from where it froze, the uptime kept running
            self._anchor_up, self._state = self._up(), PLAYING

    def pos_now(self) -> float:
        self._settle()
        if self._state == PLAYING:
            return self._anchor_pos + (self._up() - self._anchor_up)
        return self._anchor_pos

    def behind_now(self) -> float:
        """Seconds behind the live edge right now (the test's ground truth, not visible to the helper)."""
        return ((self._up() - self._live0) - (self.pos_now() - self._p0)) / 1000.0

    def _move(self, back_s: float) -> None:
        """Jump the picture back_s seconds (negative = forward, never past the live edge) and stall."""
        now = self.pos_now()
        live_pos = self._p0 + (self._up() - self._live0)
        target = min(now - back_s * 1000.0, live_pos)
        self._anchor_pos, self._anchor_up = target, self._up()
        lo, hi = self.app.stall_s
        self._stall_until = time.monotonic() + self._rng.uniform(lo, hi)
        self._state = BUFFERING

    # ---- the shell ------------------------------------------------------------------------------
    async def shell(self, cmd: str, timeout: float = 8.0) -> RunResult:
        self.calls.append(cmd)
        if self.raise_on_shell:
            raise OSError("adb: device offline")
        if "RzSeek" in cmd:
            return await self._rzseek(cmd, timeout)
        if "input keyevent" in cmd:
            return await self._keys(cmd)
        if "dumpsys media_session" in cmd:
            return RunResult(True, self._dumpsys(), 0.01)
        return RunResult(True, "", 0.0)

    async def key(self, *codes: str) -> RunResult:
        r = RunResult(True, "", 0.0)
        for c in codes:
            r = await self.shell(f"input keyevent {c}", timeout=4)
            if not r.ok:
                break
        return r

    async def playback(self, package: str | None = None) -> dict:
        self._settle()
        state = {PLAYING: "playing", PAUSED: "paused", BUFFERING: "buffering"}[self._state]
        return {"state": state, "pkg": self.app.pkg, "position": int(self._anchor_pos),
                "updated": int(self._anchor_up), "read_ok": True}

    def _dumpsys(self) -> str:
        self._settle()
        return (f"    package={self.app.pkg}\n    active=true\n"
                f"    state=PlaybackState {{state={self._state}, position={int(self._anchor_pos)}, "
                f"buffered position=0, speed=1.0, updated={int(self._anchor_up)}, actions={self._actions()}, "
                f"custom actions=[], active item id=-1, error=null}}\n"
                f"    metadata: size=4, description={self._title}, Channel, null\n")

    def _actions(self) -> int:
        return self.app.actions if self.seek_actions is None else self.seek_actions

    async def _keys(self, cmd: str) -> RunResult:
        if self.key_fail:
            return RunResult(False, "error: closed", 0.01)
        codes = re.findall(r"input keyevent (\w+)", cmd)
        rew = sum(1 for c in codes if c in ("KEYCODE_MEDIA_REWIND", "KEYCODE_DPAD_LEFT"))
        fwd = sum(1 for c in codes if c in ("KEYCODE_MEDIA_FAST_FORWARD", "KEYCODE_DPAD_RIGHT"))
        self.key_presses += rew + fwd
        if rew or fwd:
            n_rew = max(0, rew - self.app.key_swallowed) if rew else 0
            n_fwd = max(0, fwd - self.app.key_swallowed) if fwd else 0
            back = (n_rew - n_fwd) * self.app.key_step_s
            if back:
                self._move(back)
        if "KEYCODE_MEDIA_PAUSE" in codes:
            self._anchor_pos, self._anchor_up, self._state = self.pos_now(), self._up(), PAUSED
        if "KEYCODE_MEDIA_PLAY" in codes and self._state == PAUSED:
            self._anchor_up, self._state = self._up(), PLAYING
        return RunResult(True, "", 0.01)

    async def _rzseek(self, cmd: str, timeout: float) -> RunResult:
        if self.hang_forever:
            await asyncio.sleep(3600)
        args = cmd.split("RzSeek", 1)[1].split()
        if self.dex_missing:
            return RunResult(False, "Error: Could not find or load main class RzSeek\n"
                                    "java.lang.ClassNotFoundException: RzSeek", 0.2)
        verb = args[0] if args else ""
        if verb == "list":
            return RunResult(True, self._list_line() + "\n", 0.05)
        if verb not in ("seek", "seekrel") or len(args) < 3:
            return RunResult(True, '{"error":"usage: list | seek <pkg> <ms> | seekrel <pkg> <delta_ms>"}\n', 0.05)
        if self.partial_then_fail is not None and self._partial_used:
            return RunResult(False, "", 0.05)
        if self.timeouts > 0:
            self.timeouts -= 1
            await asyncio.sleep(min(timeout, 0.05))
            return RunResult(False, "", timeout)
        pkg = args[1]
        if self.no_session or pkg != self.app.pkg:
            return RunResult(True, '{"error":"no session for that package"}\n', 0.05)
        if not self._actions() & SEEK_TO_BIT:
            return RunResult(True, _j({"pkg": pkg, "error": "no SEEK_TO in actions",
                                               "actions": self._actions()}) + "\n", 0.05)
        now = self.pos_now()
        target = float(args[2]) if verb == "seek" else now + float(args[2])
        back = (now - target) / 1000.0
        self.seeks += 1
        out = _j({"pkg": pkg, "seek_to": int(target), "from": int(now), "elapsed": int(self._up())})
        if self.noop_seeks > 0:
            self.noop_seeks -= 1
            return RunResult(True, out + "\n", 0.05)
        if self.partial_then_fail is not None:
            self._partial_used = True
            back *= self.partial_then_fail
        elif self.seek_error_s:
            back += self.seek_error_s.pop(0)
        self._move(back)
        if self.ad_on_seek:
            self.ad_pods += 1
            self._title = "Ad"
            self._anchor_pos = 0.0   # a forced pod plays as its own media item: the counter restarts
        return RunResult(True, out + "\n", 0.05)

    def _list_line(self) -> str:
        if self.no_session:
            return ""
        now_up = self._up()
        return _j({"pkg": self.app.pkg, "state": self._state_settled(), "position": int(self._anchor_pos),
                           "updated": int(self._anchor_up), "speed": 1.0, "actions": self._actions(),
                           "elapsed": int(now_up), "pos_now": int(self.pos_now()),
                           "wall_ms": int(time.time() * 1000)})

    def _state_settled(self) -> int:
        self._settle()
        return self._state


def make(app: str = "youtubetv", **kw) -> SimStick:
    return SimStick(app=PROFILES[app], **kw)
