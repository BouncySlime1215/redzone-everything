"""Turns decisions into TV actions. Dry-run by default.

Single stick: tune the stick, confirm video is playing.
Two sticks ("hot standby"): the hidden stick is ALWAYS the one that gets tuned. When it's
confirmed playing, we wait for a safe moment on the visible game (no play on screen) and only
then flip the TV input (1-2s). Result: no black screens, and the cut lands exactly when it's safe.
Between switches the hidden stick pre-loads what the next switch most likely needs (the engine
picks it, see redzone/standby.py). After every flip, the stick we just left keeps playing its
game live, so "back" or "home" is one more flip instead of a tune.

Speed (each one is seconds per switch):
- Every real tune, input flip and rewind is timed per app; the medians become the decider's
  switch costs (Engine._sync_switch_costs), so it plans with measured times, not guesses.
- A switch cancels a preload of some OTHER game instead of queueing behind it.
- A replay is parked on the hidden stick (rewound to the play, paused), so it starts the
  instant we flip. Every rewind also counts the seconds spent tuning, so it lands before the snap.
- A stick whose CEC flip failed once goes straight to the TV's own remote for a while.

Guardrails:
- One command at a time; a newer command cancels an older one still running.
- Every tune is confirmed by the stick's own playback report; a failed tune is retried once,
  then reported (3 errors in 5 min = safe mode in the decider).
- A failed preload isn't retried for 2 minutes (no hammering a broken channel).
- Parked replay footage is never used for a live switch.
- The hidden stick is re-checked every few seconds; if it stopped playing (losing the TV input
  can pause a stick over CEC) it no longer counts as ready.
- A sleeping hidden stick is never woken by a preload: a Fire TV that wakes grabs the TV input
  (CEC one-touch play) and would cut the game you're watching. A real switch wakes it, and only
  at a safe moment.
- Two-stick mode drops to single-stick automatically if an input flip fails (retried after 2 min).
- A flip is HOME -> active source (polled every ~0.12 s), then the relaunched app must be playing
  the game (MEDIA_PLAY, then one fresh deep link) before the switch reports; flip_s and resume_s
  are booked separately for the phone.
- TV input unknown with neither / both sticks claiming it: a bounded HOME re-assert on the stick
  meant to be on screen (never the other one), at most 3 per episode.
- The stick we left never gets HOME or WAKEUP (either could pull the TV back to it).
- The stick on screen not playing for 8 s outside a switch: MEDIA_PLAY, then a fresh deep link.
- No stick reachable -> behaves like dry-run and says so on the phone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import statistics
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from redzone.channels import ChannelMap
from redzone.tuners.adb import Stick, adb_available, playback_ready, same_stream
from redzone.tuners.tuners import TUNERS, TuneResult, channel_matcher
from redzone.tv import SamsungTV

log = logging.getLogger(__name__)
DEVICES = Path(__file__).resolve().parent.parent / "data" / "devices.json"
MAX_SAFE_WAIT_S = 25.0     # how long a flip may wait for a safe moment before giving up
SAFE_POLL_S = 0.2          # re-check for that moment this often (a cheap local computation)
PRELOAD_BACKOFF_S = 120.0  # a preload that failed isn't retried for this long
MIN_PRELOAD_GAP_S = 5.0    # never start preloads back to back faster than this
CEC_RETRY_S = 600.0        # a stick whose CEC flip failed goes straight to the TV remote this long
CEC_SYNC_S = 30.0          # re-ask the sticks which one is on screen this often (when idle)
RESUME_WAIT_S = 6.0        # after a HOME flip, the relaunched app must be playing again within this
PRIME_RESUME_WAIT_S = 15.0  # ...Prime's player takes longer to report again after REORDER_TO_FRONT (Nov 2)
RESUME_PAUSED_S = 2.0      # ...but a player that says "paused" this long gets MEDIA_PLAY without waiting it out
RESUME_PLAY_WAIT_S = 3.0   # ...else MEDIA_PLAY (a paused player) and wait this long
RELINK_MAX_S = 25.0        # ...else one fresh deep link of the target, capped at this
RESUME_POLL_S = 0.25       # "playing the game again?" this often while resuming
STANDBY_REREADS = 3        # right before a flip: a standby whose session is missing is re-read this often...
STANDBY_REREAD_S = 0.5     # ...this far apart, before the switch is given up
PRIME_PAGE_AFTER_S = 1.0  # ...its session gone this long -> read the screen for the detail page
PRIME_PAGE_CHECKS = 3      # ...at most this many screen reads per resume
PRIME_LIVE_WAIT_S = 8.0    # ...after pressing through: the stream starts in ~3.5 s (Nov 2)
FLIP_POLL_S = 0.12         # after HOME: ask the stick "are you the active source?" this often...
FLIP_DEADLINE_S = 4.5      # ...for at most this long (it normally answers in well under a second)
RELAUNCH_AFTER_S = 0.6     # the app is relaunched no sooner than this after HOME (the launcher must draw)
TWO_STICK_RETRY_S = 120.0  # a failed flip turns two-stick mode off this long (once the input is known again)
REASSERT_FIRST_S = 15.0    # input unknown: first HOME re-assert on the intended stick no sooner than this
REASSERT_GAP_S = 60.0      # ...then at most once a minute
REASSERT_MAX = 3           # ...and at most this many per episode (a TV someone turned off stays off)
REASSERT_BOTH_S = 120.0    # both sticks claim the TV and the engine gave no safe moment: re-assert after this
VISIBLE_STALL_S = 8.0      # the stick on screen not playing this long, outside a switch -> resume it
VISIBLE_FIX_GAP_S = 30.0   # ...at most once per this (a deep link that keeps failing isn't hammered)
AWAKE_TRUST_S = 120.0      # a stick seen playing this recently is awake: skip the sleep check
REFRESH_POLL_S = 0.5       # after a flip: how often the refresh of the game we left checks for a free moment
REFRESH_TRIES = 40         # ...and how many times (~20s) before it leaves the standby to the planner
PRIME_KEEP_WAIT_S = 15.0   # after a flip: the Prime game we left gets this long to show it kept playing...
PLANNER_WISH_S = 3.0       # a refresh holds off while the planner asked for another game this recently
KEEP_CONFIRM_S = 2.0       # ...on two reads this far apart (a player that pauses a beat late isn't "ready")
ASLEEP_RECHECK_S = 60.0    # a sleeping hidden stick: don't ask again (or preload) for this long
VERIFY_SOON_S = 3.0        # after a flip, check the stick we left within a few seconds
SEEK_LATE_OK_S = 1.0       # a rewind may land this far past its target (Missed.back_s leads the snap by 3s)

LATENCY_KEEP = 15
# What a real measurement can look like; outside this it's a glitch (a fake, a hung adb), not a switch.
PLAUSIBLE = {"flip": (0.02, 30.0), "seek": (0.05, 60.0), "resume": (0.0, 60.0)}
ACTIVE_SOURCE_CMD = "dumpsys hdmi_control | grep -E -c 'mIsActiveSource: true'"
TUNE_PLAUSIBLE = (0.3, 90.0)


@dataclass
class Outcome:
    ok: bool
    target: str | None
    took: float                # seconds of switching work (tune, rewind, flip) — not the wait for a safe moment
    detail: str
    dry_run: bool
    confirmed_at: float | None = None
    path: str = ""             # "preloaded" | "parked replay" | "tuned" | "single" | "already"
    waited: float = 0.0        # seconds held for a safe moment before the flip
    screen_unchanged: bool = False  # rejected before any possible visible mutation
    flip_s: float | None = None     # HOME -> the stick is the TV's active source (the input changed)
    resume_s: float | None = None   # active source -> the relaunched app is playing the game again
    resumed: bool | None = None     # after a HOME flip: the game was confirmed playing again
    stick: str | None = None        # host of the stick that tuned (None = no tune ran)


@dataclass
class Parked:
    """The hidden stick is parked on a replay: rewound to just before the play. Old footage, not live."""
    replay_id: str
    target: str
    at: float                  # when the rewind finished
    paused: bool               # the stick confirmed it's holding still (else it drifts forward)


class LatencyBook:
    """Measured switch times: per app ("youtubetv", "prime", "espn"), plus "flip" (TV input change)
    and "seek" (replay rewind). Each estimate is the median of the last 15, so one stuck tune (or
    one lucky one) can't swing what the decider plans with."""

    def __init__(self, keep: int = LATENCY_KEEP):
        self.keep = keep
        self.samples: dict[str, deque] = {}
        self.preloaded: dict[str, deque] = {}   # app -> recent two-stick switches: was the hidden stick ready?
        self.dirty = False

    def record(self, key: str, seconds: float) -> None:
        lo, hi = PLAUSIBLE.get(key, TUNE_PLAUSIBLE)
        if lo <= seconds <= hi:
            self.samples.setdefault(key, deque(maxlen=self.keep)).append(round(seconds, 2))
            self.dirty = True

    def median(self, key: str) -> float | None:
        s = self.samples.get(key)
        return statistics.median(s) if s else None

    def note_preloaded(self, app: str, hit: bool) -> None:
        self.preloaded.setdefault(app, deque(maxlen=self.keep)).append(bool(hit))
        self.dirty = True

    def miss_rate(self, app: str) -> float:
        """Share of two-stick switches to this app that still needed a tune. Starts at 1/2 and
        learns from there, so one early miss can't make preloading look useless."""
        h = self.preloaded.get(app) or ()
        return (sum(1 for x in h if not x) + 1) / (len(h) + 2)

    def view(self) -> dict:
        return {k: {"median_s": round(statistics.median(v), 1), "n": len(v), "last_s": v[-1]}
                for k, v in self.samples.items() if v}

    def preloaded_view(self) -> dict:
        return {app: round(1 - self.miss_rate(app), 2) for app, h in self.preloaded.items() if h}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"samples": {k: list(v) for k, v in self.samples.items()},
                                   "preloaded": {k: list(v) for k, v in self.preloaded.items()},
                                   "saved": time.time()}))
        tmp.replace(path)  # atomic: a crash mid-save can't corrupt it
        self.dirty = False

    def load(self, path: Path) -> None:
        """Pick up where the last run left off. A missing or broken file just means starting fresh."""
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        for k, v in (d.get("samples") or {}).items():
            for x in (v or [])[-self.keep:]:
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    self.record(str(k), float(x))
        for k, v in (d.get("preloaded") or {}).items():
            self.preloaded[str(k)] = deque((bool(x) for x in (v or [])[-self.keep:]), maxlen=self.keep)
        self.dirty = False


