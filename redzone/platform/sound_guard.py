"""Sound guard: the TV stays muted at volume 0, proven by reading it back (sound_guard.*, all off by default).

Why: Nick's hard rule (Nov 10: "Volume must be 1000% off"). Until now only a test-session script (tvctl.py) read the
TV's mute and volume back; the app itself had no readback. This moves that script's rules into the app.

Rules (the same as tvctl.py, which worked on the real TV):
  silent      muted AND volume 0, both read back from the TV (SmartThings). A sent command proves nothing.
  silence()   volume 0, then mute, then read back; a few tries.
  check()     one watchdog step. Silent -> ok. Not silent -> incident. Mute or volume unreadable
              `unreadable_max` times in a row -> incident ("no readback, no test"). TV off -> fine, but the next
              switch silences and reads back again first.
  incident()  power the TV off (on_incident: off_tv; "block" only blocks), record what was read (values only:
              power, mute, volume, input; never an address or device id) in data/sound_guard/incident.json, and
              block: no input switch and no key reaches the TV until it is cleared by hand (delete that file, or
              clear()). The block is the machine state platform.sound_guard "blocked", with its reason.

Where it runs (redzone/tv.py, only when sound_guard.enabled built this guard):
  - before the first input switch of a session (and the first after the TV was off): silence(), then check();
    the switch is refused unless silence was read back;
  - after every input switch (in the background, so the flip itself is not slowed);
  - every check_s seconds (an asyncio task started by the first switch or the TV keepalive; no thread).
With sound_guard.must_be_silent off the guard only reads and shows (no incident, no refusal), so shipped behaviour
is unchanged. With sound_guard.enabled off no guard exists and no sound call is ever made.

Not covered here (outside redzone/tv.py): the controller's HOME/CEC fallback flip on a stick. See the decision line.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import types
from pathlib import Path
from typing import Callable

from redzone import tv as tvmod
from redzone.config import default as _shipped
from redzone.machines.platform import SOUND_GUARD

log = logging.getLogger(__name__)

MARKER = tvmod.DATA / "sound_guard" / "incident.json"
SETTLE_S = float(_shipped("sound_guard.settle_s"))   # after volume 0 + mute, before the readback (tvctl.py); read once
_POWER = {"on", "off"}
_MUTE = {"muted", "unmuted"}


@types.coroutine
def _yield_to_loop():
    """One pass of the event loop and no time at all: what asyncio.sleep(0) does inside."""
    yield


def silent(s: dict | None) -> bool:
    return tvmod.silent(s)


def _values_only(s: dict) -> dict:
    """What may be written down: known words and numbers only."""
    p, m, v, i = s.get("power"), s.get("mute"), s.get("volume"), s.get("input")
    return {"power": p if p in _POWER else (None if p is None else "other"),
            "mute": m if m in _MUTE else (None if m is None else "other"),
            "volume": v if isinstance(v, (int, float)) and not isinstance(v, bool) else None,
            "input": i if isinstance(i, str) and re.fullmatch(r"[A-Z0-9]{1,16}", i) else (None if i is None else "other")}


class SoundGuard:
    def __init__(self, tv, settings: Callable[[str, object], object] | None = None, clock: Callable[[], float] = time.time,
                 sleep=asyncio.sleep, marker: Path | None = None):
        self.tv = tv
        self.settings = settings or tv.sound_settings
        self.clock = clock
        self.sleep = sleep
        self.marker = Path(marker) if marker is not None else MARKER
        self.m = SOUND_GUARD.new(clock=clock)
        self.misses = 0
        self.checks = 0
        self.incidents = 0
        self.last: dict = {}
        self.last_at: float | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._after: asyncio.Task | None = None
        self._by_file = False
        self._stopped = False
        if self.marker.exists():
            self._by_file = True
            self.m.reset("blocked", why="a sound incident from an earlier run is still open (delete its file to clear)")

    # --- settings ------------------------------------------------------------------------------------

    def _get(self, key: str, fallback):
        try:
            v = self.settings(f"sound_guard.{key}", fallback)
        except Exception:
            return fallback
        return fallback if v is None else v

    def enabled(self) -> bool:
        return bool(self._get("enabled", False))

    def required(self) -> bool:
        return self.tv.must_be_silent()

    def period_s(self) -> float:
        return max(1.0, float(self._get("check_s", 20.0)))

    # --- reading and silencing ------------------------------------------------------------------------

    async def read(self) -> dict:
        """Power, mute, volume and input, read back through the TV's SmartThings path. None = unread."""
        s = await self.tv.sound_state()
        self.last, self.last_at = _values_only(s), self.clock()
        return s

    silent = staticmethod(silent)

    async def silence(self, tries: int | None = None) -> dict:
        """Volume 0, then mute, then read back; up to `tries` times. Returns the last readback."""
        n = max(1, int(tries if tries is not None else self._get("silence_tries", 4)))
        s: dict = {}
        for _ in range(n):
            await self.tv.st_command("audioVolume", "setVolume", [0])
            await self.tv.st_command("audioMute", "mute")
            await self.sleep(SETTLE_S)
            s = await self.read()
            if silent(s):
                break
        return s

    # --- the watchdog step ----------------------------------------------------------------------------

    def blocked_why(self) -> str:
        """Why nothing may switch or press a key on the TV ("" = it may). Deleting the incident file clears it."""
        if not self.m.is_("blocked"):
            return ""
        if self._by_file and not self.marker.exists():
            self.clear("incident file deleted by hand")
            return ""
        return self.m.why or "sound incident"

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_why())

    async def check(self, who: str = "watch") -> str:
        """One step: "ok", "off", "unreadable", "incident" or "blocked"."""
        async with self._lock:
            return await self._check(who)

    async def _check(self, who: str) -> str:
        if self.blocked:
            return "blocked"
        s = await self.read()
        self.checks += 1
        if not self.required():
            self.misses = 0
            self.m.to("ok", why="silence not required (sound_guard.must_be_silent off): read only")
            return "ok"
        if s.get("power") == "off":
            self.misses = 0
            self.m.to("unchecked", why="the TV is off: the next switch silences it and reads back first")
            return "off"
        if s.get("mute") is None or s.get("volume") is None:
            self.misses += 1
            limit = max(1, int(self._get("unreadable_max", 3)))
            if self.misses >= limit:
                await self._incident(s, who, f"silence could not be read back {self.misses} times in a row")
                return "incident"
            self.m.to("unreadable", why=f"{who}: no mute or volume readback ({self.misses} of {limit})")
            return "unreadable"
        if not silent(s):
            await self._incident(s, who, "not silent")
            return "incident"
        self.misses = 0
        self.m.to("ok", why=f"{who}: muted and volume 0 read back")
        return "ok"

    async def incident(self, s: dict, who: str, why: str) -> None:
        async with self._lock:
            await self._incident(s, who, why)

    async def _incident(self, s: dict, who: str, why: str) -> None:
        """Block first (so only calming commands reach the TV), then power off, then write the record."""
        self.incidents += 1
        self.m.to("blocked", why=f"{who}: {why}")
        row = {"at": round(self.clock(), 3), "who": who, "why": why, **_values_only(s)}
        if str(self._get("on_incident", "off_tv")) == "off_tv":
            row["tv_off_sent"] = await self.tv.st_command("switch", "off")
        try:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            self.marker.write_text(json.dumps(row, indent=1) + "\n")
            self._by_file = True
        except OSError:
            log.exception("sound guard: could not write the incident file; blocked until clear() or a restart")
        log.error("SOUND INCIDENT (%s, %s): TV %s, switching blocked until cleared by hand. Read: %s",
                  who, why, "off" if row.get("tv_off_sent") else "NOT confirmed off" if "tv_off_sent" in row
                  else "left on (on_incident: block)", _values_only(s))

    def clear(self, why: str) -> None:
        """By hand only (Nick, after the cause is found): lift the block; the next switch re-checks first."""
        try:
            self.marker.unlink(missing_ok=True)
        except OSError:
            log.exception("sound guard: could not delete the incident file")
        self._by_file = False
        self.misses = 0
        if self.m.is_("blocked"):
            self.m.to("unchecked", why=f"cleared by hand: {why}")

    # --- hooks used by SamsungTV.set_input ------------------------------------------------------------

    async def before_switch(self) -> bool:
        """May this input switch go out? Refused while blocked. With silence required, the first switch of a
        session (or after the TV was off) silences the TV and must read silence back first."""
        if self.blocked:
            return False
        if not self.required() or not self.m.is_("unchecked"):
            return True
        async with self._lock:
            if self.blocked:
                return False
            await self.silence()
            r = await self._check("first switch")
        return r == "ok"

    async def after_switch(self) -> None:
        """Re-check after every switch, in the background (the flip's own timing is not slowed)."""
        if not self.required() or self.blocked:
            return
        t = self._after
        if t is not None and not t.done():
            return
        try:
            self._after = asyncio.get_running_loop().create_task(self._safe_check("after switch"))
        except RuntimeError:
            await self._safe_check("after switch")

    def after_check(self) -> asyncio.Task | None:
        """The latest after-switch check (None: none was started). [park.fast_steps] a caller that just switched compares
        it with the one before its switch: a new one was started by THAT switch, so its result is the read-back of it."""
        return self._after

    async def settle(self) -> None:
        """Wait for a pending after-switch check (tests, shutdown)."""
        if self._after is not None:
            await asyncio.gather(self._after, return_exceptions=True)

    async def _safe_check(self, who: str) -> str:
        try:
            return await self.check(who)
        except Exception:
            log.exception("sound guard: %s check failed", who)
            return "error"

    # --- the periodic check -----------------------------------------------------------------------------

    def ensure_running(self) -> None:
        """Start the periodic check (an asyncio task, no thread) once; only while sound_guard.enabled is on."""
        if self._stopped or not self.enabled() or self.blocked:
            return
        t = self._task
        if t is not None and not t.done():
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._watch())
        except RuntimeError:
            return

    async def _watch(self) -> None:
        """Every check_s until stop(), the flag goes off, or an incident blocks (the next switch or keepalive restarts
        it once the block is cleared by hand). Always yields to the event loop, even under a sleep that returns at
        once, so it can never spin a core or outlive a cancel."""
        while not self._stopped and self.enabled():
            await self.sleep(self.period_s())
            await _yield_to_loop()
            if self._stopped or self.blocked:
                return
            await self._safe_check("watch")
            if self.blocked:
                return

    def stop(self) -> None:
        self._stopped = True
        for t in (self._task, self._after):
            if t is not None and not t.done():
                t.cancel()

    def view(self) -> dict:
        return {"state": self.m.state, "why": self.m.why, "required": self.required(), "blocked": self.blocked_why(),
                "last": dict(self.last), "last_at": self.last_at, "misses": self.misses, "checks": self.checks,
                "incidents": self.incidents, "watching": bool(self._task is not None and not self._task.done())}
