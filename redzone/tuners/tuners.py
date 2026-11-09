"""Tuners: one class per streaming app. Add ESPN or Fubo later by writing one more.

Why: one class per streaming app, so a new app is one more class and every tune checks the same 'is the new stream playing?' truth (docstring).

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

Why (flags): tuners.prime_fast_confirm takes OCR out of Prime's "playing yet?" loops and allows one guarded read per
press; tuners.espn_link_form tries the link form reported to work, since ours has failed 19 of 19. Both ship OFF.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from redzone.fsm import MachineMap
from redzone.machines.control import PRIME_TUNE
from redzone.tuners import prime_multiview as pmv
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
# A row on every Prime detail page, including games the account already has. OCR can split it across cells.
_PRIME_ROW = re.compile(r"more purchase(?:\s*\|?\s*options)?")


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


class TimedSteps(list):
    """A tune's step list that also remembers when each step happened: .marks = [{at_s, step}]. [P1-ROOTCAUSE-07]"""

    def __init__(self):
        super().__init__()
        self.t0 = time.monotonic()
        self.marks: list[dict] = []
        self.warm: bool | None = None

    def mark(self, step: str) -> None:
        """A timing note only: it doesn't join the steps that error messages and the tuner's own checks read."""
        self.marks.append({"at_s": round(time.monotonic() - self.t0, 2), "step": str(step)[:100]})

    def append(self, step) -> None:
        super().append(step)
        self.mark(step)


def _mark(steps, step: str) -> None:
    if isinstance(steps, TimedSteps):
        steps.mark(step)


@dataclass
class TuneResult:
    ok: bool
    target: str
    took: float
    steps: list[str] = field(default_factory=list)
    error: str = ""
    screen_changed: bool | None = None  # False: no visible command sent; True: sent; None: legacy unknown
    path: str = ""                      # "tile": a Prime multiview tile switch, not a tune
    warm: bool | None = None            # the app was already running before the link; None: can't tell
    kept: bool = False                  # nothing reloaded (no link sent, or Prime ignored it): its position is unchanged


@dataclass
class NewStream:
    """Tells the stream we asked for from the one that was already playing.

    Without `am start -W` we check the instant the link is sent, so for a moment the app can still
    report the OLD channel as playing. Confirming on that would flip the TV to a stick that's still
    loading, or rewind the wrong stream for a replay. A "playing" report counts when:
      - the app wasn't playing before the link, by a read that worked (so it can't be the old stream), or
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
        # A failed read before the link says nothing about what was playing: the old stream can still report "playing"
        # now. Only a read that came back and found no stream is the fast path.
        nothing_before = not playback_ready(self.before) and self.before.get("read_ok") is not False
        if (nothing_before or continuity is False
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
    warm_probe = False           # ask the stick whether the app is running before the link (one more adb call)

    def url(self, ref: str) -> str:
        raise NotImplementedError

    async def tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        started = time.monotonic()
        guard = CutGuard(may_cut)
        try:
            result = await self._tune(stick, ref, target, guard)
            result.screen_changed = guard.changed
            result.warm = getattr(result.steps, "warm", None)
            return result
        except CutWindowClosed as exc:
            return TuneResult(False, target, time.monotonic() - started, exc.steps, str(exc), guard.changed)
        except ProfileSelectionRequired as exc:
            return TuneResult(False, target, time.monotonic() - started, exc.steps, str(exc), guard.changed)

    async def _tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        t0 = time.monotonic()
        steps: list[str] = TimedSteps()
        pkg = stick.packages.get(self.role)
        if not pkg:
            return TuneResult(False, target, 0.0, steps, f"{self.app} app not found on {stick.name or stick.host}")
        before = await stick.playback(pkg)
        if stick.still_on(target, before):
            steps.append("already on it (the stick is still playing it, uninterrupted)")
            return TuneResult(True, target, time.monotonic() - t0, steps, kept=True)
        if playback_ready(before):
            stick.mark_awake()   # playing video, so it's awake: skip asking
        require_cut(may_cut, steps)
        woke = await stick.ensure_awake(may_cut=lambda: require_cut(may_cut, steps, mutating=True)) if may_cut is not None \
            else await stick.ensure_awake()
        if woke:
            steps.append("woke the stick")
        steps.warm = await self._was_warm(stick, pkg, before)
        for attempt in (1, 2):
            # A retry starts a new observation window. A buffering report from the
            # first attempt must not confirm an unchanged stream after the second.
            if attempt > 1:
                before = await stick.playback(pkg)
            watch = NewStream(before, expect=channel_matcher(target), channel_change=self.subtitle_is_channel)
            require_cut(may_cut, steps, mutating=True)
            r = await stick.view(self.url(ref), pkg, wait=self.wait_for_launch)
            if attempt == 1:
                if steps.warm is None and "brought to the front" in r.out.lower():
                    steps.warm = True
                _note_warm(steps, target)
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

    async def _was_warm(self, stick: Stick, pkg: str, before: dict) -> bool | None:
        """Was the app running before the link? A session in the report we already have says yes for free; only
        warm_probe spends a call to ask about an app with no session. None: can't tell."""
        if before.get("state") not in (None, "none") and before.get("pkg") == pkg:
            return True
        if not self.warm_probe:
            return None
        r = await stick.shell(f"pidof {pkg}", timeout=3)
        if r.ok or not r.out.strip():   # pidof exits non-zero, with no output, when the app isn't running
            return bool(r.out.strip())
        return None

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


