"""Tuners: one class per streaming app. Add ESPN or Fubo later by writing one more.

Every tune is: send the deep link -> clear any prompts -> confirm video is actually
playing (the stick's own playback report) -> retry once -> report how long it took.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from redzone.tuners.adb import Stick

PROMPTS = {
    # on-screen text (lowercase)       -> keys that clear it
    "are you still watching": ["KEYCODE_DPAD_CENTER"],
    "still there": ["KEYCODE_DPAD_CENTER"],
    "continue watching": ["KEYCODE_DPAD_CENTER"],
    "who's watching": ["KEYCODE_DPAD_CENTER"],
    "choose a profile": ["KEYCODE_DPAD_CENTER"],
    "watch live": ["KEYCODE_DPAD_CENTER"],
    "watch from beginning": ["KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"],  # pick "live", not restart
}


@dataclass
class TuneResult:
    ok: bool
    target: str
    took: float
    steps: list[str] = field(default_factory=list)
    error: str = ""


class Tuner:
    app = ""
    role = ""
    confirm_timeout = 15.0

    def url(self, ref: str) -> str:
        raise NotImplementedError

    async def tune(self, stick: Stick, ref: str, target: str) -> TuneResult:
        t0 = time.monotonic()
        steps: list[str] = []
        pkg = stick.packages.get(self.role)
        if not pkg:
            return TuneResult(False, target, 0.0, steps, f"{self.app} app not found on {stick.name or stick.host}")
        await stick.key("KEYCODE_WAKEUP")
        for attempt in (1, 2):
            r = await stick.view(self.url(ref), pkg)
            steps.append(f"deep link #{attempt}: {'ok' if r.ok else r.out[:80]}")
            if await self._confirm(stick, pkg, steps):
                return TuneResult(True, target, time.monotonic() - t0, steps)
        return TuneResult(False, target, time.monotonic() - t0, steps, "video never started")

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str]) -> bool:
        deadline = time.monotonic() + self.confirm_timeout
        checked_text = False
        while time.monotonic() < deadline:
            pb = await stick.playback(pkg)
            if pb["state"] == "playing":
                steps.append(f"playing (confirmed by stick) at {time.monotonic():.1f}")
                return True
            if not checked_text and time.monotonic() > deadline - self.confirm_timeout + 4:
                checked_text = True
                text = (await stick.screen_text()).lower()
                for prompt, keys in PROMPTS.items():
                    if prompt in text:
                        steps.append(f"cleared prompt: {prompt}")
                        await stick.key(*keys)
                        break
            await asyncio.sleep(1.0)
        return False


class YouTubeTVTuner(Tuner):
    app = "youtubetv"
    role = "youtubetv"

    def url(self, ref: str) -> str:
        return f"https://tv.youtube.com/watch/{ref}"


class PrimeTuner(Tuner):
    app = "prime"
    role = "prime"
    confirm_timeout = 25.0

    def url(self, ref: str) -> str:
        return f"https://app.primevideo.com/detail?gti={ref}"

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str]) -> bool:
        # Prime opens a detail page; press through profile picker and "Watch Live".
        for _ in range(3):
            await asyncio.sleep(3.0)
            pb = await stick.playback(pkg)
            if pb["state"] == "playing":
                steps.append("playing")
                return True
            text = (await stick.screen_text()).lower()
            pressed = False
            for prompt, keys in PROMPTS.items():
                if prompt in text:
                    steps.append(f"pressed through: {prompt}")
                    await stick.key(*keys)
                    pressed = True
                    break
            if not pressed and re.search(r"\blive\b", text):
                steps.append("pressed the focused Live button")
                await stick.key("KEYCODE_DPAD_CENTER")
        return await super()._confirm(stick, pkg, steps)


TUNERS: dict[str, Tuner] = {"youtubetv": YouTubeTVTuner(), "prime": PrimeTuner()}
