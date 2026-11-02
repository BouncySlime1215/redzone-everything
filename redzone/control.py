"""Turns decisions into TV actions. Dry-run by default.

Guardrails:
- One command at a time (a lock); a newer command cancels an older one still running.
- Every tune is confirmed by the stick's own playback report; a failed tune is retried once,
  then we go back to what was on and the decider is told (3 errors in 5 min = safe mode).
- No stick reachable -> automatically behaves like dry-run and says so on the phone.
- Replays rewind with key presses; that's marked experimental until measured in the Screen Lab.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

from redzone.channels import ChannelMap
from redzone.tuners.adb import Stick, adb_available
from redzone.tuners.tuners import TUNERS, TuneResult

log = logging.getLogger(__name__)
DEVICES = Path(__file__).resolve().parent.parent / "data" / "devices.json"


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
        self.on_target: str | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self.adb_ok = False
        self.last: Outcome | None = None

    # --- devices -----------------------------------------------------------------

    def load_devices(self) -> None:
        self.sticks = []
        if DEVICES.exists():
            try:
                d = json.loads(DEVICES.read_text())
            except ValueError:
                d = {}
            for s in d.get("sticks", []):
                st = Stick(s["host"], name=s.get("name", ""), model=s.get("model", ""), os=s.get("os", ""))
                st.packages = s.get("packages", {})
                self.sticks.append(st)

    async def start(self) -> None:
        self.adb_ok = await adb_available()
        self.load_devices()
        await self.keepalive()

    async def keepalive(self) -> None:
        """Reconnect any stick that dropped (reboots, Wi-Fi blips). Called every poll."""
        if not self.adb_ok:
            return
        for s in self.sticks:
            if s.state != "connected" or time.time() - s.last_ok > 60:
                was = s.state
                if await s.connect():
                    if not s.packages:
                        await s.identify()
                    if was not in ("connected", "unknown"):
                        log.info("stick %s reconnected", s.host)

    @property
    def main(self) -> Stick | None:
        return next((s for s in self.sticks if s.controllable), None)

    @property
    def live_control(self) -> bool:
        return not self.cfg.get("mode.dry_run", True) and self.main is not None

    # --- actions -----------------------------------------------------------------

    def submit(self, target: str | None, replay_back_s: float | None = None, on_done=None) -> None:
        """Fire-and-forget; the newest command wins."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run(target, replay_back_s, on_done))

    async def _run(self, target, replay_back_s, on_done) -> None:
        async with self._lock:
            try:
                out = await self.execute(target, replay_back_s)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never let a tuner bug kill the app
                log.exception("tune crashed")
                out = Outcome(False, target, 0.0, f"crashed: {e}", False)
            self.last = out
            if on_done:
                on_done(out)

    async def execute(self, target: str | None, replay_back_s: float | None = None) -> Outcome:
        t0 = time.time()
        if not target:
            return Outcome(False, None, 0.0, "no channel for this game", True)
        if not self.live_control:
            why = "dry run" if self.cfg.get("mode.dry_run", True) else "no stick connected"
            self.on_target = target
            return Outcome(True, target, 0.0, why, True, time.time())
        stick = self.main
        app, _, ref = target.partition(":")
        tuner = TUNERS.get(app)
        if tuner is None:
            return Outcome(False, target, 0.0, f"no tuner for {app}", False)
        if app == "youtubetv":
            ref = self.channels.yttv_code(target) or ""
        if not ref:
            return Outcome(False, target, 0.0, "channel link not set up yet", False)
        same = self.on_target == target and replay_back_s is None
        if same:
            return Outcome(True, target, 0.0, "already on it", False, time.time())
        res: TuneResult = await tuner.tune(stick, ref, target)
        if not res.ok:
            return Outcome(False, target, time.time() - t0, res.error + " | " + "; ".join(res.steps[-3:]), False)
        self.on_target = target
        if replay_back_s:
            step = float(self.cfg.get("replay.seek_step_s", 10))
            presses = min(60, math.ceil(replay_back_s / step))
            key = self.cfg.get("replay.seek_key", "KEYCODE_MEDIA_REWIND")
            await stick.shell("; ".join([f"input keyevent {key}"] * presses), timeout=20)
            await stick.key("KEYCODE_MEDIA_PLAY")
            self.on_target = None  # we're behind live now; next tune must re-tune even if same channel
        return Outcome(True, target, time.time() - t0, "; ".join(res.steps[-2:]), False, time.time())

    def status(self) -> list[dict]:
        out = []
        for i, s in enumerate(self.sticks):
            out.append({"host": s.host, "name": s.name or s.model or s.host, "os": s.os, "state": s.state,
                        "role": "main" if i == 0 else "standby"})
        return out
