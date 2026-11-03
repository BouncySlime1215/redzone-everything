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
import functools
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from redzone.tuners.adb import Stick, playback_ready, same_stream
from redzone.tuners.profiles import ProfileSelectionRequired, preference, select_profile

log = logging.getLogger(__name__)

PROMPTS = {
    # on-screen text (lowercase)       -> keys that clear it
    "are you still watching": ["KEYCODE_DPAD_CENTER"],
    "still there": ["KEYCODE_DPAD_CENTER"],
    # Not "continue watching": that's a row header on Prime / YTTV / ESPN / Peacock home pages, and
    # OK there starts whatever tile has focus (an unrelated show that then reads as "playing").
    "watch live": ["KEYCODE_DPAD_CENTER"],
}
LOADING = ("buffering", "connecting")


@functools.lru_cache(maxsize=1)
def _yttv_table():
    from redzone.discovery.yttv import load_table
    return load_table()


def channel_matcher(target: str) -> Callable[[dict], bool | None] | None:
    """For a YouTube TV channel target ("youtubetv:espn"): a check of a playback report's
    metadata. YouTube TV puts the channel in the subtitle ("SportsCenter, ESPN, null"), and it
    changes to the new channel as soon as the new stream starts: a faster, stronger signal than
    waiting on the playback clock. The check returns True (names this channel and no other),
    False (names another channel), None (no channel name to go on). channel_matcher returns None
    for targets without a channel name (Sunday Ticket slots share a generic name; event links)."""
    app, _, rest = (target or "").partition(":")
    if app != "youtubetv" or not rest:
        return None
    try:
        from redzone.discovery.yttv import _scores
        table = _yttv_table()
    except Exception:   # a bad table must never break tuning: fall back to the playback-clock rules
        log.exception("yttv channel table unavailable")
        return None
    entry = table.channels.get(rest)
    if entry is None or entry.kind == "sunday_ticket":
        return None

    def match(report: dict) -> bool | None:
        text = (report or {}).get("subtitle") or ""
        if not text.strip():
            return None
        scores = _scores(text, table)
        top = max((sc for sc, _hit in scores.values()), default=0)
        if top <= 0:
            return None
        return [k for k, (sc, _hit) in scores.items() if sc == top] == [rest]

    return match


def _same_metadata(a: dict, b: dict) -> bool:
    ka = ((a or {}).get("title") or "", (a or {}).get("subtitle") or "")
    kb = ((b or {}).get("title") or "", (b or {}).get("subtitle") or "")
    return any(ka) and ka == kb


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _channel_changed(a: dict, b: dict) -> bool:
    sa, sb = _norm((a or {}).get("subtitle") or ""), _norm((b or {}).get("subtitle") or "")
    return bool(sa and sb and sa != sb)


class CutWindowClosed(RuntimeError):
    def __init__(self, steps):
        self.steps = list(steps)
        super().__init__("safe switching window closed; playback not confirmed")


class CutGuard:
    def __init__(self, callback):
        self.callback = callback
        self.changed = False

    def __call__(self):
        return self.callback is None or self.callback()


def require_cut(may_cut, steps, *, mutating=False) -> bool:
    """Check immediately before a visible mutation, after any slow readback."""
    if may_cut is not None and not may_cut():
        raise CutWindowClosed(steps)
    if mutating and isinstance(may_cut, CutGuard):
        may_cut.changed = True
    return True


@dataclass
class TuneResult:
    ok: bool
    target: str
    took: float
    steps: list[str] = field(default_factory=list)
    error: str = ""
    screen_changed: bool | None = None  # False: no visible command sent; True: sent; None: legacy unknown