def _note_warm(steps, target: str) -> None:
    """One timing note per tune, in the row the switch log already keeps."""
    warm = steps.warm
    word = "unknown" if warm is None else "yes" if warm else "no"
    _mark(steps, f"warm: {word}")
    log.info("tune %s: warm=%s", target, word)


def apply_flags(cfg, tuners: dict[str, "Tuner"] | None = None) -> None:
    """Copy the tuners.* flags onto the tuner objects. Controller calls this; until it does they stay OFF."""
    table = TUNERS if tuners is None else tuners
    fast, link = bool(cfg.get("tuners.prime_fast_confirm", False)), bool(cfg.get("tuners.espn_link_form", False))
    for wrapped in table.values():
        tuner = getattr(wrapped, "inner", wrapped)   # providers.DiagnosingTuner wraps the real one in the running app
        tuner.warm_probe = fast or link
        if isinstance(tuner, PrimeTuner):
            tuner.fast_confirm = fast
        if isinstance(tuner, EspnTuner):
            tuner.link_form = link


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

    async def tune(self, stick: Stick, ref: str, target: str, may_cut=None) -> TuneResult:
        if ref.startswith("search:"):
            return await self._tune_by_search(stick, ref.split(":", 1)[1], target, may_cut)
        return await super().tune(stick, ref, target, may_cut)

    async def _tune_by_search(self, stick: Stick, key: str, target: str, may_cut=None) -> TuneResult:
        """A channel with no watch code (NBCSN, NHL Network, the local NBC): in-app search, select its
        tile (or its network page -> Live -> the live tile), confirmed by the media session naming
        the channel. Every key goes through the cut window like a deep link would."""
        import tempfile
        from redzone.screen_ocr import OCRResult, recognize
        from redzone.tuners.prime_search import CapturedScreen
        from redzone.tuners.yttv_search import hunt_channel
        t0 = time.monotonic()
        steps: list[str] = []
        pkg = stick.packages.get(self.role)
        if not pkg:
            return TuneResult(False, target, 0.0, steps, f"{self.app} app not found on {stick.name or stick.host}")
        before = await stick.playback(pkg)
        if stick.still_on(target, before):
            steps.append("already on it (the stick is still playing it, uninterrupted)")
            return TuneResult(True, target, time.monotonic() - t0, steps, kept=True)
        try:
            require_cut(may_cut, steps, mutating=True)
        except CutWindowClosed as exc:
            return TuneResult(False, target, time.monotonic() - t0, exc.steps, str(exc))
        seq = [0]

        async def observe():
            seq[0] += 1
            png = await stick.screencap(timeout=15)
            if not png:   # [P1-ROOTCAUSE-08] say which half failed
                return CapturedScreen(OCRResult("error", error="screencap returned nothing"), seq[0], time.monotonic())
            with tempfile.TemporaryDirectory(prefix="rz-yts-") as d:
                path = f"{d}/s.png"
                with open(path, "wb") as f:
                    f.write(png or b"")
                result = await asyncio.to_thread(recognize, path, timeout=8.0)
            return CapturedScreen(result, seq[0], time.monotonic())

        from redzone.channels import LOCAL_SEARCH_QUERY, SEARCH_QUERY
        local = key.startswith("local:")
        key = key.split(":", 1)[1] if local else key
        query = LOCAL_SEARCH_QUERY.get(key, "") if local else SEARCH_QUERY.get(key, "")
        hunt = await hunt_channel(stick, observe, key=key, table=_yttv_table(), query=query,
                                  profile=getattr(self, "preferred_profile", "") or "", collect_codes=False,
                                  channel_page=local,
                                  before_key=lambda: require_cut(may_cut, steps, mutating=True))
        steps += [f"{x['action']}: {x['detail'][:60]}" for x in hunt.steps[-6:]]
        if hunt.status != "confirmed":
            return TuneResult(False, target, time.monotonic() - t0, steps, f"search tune: {hunt.reason}")
        pb = await stick.playback(pkg)
        if playback_ready(pb):
            stick.remember(target, pb)
        steps.append(f"playing by search: {hunt.description}")
        return TuneResult(True, target, time.monotonic() - t0, steps)