class Controller:
    def __init__(self, cfg, channels: ChannelMap, latency_path: Path | None = None):
        self.cfg = cfg
        self.channels = channels
        self.sticks: list[Stick] = []
        self.hdmi: dict[str, str] = {}          # stick host -> "HDMI1"
        self.tv = SamsungTV()
        self.on_target: str | None = None        # what the visible stick shows LIVE (None after a replay)
        self.active = 0                          # index of the stick on screen
        self.hidden_target: str | None = None    # what the hidden stick is playing or loading
        self.hidden_ready = False                # ...and it's confirmed playing
        self.parked: Parked | None = None        # ...parked on a replay instead of live
        self.two_stick_ok = True                 # flips to False if an input flip ever fails
        self.latency = LatencyBook()
        self.latency_path = latency_path
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._preload_task: asyncio.Task | None = None
        self._preloading: tuple[str, str | None] | None = None   # (target, replay_id) being loaded
        self._last_preload = 0.0
        self._failed: dict[str, float] = {}      # target -> don't preload before
        self.on_preload_fail: Callable[..., None] | None = None   # (target, detail, host=): teach discovery
        # Discovery's per-stick answer (host, target) -> bool: one stick can be signed out of an
        # app the other plays (Peacock on .181 only). None = every stick can play every app.
        self.can_play: Callable[[str, str], bool] | None = None
        self._tuned_on: str | None = None        # host of the last stick a switch tuned
        self._cec_bad: dict[str, float] = {}     # stick host -> skip CEC until
        self._awake_at: dict[str, float] = {}    # stick host -> last seen awake
        self._asleep_until = 0.0
        self._verified_at = 0.0
        self._flipping = False                   # inside the flip: not cancellable
        self.adb_ok = False
        self._watchdog_applied = False
        self.last: Outcome | None = None
        self.note = ""
        self.stage = {"stage": "idle", "target": None, "reason": ""}
        self.input_unknown = False
        self._two_stick_retry_at = 0.0           # after a failed flip: two-stick mode stays off until then
        self._home_flipped = False               # the last flip pressed HOME (its app was relaunched)
        self._resume_task: asyncio.Task | None = None   # "did the relaunched app resume?" after a HOME flip
        self._cec_synced_at = 0.0
        self._adopt_once = True                  # first switch after start may adopt the screen
        self.target_names: Callable[[str], list[str]] | None = None   # engine: team names for a target
        self._flip_gen = 0                       # bumped on every flip: a CEC read that spans one is stale
        self._refresh_task: asyncio.Task | None = None   # after a flip: reload the game we left (see _refresh_left)
        self._refreshing: str | None = None      # the target the running preload is refreshing, if it is a refresh
        self._wish: tuple[str, float] | None = None   # the planner's latest preload ask (target, at), even if refused
        self._standby_miss: str | None = None    # a Prime standby whose last check found no session (one more read)
        self._exit_check = None
        self._flip_s: float | None = None       # HOME -> active source, of the last flip
        self.last_flip: dict | None = None       # the phone's latency book: flip_s / resume_s of the last flip
        self._visible_bad_since: float | None = None   # the stick on screen stopped playing at (watchdog)
        self._visible_fix_at = 0.0               # last watchdog repair (rate limit)
        self._unknown_since = 0.0                # input became unknown at
        self._unknown_target: str | None = None  # what the intended stick was showing when it did
        self._reasserts = 0                      # HOME re-asserts on the intended stick this episode
        self._reassert_at = 0.0                  # next re-assert allowed at
        self.cec_view = "unknown"                # last CEC read: one | none | both | unknown
        # The engine's "nothing live on the visible game right now" (optional). Gates the HOME
        # re-assert when both sticks claim the TV, since then one of them IS on screen.
        self.safe_moment: Callable[[], bool] | None = None
        # The engine's "the visible-stick watchdog may act" (optional; e.g. off during a recorded session).
        self.visible_watch_ok: Callable[[], bool] | None = None
        # Every playback report from any stick (the engine's show-change delay watcher), optional.
        self.on_playback: Callable[[Stick, dict], None] | None = None

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
            st.observer = self._observe_playback
            self.sticks.append(st)
            if s.get("hdmi"):
                self.hdmi[st.host] = s["hdmi"]
        self.tv = SamsungTV.from_devices()

    def _observe_playback(self, stick: Stick, report: dict) -> None:
        if self.on_playback is not None:
            self.on_playback(stick, report)

    async def boundary_reads(self) -> int:
        """One playback read of each stick showing YouTube TV, for the show-change delay watcher.
        Never during a switch, a flip or a preload: those reads would queue behind (or delay) it."""
        if not self.live_control or self._busy() or self._flipping or self.input_unknown:
            return 0
        sticks = [s for s in self.usable if str((s.tuned or {}).get("target") or "").startswith("youtubetv:")]
        for s in sticks:
            await s.playback(self._package(s, s.tuned["target"]))
        return len(sticks)

    def scorebug_sticks(self, hidden: bool = False) -> list[tuple[Stick, str, bool]]:
        """(stick, target, visible) for each stick showing a live Prime game we tuned ourselves: the
        visible one, and with `hidden` the hidden one when its preload is live. Empty during a switch,
        a flip or a preload, and in dry run. Reads nothing from the sticks."""
        if not self.live_control or self._busy() or self._flipping or self.input_unknown:
            return []
        out = []
        main, t = self.main, self.on_target
        if main is not None and t and t.startswith("prime:") and (main.tuned or {}).get("target") == t:
            out.append((main, t, True))
        h, ht = self.hidden, self.hidden_target
        if (hidden and h is not None and ht and ht.startswith("prime:") and self._live_on_hidden(ht)
                and (h.tuned or {}).get("target") == ht):
            out.append((h, ht, False))
        return out

    async def scorebug_frames(self, hidden: bool = False) -> list[dict]:
        """One screenshot of each stick scorebug_sticks() names: {stick, target, visible, tune, png,
        t0, t1}. Never presses a key. All dropped if a switch or flip started while capturing."""
        gen, out = self._flip_gen, []
        for s, target, visible in self.scorebug_sticks(hidden):
            tune = s.tuned
            t0 = time.time()
            png = await s.screencap(timeout=15)   # a 1080p frame from the 4K (2023) stick: ~6.5 s over Wi-Fi
            t1 = time.time()
            if gen != self._flip_gen or self._flipping or self._busy() or s.tuned is not tune:
                return []
            if png:
                out.append({"stick": s, "target": target, "visible": visible, "tune": tune, "png": png,
                            "t0": t0, "t1": t1})
        return out

    async def start(self) -> None:
        self.adb_ok = await adb_available()
        if not self.adb_ok:
            log.warning("adb didn't answer at startup; asking again at every keepalive")
        self.load_devices()
        if self.latency_path:
            self.latency.load(self.latency_path)
        # Startup must never die on a device hiccup: the 10 s keepalive picks up whatever failed here.
        try:
            await self.keepalive()
            await self.sync_active_from_cec()
        except Exception:
            log.exception("stick startup check failed; keepalive will retry")

    async def sync_active_from_cec(self) -> bool:
        """Which stick is the TV showing? Each stick knows if it's the HDMI-CEC active source.
        (The TV itself isn't reachable on the network, so ask the sticks.) Acts only on an
        unambiguous answer: exactly one stick says it's on screen. Returns True if it changed
        which stick we think is on screen, or cleared an unknown TV input."""
        on, gen, asked = [], self._flip_gen, 0
        for i, s in enumerate(self.sticks):
            if not s.controllable:
                continue
            r = await s.shell(ACTIVE_SOURCE_CMD, timeout=5)
            if r.ok:
                asked += 1
            if r.ok and r.out.strip().startswith("1"):
                on.append(i)
        if gen != self._flip_gen or self._flipping or self._busy():
            return False   # a switch flipped (or is flipping) while we read: these answers are stale
        self.cec_view = "one" if len(on) == 1 else ("both" if len(on) > 1 else ("none" if asked else "unknown"))
        if len(on) != 1:
            # Neither (TV off or on another input, CEC dropped) or both (a race, or a stick that
            # never heard the other's <Active Source>): no guess. keepalive re-asserts if it lasts.
            return False
        i = on[0]
        changed = self.active != i or self.input_unknown
        if changed:
            log.info("TV is showing %s (CEC active source)%s", self.sticks[i].host,
                     "; TV input known again" if self.input_unknown else "")
            if self.active != i:
                # What we thought each stick showed no longer lines up: let them be re-tuned.
                self.on_target = None
                self._set_hidden(None, False)
            self.active = i
            self._input_known()
        return changed

    def _mark_input_unknown(self, intended_target: str | None) -> None:
        """We no longer know which input the TV shows. Remember what the stick we meant to be on
        screen was playing, so a re-assert can put exactly that back."""
        if not self.input_unknown:
            self._unknown_since = time.time()
            self._unknown_target = intended_target
            self._reasserts = 0
            self._reassert_at = time.time() + REASSERT_FIRST_S
        self.input_unknown = True

    def _input_known(self) -> None:
        self.input_unknown = False
        self._reasserts = 0
        self._unknown_target = None

    async def close(self) -> None:
        """App shutdown: close each stick's always-open adb shell."""
        tasks = [t for t in (self._task, self._preload_task, self._resume_task, self._refresh_task)
                 if t and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for s in self.sticks:
            await s.close()

    async def keepalive(self) -> None:
        """Reconnect any stick that dropped (reboots, Wi-Fi blips), then check the standby. Every ~10s."""
        if self.cfg.get("mode.dry_run", True):
            return
        if not self.adb_ok:
            # One flaky `adb version` at startup used to disable the sticks until the next restart.
            self.adb_ok = await adb_available()
            if not self.adb_ok:
                return
        # Both sticks at once: a stick that times out (~6 s) mustn't hold up the other one.
        await asyncio.gather(*(self._reconnect(s) for s in self.sticks))
        # Which stick is on screen? Right away when a flip left it unknown; otherwise every ~30 s
        # (someone used the TV remote; both sticks can't be "on screen" in our books for long).
        if not self._busy() and not self._flipping and (
                self.input_unknown or time.time() - self._cec_synced_at >= CEC_SYNC_S):
            self._cec_synced_at = time.time()
            await self.sync_active_from_cec()
        if self.input_unknown:
            await self._maybe_reassert()
        if not self.two_stick_ok and time.time() >= self._two_stick_retry_at and not self.input_unknown:
            self.two_stick_ok = True
            self.note = ""
            log.info("Two-stick mode back on (the TV input is known again)")
        await self._stick_watchdog()
        await self.verify_standby()
        await self._watch_visible()
        self._maybe_reseek()

    async def _maybe_reassert(self) -> None:
        """The TV input is unknown (a flip that wasn't confirmed, someone's remote, CEC lost).
        If no stick claims the TV (TV on another input, or off) or both do (a race), press HOME on
        the stick we MEAN to be on screen: it announces <Active Source>, the TV follows, and its
        app is relaunched and confirmed playing. Never on the other stick. With neither claiming,
        nothing of ours is on screen, so no play can be cut; with both claiming, one of them is on
        screen, so it waits for the engine's safe moment (or 2 minutes without one). At most 3
        tries per episode, a minute apart: a TV someone turned off isn't turned on all night."""
        now = time.time()
        if (self._busy() or self._flipping or not self.live_control or self._reasserts >= REASSERT_MAX
                or now < self._reassert_at or self.cec_view not in ("none", "both")):
            return
        stick = self.main
        if stick is None or stick.host not in self.hdmi:
            return
        if self.cec_view == "both":
            gate = self.safe_moment
            try:
                ok = gate() if gate is not None else now - self._unknown_since >= REASSERT_BOTH_S
            except Exception:
                log.exception("safe_moment check failed")
                ok = False
            if not ok:
                return
        self._reasserts += 1
        self._reassert_at = now + REASSERT_GAP_S
        # What to relaunch after HOME: what it showed when the input got lost, else its last confirmed tune.
        target = self._unknown_target or (stick.tuned or {}).get("target")
        log.warning("TV input unknown (CEC: %s); re-asserting %s with HOME (try %d/%d)", self.cec_view,
                    stick.host, self._reasserts, REASSERT_MAX)
        async with self._lock:
            if not self.input_unknown or self.main is not stick:
                return
            self._flipping = True
            try:
                self._flip_s = None
                flipped = await self._home_flip(stick, target)
                self._flip_gen += 1
                if not flipped:
                    self.note = (f"TV input unknown: {stick.host} couldn't take the TV "
                                 f"(try {self._reasserts}/{REASSERT_MAX})")
                    return
                self.latency.record("flip", self._flip_s or 0.0)
                self._awake_at[stick.host] = time.time()
                self._cec_synced_at = time.time()
                log.info("TV is showing %s again (re-asserted)", stick.host)
                self._input_known()
                self.note = ""
                self._set_hidden(None, False)   # what the other stick shows is unconfirmed
                self.on_target = None
                if target:
                    r0 = time.monotonic()
                    how = await self._resume_playback(stick, target, self._resume_wait(target))
                    self._book_flip(stick, target, self._flip_s, time.monotonic() - r0, how)
                    # Not back yet: keep the target so the visible-stick watchdog keeps trying.
                    self.on_target = target
                    self._visible_bad_since = None if how else time.time()
            finally:
                self._flipping = False

    async def _watch_visible(self) -> None:
        """The stick on screen should be playing what we put there. An "are you still watching?"
        pause, the app crashing to the Fire TV home, or an app update stopping it all leave the
        screen dead while the decider happily "stays". Not playing for 8 s outside a switch ->
        MEDIA_PLAY (a paused player), then a fresh deep link of the current target. It only ever
        acts on a screen that is already not playing, so it can't interrupt a live play."""
        if (not self.live_control or self.input_unknown or self._busy() or self._flipping
                or (self.visible_watch_ok is not None and not self.visible_watch_ok())):
            return
        stick, target = self.main, self.on_target
        if stick is None or not target:
            self._visible_bad_since = None
            return
        pb = await stick.playback(self._package(stick, target))
        if (self._busy() or self._flipping or self.main is not stick or self.on_target != target
                or pb.get("read_ok") is False):
            return
        now = time.time()
        if pb.get("state") in ("playing", "buffering", "connecting"):
            self._visible_bad_since = None
            return
        if self._visible_bad_since is None:
            self._visible_bad_since = now
            log.info("%s (on screen) is %s, not playing %s; checking again in %.0fs", stick.host,
                     pb.get("state"), target, VISIBLE_STALL_S)
            self._schedule_visible_check(VISIBLE_STALL_S)
            return
        if now - self._visible_bad_since < VISIBLE_STALL_S:
            self._schedule_visible_check(VISIBLE_STALL_S - (now - self._visible_bad_since))
            return
        if now - self._visible_fix_at < VISIBLE_FIX_GAP_S:
            return
        if asyncio.current_task() is not self._resume_task:
            # Repair in its own task (up to ~30 s with a deep link), so a real switch (submit) or
            # Pause can cancel it instead of queueing behind it on the lock.
            if not (self._resume_task and not self._resume_task.done()):
                self._resume_task = asyncio.create_task(self._watch_visible())
            return
        self._visible_fix_at = now
        async with self._lock:
            if self.main is not stick or self.on_target != target or self.input_unknown:
                return
            self.stage = {"stage": "resuming", "target": target, "reason": "the screen stopped playing; resuming it"}
            r0 = time.monotonic()
            how = await self._resume_playback(stick, target, 0.0)
            took = time.monotonic() - r0
            if how:
                log.info("watchdog: %s playing again on %s (%s, %.1fs)", target, stick.host, how, took)
                self.latency.record("resume", took)
                self._visible_bad_since = None
                self.note = ""
                self.stage = {"stage": "confirmed", "target": target, "reason": f"resumed by {how.replace('_', ' ')}"}
            else:
                self.note = f"The TV stopped playing {target}; couldn't restart it yet (retrying)"
                self.stage = {"stage": "failed", "target": target, "reason": self.note}

    def _schedule_visible_check(self, delay: float) -> None:
        """Re-check the stick on screen after `delay`, not at the next ~10 s keepalive. A switch
        (submit) or Pause cancels it."""
        if self._resume_task and not self._resume_task.done():
            return

        async def later():
            await asyncio.sleep(max(0.0, delay) + 0.05)
            await self._watch_visible()
        self._resume_task = asyncio.create_task(later())

    async def _stick_watchdog(self) -> None:
        """Nobody touches the remote, so Fire OS would start its screensaver after 5 min and sleep
        after 20. Screensaver: back out of it (BACK never makes the TV switch inputs). The stick on
        screen asleep: wake it (it IS the TV's input, so its <Active Source> changes nothing). The
        hidden stick asleep: leave it — waking it would pull the TV to it mid-game."""
        if not self._watchdog_applied:
            self._watchdog_applied = True
            for s in self.usable:
                await s.shell("settings put secure screensaver_enabled 0; "
                              "settings put secure screensaver_activate_on_sleep 0; "
                              "settings put secure sleep_timeout 86400000; "
                              "settings put system screen_off_timeout 86400000", timeout=6)
        for s in self.usable:
            r = await s.shell("dumpsys power | grep -m1 mWakefulness=", timeout=5)
            state = r.out.strip().split("=")[-1] if r.ok else ""
            if state == "Dreaming":
                log.info("%s was in its screensaver: dismissing", s.host)
                await s.key("KEYCODE_BACK")
            elif state in ("Asleep", "Dozing") and s is self.main:
                log.info("%s (on screen) fell asleep: waking it", s.host)
                await s.key("KEYCODE_WAKEUP")
                # It may have stopped the stream: the visible-stick watchdog restarts it if it
                # isn't playing again shortly (keeping on_target tells it what to restart).
                if self._visible_bad_since is None:
                    self._visible_bad_since = time.time()

    async def _reconnect(self, s: Stick) -> None:
        if s.state == "connected" and time.time() - s.last_ok <= 60:
            return
        was = s.state
        try:
            if await s.connect():
                if not s.packages:
                    await s.identify()
                if was != "connected":
                    self._forget(s)
        except Exception:
            log.exception("reconnecting %s failed; trying again next tick", s.host)

    def _forget(self, stick: Stick) -> None:
        """A stick came back from a drop (maybe a reboot): whatever we thought it showed is stale."""
        if stick is self.main:
            self.on_target = None
        else:
            self._set_hidden(None, False)

    def save_latency(self) -> None:
        if self.latency_path and self.latency.dirty:
            try:
                self.latency.save(self.latency_path)
            except OSError:
                log.exception("couldn't save switch times")

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
                and (bool(self.tv.st_device) or self.cfg.get("two_stick.cec_flip", True))
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

    # --- what the hidden stick has -----------------------------------------------

    async def _refresh_left(self, target: str, stick: Stick | None = None, since: float | None = None) -> None:
        """After a flip: put the game we just left back on the hidden stick, live (see _execute_two).
        Prime keeps its player when its stick loses the TV input, so a Prime game that is still
        playing live there is simply kept (_kept_playing): no re-tune (~33 s).
        Otherwise (YouTube TV / Peacock pause on input loss) it's reloaded at the live edge.
        Waits for a free moment and never competes with real work: a switch (submit) or the
        planner's own preload cancels it, and the planner can displace it mid-tune (preload).
        It goes through preload, so a sleeping stick is never woken and HOME is never pressed."""
        started = since if since is not None else time.time()   # the flip
        if target.startswith("prime:") and await self._kept_playing(target, stick or self.hidden):
            return
        for _ in range(REFRESH_TRIES):
            await asyncio.sleep(REFRESH_POLL_S)
            if self.hidden_target != target or self.hidden_ready or self.on_target == target:
                return   # something else was loaded there meanwhile, or we already went back
            w = self._wish
            if w and w[0] != target and w[1] >= started and time.time() - w[1] <= PLANNER_WISH_S:
                # Since the flip the planner asked for another game on this stick (refused only for
                # now: the preload gap, a switch running). Its preload is coming and would cancel
                # this reload mid-tune, so don't start one.
                continue
            if not self._busy():
                self.preload(target, refresh=True)
                return

    async def _kept_playing(self, target: str, stick: Stick | None) -> bool:
        """Is the game we left still playing live on the stick we left? Then it's the ready standby
        as is. Only reads playback: no key, no tune, no WAKEUP. Two reads KEEP_CONFIRM_S apart must
        both be the stream we tuned there, running on the stick's own clock (Stick.still_on: Prime's
        wall-clock position runs ~1% fast; 2% is allowed). True = nothing left to do (kept, or
        something else took the stick meanwhile); False = it paused, changed or never showed
        itself within PRIME_KEEP_WAIT_S: reload it."""
        if stick is None:
            return True
        pkg = self._package(stick, target)
        end, first = time.monotonic() + PRIME_KEEP_WAIT_S, None

        def moved_on() -> bool:
            return (self.hidden is not stick or self.hidden_target != target or self.hidden_ready
                    or self.on_target == target)
        while time.monotonic() < end:
            await asyncio.sleep(REFRESH_POLL_S)
            if moved_on():
                return True
            if self._busy() or self._flipping:
                continue   # a switch or the planner's preload is using the stick: don't read under it
            pb = await stick.playback(pkg)
            if moved_on():
                return True
            if self._busy() or self._flipping or pb.get("read_ok") is False:
                continue
            if stick.still_on(target, pb):
                first = time.monotonic() if first is None else first
                if time.monotonic() - first >= KEEP_CONFIRM_S:
                    self._set_hidden(target, True)
                    self._verified_at = time.time()
                    self._awake_at[stick.host] = time.time()
                    log.info("standby %s kept playing on %s after the flip: kept, no reload", target, stick.host)
                    return True
                continue
            first = None
            if pb.get("state") in ("paused", "stopped") or self._jumped(stick, target, pb):
                log.info("standby %s on %s is %s after the flip: reloading it", target, stick.host,
                         "paused" if pb.get("state") != "playing" else "behind or a different stream")
                return False
        return False

    @staticmethod
    def _jumped(stick: Stick, target: str, pb: dict) -> bool:
        """Provably NOT the live stream we tuned: playing, but its position broke from the stick's
        clock (a pause, a seek, a new stream) by more than the drift still_on allows."""
        t = stick.tuned or {}
        if t.get("target") != target or not playback_ready(pb):
            return False
        ref = t.get("report") or {}
        elapsed = max(0, (pb.get("updated") or 0) - (ref.get("updated") or 0))
        return same_stream(ref, pb, slack_ms=3000 + int(0.02 * elapsed)) is False

    def _cancel_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done() and self._refresh_task is not asyncio.current_task():
            self._refresh_task.cancel()

    def _set_hidden(self, target: str | None, ready: bool, parked: Parked | None = None) -> None:
        self.hidden_target, self.hidden_ready, self.parked = target, ready, parked

    def _live_on_hidden(self, target: str) -> bool:
        return self.hidden_target == target and self.hidden_ready and self.parked is None

    def _parked_for(self, target: str, replay_id: str | None) -> bool:
        p = self.parked
        return bool(p and replay_id and p.replay_id == replay_id and p.target == target
                    and self.hidden_target == target and self.hidden_ready
                    and time.time() - p.at < float(self.cfg.get("two_stick.park_max_s", 240)))

    @property
    def standby(self) -> tuple[str | None, str | None]:
        """What the hidden stick has for us: (target, None) live (ready or loading), (target, replay_id)
        parked on a replay, (None, None) nothing usable (empty, failed, or parked too long ago)."""
        if not self.two_stick or not self.hidden_target:
            return (None, None)
        p = self.parked
        if p is None:
            return (self.hidden_target, None)
        return (p.target, p.replay_id) if self._parked_for(p.target, p.replay_id) else (None, None)

    @property
    def parked_id(self) -> str | None:
        return self.standby[1]

    def _busy(self) -> bool:
        return bool((self._task and not self._task.done()) or (self._preload_task and not self._preload_task.done())
                    or self._lock.locked())

    def can_tune(self, target: str | None) -> bool:
        """Could the hidden stick go here right now? A tuner, a channel link, the app installed,
        and no failed attempt in the last couple of minutes."""
        if not target or time.time() < self._failed.get(target, 0.0):
            return False
        app, ref = self._ref(target)
        tuner = TUNERS.get(app)
        if tuner is None or not ref:
            return False
        sticks = [s for s in (self.hidden, self.main) if s is not None]
        return not sticks or any(self._plays(s, target) for s in sticks)

    def _plays(self, stick: Stick | None, target: str) -> bool:
        """Has this stick the app, and hasn't discovery seen it walled (signed out) there?"""
        if stick is None or not self._package(stick, target):
            return False
        try:
            return self.can_play is None or bool(self.can_play(stick.host, target))
        except Exception:  # discovery must never block a switch
            log.exception("can_play failed")
            return True

    # --- learned switch times ------------------------------------------------------

    def _default_cost(self, key: str) -> float:
        """The shipped guess (default_config.yaml), used until we've measured this ourselves."""
        d = (getattr(self.cfg, "defaults", None) or {}).get("decider", {}).get("switch_cost", {})
        return float(d.get(key, 5.0))

    def tune_estimate(self, target_or_app: str) -> float:
        """Seconds a tune takes for this target's app: measured median, else the default guess."""
        app = target_or_app.split(":", 1)[0]
        m = self.latency.median(app)
        return m if m is not None else self._default_cost(app)

    def flip_estimate(self) -> float:
        """Seconds from "flip" to the new picture: the measured command, plus the TV's own HDMI
        re-sync (two_stick.input_settle_s — set it from a stopwatch, the TV can't tell us)."""
        m = self.latency.median("flip")
        if m is None:
            return self._default_cost("hdmi_flip")
        # flip_s is HOME -> active source (the cut). The picture coming back (resume_s) is booked
        # separately and deliberately NOT added here: it would shift tonight's decider costs.
        return m + float(self.cfg.get("two_stick.input_settle_s", 1.0))

    def switch_costs(self) -> dict[str, float]:
        """Expected seconds from "switch" to the new game on screen, per app, on the path we'd use now.

        One stick: a tune. Two sticks: a flip, plus a tune for the share of switches where the
        hidden stick wasn't already there (measured). Half-second steps keep config writes rare."""
        flip = self.flip_estimate()
        two = self.two_stick
        out = {"hdmi_flip": flip}
        for app in TUNERS:
            tune = self.tune_estimate(app)
            out[app] = flip + self.latency.miss_rate(app) * tune if two else tune
        return {k: round(v * 2) / 2 for k, v in out.items()}

    def deadline_cost(self, target: str | None) -> float:
        """Budget the actual target path, never an average of hits and misses.

        The 90th percentile and the shipped estimate are planning bounds, not
        a physical guarantee; actual exit timing is checked again at the cut.
        """
        app = (target or "unknown").split(":", 1)[0]
        def upper(key):
            samples = sorted(self.latency.samples.get(key) or ())
            p90 = samples[min(len(samples) - 1, math.ceil(len(samples) * .9) - 1)] if samples else 0
            return max(self._default_cost(key), p90)
        tune = upper(app)
        if not self.two_stick:
            return tune
        samples = sorted(self.latency.samples.get("flip") or ())
        flip = max(self._default_cost("hdmi_flip"),
                   samples[min(len(samples)-1, math.ceil(len(samples)*.9)-1)] if samples else 0)
        flip += float(self.cfg.get("two_stick.input_settle_s", 1.0))
        return flip if target and self._live_on_hidden(target) else tune + flip

    def switch_times(self) -> dict:
        """For the phone's Health card: what we measured, and what the decider is planning with."""
        return {"measured": self.latency.view(), "preloaded": self.latency.preloaded_view(),
                "cost": dict(self.cfg.get("decider.switch_cost") or {}),
                "path": "flip" if self.two_stick else "tune",
                # The input change and the picture coming back, measured separately (medians + last).
                "flip_s": self.latency.median("flip"), "resume_s": self.latency.median("resume"),
                "last_flip": dict(self.last_flip) if self.last_flip else None,
                "input": {"unknown": self.input_unknown, "cec": self.cec_view, "reasserts": self._reasserts,
                          "two_stick_ok": self.two_stick_ok}}

    # --- actions -----------------------------------------------------------------

    def submit(self, target: str | None, replay_back_s: float | None = None, on_done=None,
               may_cut: Callable[[], bool] | None = None, replay_id: str | None = None) -> None:
        """Fire-and-forget; the newest command wins. A preload of some OTHER target is cancelled
        (queueing behind a tune we no longer want would add up to 10s); one loading this very
        target is kept, and the switch picks it up the moment it's ready."""
        # [guardrail] Never cancel mid-flip: the TV could end up on the other input while we think
        # it isn't. The flip takes ~1s; the new command just waits for it.
        if self._task and not self._task.done() and not self._flipping:
            self._task.cancel()
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._cancel_refresh()   # the new switch decides what the hidden stick gets; a flip starts a new refresh
        want = (target, replay_id if replay_back_s else None)
        if self._preload_task and not self._preload_task.done() and self._preloading != want:
            self._preload_task.cancel()
        self._task = asyncio.create_task(self._run(target, replay_back_s, on_done, may_cut, replay_id, time.time()))
        if on_done:
            self._task.add_done_callback(lambda task: on_done(
                Outcome(False, target, 0.0, "switch cancelled; playback not confirmed", False)
            ) if task.cancelled() else None)

    async def _run(self, target, replay_back_s, on_done, may_cut, replay_id=None, issued_at=None) -> None:
        async with self._lock:
            if replay_back_s:
                self._adopt_once = False   # a rewound picture must never be adopted as live
            self.stage = {"stage": "tuning", "target": target, "reason": "confirming the requested stream"}
            try:
                self._tuned_on = None
                out = await self.execute(target, replay_back_s, may_cut, replay_id, issued_at)
                if out.stick is None:
                    out.stick = self._tuned_on
            except asyncio.CancelledError:
                if not self.hidden_ready:
                    self._set_hidden(None, False)   # stopped mid-tune: we don't know what it shows
                raise
            except Exception as e:  # never let a tuner bug kill the app
                log.exception("tune crashed")
                out = Outcome(False, target, 0.0, f"crashed: {e}", False)
            self.last = out
            self.stage = {"stage": "confirmed" if out.ok else "failed", "target": target, "reason": out.detail}
            if on_done:
                on_done(out)

    def cancel_pending(self) -> None:
        """Stop queued/tuning work on Pause or dry-run. Let an input flip settle."""
        if self._task and not self._task.done() and not self._flipping:
            self._task.cancel()
        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._cancel_refresh()

    def _ref(self, target: str) -> tuple[str, str]:
        app, _, ref = target.partition(":")
        if app == "youtubetv":
            ref = self.channels.yttv_code(target) or ""
        return app, ref

    def _package(self, stick: Stick, target: str) -> str | None:
        app = target.split(":", 1)[0]
        tuner = TUNERS.get(app)
        return stick.packages.get(getattr(tuner, "role", app)) if tuner else None

    async def _tune(self, stick: Stick, target: str, may_cut=None) -> TuneResult:
        app, ref = self._ref(target)
        tuner = TUNERS.get(app)
        if tuner is None or not ref:
            return TuneResult(False, target, 0.0, [], "channel link not set up yet" if tuner else f"no tuner for {app}")
        self._tuned_on = stick.host
        res = await tuner.tune(stick, ref, target, **({"may_cut": may_cut} if may_cut is not None else {}))
        if res.ok:
            self.latency.record(app, res.took)
            self._awake_at[stick.host] = time.time()
        return res

    async def _rewind(self, stick: Stick, back_s: float) -> bool:
        """Rewind about back_s and play. Whole presses, rounded UP: landing a few seconds early shows
        the snap; landing late misses it (the decider pads the replay's end for this). Up to
        SEEK_LATE_OK_S late is fine, because the target already starts 3s before the snap — so
        30.2s doesn't cost a whole extra press (10s more pre-roll)."""
        step = float(self.cfg.get("replay.seek_step_s", 10))
        if self._task is asyncio.current_task():
            self.stage["stage"] = "rewinding"
        presses = min(60, math.ceil(max(0.0, back_s - SEEK_LATE_OK_S) / step))
        t0 = time.monotonic()
        if presses:
            key = self.cfg.get("replay.seek_key", "KEYCODE_MEDIA_REWIND")
            sent = await stick.shell(" && ".join([f"input keyevent {key}"] * presses), timeout=20)
            if not sent.ok:
                return False
        sent = await stick.key("KEYCODE_MEDIA_PLAY")
        if not sent or not sent.ok:
            return False
        if presses:
            self.latency.record("seek", time.monotonic() - t0)
        return True

    async def _park(self, stick: Stick, target: str, back_s: float, replay_id: str) -> bool:
        """Rewind to just before the play and hold it there, so the replay starts the instant we flip."""
        self._set_hidden(target, False)   # mid-rewind it's neither live nor parked
        if not await self._rewind(stick, back_s):
            return False
        sent = await stick.key("KEYCODE_MEDIA_PAUSE")
        if not sent or not sent.ok:
            return False
        pb = await stick.playback(self._package(stick, target))
        if pb.get("state") not in ("playing", "paused"):
            return False
        self._set_hidden(target, True, Parked(replay_id, target, time.time(), pb.get("state") == "paused"))
        return True

    async def _unpark(self, stick: Stick) -> bool:
        """Roll the parked replay. If the stick didn't hold still, first rewind what it drifted."""
        p = self.parked
        step = float(self.cfg.get("replay.seek_step_s", 10))
        if p is not None and not p.paused and time.time() - p.at >= step / 2:
            return await self._rewind(stick, time.time() - p.at)   # ends with PLAY
        sent = await stick.key("KEYCODE_MEDIA_PLAY")
        return bool(sent and sent.ok)

    async def _still_on_visible(self, target: str) -> bool:
        if self.main is None:
            return False
        if self.on_target is None and self._adopt_once and target.startswith("youtubetv:"):
            # Just restarted and YouTube TV is already playing: adopt it when its media session
            # names this channel (the same evidence a tune is confirmed by). A different or
            # missing channel name keeps the chance for the next switch (a live tune lands on the live
            # edge, so a match later is still live); a replay ends it (see _run): a rewound
            # picture is never taken for live.
            pkg = self._package(self.main, target)
            match = channel_matcher(target)
            if not pkg or match is None:
                return False
            pb = await self.main.playback(pkg)
            if pb.get("pkg") == pkg and playback_ready(pb) and match(pb) is True:
                self._adopt_once = False
                log.info("adopting YouTube TV on screen after a restart: %s (%s)", target, pb.get("subtitle"))
                self.main.remember(target, pb)
                self.on_target = target
                return True
            return False
        if self.on_target is None and self._adopt_once and target.startswith("prime:") and self.target_names:
            # Just restarted with Prime on screen: Prime names no game, but its picture is readable,
            # so the broadcast score bug says which teams are playing (Nov 2: a restart re-tuned the
            # game already on screen and then fell back to a blowout).
            pkg = self._package(self.main, target)
            pb = await self.main.playback(pkg)
            if pkg and pb.get("pkg") == pkg and playback_ready(pb):
                text = (await self.main.ocr_text()).lower()
                names = [n.lower() for n in self.target_names(target) if n and len(n) >= 3]
                hits = {n for n in names if n in text}
                if len(hits) >= 2:
                    self._adopt_once = False
                    log.info("adopting Prime on screen after a restart: %s (score bug shows %s)", target, sorted(hits))
                    self.main.remember(target, pb)
                    self.on_target = target
                    return True
            return False
        if self.on_target is None and self._adopt_once:
            # Just restarted: the screen may already be playing this game (in an app whose deep
            # links we can't tell apart by channel). Re-tuning it would cut the game mid-play, and a
            # stick that isn't signed in to that app would fail the switch outright.
            self._adopt_once = False
            pkg = self._package(self.main, target)
            pb = await self.main.playback(pkg)
            title = (pb.get("title") or pb.get("description") or "").strip()
            # Prime reports only "PrimeVideo": that can't tell one game from another, so never adopt it.
            if (pkg and pb.get("pkg") == pkg and playback_ready(pb) and title
                    and title.lower() not in {"primevideo", "prime video", "null"}):
                log.info("adopting what's on screen after a restart: %s (%s)", target, pb.get("title") or pb.get("description"))
                self.main.remember(target, pb)
                self.on_target = target
                return True
            return False
        if self.on_target != target:
            return False
        pb = await self.main.playback(self._package(self.main, target))
        return self.main.still_on(target, pb)

    async def _sleeping(self, stick: Stick) -> bool:
        """Is this stick asleep? Only asked when we haven't seen it playing lately (it costs a round trip)."""
        if time.time() - self._awake_at.get(stick.host, 0.0) < AWAKE_TRUST_S:
            stick.mark_awake()
            return False
        r = await stick.shell("dumpsys power | grep -m1 mWakefulness=", timeout=4)
        # An unreadable power state is not evidence it is safe to wake a
        # hidden stick. WAKEUP can steal the visible HDMI input through CEC.
        asleep = not bool(r.ok and re.search(r"mWakefulness=Awake\b", r.out))
        if r.ok and not asleep:
            self._awake_at[stick.host] = time.time()
            # The tuner's own wake check trusts this, so a slow or failed second power read
            # can't make it press WAKEUP on the hidden stick (WAKEUP can grab the TV over CEC).
            stick.mark_awake()
        return asleep

    async def _wait_safe(self, may_cut: Callable[[], bool] | None) -> float | None:
        """Hold until nothing's on screen in the game we're leaving. Seconds waited; None = gave up."""
        t0 = time.monotonic()
        while may_cut is not None and not may_cut():
            if self._task is asyncio.current_task():
                self.stage.update(stage="waiting_safe_exit", reason="waiting for the current screen's safe exit window")
            if time.monotonic() - t0 >= MAX_SAFE_WAIT_S:
                return None
            await asyncio.sleep(SAFE_POLL_S)
        return time.monotonic() - t0

    async def execute(self, target: str | None, replay_back_s: float | None = None,
                      may_cut: Callable[[], bool] | None = None, replay_id: str | None = None,
                      issued_at: float | None = None) -> Outcome:
        """replay_back_s is "rewind this far from the live edge" as of issued_at (the decision);
        every second since (queued, tuning, waiting) is added so the replay still lands before the snap."""
        t0 = time.time()
        back_at = issued_at or t0
        self._exit_check = may_cut
        if not target:
            return Outcome(False, None, 0.0, "no channel for this game", True)
        if not self.live_control:
            why = "dry run" if self.cfg.get("mode.dry_run", True) else "no stick connected"
            self.on_target = target
            return Outcome(True, target, 0.0, why, True, time.time())
        if self.input_unknown:
            return Outcome(False, target, 0.0, "TV input is unknown; verify the visible stick before resuming", False)
        if self.two_stick:
            out = await self._execute_two(target, replay_back_s, may_cut, t0, replay_id, back_at)
            if out is not None:
                return out
        # Recheck after waiting for the controller lock and after a failed
        # HDMI attempt: the safe gap may have closed during those operations.
        if await self._wait_safe(may_cut) is None:
            return Outcome(False, target, time.time() - t0,
                           "no safe moment to cut — kept the current game", False, screen_unchanged=True)
        if not self.live_control:
            return Outcome(False, target, 0.0, "TV control was disabled", True)
        return await self._execute_one(target, replay_back_s, t0, back_at)

    async def _execute_one(self, target, replay_back_s, t0, back_at=None) -> Outcome:
        if not replay_back_s and await self._still_on_visible(target):
            return Outcome(True, target, 0.0, "already on it", False, time.time(), path="already")
        main = self.main
        self.on_target = None   # unknown until this tune is confirmed (a cancelled tune leaves it unknown)
        res = await self._tune(main, target, self._exit_check)
        if not res.ok:
            self._blackout_check(target, res)
            return Outcome(False, target, time.time() - t0, res.error + " | " + "; ".join(res.steps[-3:]), False,
                           path="single", screen_unchanged=getattr(res, "screen_changed", None) is False)
        if replay_back_s:
            # The live edge moved on while we tuned: rewind that much further to land before the snap.
            if not await self._rewind(main, replay_back_s + (time.time() - (back_at or t0))):
                return Outcome(False, target, time.time() - t0, "replay seek failed; position unknown", False,
                               path="single")
        self.on_target = None if replay_back_s else target
        self._visible_bad_since = None
        return Outcome(True, target, time.time() - t0, "; ".join(res.steps[-2:]), False, time.time(), path="single")

    async def _execute_two(self, target, replay_back_s, may_cut, t0, replay_id=None, back_at=None) -> Outcome | None:
        hid = self.hidden
        if hid is None:
            return None
        replay = bool(replay_back_s)
        visible_untouched = True
        if not replay and await self._still_on_visible(target):
            return Outcome(True, target, 0.0, "already on it", False, time.time(), path="already")
        no_moment = "no safe moment to cut — kept the current game"
        if not (replay and self._parked_for(target, replay_id)) and not self._live_on_hidden(target) \
                and not self._plays(hid, target) and self._plays(self.main, target):
            # The hidden stick can't play this app (e.g. signed out of Peacock there) but the
            # visible one can: tune the visible stick at a safe moment instead of failing.
            log.info("%s can't play %s: tuning the visible stick instead", hid.host, target)
            return None
        if replay and self._parked_for(target, replay_id):
            path = "parked replay"
        elif not replay and self._live_on_hidden(target) and hid.still_on(
                target, await hid.playback(self._package(hid, target))):
            path = "preloaded"
        else:
            # A standby that stopped (a stick that lost the TV input can pause its app) gets tuned
            # again here instead of failing the switch at flip time.
            path = "tuned"
            sleeping = await self._sleeping(hid)
            if sleeping:
                # Waking a Fire TV makes it take the TV input (CEC): only ever at a safe moment.
                if await self._wait_safe(may_cut) is None:
                    return Outcome(False, target, time.time() - t0, no_moment, False, path=path,
                                   waited=MAX_SAFE_WAIT_S, screen_unchanged=True)
                visible_untouched = False  # waking can already change the TV input via CEC
            self._set_hidden(target, False)
            res = await self._tune(hid, target, may_cut if sleeping else None)
            if not res.ok:
                self._set_hidden(None, False)
                self._blackout_check(target, res)
                return Outcome(False, target, time.time() - t0, res.error, False, path=path,
                               screen_unchanged=visible_untouched)
            self._set_hidden(target, True)
            if replay:
                # Park it: a replay rolling while we wait for a safe moment would run past the play.
                if not await self._park(hid, target, replay_back_s + (time.time() - (back_at or t0)), replay_id or ""):
                    return Outcome(False, target, time.time() - t0, "replay seek failed; kept current game", False,
                                   path=path, screen_unchanged=visible_untouched)
        self.latency.note_preloaded(target.split(":", 1)[0], path != "tuned")
        work = time.time() - t0
        # Wait for a safe moment on what's visible, THEN flip. This is where "never mid-play" is enforced
        # to the second, because the cut happens at the flip.
        waited = await self._wait_safe(may_cut)
        if waited is None:
            return Outcome(False, target, work, no_moment, False, path=path, waited=MAX_SAFE_WAIT_S,
                           screen_unchanged=visible_untouched)
        if not self.live_control:
            return Outcome(False, target, work, "TV control was disabled", True, path=path)
        t1 = time.time()
        self._flipping = True
        try:
            if replay:
                if not await self._unpark(hid):
                    self._set_hidden(None, False)
                    return Outcome(False, target, time.time() - t0, "replay resume failed; kept current game", False,
                                   path=path, screen_unchanged=visible_untouched)
            else:
                # Cached readiness is not proof the stream survived a pause,
                # app error, or a user change on the hidden stick.
                pb = await hid.playback(self._package(hid, target))
                for _ in range(STANDBY_REREADS):
                    # Prime's session can be missing from one read without the player stopping
                    # (soak, Nov 2: a fresh tune read "none" once at flip time and the whole switch
                    # failed). Only a missing session / failed read is re-asked; a paused or
                    # different stream is believed at once.
                    if hid.still_on(target, pb) or not (pb.get("read_ok") is False or pb.get("state") == "none"):
                        break
                    await asyncio.sleep(STANDBY_REREAD_S)
                    pb = await hid.playback(self._package(hid, target))
                if not hid.still_on(target, pb):
                    self._set_hidden(None, False)
                    return Outcome(False, target, time.time() - t0, "standby stream is no longer confirmed", False,
                                   path=path, screen_unchanged=visible_untouched)
            # Readback and replay reseeking may have consumed the whole gap.
            if may_cut is not None and not may_cut():
                return Outcome(False, target, time.time() - t0, "safe exit window closed during preparation", False,
                               path=path, screen_unchanged=visible_untouched)
            flipped = await self._flip_to(hid)
        finally:
            self._flipping = False
        if not flipped:
            self.two_stick_ok = False
            self._two_stick_retry_at = time.time() + TWO_STICK_RETRY_S
            self._set_hidden(None, False)
            self.note = f"Two-stick mode off: couldn't switch TV input ({self.tv.last_error or 'unknown'})"
            log.warning(self.note)
            self._mark_input_unknown(self.on_target)
            self.on_target = None
            return Outcome(False, target, time.time() - t0, self.note + "; visible input is unconfirmed", False,
                           path=path)
        old_target, old_stick = self.on_target, self.main
        self.active = self.sticks.index(hid)
        self._flip_gen += 1
        self.on_target = None if replay else target
        self._visible_bad_since = None
        # The stick we just left pauses YouTube TV / Peacock when it loses the TV input (seen on both
        # Fire TV sticks), so it isn't a ready standby yet. Reload that game at the live edge in the
        # background: "back"/"home" is then one flip again, and nobody comes back to a stream minutes
        # behind. Prime keeps its player: a Prime game still playing live there is kept as is
        # (_refresh_left -> _kept_playing), so going back is HOME + REORDER_TO_FRONT, not a ~33 s tune.
        # Nothing ever presses HOME or WAKEUP on it (either could pull the TV back to it).
        self._set_hidden(old_target, False)
        self._cancel_refresh()
        if old_stick is not None:
            self._awake_at[old_stick.host] = time.time()
        flip_s, took = self._flip_s, work + (time.time() - t1)
        detail = f"flipped to {self.hdmi.get(hid.host)} ({path})"
        resume_s, how = None, None
        home = self._home_flipped and not replay
        if home:
            # HOME backgrounded the app and it was relaunched: the TV already shows this stick, so
            # nothing is left to protect. Confirm the game is playing again BEFORE reporting success
            # (MEDIA_PLAY, then one fresh deep link if it landed on its home page). Not cancellable,
            # like the flip: a newer command waits for this instead of leaving the screen unknown.
            r0 = time.monotonic()
            self._flipping = True
            self.stage.update(stage="resuming", reason="confirming the game is playing after the input change")
            try:
                # Prime's player needs ~15 s to report again: at 6 s it read "none" and was re-linked
                # (page + Watch + Watch Live, ~33 s; 22:41 and 22:45 on Nov 2, one switch timed out at 75 s).
                how = await self._resume_playback(hid, target, self._resume_wait(target))
            except Exception:
                log.exception("resume check after the flip crashed")
            finally:
                self._flipping = False
            resume_s = time.monotonic() - r0
            if how is None:
                # The input DID change (that's the cut); the picture isn't confirmed. The visible-stick
                # watchdog keeps retrying, and the next switch re-tunes instead of trusting it.
                detail += "; game not confirmed playing yet (watchdog will retry)"
                self._visible_bad_since = time.time()
            elif how != "auto":
                detail += f"; resumed by {how.replace('_', ' ')}"
        self._book_flip(hid, target, flip_s, resume_s, how)
        if old_target and not replay:
            self._refresh_task = asyncio.create_task(self._refresh_left(old_target, old_stick, time.time()))
        self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
        return Outcome(True, target, took, detail, False, time.time(), path=path, waited=waited,
                       flip_s=None if flip_s is None else round(flip_s, 2),
                       resume_s=None if resume_s is None else round(resume_s, 2),
                       resumed=(how is not None) if home else None)

    async def _flip_to(self, stick: Stick) -> bool:
        """Make the TV show this stick. HOME on the stick first (it announces <Active Source> over
        CEC and the TV follows; no tokens needed), then the TV's own network remote, then SmartThings.
        Records self._flip_s (HOME -> active source) when the HOME path worked."""
        t0 = time.monotonic()
        self.stage.update(stage="verifying_input", reason="requesting and verifying the display input")
        self._home_flipped = False
        self._flip_s = None
        ok = False
        requested = self.hdmi.get(stick.host)
        if not requested:
            self.tv.last_error = "no input configured for this stick"
            return False
        if self._exit_check is not None and not self._exit_check():
            self.tv.last_error = "safe exit window closed before CEC command"
            return False
        # Fire OS has no shell CEC command and blocks the system call (HDMI_CEC permission),
        # but HOME makes the stick announce <Active Source> over the HDMI cable, and the TV
        # follows. HOME backgrounds the preloaded app, so it is relaunched right after; the
        # caller then confirms the game is really playing again (_resume_playback).
        # Always tried first: it's the only path that works on these sticks, so a rejected
        # onetouchplay below must never bar it (that turned one slow flip into ~10 min of failures).
        if await self._home_flip(stick):
            self.latency.record("flip", self._flip_s if self._flip_s is not None else time.monotonic() - t0)
            self._awake_at[stick.host] = time.time()
            self._home_flipped = True
            return True
        if time.time() >= self._cec_bad.get(stick.host, 0.0):
            r = await stick.shell("cmd hdmi_control onetouchplay", timeout=5)
            # Fire OS can return exit 0 while explaining it has no shell
            # implementation. That is a definite no-op, not a sent CEC request.
            # Generic errors/timeouts remain ambiguous and must not be retried.
            rejected = any(marker in r.out.lower() for marker in
                           ("unknown command", "no shell command implementation"))
            if not rejected:
                # Success acknowledges a request, not which input the display
                # actually shows. A lost reply also forbids a second mutation.
                observed = await self.tv.input_status()
                ok = observed == requested
                if not ok:
                    self.tv.last_error = "CEC request sent but display input could not be verified"
                    return False
            else:
                self._cec_bad[stick.host] = time.time() + CEC_RETRY_S
        if not ok:
            if self._exit_check is not None and not self._exit_check():
                self.tv.last_error = "safe exit window closed before display command"
                return False
            ok = await self.tv.set_input(requested)
        if ok:
            self._flip_s = time.monotonic() - t0
            self.latency.record("flip", self._flip_s)
            self._awake_at[stick.host] = time.time()
        return ok

    async def _is_active_source(self, stick: Stick) -> bool | None:
        """Does this stick say it is the TV's HDMI-CEC active source? None = couldn't ask."""
        r = await stick.shell(ACTIVE_SOURCE_CMD, timeout=3)
        if not r.ok and not r.out.strip():
            return None
        return r.out.strip().startswith("1")

    async def _await_active_source(self, stick: Stick, t0: float, deadline_s: float) -> float | None:
        """Poll fast until the stick is the active source. Seconds since t0 when it was, else None."""
        while True:
            if await self._is_active_source(stick):
                return time.monotonic() - t0
            if time.monotonic() - t0 >= deadline_s:
                return None
            await asyncio.sleep(FLIP_POLL_S)

    async def _relaunch(self, stick: Stick, pkg: str | None, home_at: float, target: str | None = None) -> None:
        """Bring the app HOME backgrounded back to the front, showing what it was playing.
        Re-sending the game's own link (no task flags) brings its existing task forward with the
        player on top ("current task has been brought to the front"). Launching the app's main
        screen instead opened Prime's home page, not the game (Nov 2)."""
        if not pkg:
            return
        wait = RELAUNCH_AFTER_S - (time.monotonic() - home_at)
        if wait > 0:
            await asyncio.sleep(wait)
        url = None
        if target:
            app, ref = self._ref(target)
            tuner = TUNERS.get(app)
            if tuner is not None and ref:
                try:
                    url = tuner.url(ref)
                except Exception:
                    url = None
        if url:
            await stick.shell(f"am start -a android.intent.action.VIEW -d '{url}' {pkg} >/dev/null 2>&1", timeout=8)
        else:
            await stick.shell(f"am start -n $(cmd package resolve-activity --brief {pkg} | tail -1) >/dev/null 2>&1",
                              timeout=8)

    async def _home_flip(self, stick: Stick, target: str | None = None) -> bool:
        """HOME -> <Active Source> -> TV switches to this stick; then relaunch its app.
        Verified on the stick's own CEC state (the TV isn't reachable over Wi-Fi), polled every
        ~0.12 s so a fast TV isn't held to a fixed wait. self._flip_s = HOME -> active source."""
        if target is None:
            target = self.hidden_target if stick is self.hidden else self.on_target
        pkg = self._package(stick, target) if target else None
        if target and target.startswith("prime:"):
            return await self._home_flip_prime(stick, pkg, target)
        home_at = time.monotonic()
        r = await stick.shell("input keyevent KEYCODE_HOME", timeout=8)
        if not r.ok:
            # Unsent or unknown: the app may already be in the background, so put it back.
            await self._relaunch(stick, pkg, home_at, target)
            return False
        # The relaunch runs alongside the poll (as before, ~0.6 s after HOME), so a TV that is slow
        # to follow doesn't also delay the picture coming back.
        relaunch = asyncio.create_task(self._relaunch(stick, pkg, home_at, target))
        try:
            took = await self._await_active_source(stick, home_at, FLIP_DEADLINE_S)
        finally:
            await asyncio.gather(relaunch, return_exceptions=True)
        if took is None:
            log.warning("HOME flip on %s: stick never became active source within %.1fs", stick.host,
                        FLIP_DEADLINE_S)
            return False
        self._flip_s = took
        return True

    async def _home_flip_prime(self, stick: Stick, pkg: str | None, target: str) -> bool:
        """Prime (measured on the sticks, Nov 2): the first HOME can be swallowed by its player, and
        bringing the app back before the TV has switched cancels the switch. Re-sending a link opens
        the game's page (Watch + Watch Live again, ~30 s). So: HOME (twice if the first didn't
        switch the TV), wait for <Active Source>, THEN bring the player's own screen to the front
        (REORDER_TO_FRONT): the game is still playing there."""
        r = await stick.shell("dumpsys activity activities | grep -m1 mResumedActivity", timeout=5)
        m = re.search(r"u0 (com\.amazon\.firebat/[\w.$]+)", r.out if r.ok else "")
        comp = m.group(1) if m else None
        home_at = time.monotonic()
        took = None
        for attempt in range(2):
            if not (await stick.shell("input keyevent KEYCODE_HOME", timeout=8)).ok:
                break
            # The second HOME gets a full window of its own: measured from the first press it had
            # only ~2 s, and a TV a beat slow to follow failed the flip -> two-stick mode off and an
            # unknown input (22:16, 22:20, 22:31 on Nov 2). flip_s still counts from the first HOME.
            limit = 2.5 if attempt == 0 else (time.monotonic() - home_at) + FLIP_DEADLINE_S
            took = await self._await_active_source(stick, home_at, limit)
            if took is not None:
                break
        reordered = False
        if comp:
            r = await stick.shell(f"am start -f 0x00020000 -n {comp} 2>&1", timeout=8)
            # Prime's in-app PlaybackActivity (the 4K Plus stick) is not exported: the reorder is
            # refused ("Permission Denial ... not exported") and the TV sat on the Fire TV home
            # screen until a full re-link (soak, Nov 3). The game's link brings Prime's task back
            # (its detail page, then pressed through by _resume_playback).
            reordered = r.ok and not any(m in r.out for m in ("Permission Denial", "not exported", "Error"))
            if not reordered:
                log.info("%s: Prime's player screen can't be brought back (%s); re-opening the game's page",
                         stick.host, (r.out.strip().splitlines() or ["no reply"])[-1][:100])
        if not reordered:
            await self._relaunch(stick, pkg, home_at, target)
        if took is None:
            log.warning("HOME flip on %s (Prime): stick never became active source", stick.host)
            return False
        self._flip_s = took
        return True

    # --- after the flip: is the game playing again? ----------------------------------------

    def _plays_target(self, stick: Stick, target: str, pb: dict, pkg: str | None) -> bool:
        """Is this report the target's stream, moving? Same app, playing with a positive speed, and
        no sign it's something else (YouTube TV names its channel; a remembered title that changed
        means a hero preview or trailer autoplaying on the app's home page)."""
        if not playback_ready(pb):
            return False
        if pkg and pb.get("pkg") and pb.get("pkg") != pkg:
            return False
        match = channel_matcher(target)
        verdict = match(pb) if match else None
        if verdict is not None:
            return verdict
        t = stick.tuned or {}
        title = ((t.get("report") or {}).get("title") or "").strip() if t.get("target") == target else ""
        now = (pb.get("title") or "").strip()
        return not (title and now and title != now)

    @staticmethod
    def _resume_wait(target: str) -> float:
        """How long a relaunched app gets to play again on its own after a HOME flip."""
        return PRIME_RESUME_WAIT_S if target.startswith("prime:") else RESUME_WAIT_S

    async def _await_playing(self, stick: Stick, target: str, pkg: str | None, wait_s: float,
                             paused_s: float | None = None) -> dict | None:
        """The target playing within wait_s, else None. With paused_s: also None once the player has
        said "paused" that long (pressing play beats waiting out a long grace)."""
        t0 = time.monotonic()
        end, paused_at = t0 + wait_s, None
        while True:
            pb = await stick.playback(pkg)
            if self._plays_target(stick, target, pb, pkg):
                return pb
            now = time.monotonic()
            if pb.get("state") == "paused" and (not pkg or pb.get("pkg") in (None, pkg)):
                paused_at = now if paused_at is None else paused_at
                if paused_s is not None and now - paused_at >= paused_s:
                    return None
            else:
                paused_at = None
            if now >= end:
                return None
            await asyncio.sleep(RESUME_POLL_S)

    async def _clear_screen_prompt(self, stick: Stick, target: str, pkg: str | None) -> bool:
        """A "Who's watching?" / "Still watching?" the stick's UI dump can't see (YouTube TV):
        cleared from a screenshot by the tuner (redzone/tuners/screen_prompts.py)."""
        clear = getattr(TUNERS.get(target.split(":", 1)[0]), "clear_screen_prompt", None)
        if clear is None or not pkg:
            return False
        steps: list[str] = []
        try:
            how = await clear(stick, pkg, steps)
        except Exception:   # a screenshot read must never break the watchdog
            log.exception("screen prompt check failed on %s", stick.host)
            return False
        if steps:
            log.info("%s: %s", stick.host, "; ".join(steps))
        return how is not None

    async def _resume_prime(self, stick: Stick, target: str, pkg: str, wait_s: float) -> str | None:
        """Prime after a HOME flip (soak, Nov 2): REORDER_TO_FRONT lands on the game's detail page
        with no session ("none") and it never plays again by itself; the old answer (wait 6 s, then
        a fresh deep link) left ~50 s of a static page on screen. Now: the moment the session has
        been gone PRIME_PAGE_AFTER_S, read the screen and press through Watch -> Watch Live (~5 s).
        A player that does come back by itself (or pauses and resumes) is just waited for."""
        tuner = TUNERS.get("prime")
        press = getattr(tuner, "resume_from_page", None)
        t0 = time.monotonic()
        gone_since, checks, pressed = None, 0, False
        while True:
            pb = await stick.playback(pkg)
            if self._plays_target(stick, target, pb, pkg):
                stick.remember(target, pb)
                return "watch_live" if pressed else "auto"
            now = time.monotonic()
            if now - t0 >= wait_s:
                return None
            gone = pb.get("read_ok") is not False and pb.get("state") in ("none", "stopped")
            gone_since = (gone_since or now) if gone else None
            if (press is not None and not pressed and gone_since is not None
                    and now - gone_since >= PRIME_PAGE_AFTER_S and checks < PRIME_PAGE_CHECKS):
                checks += 1
                steps: list[str] = []
                try:
                    pressed = await press(stick, pkg, steps)
                except Exception:   # a screen read must never break the resume
                    log.exception("Prime press-through after the flip failed on %s", stick.host)
                if steps:
                    log.info("%s: %s", stick.host, "; ".join(steps))
                if pressed:
                    # The stream needs ~3.5 s after Watch Live: give it that long past the deadline.
                    wait_s = max(wait_s, time.monotonic() - t0 + PRIME_LIVE_WAIT_S)
                gone_since = None
            await asyncio.sleep(RESUME_POLL_S)

    async def _resume_playback(self, stick: Stick, target: str, first_wait_s: float) -> str | None:
        """Make the stick on screen play `target` again: wait for it, then MEDIA_PLAY (a paused
        player), then a prompt read from a screenshot, then one fresh deep link. Returns how it came
        back ("auto", "media_play", "prompt", "relink"), or None if it didn't. Only ever for the stick the TV is showing."""
        pkg = self._package(stick, target)
        prime = target.startswith("prime:")
        if prime and pkg:
            # Prime after a HOME flip: wait for it (a stale "paused" that plays again by itself is
            # waited out, never pressed), and press through the detail page it lands on (soak, Nov 2).
            how = await self._resume_prime(stick, target, pkg, max(first_wait_s, PRIME_RESUME_WAIT_S))
            if how:
                return how
            first_wait_s = 0.0
        # Never the "paused" shortcut for Prime: the older stick's Prime pauses on input loss, keeps
        # reporting that stale "paused" after REORDER_TO_FRONT and plays again by itself at ~10 s,
        # while MEDIA_PLAY on it went unanswered and led to a re-link (19:59 and 23:16 on Nov 2;
        # the 23:16 one became a 75 s switch failure). It waits out its whole grace.
        paused_s = RESUME_PAUSED_S if first_wait_s > RESUME_WAIT_S and not prime else None
        pb = await self._await_playing(stick, target, pkg, first_wait_s, paused_s)
        if pb is not None:
            stick.remember(target, pb)
            return "auto"
        last = await stick.playback(pkg)
        if last.get("state") == "paused" and (not pkg or last.get("pkg") in (None, pkg)):
            log.info("%s: %s is paused on screen; pressing play", stick.host, target)
            await stick.key("KEYCODE_MEDIA_PLAY")
            pb = await self._await_playing(stick, target, pkg, RESUME_PLAY_WAIT_S)
            if pb is not None:
                stick.remember(target, pb)
                return "media_play"
        # The app may be up but behind its own prompt (YouTube TV's "Who's watching?" after a
        # restart, "Still watching?"). Only after the quick fixes: a screenshot read takes ~2 s.
        if await self._clear_screen_prompt(stick, target, pkg):
            pb = await self._await_playing(stick, target, pkg, RESUME_PLAY_WAIT_S)
            if pb is not None:
                stick.remember(target, pb)
                return "prompt"
        # Read playback again right before a re-link (never act on a stale read): a player that
        # came back by itself meanwhile must not be torn down for a ~33 s re-tune on screen.
        last = await stick.playback(pkg)
        if self._plays_target(stick, target, last, pkg):
            stick.remember(target, last)
            return "auto"
        log.warning("%s isn't playing %s (%s); loading it again", stick.host, target, last.get("state"))
        try:
            # Prime needs its page, "Watch" and "Watch Live" before video: ~33 s on these sticks (Nov 2).
            limit = 55.0 if target.startswith("prime:") else RELINK_MAX_S
            res = await asyncio.wait_for(self._tune(stick, target), limit)
        except asyncio.TimeoutError:
            log.warning("re-loading %s on %s timed out", target, stick.host)
            return None
        if res.ok:
            return "relink"
        log.warning("re-load of %s on %s failed: %s", target, stick.host, res.error)
        return None

    def _book_flip(self, stick: Stick, target: str | None, flip_s: float | None, resume_s: float | None,
                   how: str | None) -> None:
        """The latency book for the phone: input change and picture-back, measured separately."""
        if resume_s is not None and how is not None:
            self.latency.record("resume", resume_s)
        self.last_flip = {"at": time.time(), "stick": stick.host, "hdmi": self.hdmi.get(stick.host),
                          "target": target, "flip_s": None if flip_s is None else round(flip_s, 2),
                          "resume_s": None if resume_s is None else round(resume_s, 2),
                          "resumed": how is not None, "how": how or "unconfirmed"}

    # --- the hidden stick between switches ---------------------------------------------

    def preload(self, target: str | None, back_s: float | None = None, replay_id: str | None = None,
                *, refresh: bool = False) -> bool:
        """Put the hidden stick on `target`: live, or parked on a replay (back_s from the live edge,
        replay_id). Returns True if a preload started. WHAT to preload is the engine's call
        (standby.Planner); this only refuses what can't or shouldn't happen right now.
        refresh=True is _refresh_left reloading the game we just left: it skips the preload gap and
        doesn't use it up, and the planner wins over it — a planner preload of another target
        cancels it, even mid-tune."""
        if not (self.live_control and self.two_stick and target) or self.input_unknown:
            return False   # unknown input: the "hidden" stick may be the one on screen
        if replay_id is None and (target == self.on_target or self._live_on_hidden(target)):
            return False
        if replay_id is not None and self._parked_for(target, replay_id):
            return False
        now = time.time()
        if not refresh and self.can_tune(target) and self._plays(self.hidden, target):
            self._wish = (target, now)   # what the planner wants there, even if refused just now
        displace = None
        if not refresh and self._refreshing not in (None, target) and self._preload_task \
                and not self._preload_task.done():
            displace = self._preload_task   # a refresh of the game we left: the planner's pick wins
        busy = bool(self._task and not self._task.done()) if displace else self._busy()
        if busy or now < self._asleep_until or not self.can_tune(target) or not self._plays(self.hidden, target) \
                or (not refresh and now - self._last_preload < MIN_PRELOAD_GAP_S):
            return False
        if displace is not None:
            log.info("planner wants %s on the hidden stick: dropping the refresh of %s", target, self._refreshing)
            displace.cancel()
        if not refresh:
            self._last_preload = now
            self._cancel_refresh()
        self._preloading = (target, replay_id)
        self._refreshing = target if refresh else None
        self._preload_task = asyncio.create_task(self._preload(target, back_s, replay_id, now))
        return True

    async def _preload(self, target: str, back_s: float | None, replay_id: str | None, t0: float) -> None:
        try:
            async with self._lock:
                hid = self.hidden
                if hid is None:
                    return
                if await self._sleeping(hid):
                    # Waking it would make it grab the TV input mid-game. The next real switch wakes it.
                    self._asleep_until = time.time() + ASLEEP_RECHECK_S
                    log.info("standby stick %s is asleep — not waking it just to preload", hid.host)
                    return
                if replay_id is None or not self._live_on_hidden(target):
                    self._set_hidden(target, False)
                    res = await self._tune(hid, target)
                    if not res.ok:
                        self._set_hidden(None, False)
                        self._failed[target] = time.time() + PRELOAD_BACKOFF_S
                        self._blackout_check(target, res)
                        if self.on_preload_fail:
                            self.on_preload_fail(target, res.error + " | " + "; ".join(res.steps[-3:]),
                                                 host=hid.host)
                        return
                    self._set_hidden(target, True)
                if replay_id is not None:
                    if not await self._park(hid, target, (back_s or 0.0) + (time.time() - t0), replay_id):
                        self._failed[target] = time.time() + PRELOAD_BACKOFF_S
                        return
                self._verified_at = time.time()
        except asyncio.CancelledError:
            self._set_hidden(None, False)   # stopped mid-tune: we don't know what it's showing
            raise
        finally:
            if self._preload_task is asyncio.current_task():   # not a preload that displaced this one
                self._preloading, self._refreshing = None, None

    def _maybe_reseek(self) -> None:
        """A parked replay that didn't hold still drifts forward: pull it back every ~20s (from
        keepalive), so the fix-up at the flip is a press or two, not a long run of rewinds."""
        p = self.parked
        step = float(self.cfg.get("replay.seek_step_s", 10))
        if not (self.live_control and self.two_stick) or self.input_unknown or p is None or p.paused or self._busy() \
                or time.time() - p.at < 2 * step or not self._parked_for(p.target, p.replay_id):
            return
        self._preloading = (p.target, p.replay_id)
        self._preload_task = asyncio.create_task(self._reseek(p))

    async def _reseek(self, p: Parked) -> None:
        try:
            async with self._lock:
                hid = self.hidden
                if hid is not None and self.parked is p:
                    await self._park(hid, p.target, time.time() - p.at, p.replay_id)
        except asyncio.CancelledError:
            self._set_hidden(None, False)
            raise
        finally:
            self._preloading = None

    def _verify_every(self) -> float:
        return float(self.cfg.get("two_stick.verify_s", 15))

    async def verify_standby(self) -> None:
        """Is the hidden stick still showing what we think? Losing the TV input can pause a stick
        (CEC), and an app can drop back to its menu. If it isn't, stop counting it as ready."""
        if not (self.live_control and self.two_stick) or self._busy():
            return
        hid, target, parked = self.hidden, self.hidden_target, self.parked
        if hid is None or not target or not self.hidden_ready:
            return
        if time.time() - self._verified_at < self._verify_every():
            return
        pb = await hid.playback(self._package(hid, target))
        self._verified_at = time.time()
        if self.hidden_target != target or self.parked is not parked or self._busy():
            return   # it changed while we looked
        if pb.get("read_ok") is False:
            # adb didn't answer: that says nothing about the stream. Ask again soon, keep it.
            self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
            return
        state = pb.get("state")
        if state in ("playing", "buffering", "paused"):
            self._awake_at[hid.host] = time.time()
        expect = ("paused",) if parked and parked.paused else ("playing", "buffering")
        prime_live = parked is None and target.startswith("prime:")
        if prime_live and state not in expect and state not in ("paused", "stopped") \
                and self._standby_miss != target:
            # Prime's session can be missing from one read without the player stopping (it read
            # "none" for seconds after a reorder, Nov 2), and a dropped Prime standby costs a ~33 s
            # reload: one more read, a few seconds from now, before believing it.
            self._standby_miss = target
            self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
            return
        self._standby_miss = None
        if state not in expect:
            log.info("standby %s is %s, not %s — it'll be reloaded", target, state, "/".join(expect))
            self._set_hidden(None, False)
        elif prime_live and self._jumped(hid, target, pb):
            # Every Prime game reads "PrimeVideo" on a wall-clock position, so "playing" alone can't
            # tell live from a player that paused and resumed behind the live edge: its position
            # must still keep pace with the stick's clock (~1% drift is normal; 2% is allowed).
            log.info("standby %s is playing but fell off the live edge — it'll be reloaded", target)
            self._set_hidden(None, False)

    def verify_hidden(self, target: str | None) -> asyncio.Task | None:
        """Pre-game check (redzone/plan.py): tune `target` on the HIDDEN stick and report whether it
        played. Never the stick on screen; runs as a preload, so any switch cancels it. None = it
        can't run now (dry run, one stick, busy, asleep, no link). The task's result is the
        TuneResult, or None when the hidden stick was asleep (it is never woken for a check)."""
        if not (self.live_control and self.two_stick and target) or self.hidden is None or self.input_unknown:
            return None
        if self._busy() or time.time() < self._asleep_until or not self.can_tune(target):
            return None
        self._cancel_refresh()
        self._preloading, self._refreshing = (target, None), None
        self._preload_task = asyncio.create_task(self._verify_hidden(target))
        return self._preload_task

    async def _verify_hidden(self, target: str) -> TuneResult | None:
        try:
            async with self._lock:
                hid = self.hidden
                if hid is None:
                    return TuneResult(False, target, 0.0, [], "two-stick mode off")
                if await self._sleeping(hid):
                    self._asleep_until = time.time() + ASLEEP_RECHECK_S
                    return None
                self._set_hidden(target, False)
                res = await self._tune(hid, target)
                if res.ok:
                    self._set_hidden(target, True)
                else:
                    self._set_hidden(None, False)
                    self._blackout_check(target, res)
                self._verified_at = time.time()
                return res
        except asyncio.CancelledError:
            self._set_hidden(None, False)   # stopped mid-tune: we don't know what it's showing
            raise
        finally:
            if self._preload_task is asyncio.current_task():
                self._preloading, self._refreshing = None, None

    async def scan_sunday_ticket(self, codes: list[str], games: list, on_found=None, *, may_run=None) -> dict[str, str]:
        """Flip through the Sunday Ticket channels and read each one's game title off the screen.
        Uses the hidden stick in two-stick mode (you never see it), else the main stick (you will)."""
        from redzone.channels import match_title
        stick = self.hidden or self.main
        found: dict[str, str] = {}
        if stick is None or not codes:
            return found
        tuner = TUNERS["youtubetv"]

        def allowed():
            # Unknown input: the stick we'd scan on may be the one on screen.
            return (not self.cfg.get("mode.dry_run", True) and not self.input_unknown
                    and (may_run is None or may_run()))

        async with self._lock:
            if not allowed():
                return found
            if stick is self.hidden and await self._sleeping(stick):
                # The tuner would press WAKEUP, and a Fire TV that wakes grabs the TV input over CEC.
                log.info("Sunday Ticket scan skipped: the hidden stick %s is asleep", stick.host)
                return found
            try:
                for code in codes:
                    if not allowed():
                        break
                    res = await tuner.tune(stick, code, f"youtubetv:st:{code}", may_cut=allowed)
                    if not allowed():
                        break
                    if not res.ok:
                        continue
                    for keys in ((), ("KEYCODE_DPAD_DOWN",)):  # title shown by the player overlay
                        if not allowed():
                            break
                        if keys:
                            await stick.key(*keys)
                            await asyncio.sleep(1.0)
                        text = await stick.screen_text()
                        if not allowed():
                            break
                        g = match_title(text, games)
                        if g:
                            found[g.game_id] = code
                            if on_found:
                                on_found(g, code)
                            break
                    log.info("ST scan %s -> %s (%s)", code, found and list(found)[-1], "ok" if res.ok else res.error)
            finally:
                if stick is self.hidden:
                    self._set_hidden(None, False)
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
                        "ready": self.hidden_ready if s is self.hidden else None,
                        "parked_replay": bool(self.parked) if s is self.hidden else None})
        return out