@dataclass
class NewStream:
    """Tells the stream we asked for from the one that was already playing.

    Without `am start -W` we check the instant the link is sent, so for a moment the app can still
    report the OLD channel as playing. Confirming on that would flip the TV to a stick that's still
    loading, or rewind the wrong stream for a replay. A "playing" report counts when:
      - the app wasn't playing before the link (so it can't be the old stream), or
      - we've seen it stop / buffer since the link (the switch happened), or
      - its playback clock jumped (a different stream), or
      - (with `expect`) the app's metadata now names the channel we asked for and the old report
        didn't (YouTube TV: "SportsCenter, ESPN"). Confirms in ~1-2 s; the clock rules alone can
        take a whole attempt (15 s) on a live stream with no usable positions.
    An unchanged old playback report never confirms a new target, however long we wait.
    With `expect`, a report carrying exactly the old report's title and channel, where that
    channel is a different one, is the old stream and never confirms either.
    This detects a playback transition; actual game identity still needs device validation.
    """
    before: dict
    gap: bool = False
    report: dict | None = None     # the report that confirmed it
    expect: Callable[[dict], bool | None] | None = None
    by_name: bool = False          # confirmed by the channel name in the app's metadata
    channel_change: bool = False   # the app's subtitle is its channel (YouTube TV): a new one = new stream

    def check(self, pb: dict, now: float | None = None) -> bool:
        if not playback_ready(pb):
            # Missing session data can also mean the query failed. Only a reported
            # playback state is evidence of a transition.
            self.gap = self.gap or pb.get("state") in ("buffering", "connecting", "stopped", "paused")
            return False
        named = None
        if self.expect is not None:
            named = self.expect(pb)
            if named is True and self.expect(self.before) is not True:
                self.report, self.by_name = pb, True
                return True
            if named is False and _same_metadata(self.before, pb):
                return False   # still the old stream: same title, same (other) channel
        if self.channel_change and named is None and _channel_changed(self.before, pb):
            # YouTube TV's subtitle is the channel; a stream never changes channel by itself (its
            # title can, at the top of the hour, so only the channel counts). For targets with no
            # channel name to expect (event links, Sunday Ticket codes).
            self.report, self.by_name = pb, True
            return True
        continuity = same_stream(self.before, pb)
        if (not playback_ready(self.before) or continuity is False
                or (self.gap and continuity is None)):
            self.report = pb
            return True
        return False