class PrimeTuner(Tuner):
    app = "prime"
    role = "prime"
    confirm_timeout = 25.0
    page_wait_s = 3.0        # the detail page needs a moment before its buttons can be read
    # The press-through reads the detail page, so it must be fully drawn first: keep `-W` here.
    # (The video can't start before the page is up anyway, so `-W` costs Prime nothing.)
    wait_for_launch = True
    fast_confirm = False     # tuners.prime_fast_confirm

    def __init__(self) -> None:
        # Per stick (host): what the confirm loop saw since the deep link (tuners.prime_tune: linked -> page ->
        # pressed), the player screen before the link and when the link went out.
        self.tune_m = MachineMap(PRIME_TUNE, clock=time.monotonic)
        self._before_player: dict[str, str | None] = {}
        self._link_at: dict[str, float] = {}
        self._page_ocr: dict[str, str] = {}      # what _on_detail_page's OCR last read, for the purchase guard
        self._ignored: set[str] = set()          # hosts whose link Prime ignored this tune: the same stream played on
        self._gap_after_press: dict[str, bool] = {}   # fast confirm: the player stopped or loaded after our press

    def _saw_page(self, host: str) -> bool:
        return self.tune_m.state(host) in ("page", "pressed")

    def _pressed(self, host: str) -> bool:
        return self.tune_m.state(host) == "pressed"

    def url(self, ref: str) -> str:
        return f"https://app.primevideo.com/detail?gti={ref}"

    async def _confirm(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream | None = None,
                       may_cut=None) -> bool:
        watch = watch or NewStream({"state": "none"})
        if self.fast_confirm:
            return await self._confirm_fast(stick, pkg, steps, watch, may_cut)
        # Prime opens a detail page; press through profile picker and "Watch Live".
        for _ in range(3):
            if await self._watch_for(stick, pkg, watch, self.page_wait_s):
                steps.append("playing")
                return True
            _mark(steps, "waited for the player")
            if (await stick.playback(pkg)).get("state") in LOADING:
                # [P1-ROOTCAUSE-07] loading already: the screen read (uiautomator, then up to 16 s of OCR) can't help
                _mark(steps, "loading: screen read skipped")
                break
            text = (await stick.screen_text()).lower()
            _mark(steps, f"screen read ({getattr(stick, 'screen_source', '?')})")
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
                self._page_ocr.pop(stick.host, None)
                on_page = await self._on_detail_page(stick)
                if on_page and not self._saw_page(stick.host):
                    self.tune_m[stick.host].to("page", why="detail page in front")
                # Real money: the press below is the only one in this loop with no guard of its own. Every word
                # this round already read counts; no new read is made here.
                buying = self._buying(f"{text} {self._page_ocr.get(stick.host, '')}")
                if buying:
                    _mark(steps, "purchase screen: no press")
                if len(done) < 2 and not (live_pressed and not done) and on_page and not buying:
                    if may_cut is not None and not may_cut():
                        break
                    await stick.key("KEYCODE_DPAD_CENTER")
                    self.tune_m[stick.host].to("pressed", why="pressed watch" if not done else "pressed watch live")
                    steps.append("pressed: watch (focused button)" if not done else "pressed: watch live (panel)")
        return await super()._confirm(stick, pkg, steps, watch, may_cut)

    @staticmethod
    def _buying(text: str) -> bool:
        """Could OK on this screen spend money? Prime's "More purchase options" row alone is not a yes."""
        from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
        clean = _PRIME_ROW.sub(" ", normalize_text(text))
        verdict = classify_screen(clean)
        return is_purchase_screen(clean) or bool(verdict and verdict.kind == "upsell")

    async def _confirm_fast(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream, may_cut=None) -> bool:
        """tuners.prime_fast_confirm. OCR only in the one read that decides a press, never while polling. A read
        that leads to no press is the last read for this link. A press needs the word "watch" on a screen that
        passed the purchase guard, so an unreadable screen is never pressed."""
        from redzone.discovery.providers import classify_screen, normalize_text
        host, reads, presses = stick.host, 0, 0
        self._gap_after_press[host] = False
        for _ in range(3):
            if await self._watch_for(stick, pkg, watch, self.page_wait_s):
                steps.append("playing")
                return True
            link = max((i for i, s in enumerate(steps) if s.startswith("deep link #")), default=-1)
            done = [s for s in steps[link + 1:] if s.startswith("pressed: watch")]
            live_pressed = any(s.endswith(": watch live") for s in steps[link + 1:])
            if len(done) >= 2 or live_pressed or reads > presses:
                break
            if (await stick.playback(pkg)).get("state") in LOADING:
                _mark(steps, "loading: screen read skipped")
                break
            text = (await stick.ocr_text()).lower()
            reads += 1
            _mark(steps, "screen read (ocr, one per press)")
            if "regional restriction" in text or "not available in your location" in text or "blackout" in text:
                steps.append("blackout: this game isn't available in your area on Prime")
                return False
            pb = await stick.playback(pkg)
            if pb.get("state") in LOADING:
                break
            clean = _PRIME_ROW.sub(" ", normalize_text(text))
            verdict = classify_screen(clean)
            if self._buying(text) or (verdict and verdict.stops):
                steps.append("stopped at an account, payment, or unavailable-content screen")
                return False
            if await self._press_prompt(stick, clean, steps, "pressed through", may_cut):
                presses += 1
                self._gap_after_press[host] = False
                continue
            on_page = await self._on_detail_page(stick, text)
            if on_page:
                if not self._saw_page(host):
                    self.tune_m[host].to("page", why="detail page in front")
                if "watch" not in text:
                    continue
                if may_cut is not None and not may_cut():
                    break
                await stick.key("KEYCODE_DPAD_CENTER")
                presses += 1
                self._gap_after_press[host] = False
                self.tune_m[host].to("pressed", why="pressed watch" if not done else "pressed watch live")
                steps.append("pressed: watch (focused button)" if not done else "pressed: watch live (panel)")
            elif await self._read_shows_game(stick, pb, text, watch):
                steps.append("playing")
                return True
        return await self._poll_until_playing(stick, pkg, steps, watch)

    async def _read_shows_game(self, stick: Stick, pb: dict, text: str, watch: NewStream) -> bool:
        """The round's read found no page. It takes the place of the OCR _watch_for runs with the flag off: our press
        led to a screen without the page, or Prime ignored a link to the game it was already showing. A blank or
        half-read screen ("watch" still in it) proves nothing, so it never confirms."""
        if not text.strip() or "watch" in text or not playback_ready(pb):
            return False
        host, token = stick.host, await self._player_token(stick)
        waited = time.monotonic() - self._link_at.get(host, time.monotonic())
        ignored = token == self._before_player.get(host) and waited >= 6.0 and not self._saw_page(host)
        if token and (self._pressed(host) or ignored):
            watch.report = pb
            if not self._pressed(host):
                self._ignored.add(host)
            return True
        return False

    async def _poll_until_playing(self, stick: Stick, pkg: str, steps: list[str], watch: NewStream) -> bool:
        """The last wait with the flag on: the same "playing yet?" check as Tuner._confirm, with no screen reads."""
        start = time.monotonic()
        while time.monotonic() - start < self.confirm_timeout:
            if watch.check(await stick.playback(pkg)):
                steps.append(f"playing (confirmed by stick) after {time.monotonic() - start:.1f}s")
                return True
            await asyncio.sleep(self.poll_s)
        return False

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
        self._before_player[stick.host] = await self._player_token(stick)
        m = self.tune_m[stick.host]
        if not m.to("linked", why=f"deep link {target}"):
            m.reset("linked", why=f"deep link {target}")   # a fresh link restarts the age
        self._link_at[stick.host] = time.monotonic()
        self._ignored.discard(stick.host)
        res = await super().tune(stick, ref, target, **kw)
        if res.ok and stick.host in self._ignored:
            res.kept = True   # Prime ignored the link: the stream never reloaded, so it is as far behind live as it was
        return res

    async def _watch_for(self, stick: Stick, pkg: str, watch: NewStream, seconds: float) -> bool:
        end = time.monotonic() + seconds
        before = self._before_player.get(stick.host)
        while True:
            pb = await stick.playback(pkg)
            if self.fast_confirm:
                if await self._fast_player_check(stick, pb, before, watch):
                    return True
            elif playback_ready(pb):
                token = await self._player_token(stick)
                pressed = self._pressed(stick.host)
                # A new player screen, or (when the page opened inside the old player) our press
                # followed by a screen that no longer shows the page.
                if token and (token != before or (pressed and not await self._on_detail_page(stick))):
                    watch.report = pb
                    return True
                # A link to the game the player is already showing is ignored by Prime: no page ever
                # comes up and the same player keeps playing it (seen after a service restart, Nov 2).
                waited = time.monotonic() - self._link_at.get(stick.host, time.monotonic())
                if (token and token == before and not pressed and waited >= 6.0
                        and not self._saw_page(stick.host)
                        and not await self._on_detail_page(stick)):
                    watch.report = pb
                    self._ignored.add(stick.host)
                    return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(self.poll_s)

    async def _fast_player_check(self, stick: Stick, pb: dict, before: str | None, watch: NewStream) -> bool:
        """_watch_for with the flag on: no OCR. A new player screen confirms; after our press, so does the same
        player coming back from a stop or a load. Anything else waits for the round's one read."""
        host = stick.host
        if not playback_ready(pb):
            if self._pressed(host) and pb.get("state") in ("buffering", "connecting", "stopped"):
                self._gap_after_press[host] = True
            return False
        token = await self._player_token(stick)
        if token and (token != before or (self._pressed(host) and self._gap_after_press.get(host))):
            watch.report = pb
            return True
        return False

    _DETAIL_WORDS = ("watchlist", "watch live", "more purchase options", "included with", "resume")

    async def _on_detail_page(self, stick: Stick, text: str | None = None) -> bool:
        """Prime's detail page or its Watch Live / Resume panel is in front, not a playing game.
        The page can live inside the player activity (a League Pass page opened over the last game,
        Nov 2) and uiautomator reads it as blank, so a player activity is checked with a screenshot.
        `text`: a screen already read this round (no second OCR)."""
        r = await stick.shell("dumpsys activity activities | grep -m1 mResumedActivity", timeout=5)
        if not r.ok or "com.amazon.firebat" not in r.out:
            return False
        if not any(p in r.out for p in self._PLAYERS):
            return True
        if text is None:
            text = (await stick.ocr_text()).lower()
            self._page_ocr[stick.host] = text
        return any(w in text for w in self._DETAIL_WORDS)

    # OCR splits rows ("More purchase | options") and the 4K Plus page shows Watchlist as a bare "+".
    _PAGE_WORDS = ("watchlist", "more purchase", "included with")
    PANEL_WAIT_S = 3.0       # after "Watch": the Watch Live / Resume panel is up within ~1 s (Nov 2)
    PANEL_BLIND_S = 1.5      # ...so "Watch Live" is pressed this long after "Watch" if nothing started

    async def resume_from_page(self, stick: Stick, pkg: str, steps: list[str]) -> bool:
        """After a HOME flip, REORDER_TO_FRONT brings Prime back to the game's DETAIL PAGE, not the
        player: its session is gone ("none") and it never comes back by itself (40 s probe, Nov 2,
        both sticks). A fresh deep link costs ~45 s on screen; pressing through costs ~5 s:
        the page opens with "Watch" focused -> the panel opens with "Watch Live" focused -> live.
        Presses only while nothing is playing or loading (CENTER in a player pauses it), and only
        on a screen read as the page or the panel. True = pressed through (caller confirms play)."""
        if not await self._idle(stick, pkg):
            return False
        text = (await stick.ocr_text()).lower()
        if self._buying(text):
            steps.append("stopped at an account, payment, or unavailable-content screen")
            return False
        if "watch live" in text:
            if not await self._idle(stick, pkg):
                return False
            await stick.key("KEYCODE_DPAD_CENTER")
            steps.append("pressed: watch live (panel after the flip)")
            return True
        if not (any(w in text for w in self._PAGE_WORDS) and "watch" in text):
            return False
        if not await self._idle(stick, pkg):
            return False
        await stick.key("KEYCODE_DPAD_CENTER")
        steps.append("pressed: watch (detail page after the flip)")
        # The panel opens with "Watch Live" focused within ~1 s. A screen read can take seconds on a
        # busy Mac (the soak saw the panel missed by OCR and a ~45 s re-link follow), so it's not
        # required: once PANEL_BLIND_S has passed with nothing playing or loading, the second press
        # goes anyway. With no session there is no stream to pause; on the page itself a CENTER is
        # "Watch" again, which just opens the panel.
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.PANEL_WAIT_S:
            await asyncio.sleep(0.3)
            pb = await stick.playback(pkg)
            if pb.get("state") in LOADING or playback_ready(pb):
                return True   # this page went straight to the stream
            if time.monotonic() - t0 >= self.PANEL_BLIND_S:
                break
        if not await self._idle(stick, pkg):
            return True
        await stick.key("KEYCODE_DPAD_CENTER")
        steps.append("pressed: watch live (panel)")
        return True

    @staticmethod
    async def _idle(stick: Stick, pkg: str) -> bool:
        """No Prime session playing, paused or loading: a CENTER here can't pause a stream."""
        pb = await stick.playback(pkg)
        return pb.get("read_ok") is not False and pb.get("state") in ("none", "stopped", None)

    async def _press_prompt(self, stick: Stick, text: str, steps: list[str], verb: str, may_cut=None) -> bool:
        # "Watch Live" gets one press per deep link. A later read still showing it (the detail page under a
        # loading player) must not press again: in the player that's pause, on the page it can hit
        # "Watch from beginning" if focus moved.
        link = max((i for i, s in enumerate(steps) if s.startswith("deep link #")), default=-1)
        if any(s.endswith(": watch live") for s in steps[link + 1:]):
            from redzone.discovery.providers import normalize_text
            text = normalize_text(text).replace("watch live", "")
        return await super()._press_prompt(stick, text, steps, verb, may_cut)


