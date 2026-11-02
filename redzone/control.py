"""Turns decisions into TV actions. Dry-run by default.

Single stick: tune the stick, confirm video is playing.
Two sticks ("hot standby"): the hidden stick is ALWAYS the one that gets tuned. When it's
confirmed playing, we wait for a safe moment on the visible game (no play on screen) and only
then flip the TV input (1-2s). Result: no black screens, and the cut lands exactly when it's safe.
Between switches, the hidden stick pre-loads the most likely next game.

Guardrails:
- One command at a time; a newer command cancels an older one still running.
- Every tune is confirmed by the stick's own playback report; a failed tune is retried once,
  then reported (3 errors in 5 min = safe mode in the decider).
- Two-stick mode drops to single-stick automatically if an input flip fails.
- No stick reachable -> behaves like dry-run and says so on the phone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from redzone.channels import ChannelMap
from redzone.tuners.adb import Stick, adb_available
from redzone.tuners.tuners import TUNERS, TuneResult
from redzone.tv import SamsungTV

log = logging.getLogger(__name__)
DEVICES = Path(__file__).resolve().parent.parent / "data" / "devices.json"
MAX_SAFE_WAIT_S = 25.0   # how long a flip may wait for a safe moment before giving up


@dataclass
class Outcome:
    ok: bool
    target: str | None
    took: float
    detail: str
    dry_run: bool
    confirmed_at: float | None = None


class Controller:
    def __init__(self, cfg, channels: ChannelMap):
        self.cfg = cfg
        self.channels = channels
        self.sticks: list[Stick] = []
        self.hdmi: dict[str, str] = {}          # stick host -> "HDMI1"
        self.tv = SamsungTV()
        self.on_target: str | None = None
        self.active = 0                          # index of the stick on screen
        self.hidden_target: str | None = None    # what the hidden stick is playing
        self.hidden_ready = False
        self.two_stick_ok = True                 # flips to False if an input flip ever fails
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._preload_task: asyncio.Task | None = None
        self._last_preload = 0.0
        self.adb_ok = False
        self.last: Outcome | None = None
        self.note = ""

    # --- devices -----------------------------------------------------------------

    def load_devices(self) -> None:
        for s in self.sticks:
            s.drop_session()   # each stick keeps an adb shell open; don't leave the old ones running
        self.sticks, self.hdmi = [], {}
        d = {}
        if DEVICES.exists():
            try:
                d = json.loads(DEVICES.read_text())
            except ValueError:
                d = {}
        for s in d.get("sticks", []):
            st = Stick(s["host"], name=s.get("name", ""), model=s.get("model", ""), os=s.get("os", ""))
            st.packages = s.get("packages", {})
            self.sticks.append(st)
            if s.get("hdmi"):
                self.hdmi[st.host] = s["hdmi"]
        self.tv = SamsungTV.from_devices()

    async def start(self) -> None:
        self.adb_ok = await adb_available()
        self.load_devices()
        await self.keepalive()

    async def close(self) -> None:
        """App shutdown: close each stick's always-open adb shell."""
        for s in self.sticks:
            await s.close()

    async def keepalive(self) -> None:
        """Reconnect any stick that dropped (reboots, Wi-Fi blips). Called every ~10s."""
        if not self.adb_ok:
            return
        for s in self.sticks:
            if s.state != "connected" or time.time() - s.last_ok > 60:
                if await s.connect() and not s.packages:
                    await s.identify()

    @property
    def usable(self) -> list[Stick]:
        return [s for s in self.sticks if s.controllable]

    @property
    def main(self) -> Stick | None:
        u = self.usable
        if not u:
            return None
        return self.sticks[self.active] if self.sticks[self.active] in u else u[0]

    @property
    def two_stick(self) -> bool:
        u = self.usable
        return (self.cfg.get("two_stick.enabled", True) and self.two_stick_ok and len(u) >= 2
                and all(s.host in self.hdmi for s in u[:2]))

    @property
    def hidden(self) -> Stick | None:
        if not self.two_stick:
            return None
        u = self.usable[:2]
        return u[1] if self.main is u[0] else u[0]

    @property
    def live_control(self) -> bool:
        return not self.cfg.get("mode.dry_run", True) and self.main is not None

    # --- actions -----------------------------------------------------------------

    def submit(self, target: str | None, replay_back_s: float | None = None, on_done=None,
               may_cut: Callable[[], bool] | None = None) -> None:
        """Fire-and-forget; the newest command wins."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run(target, replay_back_s, on_done, may_cut))

    async def _run(self, target, replay_back_s, on_done, may_cut) -> None:
        async with self._lock:
            try:
                out = await self.execute(target, replay_back_s, may_cut)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let a tuner bug kill the app
                log.exception("tune crashed")
                out = Outcome(False, target, 0.0, f"crashed: {e}", False)
            self.last = out
            if on_done:
                on_done(out)

    def _ref(self, target: str) -> tuple[str, str]:
        app, _, ref = target.partition(":")
        if app == "youtubetv":
            ref = self.channels.yttv_code(target) or ""
        return app, ref

    async def _tune(self, stick: Stick, target: str, replay_back_s: float | None) -> TuneResult:
        app, ref = self._ref(target)
        tuner = TUNERS.get(app)
        if tuner is None or not ref:
            return TuneResult(False, target, 0.0, [], "channel link not set up yet" if tuner else f"no tuner for {app}")
        res = await tuner.tune(stick, ref, target)
        if res.ok and replay_back_s:
            await self._rewind(stick, replay_back_s)
        return res

    async def _rewind(self, stick: Stick, back_s: float) -> None:
        step = float(self.cfg.get("replay.seek_step_s", 10))
        presses = min(60, math.ceil(back_s / step))
        key = self.cfg.get("replay.seek_key", "KEYCODE_MEDIA_REWIND")
        await stick.shell("; ".join([f"input keyevent {key}"] * presses), timeout=20)
        await stick.key("KEYCODE_MEDIA_PLAY")

    async def execute(self, target: str | None, replay_back_s: float | None = None,
                      may_cut: Callable[[], bool] | None = None) -> Outcome:
        t0 = time.time()
        if not target:
            return Outcome(False, None, 0.0, "no channel for this game", True)
        if not self.live_control:
            why = "dry run" if self.cfg.get("mode.dry_run", True) else "no stick connected"
            self.on_target = target
            return Outcome(True, target, 0.0, why, True, time.time())
        if self.two_stick:
            out = await self._execute_two(target, replay_back_s, may_cut, t0)
            if out is not None:
                return out
        return await self._execute_one(target, replay_back_s, t0)

    async def _execute_one(self, target, replay_back_s, t0) -> Outcome:
        if self.on_target == target and replay_back_s is None:
            return Outcome(True, target, 0.0, "already on it", False, time.time())
        res = await self._tune(self.main, target, replay_back_s)
        if not res.ok:
            self._blackout_check(target, res)
            return Outcome(False, target, time.time() - t0, res.error + " | " + "; ".join(res.steps[-3:]), False)
        self.on_target = None if replay_back_s else target
        return Outcome(True, target, time.time() - t0, "; ".join(res.steps[-2:]), False, time.time())

    async def _execute_two(self, target, replay_back_s, may_cut, t0) -> Outcome | None:
        hid = self.hidden
        if hid is None:
            return None
        if not (self.hidden_target == target and self.hidden_ready and replay_back_s is None):
            self.hidden_target, self.hidden_ready = target, False
            res = await self._tune(hid, target, replay_back_s)
            if not res.ok:
                self._blackout_check(target, res)
                return Outcome(False, target, time.time() - t0, res.error, False)
            self.hidden_ready = True
        # Wait for a safe moment on what's visible, THEN flip. This is where "never mid-play" is enforced
        # to the second, because the cut happens at the flip.
        waited = 0.0
        while may_cut is not None and not may_cut() and waited < MAX_SAFE_WAIT_S:
            await asyncio.sleep(0.5)
            waited += 0.5
        if may_cut is not None and not may_cut():
            return Outcome(False, target, time.time() - t0, "no safe moment to cut — kept the current game", False)
        if not await self._flip_to(hid):
            self.two_stick_ok = False
            self.note = f"Two-stick mode off: couldn't switch TV input ({self.tv.last_error or 'unknown'})"
            log.warning(self.note)
            return None  # fall back to single-stick for this command
        old_target = self.on_target
        self.active = self.sticks.index(hid)
        self.on_target = None if replay_back_s else target
        self.hidden_target, self.hidden_ready = old_target, old_target is not None
        return Outcome(True, target, time.time() - t0, f"flipped to {self.hdmi.get(hid.host)}", False, time.time())

    async def _flip_to(self, stick: Stick) -> bool:
        """Make the TV show this stick. CEC one-touch-play from the stick first (no tokens needed),
        then the TV's own network remote, then SmartThings."""
        r = await stick.shell("cmd hdmi_control onetouchplay", timeout=5)
        if r.ok and "unknown" not in r.out.lower() and "error" not in r.out.lower():
            return True
        return await self.tv.set_input(self.hdmi.get(stick.host, "HDMI1"))

    def preload(self, target: str | None) -> None:
        """Keep the hidden stick on the most likely next game (at most every 30s)."""
        if not (self.live_control and self.two_stick and target) or target == self.on_target:
            return
        if target == self.hidden_target or time.time() - self._last_preload < 30:
            return
        if (self._task and not self._task.done()) or (self._preload_task and not self._preload_task.done()):
            return
        self._last_preload = time.time()
        self._preload_task = asyncio.create_task(self._preload(target))

    async def _preload(self, target: str) -> None:
        async with self._lock:
            hid = self.hidden
            if hid is None:
                return
            self.hidden_target, self.hidden_ready = target, False
            res = await self._tune(hid, target, None)
            self.hidden_ready = res.ok

    async def scan_sunday_ticket(self, codes: list[str], games: list, on_found=None) -> dict[str, str]:
        """Flip through the Sunday Ticket channels and read each one's game title off the screen.
        Uses the hidden stick in two-stick mode (you never see it), else the main stick (you will)."""
        from redzone.channels import match_title
        stick = self.hidden or self.main
        found: dict[str, str] = {}
        if stick is None or not codes:
            return found
        tuner = TUNERS["youtubetv"]
        async with self._lock:
            for code in codes:
                res = await tuner.tune(stick, code, f"youtubetv:st:{code}")
                text = ""
                for keys in ((), ("KEYCODE_DPAD_DOWN",)):  # the title shows with the player overlay
                    if keys:
                        await stick.key(*keys)
                        await asyncio.sleep(1.0)
                    text = await stick.screen_text()
                    g = match_title(text, games)
                    if g:
                        found[g.game_id] = code
                        if on_found:
                            on_found(g, code)
                        break
                log.info("ST scan %s -> %s (%s)", code, found and list(found)[-1], "ok" if res.ok else res.error)
            if stick is self.hidden:
                self.hidden_target, self.hidden_ready = None, False
            else:
                self.on_target = None
        return found

    def _blackout_check(self, target: str, res: TuneResult) -> None:
        text = " ".join(res.steps).lower() + res.error.lower()
        if "regional" in text or "blackout" in text or "not available in your location" in text:
            self.note = f"{target} is blacked out here"

    def status(self) -> list[dict]:
        out = []
        for i, s in enumerate(self.sticks):
            role = "on screen" if self.two_stick and s is self.main else (
                "standby" if self.two_stick and s is self.hidden else ("main" if s is self.main else "spare"))
            out.append({"host": s.host, "name": s.name or s.model or s.host, "os": s.os, "state": s.state,
                        "role": role, "hdmi": self.hdmi.get(s.host),
                        "standby_for": self.hidden_target if s is self.hidden else None,
                        "ready": self.hidden_ready if s is self.hidden else None})
        return out