class Tuner:
    app = ""
    role = ""
    preferred_profile = ""
    confirm_timeout = 15.0   # per deep-link attempt
    poll_s = 0.25            # "playing yet?" this often while confirming
    prompt_grace_s = 3.0     # not playing this long after the link -> read the screen for a prompt
    loading_grace_s = 6.0    # ...but a stick that says it's buffering/connecting gets this long first
    reread_s = 1.5           # a blank screen read was too early (app still opening): look again this soon
    max_reads = 3            # screen reads per deep link, at most
    wait_for_launch = False  # `am start -W`: only for apps whose next step needs the page fully drawn
    subtitle_is_channel = False  # the media session's subtitle names the channel (see NewStream)
    screenshot_prompts = False   # its prompts can be read and cleared from a screenshot (screen_prompts)

    def url(self, ref: str) -> str:
        raise NotImplementedError

    async def tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        started = time.monotonic()
        guard = CutGuard(may_cut)
        try:
            result = await self._tune(stick, ref, target, guard)
            result.screen_changed = guard.changed
            return result
        except CutWindowClosed as exc:
            return TuneResult(False, target, time.monotonic() - started, exc.steps, str(exc), guard.changed)
        except ProfileSelectionRequired as exc:
            return TuneResult(False, target, time.monotonic() - started, exc.steps, str(exc), guard.changed)

    async def _tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        t0 = time.monotonic()
        steps: list[str] = []
        pkg = stick.packages.get(self.role)
        if not pkg:
            return TuneResult(False, target, 0.0, steps, f"{self.app} app not found on {stick.name or stick.host}")
        before = await stick.playback(pkg)
        if stick.still_on(target, before):
            steps.append("already on it (the stick is still playing it, uninterrupted)")
            return TuneResult(True, target, time.monotonic() - t0, steps)
        if playback_ready(before):
            stick.mark_awake()   # playing video, so it's awake: skip asking
        require_cut(may_cut, steps)
        woke = await stick.ensure_awake(may_cut=lambda: require_cut(may_cut, steps, mutating=True)) if may_cut is not None \
            else await stick.ensure_awake()
        if woke:
            steps.append("woke the stick")
        for attempt in (1, 2):
            # A retry starts a new observation window. A buffering report from the
            # first attempt must not confirm an unchanged stream after the second.
            if attempt > 1:
                before = await stick.playback(pkg)
            watch = NewStream(before, expect=channel_matcher(target), channel_change=self.subtitle_is_channel)
            require_cut(may_cut, steps, mutating=True)
            r = await stick.view(self.url(ref), pkg, wait=self.wait_for_launch)
            if _cant_open(r.out):
                why = r.out.strip().splitlines()[0][:120]
                steps.append(f"deep link #{attempt}: {why}")
                return TuneResult(False, target, time.monotonic() - t0, steps, f"{self.app} couldn't open the link")
            steps.append(f"deep link #{attempt}: {'ok' if r.ok else r.out[:80]}")
            if not r.ok:
                if self.wait_for_launch and "timeout" in r.out.lower():
                    # `-W` gave up on a slow cold start (Prime on the 2018 stick): the link was sent and
                    # the page usually appears a moment later. Watch for it; never send it a second time.
                    if await self._confirm(stick, pkg, steps, watch, may_cut):
                        stick.remember(target, watch.report or {})
                        return TuneResult(True, target, time.monotonic() - t0, steps)
                return TuneResult(False, target, time.monotonic() - t0, steps,
                                  "deep link failed; playback is unconfirmed")
            if "brought to the front" in r.out.lower():
                # The app was in the background (e.g. the hidden stick after a HOME flip): resend
                # the link as a fresh task so it actually opens it.
                require_cut(may_cut, steps, mutating=True)
                r = await stick.view(self.url(ref), pkg, wait=self.wait_for_launch, fresh=True)
                steps.append("app was in the background: resent the link as a new task")
                if not r.ok:   # Android prints the same warning even when the fresh launch works
                    return TuneResult(False, target, time.monotonic() - t0, steps,
                                      "the app did not accept the game link")
            if await self._confirm(stick, pkg, steps, watch, may_cut):
                stick.remember(target, watch.report or {})
                return TuneResult(True, target, time.monotonic() - t0, steps)
        return TuneResult(False, target, time.monotonic() - t0, steps, "video never started")

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream | None = None,
                       may_cut=None) -> bool:
        watch = watch or NewStream({"state": "none"})
        start = time.monotonic()
        screen: asyncio.Task | None = None
        reads, next_read = 0, None      # screen reads so far; when the next may start (None = after grace)
        try:
            while time.monotonic() - start < self.confirm_timeout:
                pb = await stick.playback(pkg)
                if watch.check(pb):
                    how = f" ('{pb.get('subtitle')}' named in the app's metadata)" if watch.by_name else ""
                    steps.append(f"playing (confirmed by stick) after {time.monotonic() - start:.1f}s{how}")
                    return True
                now = time.monotonic()
                if screen is not None and screen.done():
                    text, screen = _text_of(screen), None
                    pressed = await self._press_prompt(stick, text, steps, "cleared prompt", may_cut)
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
                await asyncio.gather(screen, return_exceptions=True)

    def _grace(self, pb: dict) -> float:
        return self.loading_grace_s if pb.get("state") in LOADING else self.prompt_grace_s

    async def _press_prompt(self, stick: Stick, text: str, steps: list[str], verb: str, may_cut=None) -> bool:
        from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
        text = normalize_text(text)
        if self.screenshot_prompts and getattr(stick, "screen_source", "") == "ocr":
            # The app draws its own UI (YouTube TV): uiautomator can't see the picker's focus, so
            # read and drive it from a fresh screenshot instead (screen_prompts has the rules).
            from redzone.tuners import screen_prompts
            cleared = await screen_prompts.clear_prompt(
                stick, preference(stick, self.app, self.preferred_profile), steps,
                lambda: require_cut(may_cut, steps, mutating=True), package=stick.packages.get(self.role, ""))
            return cleared is not None
        verdict = classify_screen(text)
        if is_purchase_screen(text) or (verdict and verdict.stops):
            steps.append("stopped at an account, payment, or unavailable-content screen")
            return False
        selected = await select_profile(stick, text, preference(stick, self.app, self.preferred_profile),
                                        steps, lambda: require_cut(may_cut, steps, mutating=True))
        if selected is not None:
            return selected
        for prompt, keys in PROMPTS.items():
            if prompt in text:
                require_cut(may_cut, steps, mutating=True)
                steps.append(f"{verb}: {prompt}")
                await stick.key(*keys)
                return True
        return False

    async def clear_screen_prompt(self, stick: Stick, pkg: str, steps: list[str], may_cut=None) -> str | None:
        """For the visible-stick watchdog: the app is in front but not playing. Clear a profile
        picker or "still watching?" read from a screenshot. Returns "profile" / "still_watching",
        or None (nothing cleared, not this kind of app, or evidence too unclear to press)."""
        if not self.screenshot_prompts:
            return None
        from redzone.tuners import screen_prompts
        try:
            return await screen_prompts.clear_prompt(
                stick, preference(stick, self.app, self.preferred_profile), steps,
                lambda: require_cut(may_cut, steps, mutating=True), package=pkg)
        except (ProfileSelectionRequired, CutWindowClosed):
            return None

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
    subtitle_is_channel = True   # "SportsCenter, ESPN, null" (seen on the stick, Nov 2)
    screenshot_prompts = True    # Cobalt: invisible to uiautomator, visible in screencap (Nov 2)

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

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream | None = None,
                       may_cut=None) -> bool:
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
            if (await stick.playback(pkg)).get("state") in LOADING:
                # The stream is already loading (pre-roll, promo slate): a CENTER now pauses the player.
                break
            if not await self._press_prompt(stick, text, steps, "pressed through", may_cut):
                link = max((i for i, s in enumerate(steps) if s.startswith("deep link #")), default=-1)
                done = [s for s in steps[link + 1:] if s.startswith("pressed: watch")]
                live_pressed = any(s.endswith(": watch live") for s in steps[link + 1:])
                # A live event's detail page opens with its play button focused, but it says just
                # "Watch" on Monumental+/League Pass, and that opens a "Watch Live / Resume" panel with
                # Watch Live focused (Nov 2): two presses. uiautomator often can't read either screen,
                # so the second press only happens while the detail page (not the player) is in front;
                # in the player a CENTER would pause it.
                on_page = await self._on_detail_page(stick)
                if on_page:
                    self._saw_page = getattr(self, "_saw_page", {})
                    self._saw_page[stick.host] = True
                if len(done) < 2 and not (live_pressed and not done) and on_page:
                    if may_cut is not None and not may_cut():
                        break
                    await stick.key("KEYCODE_DPAD_CENTER")
                    self._pressed = getattr(self, "_pressed", {})
                    self._pressed[stick.host] = True
                    steps.append("pressed: watch (focused button)" if not done else "pressed: watch live (panel)")
        return await super()._confirm(stick, pkg, steps, watch, may_cut)

    _PLAYERS = ("IgnitionActivity", "PlaybackActivity")

    async def _player_token(self, stick: Stick) -> str | None:
        """Prime's player screen in front, as its ActivityRecord id; None when it isn't."""
        r = await stick.shell("dumpsys activity activities | grep -m1 mResumedActivity", timeout=5)
        out = r.out if r.ok else ""
        if "com.amazon.firebat" not in out or not any(p in out for p in self._PLAYERS):
            return None
        m = re.search(r"ActivityRecord\{(\w+)", out)
        return m.group(1) if m else "player"

    async def tune(self, stick: Stick, ref: str, target: str, **kw):
        # Prime reports every live game as one "PrimeVideo" session on a wall-clock position, and
        # the game already playing keeps running behind the detail page (Nov 2): "playing" alone
        # would confirm the OLD game. A new game is a new player screen (a new ActivityRecord).
        self._before_player = getattr(self, "_before_player", {})
        self._before_player[stick.host] = await self._player_token(stick)
        self._pressed = getattr(self, "_pressed", {})
        self._pressed[stick.host] = False
        self._saw_page = getattr(self, "_saw_page", {})
        self._saw_page[stick.host] = False
        self._link_at = getattr(self, "_link_at", {})
        self._link_at[stick.host] = time.monotonic()
        return await super().tune(stick, ref, target, **kw)

    async def _watch_for(self, stick: Stick, pkg: str, watch: NewStream, seconds: float) -> bool:
        end = time.monotonic() + seconds
        before = getattr(self, "_before_player", {}).get(stick.host)
        while True:
            pb = await stick.playback(pkg)
            if playback_ready(pb):
                token = await self._player_token(stick)
                pressed = getattr(self, "_pressed", {}).get(stick.host)
                # A new player screen, or (when the page opened inside the old player) our press
                # followed by a screen that no longer shows the page.
                if token and (token != before or (pressed and not await self._on_detail_page(stick))):
                    watch.report = pb
                    return True
                # A link to the game the player is already showing is ignored by Prime: no page ever
                # comes up and the same player keeps playing it (seen after a service restart, Nov 2).
                waited = time.monotonic() - getattr(self, "_link_at", {}).get(stick.host, time.monotonic())
                if (token and token == before and not pressed and waited >= 6.0
                        and not getattr(self, "_saw_page", {}).get(stick.host)
                        and not await self._on_detail_page(stick)):
                    watch.report = pb
                    return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(self.poll_s)

    _DETAIL_WORDS = ("watchlist", "watch live", "more purchase options", "included with", "resume")

    async def _on_detail_page(self, stick: Stick) -> bool:
        """Prime's detail page or its Watch Live / Resume panel is in front, not a playing game.
        The page can live inside the player activity (a League Pass page opened over the last game,
        Nov 2) and uiautomator reads it as blank, so a player activity is checked with a screenshot."""
        r = await stick.shell("dumpsys activity activities | grep -m1 mResumedActivity", timeout=5)
        if not r.ok or "com.amazon.firebat" not in r.out:
            return False
        if not any(p in r.out for p in self._PLAYERS):
            return True
        text = (await stick.ocr_text()).lower()
        return any(w in text for w in self._DETAIL_WORDS)

    async def _press_prompt(self, stick: Stick, text: str, steps: list[str], verb: str, may_cut=None) -> bool:
        # "Watch Live" gets one press per deep link. A later read still showing it (the detail page under a
        # loading player) must not press again: in the player that's pause, on the page it can hit
        # "Watch from beginning" if focus moved.
        link = max((i for i, s in enumerate(steps) if s.startswith("deep link #")), default=-1)
        if any(s.endswith(": watch live") for s in steps[link + 1:]):
            from redzone.discovery.providers import normalize_text
            text = normalize_text(text).replace("watch live", "")
        return await super()._press_prompt(stick, text, steps, verb, may_cut)


class EspnTuner(Tuner):
    """ESPN+ events in the ESPN app (experimental: verify on the stick first)."""
    app = "espn"
    role = "espn"
    confirm_timeout = 20.0

    def url(self, ref: str) -> str:
        return f"https://www.espn.com/watch/player/_/id/{ref}"


TUNERS: dict[str, Tuner] = {"youtubetv": YouTubeTVTuner(), "prime": PrimeTuner(), "espn": EspnTuner()}