class PrimeMultiviewTuner(PrimeTuner):
    """Prime with its NBA multiview (redzone/tuners/prime_multiview.py). OFF (`enabled` False) it is
    exactly PrimeTuner. ON, it knows each stick's tile order, and a tune to a game that is a tile
    there makes that tile main with the probe's key path (~1 s) instead of a deep link (~32 s).
    A game that isn't a tile, or a tile switch that can't be verified, falls back to a normal tune
    (which leaves the multiview, so that stick's session is forgotten)."""

    def __init__(self, keys: "pmv.KeyMap | None" = None, verify_s: float = 3.0, poll_s: float = 0.3):
        super().__init__()
        self.enabled = False
        self.keys = keys
        self.sessions: dict[str, pmv.Session] = {}
        self.names: Callable[[str], list[str]] | None = None   # target -> labels (the engine's team names)
        self.verify_s, self.verify_poll_s = verify_s, poll_s
        self.results: list[dict] = []                           # every tile switch, for the gate

    # --- state ---------------------------------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.enabled and self.keys is not None and self.keys.can_switch

    def session(self, host: str) -> "pmv.Session | None":
        return self.sessions.get(host) if self.ready else None

    def has_tile(self, host: str, target: str | None) -> bool:
        s = self.session(host)
        return bool(target and s and s.slot(target) is not None)

    def is_main(self, host: str, target: str | None) -> bool:
        s = self.session(host)
        return bool(target and s and s.main_target == target)

    def forget(self, host: str) -> None:
        self.sessions.pop(host, None)

    def _labels(self, targets) -> dict[str, list[str]]:
        out = {}
        for t in targets:
            try:
                out[t] = [n for n in (self.names(t) if self.names else []) if n]
            except Exception:   # a names bug must never press a key
                log.exception("prime multiview: names failed for %s", t)
                out[t] = []
        return out

    async def _ui(self, stick: Stick) -> "list[pmv.Node] | None":
        r = await stick.run("shell", pmv.UI_DUMP, timeout=8)
        return pmv.parse_nodes(r.out if r.ok else "")

    async def read_main(self, stick: Stick, targets) -> str | None:
        return pmv.main_tile(await self._ui(stick), self._labels(targets))

    # --- tile switch ---------------------------------------------------------------------------
    async def switch(self, stick: Stick, target: str, may_cut=None, clock=time.monotonic) -> TuneResult:
        """Make `target` the main tile (it gets the audio). Verified from the accessibility tree;
        an unverified result forgets the session (the next switch is a normal tune)."""
        t0 = clock()
        steps: list[str] = []
        s = self.session(stick.host)
        slot = s.slot(target) if s else None
        if s is None or slot is None:
            return TuneResult(False, target, 0.0, steps, "not a multiview tile here", False, "tile")
        if slot == s.main:
            return TuneResult(True, target, 0.0, ["already the main tile"], "", False, "tile")
        path = self.keys.path(s.main, slot)
        if path is None:
            return TuneResult(False, target, 0.0, steps, f"no key path {s.main}>{slot}", False, "tile")
        if may_cut is not None and not may_cut():
            return TuneResult(False, target, 0.0, steps, "cut window closed", False, "tile")
        await stick.key(*pmv.codes(path))
        steps.append(f"tile {slot} -> main ({' '.join(path)})")
        seen = None
        while clock() - t0 < self.verify_s:
            seen = await self.read_main(stick, s.tiles)
            if seen == target:
                break
            await asyncio.sleep(self.verify_poll_s)
        took = clock() - t0
        ok = seen == target
        self.results.append({"ok": ok, "took": round(took, 2), "slot": slot, "seen": seen is not None})
        if not ok:
            self.forget(stick.host)
            why = "main tile not verified" if seen is None else "a different tile became main"
            return TuneResult(False, target, took, steps + [why], why, True, "tile")
        s.promoted(slot, self.keys.after)
        pb = await stick.playback(stick.packages.get(self.role))
        if playback_ready(pb):
            stick.remember(target, pb)
        steps.append("verified main tile")
        return TuneResult(True, target, took, steps, "", True, "tile")

    async def tune(self, stick: Stick, ref: str, target: str, **kw):
        if self.has_tile(stick.host, target):
            res = await self.switch(stick, target, kw.get("may_cut"))
            if res.ok or res.screen_changed is False and "cut window" in res.error:
                return res
            log.info("prime multiview: %s on %s: %s; normal tune instead", target, stick.host, res.error)
        res = await super().tune(stick, ref, target, **kw)
        if res.screen_changed is not False:
            self.forget(stick.host)   # a deep link leaves the multiview
        return res

    # --- building one (hidden stick only: it takes over the screen of the stick it runs on) ----
    async def build(self, stick: Stick, tiles: list[str], clock=time.monotonic) -> bool:
        """With tiles[0] playing on this stick (a normal tune), open multiview and add up to two more
        games from the picker, each verified. Leaves a Session with what actually got in."""
        if not (self.ready and self.keys.can_build) or not tiles:
            return False
        tiles = list(tiles)[:pmv.MAX_TILES]
        await stick.key(*pmv.codes(self.keys.open))
        if await self.read_main(stick, tiles[:1]) != tiles[0]:
            self.forget(stick.host)
            return False
        s = pmv.Session(stick.host, [tiles[0]], 0)
        self.sessions[stick.host] = s
        for t in tiles[1:]:
            await stick.key(*pmv.codes(self.keys.add_open))
            rows, focus = pmv.picker_rows(await self._ui(stick))
            names = self._labels([t])[t]
            want = next((i for i, r in enumerate(rows) if pmv._hits(r, names)), None)
            if want is None or focus is None or want < focus:
                await stick.key(pmv.KEYCODE["back"])   # not offered (or can't tell): close the picker
                continue
            await stick.key(*pmv.codes([self.keys.add_next] * (want - focus) + [self.keys.add_select]))
            if self.keys.add_slot == "main":
                s.tiles.insert(0, t)
            else:
                s.tiles.append(t)
            if await self.read_main(stick, s.tiles) != s.main_target:
                self.forget(stick.host)
                return False
        return len(s.tiles) > 1


