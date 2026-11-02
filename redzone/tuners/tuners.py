"""Tuners: one class per streaming app. Add ESPN or Fubo later by writing one more.

Every tune is: already playing it? done -> wake the stick only if it's asleep -> send the deep
link -> ask the stick "playing yet?" 4 times a second until the NEW stream plays -> read the
screen for a prompt only if it's slow -> retry once -> report how long it took.

Why it's built this way (speed):
- The deep link is a plain `am start`, which returns as soon as the app has the link. `-W` made
  us wait for the app to finish opening before we even started checking. (Prime keeps `-W`: its
  next step reads the detail page, which must be fully drawn.)
- Each "playing yet?" check is one small query over the stick's always-open shell (~30-60 ms),
  so checking every 0.25 s is cheap and we see the video within ~0.25 s of it starting.
- Reading the screen (uiautomator, 1-3 s) only happens when the video is slow to start, and it
  runs alongside the checks instead of pausing them.
Guardrail: right after the link, the app can still report the OLD channel as playing. A report
only counts once it's provably the new stream (see NewStream).
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from redzone.tuners.adb import Stick, same_stream

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
LOADING = ("buffering", "connecting")


@dataclass
class TuneResult:
    ok: bool
    target: str
    took: float
    steps: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class NewStream:
    """Tells the stream we asked for from the one that was already playing.

    Without `am start -W` we check the instant the link is sent, so for a moment the app can still
    report the OLD channel as playing. Confirming on that would flip the TV to a stick that's still
    loading, or rewind the wrong stream for a replay. A "playing" report counts when:
      - the app wasn't playing before the link (so it can't be the old stream), or
      - we've seen it stop / buffer since the link (the switch happened), or
      - its playback clock jumped (a different stream), or
      - Android said the link never reached the app ("brought to the front"): nothing will change, or
      - it's kept playing for `settle_s` after the link without any of that (an app that switches
        without a visible gap, or a link to what was already on). Never worse than before.
    """
    before: dict
    settle_s: float = 2.5
    sent_at: float = field(default_factory=time.monotonic)
    gap: bool = False
    undelivered: bool = False      # Android didn't hand the link to the app
    report: dict | None = None     # the report that confirmed it

    def check(self, pb: dict, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if pb.get("state") != "playing":
            self.gap = True
            return False
        if (self.before.get("state") != "playing" or self.gap or self.undelivered
                or same_stream(self.before, pb) is False or now - self.sent_at >= self.settle_s):
            self.report = pb
            return True
        return False


class Tuner:
    app = ""
    role = ""
    confirm_timeout = 15.0   # per deep-link attempt
    poll_s = 0.25            # "playing yet?" this often while confirming
    prompt_grace_s = 3.0     # not playing this long after the link -> read the screen for a prompt
    loading_grace_s = 6.0    # ...but a stick that says it's buffering/connecting gets this long first
    reread_s = 1.5           # a blank screen read was too early (app still opening): look again this soon
    max_reads = 3            # screen reads per deep link, at most
    settle_s = 2.5           # see NewStream
    wait_for_launch = False  # `am start -W`: only for apps whose next step needs the page fully drawn

    def url(self, ref: str) -> str:
        raise NotImplementedError

    async def tune(self, stick: Stick, ref: str, target: str) -> TuneResult:
        t0 = time.monotonic()
        steps: list[str] = []
        pkg = stick.packages.get(self.role)
        if not pkg:
            return TuneResult(False, target, 0.0, steps, f"{self.app} app not found on {stick.name or stick.host}")
        before = await stick.playback(pkg)
        if stick.still_on(target, before):
            steps.append("already on it (the stick is still playing it, uninterrupted)")
            return TuneResult(True, target, time.monotonic() - t0, steps)
        if before["state"] == "playing":
            stick.mark_awake()   # playing video, so it's awake: skip asking
        if await stick.ensure_awake():
            steps.append("woke the stick")
        watch = NewStream(before, settle_s=self.settle_s)
        for attempt in (1, 2):
            watch.sent_at = time.monotonic()
            r = await stick.view(self.url(ref), pkg, wait=self.wait_for_launch)
            if _cant_open(r.out):
                why = r.out.strip().splitlines()[0][:120]
                steps.append(f"deep link #{attempt}: {why}")
                return TuneResult(False, target, time.monotonic() - t0, steps, f"{self.app} couldn't open the link")
            steps.append(f"deep link #{attempt}: {'ok' if r.ok else r.out[:80]}")
            watch.undelivered = "brought to the front" in r.out
            if await self._confirm(stick, pkg, steps, watch):
                stick.remember(target, watch.report or {})
                return TuneResult(True, target, time.monotonic() - t0, steps)
        return TuneResult(False, target, time.monotonic() - t0, steps, "video never started")

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream | None = None) -> bool:
        watch = watch or NewStream({"state": "none"})
        start = time.monotonic()
        screen: asyncio.Task | None = None
        reads, next_read = 0, None      # screen reads so far; when the next may start (None = after grace)
        try:
            while time.monotonic() - start < self.confirm_timeout:
                pb = await stick.playback(pkg)
                if watch.check(pb):
                    steps.append(f"playing (confirmed by stick) after {time.monotonic() - start:.1f}s")
                    return True
                now = time.monotonic()
                if screen is not None and screen.done():
                    text, screen = _text_of(screen), None
                    pressed = await self._press_prompt(stick, text, steps, "cleared prompt")
                    # Blank = read too early (the app was still opening): look again soon. Otherwise
                    # one read per link, as before -- pressing CENTER on a screen we misread is risky.
                    blank = not pressed and len(text.strip()) < 20 and reads < self.max_reads
                    next_read = now + self.reread_s if blank else float("inf")
                elif screen is None and now >= (start + self._grace(pb) if next_read is None else next_read):
                    screen = asyncio.create_task(stick.screen_text())   # slow: runs alongside the checks
                    reads += 1
                await asyncio.sleep(self.poll_s)
            return False
        finally:
            if screen is not None:
                screen.cancel()

    def _grace(self, pb: dict) -> float:
        return self.loading_grace_s if pb.get("state") in LOADING else self.prompt_grace_s

    async def _press_prompt(self, stick: Stick, text: str, steps: list[str], verb: str) -> bool:
        for prompt, keys in PROMPTS.items():
            if prompt in text:
                steps.append(f"{verb}: {prompt}")
                await stick.key(*keys)
                return True
        return False

    async def _watch_for(self, stick: Stick, pkg: str, watch: NewStream, seconds: float) -> bool:
        """Ask "playing yet?" every poll_s for up to `seconds` (instead of sleeping, then asking once)."""
        end = time.monotonic() + seconds
        while True:
            if watch.check(await stick.playback(pkg)):
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(self.poll_s)


def _text_of(task: asyncio.Task) -> str:
    if task.cancelled() or task.exception() is not None:
        return ""
    return (task.result() or "").lower()


def _cant_open(out: str) -> bool:
    """The app can't take this link at all (not installed, no such screen): retrying won't help."""
    low = out.lower()
    return "unable to resolve intent" in low or ("error:" in low and "does not exist" in low)


class YouTubeTVTuner(Tuner):
    app = "youtubetv"
    role = "youtubetv"

    def url(self, ref: str) -> str:
        return f"https://tv.youtube.com/watch/{ref}"


class PrimeTuner(Tuner):
    app = "prime"
    role = "prime"
    confirm_timeout = 25.0
    page_wait_s = 3.0        # the detail page needs a moment before its buttons can be read
    # The press-through reads the detail page, so it must be fully drawn first: keep `-W` here.
    # (The video can't start before the page is up anyway, so `-W` costs Prime nothing.)
    wait_for_launch = True

    def url(self, ref: str) -> str:
        return f"https://app.primevideo.com/detail?gti={ref}"

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream | None = None) -> bool:
        watch = watch or NewStream({"state": "none"})
        # Prime opens a detail page; press through profile picker and "Watch Live".
        for _ in range(3):
            if await self._watch_for(stick, pkg, watch, self.page_wait_s):
                steps.append("playing")
                return True
            text = (await stick.screen_text()).lower()
            if "regional restriction" in text or "not available in your location" in text or "blackout" in text:
                steps.append("blackout: this game isn't available in your area on Prime")
                return False
            if not await self._press_prompt(stick, text, steps, "pressed through") and re.search(r"\blive\b", text):
                steps.append("pressed the focused Live button")
                await stick.key("KEYCODE_DPAD_CENTER")
        return await super()._confirm(stick, pkg, steps, watch)


class EspnTuner(Tuner):
    """ESPN+ events in the ESPN app (experimental: verify on the stick first)."""
    app = "espn"
    role = "espn"
    confirm_timeout = 20.0

    def url(self, ref: str) -> str:
        return f"https://www.espn.com/watch/player/_/id/{ref}"


TUNERS: dict[str, Tuner] = {"youtubetv": YouTubeTVTuner(), "prime": PrimeTuner(), "espn": EspnTuner()}