class EspnTuner(Tuner):
    """ESPN+ events in the ESPN app (experimental: verify on the stick first)."""
    app = "espn"
    role = "espn"
    confirm_timeout = 20.0
    link_form = False        # tuners.espn_link_form: the form reported to work on ESPN+ (unverified here)

    def url(self, ref: str) -> str:
        if self.link_form:
            return f"sportscenter://x-callback-url/showWatchStream?playID={ref}"
        return f"https://www.espn.com/watch/player/_/id/{ref}"


class ReelTuner(Tuner):
    """The catch-up reel (redzone/reel.py), served by this Mac at /reel.mp4, played by VLC or Silk.
    Experimental until checked on the stick (reel.play: false by default). The ref is the reel's
    build time, so each new reel is a new URL (no stale cached copy)."""
    app = "reel"
    role = "reel"
    confirm_timeout = 10.0

    def url(self, ref: str) -> str:
        return f"{reel_base_url()}/reel.mp4?v={ref}"


def reel_base_url() -> str:
    """http://<this Mac's LAN address>:<port>, as the sticks on the same Wi-Fi reach it."""
    import os
    import socket
    port = os.environ.get("PORT") or "8777"
    ip = "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))   # no packet is sent: this just picks the LAN interface
            ip = sk.getsockname()[0]
    except OSError:
        pass
    return f"http://{ip}:{port}"


# Prime's tuner is the multiview one; with tuners.prime_multiview off it is plain PrimeTuner.
TUNERS: dict[str, Tuner] = {"youtubetv": YouTubeTVTuner(), "prime": PrimeMultiviewTuner(), "espn": EspnTuner(),
                            "reel": ReelTuner()}
