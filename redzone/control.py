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
- A flip is the TV's own input change first (redzone/tv.py: the local Samsung remote or
  SmartThings, whichever scripts/tv_setup.py proved): no HOME, no launcher, no relaunch, and the
  stick we flip to must still be playing right after. Only if that path isn't set up or fails:
  HOME -> active source (polled every ~0.12 s), then the relaunched app must be playing the game
  (MEDIA_PLAY, then one fresh deep link) before the switch reports;
  flip_s and resume_s are booked separately for the phone.
- A stick whose HDMI-CEC is off (scripts/stick_cec.py, so losing the input never pauses it) gets
  CEC switched back on only for a HOME fallback onto it, and off again once the TV shows it.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from redzone import config
from redzone.channels import ChannelMap
from redzone.tuners.adb import STICK_HUNG, Stick, adb_available, playback_ready, same_stream
from redzone.tuners import prime_multiview as pmv
from redzone.tuners.tuners import TUNERS, PrimeMultiviewTuner, TuneResult, channel_matcher
from redzone.tv import SamsungTV

log = logging.getLogger(__name__)
DEVICES = Path(__file__).resolve().parent.parent / "data" / "devices.json"
MAX_SAFE_WAIT_S = 25.0     # how long a flip may wait for a safe moment before giving up
SAFE_POLL_S = 0.2          # re-check for that moment this often (a cheap local computation)
PRELOAD_BACKOFF_S = 120.0  # a preload that failed isn't retried for this long
STANDBY_REPAUSE_N = 3      # [APP-04] the same standby paused off screen this many times...
STANDBY_REPAUSE_S = float(config.default("two_stick.standby_repause_s", 600))  # ...within this long is not CEC: the app is blocked there (a forced update)
MIN_PRELOAD_GAP_S = 5.0    # never start preloads back to back faster than this
STANDBY_NOT_READY = "standby not ready"   # [jump mode] a jump that may only flip to a ready standby found none
CEC_RETRY_S = 600.0        # a stick whose CEC flip failed goes straight to the TV remote this long
CEC_SYNC_S = 30.0          # re-ask the sticks which one is on screen this often (when idle)
RESUME_WAIT_S = 6.0        # after a HOME flip, the relaunched app must be playing again within this
TV_PLAYING_WAIT_S = 2.5    # after a TV-input flip: the stick we flipped to must show it's playing within this
# [SYNC 1] Flip only onto a stream that is steady, and book any stall into its delay.
PER_STICK_MIN = 3          # tunes on one stick model before its own median replaces the shared one
PER_STICK_DEADLINE_MIN = 8 # ...and before its own p90 replaces the shared bound in deadline_cost (rule #1)
FLIP_STEADY_S = 1.0        # the hidden stick must have reported PLAYING, with no state change, this long
FLIP_STEADY_WAIT_S = 3.0   # ...and gets this long to get there (buffering/connecting) before it's re-tuned
FLIP_SETTLE_S = 3.0        # a flip "settled" = the stick we flipped to is PLAYING within this (North Star row)
BUFFER_DRIFT_S = 2.0       # [STK-06] a hold further than this from its target is rebuilt (Nick, Nov 4)
HOLD_RESUME_LOCK_S = 1.0   # [time-shift buffer] a hold's resume waits at most this for the control lock
SCRUB_APPS = ("youtubetv",)   # [scrub entry] apps whose overlay scrub is proven to hold a buffer (Nick, Nov 6)
BUFFER_WAIT_S = 90.0       # ...at its game's next dead ball; none by then (a 90 s-held NFL stream reads "a play is
                           # on screen" for minutes, night lab): rebuild anyway. It's off screen and not flippable
                           # while it rebuilds, so no play is cut either way.
STALL_MIN_S = 0.5          # lag behind the tune anchor below this is read noise: not booked
STALL_MAX_S = 20.0         # above this it's not a buffering stall (a seek? another stream?): logged, not booked
TV_FLIP_MAX_S = 6.0        # a TV-input command + readback taking longer than this isn't a fast path (logged)
CEC_AGREE = 2              # CEC alone (no TV readback): this many syncs in a row must agree before we believe
                           # the TV moved without us (Nov 2-3: the sticks' flags alternated every sync, no switch)
CEC_STATE_EVERY_S = 300.0  # re-read each stick's HDMI-CEC setting this often
CEC_REENABLE_S = 0.6       # HOME fallback onto a stick with CEC off: CEC on, wait this long, then HOME
TV_MAINTAIN_S = 60.0       # keepalive: TV sign-in refresh + socket warm-up at most this often

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
WAIT_SCREEN_LOOK_S = 4.0  # a switch waiting for a safe moment looks at the screen this often (stalled player)
AWAKE_TRUST_S = 120.0      # a stick seen playing this recently is awake: skip the sleep check
REFRESH_POLL_S = 0.5       # after a flip: how often the refresh of the game we left checks for a free moment
REFRESH_TRIES = 40         # ...and how many times (~20s) before it leaves the standby to the planner
PRIME_KEEP_WAIT_S = 15.0   # after a flip: the Prime game we left gets this long to show it kept playing...
PLANNER_WISH_S = 3.0       # a refresh holds off while the planner asked for another game this recently
KEEP_CONFIRM_S = 2.0       # ...on two reads this far apart (a player that pauses a beat late isn't "ready")
ASLEEP_RECHECK_S = 60.0    # a sleeping hidden stick: don't ask again (or preload) for this long
VERIFY_SOON_S = 3.0        # after a flip, check the stick we left within a few seconds
STEAL_LOCK_WAIT_S = 10.0   # playbook=TV-04: how long the undo waits for the sticks to be free
STUCK_BUFFER_S = 20.0      # hardening.sticks: "buffering"/"connecting" this long is a stalled player, not a load
STUCK_GAP_S = 30.0         # ...on reads at most this far apart (else it may have played in between)
SEEK_LATE_OK_S = 1.0       # a rewind may land this far past its target (Missed.back_s leads the snap by 3s)

LATENCY_KEEP = 15
# What a real measurement can look like; outside this it's a glitch (a fake, a hung adb), not a switch.
PLAUSIBLE = {"flip": (0.02, 30.0), "seek": (0.05, 60.0), "resume": (0.0, 60.0), "tv_flip": (0.02, 30.0),
             "home_flip": (0.02, 30.0), "prime_tile": (0.05, 30.0)}
ACTIVE_SOURCE_CMD = "dumpsys hdmi_control | grep -E -c 'mIsActiveSource: true'"
CEC_GET_CMD = "settings get global hdmi_control_enabled"
CEC_SET_CMD = "settings put global hdmi_control_enabled {v}"

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
    settled: bool | None = None     # [SYNC 1] the flipped-to stick was PLAYING within FLIP_SETTLE_S
    stall_s: float | None = None    # [SYNC 1] buffering booked into that target's delay at this flip
    stick: str | None = None        # host of the stick that tuned (None = no tune ran)
    marks: list = field(default_factory=list)  # [(step, wallclock)] for the switch trace (redzone/trace.py)
    provenance: dict = field(default_factory=dict)  # [provenance] what the controller contributed: {module: value}


@dataclass
class LeftHold:
    """playbook=STK-07 [buffer.hold_left]: the game we just left, paused on the stick we left at the
    dead ball we left it on, so going back starts there (a catch-up) instead of at the live edge."""
    target: str
    host: str
    at: float                  # wall time it paused (the flip that left it)
    base_s: float              # what it already ran behind live before (a time-shift hold)

    def behind_s(self, now: float) -> float:
        return round(self.base_s + max(0.0, now - self.at), 1)


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
        base = key.split("@", 1)[0]
        lo, hi = PLAUSIBLE.get(key, PLAUSIBLE.get(base.split(":", 1)[0], TUNE_PLAUSIBLE))
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
        self.calibrated_flip: tuple[float, float] | None = None   # [calib.auto] (median, max) TV flip
        self.hidden_target: str | None = None    # what the hidden stick is playing or loading
        self.preload_why = ""                    # [jump mode] why the last preload was refused ("" = started)
        self._preloaded_only = False             # [jump mode] this switch may only flip to a ready standby
        self.hidden_ready = False                # ...and it's confirmed playing
        self._hidden_entry = 0.0                 # [seek model] entry rewind to run on the hidden stick before the flip
        self._hidden_entry_done = False
        self._entry_rewind = 0.0                 # [EXP-741 B] the running switch's entry rewind (arm_entry_rewind)
        self.bj_buffer_why = ""                  # [buffered jump] why buffer_standby last didn't start (jump_status)
        self._safe_waiting = False               # [EXP-741 E] a switch is waiting for its moment (tap_bug_frames)
        self._entry_cue = ""
        self.seek_log_path = None                # [calib.auto] data/seeks.jsonl: one row per measured seek
        self._last_seek_sent = 0
        self.parked: Parked | None = None        # ...parked on a replay instead of live
        self._snap_park: Parked | None = None   # [buffered jump] the hidden stick paused just before its next snap
        self._snap_task: asyncio.Task | None = None
        self.two_stick_ok = True                 # flips to False if an input flip ever fails
        self.latency = LatencyBook()
        self.latency_path = latency_path
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._preload_task: asyncio.Task | None = None
        self._preloading: tuple[str, str | None] | None = None   # (target, replay_id) being loaded
        self._last_preload = 0.0
        self._failed: dict[str, float] = {}      # target -> don't preload before
        self._stuck_since: tuple[str, float] | None = None   # [buffered jump] standby not ready, nothing running
        self._played_at: dict[str, float] = {}   # host -> when its last tune reported playing
        self._repaused: dict[tuple[str, str], list[float]] = {}   # [APP-04] (host, target) -> STK-05 drop times
        self.on_preload_fail: Callable[..., None] | None = None   # (target, detail, host=): teach discovery
        # Discovery's per-stick answer (host, target) -> bool: one stick can be signed out of an
        # app the other plays (Peacock on .181 only). None = every stick can play every app.
        self.can_play: Callable[[str, str], bool] | None = None
        self._tuned_on: str | None = None        # host of the last stick a switch tuned
        # [time-shift buffer] target -> seconds its stream is held behind the live edge (on whichever
        # stick plays it). A fresh tune clears it. behind_of(target) is the engine's B for a target
        # (0 = no hold); on_behind(target, new, old) tells the delay book (DelayBook.set_behind).
        self.held: dict[str, float] = {}
        # [SYNC A] stick host -> wall time of its last tune command, and -> seconds it plays behind its live
        # edge as MEASURED (sync_edge.py via the probe; `held` is what the app asked for)
        self.tuned_at: dict[str, float] = {}
        self.behind_live: dict[str, float] = {}
        # ...and -> seconds its picture runs behind REAL LIFE, where the app stamps frames with the wall clock
        # (Prime's epoch positions, overseer Nov 5 3:15 AM)
        self.behind_real: dict[str, float] = {}
        self.behind_of: Callable[[str], float] = lambda target: 0.0
        # [scrub entry] engine: is the overlay scrub on for this target's league (timing.behind_live_s.scrub_entry
        # per league)? None = the global setting only.
        self.scrub_of: Callable[[str], bool] | None = None
        # [scrub entry] the overlay label reader (#375) as enter_buffer's read_behind (None = yttv_seek's own default)
        # and where a measured reading goes (engine.label_behind, #376: delay = D_edge(channel) + behind_live)
        self.read_behind: Callable[..., Any] | None = None
        # [label reader] redzone.tuners.yttv_label.LabelProbe when timing.behind_live_s.label_reader is on (engine)
        self.label_probe: Any = None
        self.on_label: Callable[[str, float, float | None, float], None] | None = None
        self.dead_ball: Callable[[str], bool] = lambda target: True   # [STK-06] engine: is target's game dead now?
        self._buffer_bad: dict[str, float] = {}                      # target -> when its hold went bad
        self._scrub_backoff: dict[str, float] = {}                   # [scrub entry] target -> no scrub before this
        self.buffer_log_path: Path | None = None
        self.on_behind: Callable[[str, float, float], None] | None = None   # (target, new s, old s)
        # [SYNC 1] stick host -> ((target, stick `updated` ms), first monotonic seen): how long a stick
        # has provably played its stream with no state change (_note_steady).
        self._steady: dict[str, tuple] = {}
        self.settle_book = {"n": 0, "ok": 0}   # flips checked / PLAYING within FLIP_SETTLE_S
        self._cec_bad: dict[str, float] = {}     # stick host -> skip CEC until
        self._awake_at: dict[str, float] = {}    # stick host -> last seen awake
        self._asleep_until = 0.0
        self._verified_at = 0.0
        self._flipping = False                   # inside the flip: not cancellable
        self._holding: tuple | None = None       # [time-shift buffer] (stick, target, token) of a hold in progress
        self._left: LeftHold | None = None       # [buffer.hold_left] STK-07: the left game, paused where we left it
        self.adb_ok = False
        self._watchdog_applied = False
        self.last: Outcome | None = None
        self.note = ""
        self.stage = {"stage": "idle", "target": None, "reason": ""}
        self._marks: list | None = None          # steps of the switch now running (redzone/trace.py)
        self.input_unknown = False
        self._two_stick_retry_at = 0.0           # after a failed flip: two-stick mode stays off until then
        self._home_flipped = False               # the last flip pressed HOME (its app was relaunched)
        self._resume_task: asyncio.Task | None = None   # "did the relaunched app resume?" after a HOME flip
        self._cec_synced_at = 0.0
        self._adopt_once = True                  # first switch after start may adopt the screen
        self._replayed = False                   # a rewound picture was shown since start: never read back as live
        self.target_names: Callable[[str], list[str]] | None = None   # engine: team names for a target
        self.prime_plan: list[str] = []          # engine: Prime NBA games for a multiview, best first
        self._flip_gen = 0                       # bumped on every flip: a CEC read that spans one is stale
        self._replay_shown: tuple[str, Any, int] | None = None   # (target, stick, flip gen) of a replay put on screen
        self._steal_task: asyncio.Task | None = None     # playbook=TV-04 (_undo_steal)
        self._refresh_task: asyncio.Task | None = None   # after a flip: reload the game we left (see _refresh_left)
        self._refreshing: str | None = None      # the target the running preload is refreshing, if it is a refresh
        self._wish: tuple[str, float] | None = None   # the planner's latest preload ask (target, at), even if refused
        self._standby_miss: str | None = None    # a Prime standby whose last check found no session (one more read)
        self._exit_check = None
        self._flip_s: float | None = None       # HOME -> active source, of the last flip
        self.last_flip: dict | None = None       # the phone's latency book: flip_s / resume_s of the last flip
        self._visible_bad_since: float | None = None   # the stick on screen stopped playing at (watchdog)
        self._visible_state = ""                       # ... and how: paused / frozen / none / stopped
        self._visible_fix_at = 0.0               # last watchdog repair (rate limit)
        self._loading_since: dict[tuple[str, str], tuple[float, float]] = {}   # (host, target) -> (first, last) loading read
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
        self.tv.corroborate = self._cec_corroborate
        self.tv.hardening = self._st_hardening
        self._flip_how = ""                      # "tv:local" | "tv:smartthings" | "home" | "" (last flip)
        self._why: dict | None = None            # [provenance] the switch now running: how each step went
        self._cec_on: dict[str, bool | None] = {}   # stick host -> its HDMI-CEC setting (None = unread)
        self._cec_read_at = 0.0
        self._cec_restore: dict[str, asyncio.Task] = {}   # host -> "turn CEC off again" after a HOME fallback
        self._cec_pending: tuple[int | None, int] = (None, 0)   # (stick index CEC alone says, syncs in a row)
        self._tv_maintained_at = 0.0

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
        self.tv.corroborate = self._cec_corroborate
        self.tv.hardening = self._st_hardening

    def _st_hardening(self) -> bool:
        try:
            return bool(self.cfg.get("hardening.smartthings", False))
        except Exception:
            return False

    def _observe_playback(self, stick: Stick, report: dict) -> None:
        if self.on_playback is not None:
            self.on_playback(stick, report)

    async def boundary_reads(self) -> int:
        """One playback read of each stick showing YouTube TV, for the show-change delay watcher.
        Never during a switch, a flip or a preload: those reads would queue behind (or delay) it."""
        if not self.live_control or self._busy() or self._flipping or self.input_unknown:
            return 0
        # Take each target now: a switch can clear s.tuned while we await another stick's read
        # (e2e night test: TypeError 'NoneType' object is not subscriptable).
        reads = [(s, t) for s in self.usable
                 if str(t := (s.tuned or {}).get("target") or "").startswith("youtubetv:")]
        for s, target in reads:
            await s.playback(self._package(s, target))
        return len(reads)

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

    async def tap_bug_frames(self, target: str, band_from: float | None = None) -> list[dict]:
        """[EXP-741 E] One frame of the hidden stick while a switch to `target` (a live Prime game, already
        playing there) waits for its moment: the tap's score-bug clock read. Never presses a key. Empty
        unless the switch is in that wait, and dropped if a flip or retune started while capturing."""
        h = self.hidden
        if not (self._safe_waiting and self.live_control and not self._flipping and not self.input_unknown
                and h is not None and target.startswith("prime:") and self.hidden_target == target
                and (h.tuned or {}).get("target") == target and self._live_on_hidden(target)):
            return []
        gen, tune, t0 = self._flip_gen, h.tuned, time.time()
        png = await h.screencap_band(band_from, timeout=15) if band_from is not None else b""
        if not png:
            png = await h.screencap(timeout=15)
        t1 = time.time()
        if not png or gen != self._flip_gen or self._flipping or h.tuned is not tune or not self._safe_waiting:
            return []
        return [{"stick": h, "target": target, "visible": False, "tune": tune, "png": png, "t0": t0, "t1": t1}]

    async def scorebug_frames(self, hidden: bool = False, band_from: float | None = None) -> list[dict]:
        """One screenshot of each stick scorebug_sticks() names: {stick, target, visible, tune, png,
        t0, t1}. Never presses a key. All dropped if a switch or flip started while capturing.
        [delay.scorebug_band] With band_from, only the rows from there down cross Wi-Fi as raw pixels
        (no PNG encode on the stick); a full screencap when that fails."""
        gen, out = self._flip_gen, []
        for s, target, visible in self.scorebug_sticks(hidden):
            tune = s.tuned
            t0 = time.time()
            png = b""
            if band_from is not None:
                png = await s.screencap_band(band_from, timeout=15)
            if not png:
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

    async def read_cec_state(self) -> dict[str, bool | None]:
        """Each stick's HDMI-CEC setting (settings global hdmi_control_enabled). Off = it never
        pauses when it loses the input, and never takes the TV by itself; also: HOME can't switch to it."""
        for s in self.usable:
            r = await s.shell(CEC_GET_CMD, timeout=4)
            v = r.out.strip().splitlines()[-1].strip() if r.ok and r.out.strip() else ""
            self._cec_on[s.host] = True if v == "1" else False if v == "0" else self._cec_on.get(s.host)
        return dict(self._cec_on)

    def cec_off(self, stick: Stick) -> bool:
        return self._cec_on.get(stick.host) is False

    def _stick_on(self, hdmi: str) -> Stick | None:
        return next((s for s in self.usable if self.hdmi.get(s.host) == hdmi), None)

    async def _cec_corroborate(self, hdmi: str) -> bool | None:
        """The TV was asked for `hdmi`: does the stick on that input say it's the active source now?
        A Samsung tells the device it selects (<Set Stream Path>), so with CEC on this follows within
        ~1 s. With CEC off (or unreadable) there's no answer: None, never a guess."""
        stick = self._stick_on(hdmi)
        if stick is None or self.cec_off(stick):
            return None
        end = time.monotonic() + 1.5
        while True:
            yes = await self._is_active_source(stick)
            if yes:
                return True
            if yes is None or time.monotonic() >= end:
                return None
            await asyncio.sleep(FLIP_POLL_S)

    async def _sync_from_tv(self) -> bool | None:
        """The TV's own answer (SmartThings readback), when there is one. None = no readback."""
        if not self.tv.has_readback:
            return None
        gen = self._flip_gen
        try:
            seen = await self.tv.input_status()
        except Exception:
            log.exception("TV input readback failed")
            return None
        if seen is None or gen != self._flip_gen or self._flipping or self._busy():
            return None if seen is None else False
        stick = self._stick_on(seen)
        if stick is None:
            self.cec_view = "none"   # the TV is on some other input (or its own apps)
            return False
        i = self.sticks.index(stick)
        self.cec_view = "one"
        changed = self.active != i or self.input_unknown
        if changed:
            log.info("TV is showing %s (%s, TV readback)%s", stick.host, seen,
                     "; TV input known again" if self.input_unknown else "")
            left, left_target = (self.main, self.on_target) if self.active != i and not self.input_unknown else (None, None)
            if self.active != i:
                self.on_target = None
                self._set_hidden(None, False)
            self.active = i
            self._input_known()
            if left is not None and left_target and self.cfg.get("hardening.tv", False) \
                    and not (self._steal_task and not self._steal_task.done()):
                self._steal_task = asyncio.create_task(self._undo_steal(left, left_target, stick))
        return changed

    async def _undo_steal(self, back: Stick, target: str, now_on: Stick) -> None:
        """playbook=TV-04 (hardening.tv): the TV jumped by itself, not by our command, to a stick that
        isn't playing anything (a stick that rebooted woke the TV over HDMI-CEC onto its home screen),
        while the stick it left still plays our game. Put the TV straight back: the jump already cut
        that game, so going back now only shortens the interruption. Never when the stick now on screen
        plays a stream (a person may have picked it) or the one we left stopped."""
        try:
            pb_now = await now_on.playback(self._package(now_on, target))
            mine = (now_on.tuned or {}).get("target")
            if pb_now.get("read_ok") is False or (pb_now.get("state") == "playing"
                                                 and not (mine and now_on.still_on(mine, pb_now))):
                # Playing something we didn't put there: a person may have picked it. (A standby we
                # tuned there ourselves, e.g. a reload that ran before the jump was seen, doesn't count.)
                log.info("playbook=TV-04 %s is playing (or unreadable): a person may have picked it; left as is", now_on.host)
                return
            def ours(pb: dict) -> bool:
                # Still our stream there: playing, or paused because it lost the TV input (CEC on).
                return (back.tuned or {}).get("target") == target and pb.get("state") in ("playing", "paused")
            if not ours(await back.playback(self._package(back, target))):
                log.info("playbook=TV-04 %s no longer has %s: nothing to go back to", back.host, target)
                return
            try:
                await asyncio.wait_for(self._lock.acquire(), STEAL_LOCK_WAIT_S)
            except asyncio.TimeoutError:
                log.info("playbook=TV-04 the sticks stayed busy %.0fs (switch=%s, preload=%s %s): no undo",
                         STEAL_LOCK_WAIT_S, bool(self._task and not self._task.done()),
                         bool(self._preload_task and not self._preload_task.done()), self._preloading)
                return
            self._lock.release()
            async with self._lock:
                if self.main is not now_on or self.input_unknown or (self._task and not self._task.done()):
                    log.info("playbook=TV-04 a switch of ours took over meanwhile: no undo")
                    return
                if not ours(await back.playback(self._package(back, target))):
                    log.info("playbook=TV-04 %s was re-tuned meanwhile: nothing to go back to", back.host)
                    return
                self._flipping = True
                gate, self._exit_check = self._exit_check, None   # no switch of ours is waiting on a moment
                try:
                    flipped = await self._flip_to(back)
                finally:
                    self._flipping = False
                    self._exit_check = gate
                if not flipped:
                    log.warning("playbook=TV-04 couldn't put the TV back on %s: %s", back.host, self.tv.last_error)
                    return
                self.active = self.sticks.index(back)
                self._flip_gen += 1
                self._flipping = True
                try:   # on screen again, so pressing play can't cut anything (the re-assert ladder)
                    how = await self._resume_playback(back, target, self._resume_wait(target))
                finally:
                    self._flipping = False
                self.on_target = target
                self._visible_bad_since = None if how else time.time()
                log.info("playbook=TV-04 the TV jumped to %s by itself (not playing); back on %s (%s)",
                         now_on.host, back.host, target)
        except asyncio.CancelledError:
            log.info("playbook=TV-04 undo cancelled")
            raise
        except Exception:   # undoing a steal must never break the controller
            log.exception("playbook=TV-04 undo failed")

    async def sync_active_from_cec(self) -> bool:
        """Which stick is the TV showing? The TV's own readback (SmartThings) when it has one; else
        each stick's HDMI-CEC active-source flag. Acts only on an unambiguous answer: exactly one
        stick says it's on screen, and (CEC alone, input not lost) the same answer CEC_AGREE syncs in
        a row: on Nov 2-3 the flags alternated between the sticks every 30 s with no switch at all,
        and each "change" threw away both standbys. Returns True if it changed which stick we think
        is on screen, or cleared an unknown TV input."""
        tv = await self._sync_from_tv()
        if tv is not None:
            return tv
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
        if self.active != i and not self.input_unknown and self._flip_gen > 0:
            # CEC alone, and it disagrees with what our own flips say: believe it only when it
            # says the same thing again (a stale flag on a stick, the TV off, a race).
            prev, n = self._cec_pending
            self._cec_pending = (i, n + 1 if prev == i else 1)
            if self._cec_pending[1] < CEC_AGREE:
                log.debug("CEC says %s is on screen (once); waiting for it to agree", self.sticks[i].host)
                return False
        self._cec_pending = (None, 0)
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
        tasks = [t for t in (self._task, self._preload_task, self._resume_task, self._refresh_task,
                             *self._cec_restore.values()) if t and not t.done()]
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
        if time.time() - self._tv_maintained_at >= TV_MAINTAIN_S and not self._busy() and not self._flipping:
            self._tv_maintained_at = time.time()
            await self.tv.maintain()
        if time.time() - self._cec_read_at >= CEC_STATE_EVERY_S and not self._busy() and not self._flipping:
            self._cec_read_at = time.time()
            await self.read_cec_state()
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
                flipped = await self._home_flip_cec(stick, target)
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

    def _stuck_loading(self, stick: Stick, target: str, state: str | None) -> bool:
        """hardening.sticks: a player that has read "buffering"/"connecting" for STUCK_BUFFER_S in a
        row is stalled (a dead stream spins forever; a real load or rebuffer takes a few seconds).
        Off: never, as before (buffering always counted as playing)."""
        key = (stick.host, target)
        if state not in ("buffering", "connecting"):
            self._loading_since.pop(key, None)
            return False
        try:
            on = bool(self.cfg.get("hardening.sticks", False))
        except Exception:
            on = False
        if not on:
            return False
        now = time.time()
        first, last = self._loading_since.get(key, (now, now))
        if now - last > STUCK_GAP_S:
            first = now                    # reads too far apart: it may have played in between
        self._loading_since[key] = (first, now)
        if now - first < STUCK_BUFFER_S:
            return False
        log.warning("%s has been %s on %s for %.0fs: treating it as stalled", stick.host, state, target, now - first)
        return True

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
        if pb.get("state") in ("playing", "buffering", "connecting") and not self._stuck_loading(
                stick, target, pb.get("state")):
            self._visible_bad_since = None
            return
        # How it's dead, for the watchdog (redzone/platform/agent_fix.py): "playing" here means stuck loading.
        st = str(pb.get("state") or "stopped")
        self._visible_state = "frozen" if st in ("playing", "buffering", "connecting") else st
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
        self._loading_since.pop((stick.host, target), None)   # a repair starts a fresh load
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
        """A stick came back from a drop (maybe a reboot): whatever we thought it showed is stale.

        The stick on screen: forgetting its target used to leave nothing to restore it (the visible
        watchdog and the supervisor's screen rule both need on_target), so a reboot showed the Fire TV
        home until the decider happened to pick another game. With hardening.sticks the target is kept
        and the watchdog checks it at once: still playing (a short adb blip) -> nothing happens; not
        playing (a reboot) -> the usual resume / re-link after VISIBLE_STALL_S, on that stick only."""
        if stick is self.main:
            if self.on_target and self._hardening("sticks"):
                log.info("%s (on screen) is back after a drop: checking it still plays %s", stick.host,
                         self.on_target)
                self._visible_bad_since = None
                self._loading_since.pop((stick.host, self.on_target), None)
                self._schedule_visible_check(0.0)
                return
            self.on_target = None
        else:
            self._set_hidden(None, False)

    def _hardening(self, part: str) -> bool:
        try:
            return bool(self.cfg.get(f"hardening.{part}", False))
        except Exception:
            return False

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
                and (self.tv.ready() or bool(self.tv.st_device) or self.cfg.get("two_stick.cec_flip", True))
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
        left = stick or self.hidden
        # With its HDMI-CEC off, the stick we left never hears it lost the input, so YouTube TV and
        # Peacock keep playing too: keep the game instead of reloading it (no pause, no re-tune).
        keep = target.startswith("prime:") or (left is not None and self.cec_off(left))
        if keep and await self._kept_playing(target, left):
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
            self._note_steady(stick, target, pb)
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

    # --- [buffer.hold_left] playbook=STK-07: hold the game we left -----------------------------------

    def _holds_left(self, target: str, stick: Stick | None) -> bool:
        """Should the stick we just left hold `target` paused where we left it?"""
        if stick is None or not self.cfg.get("buffer.hold_left"):
            return False
        if self._ref(target)[0] not in self.cfg.get("buffer.hold_left_apps"):
            return False
        mv = self.prime_mv
        if mv is not None and mv.session(stick.host) is not None:
            return False   # a multiview tile is one stream of several: pausing it pauses them all
        return (stick.tuned or {}).get("target") == target

    def _left_for(self, stick: Stick, target: str) -> LeftHold | None:
        lh = self._left
        if lh is None or lh.host != stick.host or lh.target != target or self.hidden_target != target:
            return None
        return lh

    async def _left_paused(self, stick: Stick, target: str) -> bool:
        """The left-game hold is still paused where we left it, and not past the behind-live cap."""
        pkg = self._package(stick, target)
        pb = await stick.playback(pkg)
        lh = self._left_for(stick, target)
        return bool(lh and pb.get("state") == "paused" and pb.get("pkg") in (None, pkg)
                    and lh.behind_s(time.time()) < float(self.cfg.get("timing.behind_live_s.max_s")))

    async def _hold_left(self, target: str, stick: Stick, t_flip: float) -> None:
        """Right after the flip that left `target`: pause it on the stick we left (a no-op for YouTube
        TV, which pauses itself on input loss when HDMI-CEC is on) and keep it as the ready standby.
        The pause must land within buffer.hold_left_slop_s of the flip, so it holds the dead ball we
        left. Anything else (a late or refused pause, another stream) reloads it at the live edge as
        before (_refresh_left). Never HOME or WAKEUP: either could pull the TV back to it."""
        pkg = self._package(stick, target)
        base = self.held.get(target, 0.0)

        def moved_on() -> bool:
            return (self.hidden is not stick or self.hidden_target != target or self.hidden_ready
                    or self.on_target == target)
        sent = await stick.key("KEYCODE_MEDIA_PAUSE")
        paused_at = time.time()
        if moved_on():
            return
        slop = float(self.cfg.get("buffer.hold_left_slop_s"))
        if sent and sent.ok and paused_at - t_flip <= slop:
            pb = await stick.playback(pkg)
            if moved_on():
                return
            if pb.get("state") == "paused" and pb.get("pkg") in (None, pkg):
                stick.remember(target, pb)   # re-anchor: the pause broke its position from the tune's
                # With HDMI-CEC on it paused at the input change itself; with it off, at our key.
                at = paused_at if self.cec_off(stick) else t_flip
                self._left = LeftHold(target, stick.host, at, base)
                self._set_hidden(target, True)
                self._verified_at = time.time()
                self._buffer_log("left_held", target, stick.host, base, None)
                log.info("playbook=STK-07 holding %s on %s at the dead ball we left it on", target, stick.host)
                return
        log.info("playbook=STK-07 couldn't hold %s on %s (%s): reloading it at the live edge", target, stick.host,
                 "pause refused" if not (sent and sent.ok) else
                 f"pause {paused_at - t_flip:.1f}s after the flip" if paused_at - t_flip > slop else "not paused")
        await self._refresh_left(target, stick, t_flip)

    def _watch_left(self, hid: Stick, target: str, pb: dict, lh: LeftHold) -> None:
        """verify_standby for a left-game hold: an intentional pause, so not STK-05. Guarded like a
        time-shift hold (STK-06): one that resumed by itself or reached the behind-live cap
        (timing.behind_live_s.max_s) is reloaded at the live edge."""
        now = time.time()
        cap = float(self.cfg.get("timing.behind_live_s.max_s"))
        behind = lh.behind_s(now)
        if pb.get("state") == "paused" and behind < cap:
            every = self._verify_every()
            self._verified_at = now - every + min(every, cap - behind)   # look again at the cap at the latest
            return
        kind = "left_expired" if pb.get("state") == "paused" else "left_lost"
        log.info("playbook=STK-06 left-game hold of %s on %s %s (%.1fs behind live, cap %.0fs): reloading it at "
                 "the live edge", target, hid.host, "reached the cap" if kind == "left_expired" else
                 f"is {pb.get('state')}", behind, cap)
        self._buffer_log(kind, target, hid.host, behind, None)
        self._left = None
        self._set_hidden(target, False)
        self.preload(target, refresh=True)

    def _cancel_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done() and self._refresh_task is not asyncio.current_task():
            self._refresh_task.cancel()

    def _set_hidden(self, target: str | None, ready: bool, parked: Parked | None = None) -> None:
        self.hidden_target, self.hidden_ready, self.parked = target, ready, parked
        sp = getattr(self, "_snap_park", None)
        if sp is not None and (target != sp.target or not ready or parked is not None):
            self._snap_park = None   # [buffered jump] the stick changed under the park: it's not paused there any more

    def _live_on_hidden(self, target: str) -> bool:
        return self.hidden_target == target and self.hidden_ready and self.parked is None

    def _refused(self, why: str) -> bool:
        self.preload_why = why
        return False

    def standby_ready(self, target: str | None) -> bool:
        """[jump mode] The hidden stick is playing this target live, ready for a flip."""
        return bool(target) and self.two_stick and self._live_on_hidden(target)

    def slow_tune(self, target: str | None) -> bool:
        """[jump mode] A YouTube TV target reached only by in-app search (the locals): ~11-15 s, not ~3-4 s."""
        if not target or not target.startswith("youtubetv:"):
            return False
        return self._ref(target)[1].startswith("search:")

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

    def tune_estimate(self, target_or_app: str, stick: Stick | None = None) -> float:
        """Seconds a tune takes for this target's app: measured on this stick's model once it has
        PER_STICK_MIN tunes, else measured over both sticks, else the default guess."""
        app = target_or_app.split(":", 1)[0]
        model = getattr(stick, "model", None)
        mine = self.latency.samples.get(f"{app}@{model}") if model else None
        if mine and len(mine) >= PER_STICK_MIN:
            return statistics.median(mine)
        m = self.latency.median(app)
        return m if m is not None else self._default_cost(app)

    def flip_path(self) -> str:
        """[switch timing] The flip _flip_to will try first: "tv_flip" (the TV's own input change,
        SmartThings or a setup-proven local key) or "home_flip" (HOME on the stick, CEC)."""
        try:
            return "tv_flip" if self.tv.ready() and self.tv.paths() else "home_flip"
        except Exception:  # a broken TV object must never block planning
            log.exception("tv path check failed")
            return "home_flip"

    def _flip_samples(self, path: str) -> list[float]:
        """Measured flips of this path only: a HOME flip (2-6 s, then a relaunch) is not a TV input
        change (~2 s, nothing pressed), and mixing them planned the TV path with HOME's numbers.
        Older saves booked HOME flips only under "flip"."""
        s = self.latency.samples.get(path)
        if not s and path == "home_flip":
            s = self.latency.samples.get("flip")
        return sorted(s or ())

    def _tv_prior(self) -> tuple[float, float]:
        """(median, upper) of the TV input change before we've timed our own: the SmartThings
        switch measured on the user's TV, Nov 4 2026 (live lab Nov 4, CEC off: 30/30, median 1.89 s, max 2.02 s). The upper bound is the measured max: deadlines guard rule #1."""
        if self.calibrated_flip is not None:
            return self.calibrated_flip   # [calib.auto] last nights' accepted figures (redzone/calibration.py)
        from redzone.contracts.switching import flip_bound
        return flip_bound(self.cfg.get("two_stick.tv_flip_prior_s") or {})

    def flip_estimate(self) -> float:
        """Seconds from "flip" to the new picture: the measured command, plus the TV's own HDMI
        re-sync (two_stick.input_settle_s — set it from a stopwatch, the TV can't tell us)."""
        settle = float(self.cfg.get("two_stick.input_settle_s", 1.0))
        path = self.flip_path()
        samples = self._flip_samples(path)
        if path == "tv_flip":
            return (statistics.median(samples) if samples else self._tv_prior()[0]) + settle
        if not samples:
            return self._default_cost("hdmi_flip")
        # flip_s is HOME -> active source (the cut). The picture coming back (resume_s) is booked
        # separately and deliberately NOT added here: it would shift tonight's decider costs.
        return statistics.median(samples) + settle

    def _flip_upper(self) -> float:
        """The flip's planning bound: p90 of this path's measurements (TV path: never under the
        measured prior's max until we have 5 of our own), plus the HDMI settle."""
        settle = float(self.cfg.get("two_stick.input_settle_s", 1.0))
        path = self.flip_path()
        samples = self._flip_samples(path)
        p90 = samples[min(len(samples) - 1, math.ceil(len(samples) * .9) - 1)] if samples else 0.0
        if path == "tv_flip":
            return (p90 if len(samples) >= 5 else max(p90, self._tv_prior()[1])) + settle
        return max(self._default_cost("hdmi_flip"), p90) + settle

    def switch_costs(self) -> dict[str, float]:
        """Expected seconds from "switch" to the new game on screen, per app, on the path we'd use now.

        One stick: a tune. Two sticks: a flip, plus a tune for the share of switches where the
        hidden stick wasn't already there (measured). Half-second steps keep config writes rare."""
        flip = self.flip_estimate()
        two = self.two_stick
        out = {"hdmi_flip": flip}
        for app in TUNERS:
            tune = self.tune_estimate(app)   # both sticks: the decider's long-run figure
            out[app] = flip + self.latency.miss_rate(app) * tune if two else tune
        return {k: round(v * 2) / 2 for k, v in out.items()}

    @property
    def prime_mv(self) -> PrimeMultiviewTuner | None:
        """The Prime multiview tuner when tuners.prime_multiview is on and the probe's key map can
        switch tiles; None otherwise (every Prime switch is then a normal tune or flip)."""
        t = TUNERS.get("prime")
        if not isinstance(t, PrimeMultiviewTuner):
            return None
        t.enabled = bool(self.cfg.get("tuners.prime_multiview", False))
        if t.enabled and t.keys is None and not getattr(self, "_prime_keys_tried", False):
            self._prime_keys_tried = True
            t.keys = pmv.load_keys()
            if t.keys is None:
                log.warning("tuners.prime_multiview is on but data/prime_multiview_keys.json is missing or broken")
        if t.names is None and self.target_names is not None:
            t.names = self.target_names
        return t if t.ready else None

    def tile_cost(self, p90: bool = False) -> float:
        """Seconds for a Prime multiview tile switch: measured here, else the probe's timings, else
        tuners.prime_tile_s / prime_tile_p90_s."""
        xs = sorted(self.latency.samples.get("prime_tile") or ())
        if xs:
            return xs[min(len(xs) - 1, math.ceil(len(xs) * .9) - 1)] if p90 else statistics.median(xs)
        mv = TUNERS.get("prime")
        probe = mv.keys.tile_cost(.9 if p90 else .5) if isinstance(mv, PrimeMultiviewTuner) and mv.keys else None
        if probe is not None:
            return probe
        return float(self.cfg.get("tuners.prime_tile_p90_s" if p90 else "tuners.prime_tile_s",
                                  pmv.DEFAULT_TILE_P90_S if p90 else pmv.DEFAULT_TILE_S))

    def _tile_path_cost(self, target: str | None) -> float | None:
        """A Prime multiview tile on the stick on screen: a ~1 s tile switch, no flip. A tile on the
        hidden stick: switch it there first (off screen), then the usual flip."""
        mv = self.prime_mv
        if mv is None or not target:
            return None
        main = self.main
        if main is not None and mv.has_tile(main.host, target):
            return 0.0 if mv.is_main(main.host, target) else self.tile_cost(p90=True)
        return None   # a tile on the hidden stick is costed with its flip (deadline_cost)

    def deadline_cost(self, target: str | None) -> float:
        """Budget the actual target path, never an average of hits and misses.

        The 90th percentile and the shipped estimate are planning bounds, not
        a physical guarantee; actual exit timing is checked again at the cut.
        """
        tile = self._tile_path_cost(target)
        if tile is not None:
            return tile
        app = (target or "unknown").split(":", 1)[0]
        def upper(key):
            samples = sorted(self.latency.samples.get(key) or ())
            p90 = samples[min(len(samples) - 1, math.ceil(len(samples) * .9) - 1)] if samples else 0
            return max(self._default_cost(key), p90)
        tune = upper(app)
        tuner = self.hidden if self.two_stick else self.main
        mine = sorted(self.latency.samples.get(f"{app}@{getattr(tuner, 'model', None)}") or ())
        if len(mine) >= PER_STICK_DEADLINE_MIN:
            # This stick model's own p90 once it has enough tunes (4K Plus p50 2.33 s vs 4K 2023
            # 3.74 s, live lab Nov 4): a fast stick isn't priced as the slow one, nor the reverse.
            tune = mine[min(len(mine) - 1, math.ceil(len(mine) * .9) - 1)]
        if not self.two_stick:
            return tune
        flip = self._flip_upper()
        mv = self.prime_mv
        hid = self.hidden
        if mv is not None and target and hid is not None and mv.has_tile(hid.host, target):
            # Off-screen tile switch on the hidden stick, then the flip.
            return flip + (0.0 if mv.is_main(hid.host, target) else self.tile_cost(p90=True))
        # [scrub entry] a target the hidden stick still has to scrub pays for the scrub before the flip
        scrub = self._scrub_cost(target)
        return flip + scrub if target and self._live_on_hidden(target) else tune + scrub + flip

    def switch_times(self) -> dict:
        """For the phone's Health card: what we measured, and what the decider is planning with."""
        return {"measured": self.latency.view(), "preloaded": self.latency.preloaded_view(),
                "cost": dict(self.cfg.get("decider.switch_cost") or {}),
                "path": "flip" if self.two_stick else "tune",
                # The input change and the picture coming back, measured separately (medians + last).
                "flip_s": self.latency.median("flip"), "resume_s": self.latency.median("resume"),
                "flip_path": self.flip_path(), "flip_plan_s": round(self.flip_estimate(), 2),
                "last_flip": dict(self.last_flip) if self.last_flip else None,
                "input": {"unknown": self.input_unknown, "cec": self.cec_view, "reasserts": self._reasserts,
                          "two_stick_ok": self.two_stick_ok}}

    # --- actions -----------------------------------------------------------------

    def submit(self, target: str | None, replay_back_s: float | None = None, on_done=None,
               may_cut: Callable[[], bool] | None = None, replay_id: str | None = None,
               entry_rewind_s: float = 0.0, entry_cue: str = "", preloaded_only: bool = False) -> None:
        """Fire-and-forget; the newest command wins. preloaded_only [jump mode]: flip to the hidden stick's
        ready stream or nothing (never a tune on the way: a jump that tunes misses its dead ball). A preload of some OTHER target is cancelled
        (queueing behind a tune we no longer want would add up to 10s); one loading this very
        target is kept, and the switch picks it up the moment it's ready."""
        # [guardrail] Never cancel mid-flip: the TV could end up on the other input while we think
        # it isn't. The flip takes ~1s; the new command just waits for it.
        if self._task and not self._task.done() and not self._flipping:
            self._task.cancel()
        elif self._task and not self._task.done():
            log.info("playbook=BTN-05 a command for %s arrived mid-flip: it runs once the flip lands", target)
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._cancel_refresh()   # the new switch decides what the hidden stick gets; a flip starts a new refresh
        want = (target, replay_id if replay_back_s else None)
        if self._preload_task and not self._preload_task.done() and self._preloading != want:
            self._preload_task.cancel()
        self._task = asyncio.create_task(self._run(target, replay_back_s, on_done, may_cut, replay_id, time.time(),
                                                   entry_rewind_s, entry_cue, preloaded_only))
        if on_done:
            self._task.add_done_callback(lambda task: on_done(
                Outcome(False, target, 0.0, "switch cancelled; playback not confirmed", False)
            ) if task.cancelled() else None)

    async def _run(self, target, replay_back_s, on_done, may_cut, replay_id=None, issued_at=None,
                   entry_rewind_s: float = 0.0, entry_cue: str = "", preloaded_only: bool = False) -> None:
        async with self._lock:
            self._preloaded_only = preloaded_only
            if replay_back_s:
                self._adopt_once = False   # a rewound picture must never be adopted as live
                self._replayed = True
            self.stage = {"stage": "tuning", "target": target, "reason": "confirming the requested stream"}
            try:
                self._tuned_on = None
                out = await self.execute(target, replay_back_s, may_cut, replay_id, issued_at,
                                         entry_rewind_s=entry_rewind_s, entry_cue=entry_cue)
                if out.stick is None:
                    out.stick = self._tuned_on
            except asyncio.CancelledError:
                if not self.hidden_ready:
                    self._set_hidden(None, False)   # stopped mid-tune: we don't know what it shows
                raise
            except Exception as e:  # never let a tuner bug kill the app
                log.exception("tune crashed")
                out = Outcome(False, target, 0.0, f"crashed: {e}", False)
            finally:
                self._preloaded_only = False
            self.last = out
            self.stage = {"stage": "confirmed" if out.ok else "failed", "target": target, "reason": out.detail}
            if on_done:
                on_done(out)

    def arm_entry_rewind(self, rewind_s: float) -> None:
        """[EXP-741 B] Set the running switch's entry rewind from its may_cut, i.e. at the cut, not at submit:
        a phone tap only learns it needs one when no dead ball has come without one. With the "hidden" cue
        the hidden stick seeks before the flip; otherwise the visible stick rewinds right after it."""
        self._entry_rewind = float(rewind_s or 0.0)
        self._hidden_entry = self._entry_rewind if getattr(self, "_entry_cue", "") == "hidden" else 0.0

    def cancel_pending(self) -> None:
        """Stop queued/tuning work on Pause or dry-run. Let an input flip settle."""
        if self._task and not self._task.done() and not self._flipping:
            self._task.cancel()
        if self._preload_task and not self._preload_task.done():
            self._preload_task.cancel()
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._cancel_refresh()

    async def _reset_through_other(self, stick: Stick, target: str, tuner) -> TuneResult | None:
        """Tune `stick` to another channel of the same app so the next tune of `target` starts at the live edge.
        None = done (go on with the tune); a failed TuneResult = no reset was possible, so no tune either.
        The channel: yttv_seek.reset_channel if set, else any other channel with a watch code."""
        app = target.split(":", 1)[0]
        other = str(self.cfg.get("yttv_seek.reset_channel", "") or "")
        if not other and app == "youtubetv":
            here = target.split(":", 1)[1] if ":" in target else ""
            other = next((k for k in sorted(self.channels.codes) if k != here), "")
        other_t = f"{app}:{other}" if other and ":" not in other else other
        _, ref = self._ref(other_t) if other_t else ("", "")
        if not ref or other_t == target:
            return TuneResult(False, target, 0.0, [], "may be behind live on this channel; no other channel to reset "
                              "through (yttv_seek.reset_channel)", screen_changed=False)
        res = await tuner.tune(stick, ref, other_t)
        if not res.ok:
            return TuneResult(False, target, res.took, list(res.steps), f"reset through {other_t} failed: {res.error}")
        stick.behind_on = None
        return None

    def _ref(self, target: str) -> tuple[str, str]:
        app, _, ref = target.partition(":")
        if app == "youtubetv":
            ref = self.channels.yttv_code(target) or ""
            if not ref:
                key = self.channels.yttv_search_key(target)
                ref = self.channels.search_ref(key) if key else ""
        return app, ref

    def _package(self, stick: Stick, target: str) -> str | None:
        app = target.split(":", 1)[0]
        tuner = TUNERS.get(app)
        return stick.packages.get(getattr(tuner, "role", app)) if tuner else None

    async def _tune(self, stick: Stick, target: str, may_cut=None) -> TuneResult:
        if self._holding and self._holding[0] is stick:
            self._holding = None   # a re-tune throws a pending hold's pause away (see _hold)
        if self._left is not None and self._left.host == stick.host:
            self._left = None      # ...and a left-game hold (STK-07): it's at the live edge now
        app, ref = self._ref(target)
        tuner = TUNERS.get(app)
        if tuner is None or not ref:
            return TuneResult(False, target, 0.0, [], "channel link not set up yet" if tuner else f"no tuner for {app}")
        br = getattr(stick, "breaker", None)
        if br is not None and br.is_open:
            # Hung stick: fail now (nothing sent) instead of waiting out every adb timeout again.
            return TuneResult(False, target, 0.0, [], f"{STICK_HUNG}: {stick.host}, retry in {br.retry_in():.0f}s",
                              screen_changed=False)
        if getattr(stick, "behind_on", None) == target:
            # [Nick's 90 s run, Nov 7] this stick may be behind live on this very channel, and a same-channel deep
            # link does not reset that: go through another channel first, or fail (never claim a live edge).
            reset = await self._reset_through_other(stick, target, tuner)
            if reset is not None:
                return reset
        self._tuned_on = stick.host
        self.tuned_at[stick.host] = time.time()
        res = await tuner.tune(stick, ref, target, **({"may_cut": may_cut} if may_cut is not None else {}))
        if res.ok and getattr(stick, "behind_on", None) not in (None, target):
            stick.behind_on = None   # it plays another channel now; that one starts at its live edge
        if not res.ok and br is not None and br.state != "closed" and not res.error.startswith(STICK_HUNG):
            # The tune died because the stick hung, not because of the channel: say so, so the
            # target isn't cooled down or marked unplayable for a stick fault.
            res.error = f"{STICK_HUNG}: {res.error}"
        if res.ok:
            self._played_at[stick.host] = time.time()   # [scrub entry] the post-tune banner clock starts here
            self._set_held(target, 0.0)   # a fresh tune plays from the live edge
            # A Prime multiview tile switch is ~1 s, not a ~32 s tune: never let it into Prime's median.
            self.latency.record("prime_tile" if res.path == "tile" else app, res.took)
            if res.path != "tile" and getattr(stick, "model", None):
                # Per stick model too: the live lab (Nov 4) had a hidden-stick tune to PLAYING at p50
                # 2.33 s on the 4K Plus vs 3.74 s on the 4K 2023, so one median misprices one of them.
                self.latency.record(f"{app}@{stick.model}", res.took)
            self._awake_at[stick.host] = time.time()
            self.__dict__.setdefault("_tuned_at", {})[stick.host] = time.time()
        return res

    def _set_held(self, target: str, behind_s: float) -> None:
        old = self.held.get(target, 0.0)
        if old == behind_s:
            return
        if behind_s:
            self.held[target] = behind_s
        else:
            self.held.pop(target, None)
        if self.on_behind is not None:
            self.on_behind(target, behind_s, old)

    async def _hold(self, stick: Stick, target: str, behind_s: float, base_s: float = 0.0) -> bool:
        """[time-shift buffer] Hold the hidden stick's fresh live tune behind_s behind the live edge:
        pause, wait it out WITHOUT the control lock (switches go on meanwhile; the stick isn't
        ready, so nothing flips to it), then play. True = it's playing behind_s behind live.
        On any doubt the stream is reloaded at the live edge rather than left at an unknown offset.

        The resume is on time: it waits at most HOLD_RESUME_LOCK_S for the lock. A switch that holds
        the lock for longer (waiting on a safe moment) made a 60 s hold come out 81.7 s in the night
        lab. Without the lock it still only plays a stick nobody touched since the pause: any
        re-tune (_tune) or retarget drops the hold, and `held` is only written if that's still so."""
        pkg = self._package(stick, target)
        async with self._lock:
            if self.hidden is not stick or self.hidden_target != target:
                return False
            sent = await stick.key("KEYCODE_MEDIA_PAUSE")
            pb = await stick.playback(pkg) if sent and sent.ok else {}
            if pb.get("state") != "paused":
                await stick.key("KEYCODE_MEDIA_PLAY")
                log.info("buffer: %s on %s didn't pause (%s); left at the live edge", target, stick.host,
                         pb.get("state"))
                return False
            paused_at = time.monotonic()
            token = object()
            self._holding = (stick, target, token)
        await asyncio.sleep(behind_s)

        def ours() -> bool:
            h = self._holding
            return self.hidden is stick and self.hidden_target == target and h is not None and h[2] is token

        try:
            await asyncio.wait_for(self._lock.acquire(), HOLD_RESUME_LOCK_S)
            locked = True
        except asyncio.TimeoutError:
            locked = False
        try:
            if not ours():
                return False      # flipped, retargeted or re-tuned meanwhile: whoever did that owns the stick
            if not locked:
                log.info("buffer: resuming %s on %s on time; a switch holds the lock", target, stick.host)
            sent = await stick.key("KEYCODE_MEDIA_PLAY")
            pb = await stick.playback(pkg) if sent and sent.ok else {}
            if not ours():
                return False
            if pb.get("state") != "playing":
                if not locked:
                    # Not ours to reload while a switch runs: the stick stays unready, and the
                    # standby check re-tunes a paused standby (STK-05).
                    log.warning("buffer: %s on %s didn't resume (%s); left to the standby check", target,
                                stick.host, pb.get("state"))
                    return False
                log.warning("buffer: %s on %s didn't resume (%s); reloading at the live edge", target,
                            stick.host, pb.get("state"))
                self._set_hidden(target, False)
                res = await self._tune(stick, target)
                self._set_hidden(target if res.ok else None, res.ok)
                return False
            held = self._held_for(stick, target, pb, time.monotonic() - paused_at)
            # Re-anchor the stream here: the pause broke its position from the tune's, so without this
            # still_on fails and the next flip re-tunes it at the live edge, throwing the hold away.
            stick.remember(target, pb)
            held = round(base_s + held, 1)   # base_s: what it already ran behind (a watchdog top-up, STK-06)
            self._set_held(target, min(held, float(self.cfg.get("timing.behind_live_s.max_s", 90.0)) + 5.0))
            log.info("buffer: %s on %s held %.1fs behind live", target, stick.host, held)
            return True
        finally:
            if locked:
                self._lock.release()
            h = self._holding
            if h is not None and h[2] is token:
                self._holding = None

    def _seek_sample(self, stick: Stick, target: str, asked_s: float, pb: dict) -> float | None:
        """[calib.auto] How far one seek really moved the picture: the stick's clock against its position since
        the stream's anchor (as the live lab measured it by hand), with the presses sent. Appended to
        data/seeks.jsonl for calibration.seek_fit. Never raises; None when it can't be told."""
        t = stick.tuned or {}
        a = (t.get("report") or {}) if t.get("target") == target else {}
        pa, ua, pn, un = a.get("position"), a.get("updated"), pb.get("position"), pb.get("updated")
        if None in (pa, ua, pn, un) or pa <= 0 or pn <= 0 or un < ua:
            return None
        landed = ((un - ua) - (pn - pa)) / 1000.0
        if self.seek_log_path is not None:
            row = {"t": round(time.time(), 3), "host": stick.host, "model": getattr(stick, "model", ""),
                   "target": target, "sent": int(self._last_seek_sent), "asked_s": round(float(asked_s), 2),
                   "landed_s": round(landed, 2)}
            try:
                with open(self.seek_log_path, "a") as fh:
                    fh.write(json.dumps(row) + "\n")
            except OSError:
                log.debug("could not append to %s", self.seek_log_path)
        return landed

    def _held_for(self, stick: Stick, target: str, pb: dict, span_s: float) -> float:
        """[hold calibration] How far behind live a hold really left the stream. Best: the stick's own
        clock against its position since the last anchor (clock elapsed - position elapsed: what the
        live lab used, error under 0.5 s). Only this pause: the caller adds base_s, what the stream already
        ran behind (a watchdog top-up re-anchors after the first hold, so adding self.held too counted it
        twice and the STK-06 rebuild never converged). Else the wall span
        between the confirmed pause and the confirmed play less timing.behind_live_s.hold_overreport_s
        (live lab, Nov 4: that span over-reports by ~1.4 s; 1.5 s default)."""
        t = stick.tuned or {}
        a = t.get("report") or {} if t.get("target") == target else {}
        pa, ua, pn, un = a.get("position"), a.get("updated"), pb.get("position"), pb.get("updated")
        if None not in (pa, ua, pn, un) and pa > 0 and pn > 0 and un >= ua:
            lag = ((un - ua) - (pn - pa)) / 1000.0
            if 0.0 < lag <= span_s + 5.0:
                return round(lag, 1)
        over = float(self.cfg.get("timing.behind_live_s.hold_overreport_s", 0.0) or 0.0)
        return round(max(0.0, span_s - over), 1)

    def _replay_scrubs(self, target: str | None) -> bool:
        """[replay on the scrub, Nick Nov 7 17:56Z] MEDIA_REWIND never seeks on YouTube TV (proven Nov 6; 275 'replay
        seek failed' lines in service.log, each burning 20-24 s). A YouTube TV catch-up seeks by the overlay scrub."""
        return bool(target) and str(target).split(":", 1)[0] == "youtubetv" and \
            str(self.cfg.get("replay.yttv_seek", "scrub")).lower() == "scrub"

    def _scrub_park_refusal(self, target: str, back_s: float) -> str:
        """Why a YouTube TV replay can't be parked right now ('' = it can be tried): checked before any key."""
        from redzone.tuners import replay_park
        if time.time() < self.__dict__.get("_replay_scrub_backoff", {}).get(target, 0.0):
            return "a failed replay scrub on this channel is backing off"
        k = replay_park.lefts_for(back_s, self.cfg)
        cap = int(self.cfg.get("replay.yttv_max_lefts") or replay_park.PARK_DEFAULTS["yttv_max_lefts"])
        if k > cap:
            return f"{back_s:.0f}s back needs {k} LEFTs (> {cap})"
        return ""

    async def _scrub_park(self, stick: Stick, target: str, back_s: float, replay_id: str) -> bool:
        """[replay on the scrub, Nick Nov 7] Park the HIDDEN stick park_lead_s before the snap by the label-measured
        overlay scrub (redzone/tuners/replay_park.py). False = keep the current game (fails closed)."""
        from redzone.tuners import replay_park
        backoff = self.__dict__.setdefault("_replay_scrub_backoff", {})
        if time.time() < backoff.get(target, 0.0):
            return False   # a failed replay scrub isn't retried for replay.scrub_retry_s (no key storms)
        if self._task is asyncio.current_task():
            self.stage["stage"] = "rewinding"
        # [Nick, Nov 8 3778a401] after a tune YouTube TV's LIVE/title banner sits up ~20-30 s and keys do nothing
        # there ('focus guard: no scrub label after the LEFT', 62 s): a freshly tuned stick waits it out first.
        # The replay is that much further back when the scrub starts. No key is sent during the wait.
        still_hidden = lambda: self.hidden is stick and not self._flipping   # noqa: E731
        ready_at = self.__dict__.get("_tuned_at", {}).get(stick.host, 0.0) + float(
            self.cfg.get("replay.park_after_tune_s", 22.0))
        if time.time() < ready_at:
            t_w = time.time()
            while time.time() < ready_at:
                if not still_hidden():
                    return False
                await asyncio.sleep(min(0.5, ready_at - time.time()))
            back_s += time.time() - t_w
        probe = self.label_probe
        if probe is None:
            # the scrub-entry label reader (timing.behind_live_s.label_reader) is off: a replay still never parks
            # blind, so it gets its own probe (same local OCR, frames read then deleted)
            if getattr(self, "_replay_probe", None) is None:
                from redzone.tuners.yttv_label import LabelProbe
                self._replay_probe = LabelProbe(self.cfg)
            probe = self._replay_probe
        r = await replay_park.park(stick, back_s, probe=probe, cfg=self.cfg,
                                   pkg=self._package(stick, target), still_hidden=still_hidden)
        if r.moved:
            stick.behind_on = target   # keys moved it: the next live tune of target resets first (#391)
        self._last_seek_sent = r.lefts
        if not r.ok:
            log.info("replay scrub %s on %s: %s", target, stick.host, r.reason)
            # [CI night, Nov 7] without this the planner re-parked the same replay every ~5 s: a key storm
            backoff[target] = time.time() + float(self.cfg.get("replay.scrub_retry_s", 120.0))
            return False
        self.latency.record("seek", r.took_s)
        log.info("replay scrub %s on %s: %s", target, stick.host, r.steps[-1] if r.steps else r.reason)
        self._set_hidden(target, True, Parked(replay_id, target, time.time(), r.paused))
        return True

    async def _rewind(self, stick: Stick, back_s: float) -> bool:
        """Rewind about back_s and play. Whole presses, rounded UP: landing a few seconds early shows
        the snap; landing late misses it (the decider pads the replay's end for this). Up to
        SEEK_LATE_OK_S late is fine, because the target already starts 3s before the snap — so
        30.2s doesn't cost a whole extra press (10s more pre-roll)."""
        from redzone.contracts.switching import seek_model
        if self._task is asyncio.current_task():
            self.stage["stage"] = "rewinding"
        # [seek model] live lab, Nov 4: a 15 s step, the batch's first press swallowed (one extra is
        # sent), and a 2-4 s stall that itself puts the picture further behind (seek_model).
        presses = seek_model(self.cfg).sent(back_s)
        self._last_seek_sent = presses
        t0 = time.monotonic()
        if presses:
            key = self.cfg.get("replay.seek_key", "KEYCODE_MEDIA_REWIND")
            # sent() already holds the wake press (the batch's first only shows YouTube TV's seek bar)
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
        if self._replay_scrubs(target):
            return await self._scrub_park(stick, target, back_s, replay_id)
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
            if self._replay_scrubs(p.target):
                return False   # a drifted YouTube TV park can't be stepped back a few s (the scrub's floor is ~38 s)
            return await self._rewind(stick, time.time() - p.at)   # ends with PLAY
        if p is not None and self._replay_scrubs(p.target):
            # [Nick Nov 6] a YouTube TV pause can lose its place: a 120 s pause reset the position, and on non-game
            # programming a resumed pause went back to live 20-30 s later. Only a short park the player still reports
            # PAUSED is rolled; anything else keeps the current game (never a live picture shown as the replay).
            age = time.time() - p.at
            if age > float(self.cfg.get("replay.park_max_age_s", 90.0)):
                log.info("replay park on %s is %.0fs old: not rolled", stick.host, age)
                return False
            pb = await stick.playback(self._package(stick, p.target))
            if pb.get("state") != "paused":
                log.info("replay park on %s reads %s, not paused: not rolled", stick.host, pb.get("state"))
                return False
        sent = await stick.key("KEYCODE_MEDIA_PLAY")
        return bool(sent and sent.ok)

    async def read_screen(self, targets) -> str | None:
        """[overseer Nov 7 23:00] After a restart nothing says which game the TV shows (on_target None), so jump
        mode held 'screen_game_unknown' for the whole night. Read the main stick's YouTube TV media session and
        adopt the one live target whose channel it names. No tune, no key: a read only. Never after a replay
        (a rewound picture is not live), never during a switch or flip, never on an unknown input."""
        stick = self.main
        if (self.on_target is not None or self._replayed or stick is None or self.input_unknown or self._flipping
                or (self._task and not self._task.done()) or self._lock.locked()):
            return None
        yt = sorted({t for t in targets if t and t.startswith("youtubetv:")})
        pkg = self._package(stick, yt[0]) if yt else None
        if not pkg:
            return None
        pb = await stick.playback(pkg)
        if pb.get("pkg") != pkg or not playback_ready(pb):
            return None
        hits = [t for t in yt if (m := channel_matcher(t)) is not None and m(pb) is True]
        if len(hits) != 1 or self.on_target is not None or self.main is not stick or (self._task and not self._task.done()):
            return None
        self._adopt_once = False
        log.info("read back the screen after a restart: %s (%s)", hits[0], pb.get("subtitle"))
        stick.remember(hits[0], pb)
        self.on_target = hits[0]
        return hits[0]

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
        """Hold until nothing's on screen in the game we're leaving. Seconds waited; None = gave up.

        While a switch waits here it holds the lock, which blocks the visible-stick watchdog. So it
        looks at the screen itself every few seconds and clears a stalled player ("Still watching?",
        a crash to home): e2e night test, seed 2, a prompt sat on screen 96 s through four ~25 s
        "no safe moment" waits in a row."""
        t0 = time.monotonic()
        look_at = t0 + WAIT_SCREEN_LOOK_S
        try:
            return await self._wait_safe_loop(may_cut, t0, look_at)
        finally:
            self._safe_waiting = False

    async def _wait_safe_loop(self, may_cut, t0: float, look_at: float) -> float | None:
        while may_cut is not None and not may_cut():
            mine = self._task is asyncio.current_task()
            self._safe_waiting = mine
            if mine:
                self.stage.update(stage="waiting_safe_exit", reason="waiting for the current screen's safe exit window")
            if time.monotonic() - t0 >= MAX_SAFE_WAIT_S:
                return None
            if mine and time.monotonic() >= look_at:
                look_at = time.monotonic() + WAIT_SCREEN_LOOK_S
                try:
                    await self._unstall_visible()
                except asyncio.CancelledError:
                    raise
                except Exception:   # a repair must never break the switch that is waiting
                    log.exception("screen check while waiting for a safe moment failed")
            await asyncio.sleep(SAFE_POLL_S)
        return time.monotonic() - t0

    async def _unstall_visible(self) -> None:
        """The visible-stick watchdog's check and repair (_watch_visible), run inside a switch that
        holds the lock while it waits for a safe moment. It only acts on a screen that is already not
        playing, so it can't cut a live play; the switch's own safe-moment wait is unchanged.

        During a catch-up replay on_target is None by design (the picture is behind live), and that
        is where the e2e night's prompt came up: then only the on-screen prompt is cleared (read from
        a screenshot, CENTER only when a prompt is seen), never MEDIA_PLAY or a re-link to live."""
        stick = self.main
        if (stick is None or not self.live_control or self.input_unknown or self._flipping
                or (self.visible_watch_ok is not None and not self.visible_watch_ok())):
            return
        replay = self.on_target is None
        target = self.on_target or self._replay_target(stick)
        if not target:
            # The TV shows this stick but we've forgotten what (it came back from an adb drop without
            # hardening.sticks, _forget): night lab seed 2, a "Still watching?" sat 110 s on screen
            # through four "no safe moment" waits. Prompt-only, like a replay: a paused app that draws
            # its own prompts gets CENTER only when a screenshot shows the prompt; nothing else.
            target = await self._paused_prompt_app(stick)
            if not target:
                return
        pkg = self._package(stick, target)
        pb = await stick.playback(pkg)
        if self.main is not stick or (self.on_target or self._replay_target(stick) or target) != target \
                or pb.get("read_ok") is False:
            return
        now = time.time()
        if pb.get("state") in ("playing", "buffering", "connecting") and not self._stuck_loading(
                stick, target, pb.get("state")):
            self._visible_bad_since = None
            return
        if self._visible_bad_since is None:
            self._visible_bad_since = now
            log.info("%s (on screen) is %s, not playing %s, while a switch waits; checking again",
                     stick.host, pb.get("state"), target)
            return
        if now - self._visible_bad_since < VISIBLE_STALL_S or now - self._visible_fix_at < VISIBLE_FIX_GAP_S:
            return
        self._visible_fix_at = now
        self._loading_since.pop((stick.host, target), None)
        r0 = time.monotonic()
        if replay:
            how = "prompt" if await self._clear_screen_prompt(stick, target, pkg) else None
        else:
            how = await self._resume_playback(stick, target, 0.0)
        if how:
            log.info("watchdog (during a switch wait): %s playing again on %s (%s, %.1fs)", target,
                     stick.host, how, time.monotonic() - r0)
            self.latency.record("resume", time.monotonic() - r0)
            self._visible_bad_since = None
            self.note = ""
        elif not replay:
            self.note = f"The TV stopped playing {target}; couldn't restart it yet (retrying)"

    async def _paused_prompt_app(self, stick: Stick) -> str | None:
        """"<app>:" for an app on this stick that draws its own on-screen prompts (YouTube TV) and
        reads paused in front, else None. Only for a prompt-only check (_unstall_visible)."""
        for app, tuner in TUNERS.items():
            pkg = stick.packages.get(getattr(tuner, "role", app))
            if not pkg or not getattr(tuner, "screenshot_prompts", False):
                continue
            pb = await stick.playback(pkg)
            if pb.get("state") == "paused" and pb.get("pkg") in (None, pkg):
                return f"{app}:"
        return None

    def _replay_target(self, stick: Any) -> str | None:
        """The target of the catch-up replay this stick was flipped to, while nothing has flipped since."""
        r = self._replay_shown
        if r is None or r[1] is not stick or r[2] != self._flip_gen or self.on_target is not None:
            return None
        return r[0]

    async def execute(self, target: str | None, replay_back_s: float | None = None,
                      may_cut: Callable[[], bool] | None = None, replay_id: str | None = None,
                      issued_at: float | None = None, entry_rewind_s: float = 0.0, entry_cue: str = "") -> Outcome:
        """replay_back_s is "rewind this far from the live edge" as of issued_at (the decision);
        every second since (queued, tuning, waiting) is added so the replay still lands before the snap.

        entry_rewind_s [cut timing]: a LIVE switch whose picture should come up this far behind the
        live edge (whole seek steps), on the dead ball before the play it would otherwise join
        mid-way. The game stays the live target; only its delay grows by that much.

        entry_cue "hidden" [seek model]: with two sticks the rewind runs on the hidden stick BEFORE the
        flip (no glimpse of the live edge, and the 2-4 s seek stall passes off screen); otherwise, or if
        that path isn't taken, the visible stick rewinds right after the flip."""
        marks = self._marks = [("started", time.time())]
        self._hidden_entry = entry_rewind_s if (entry_cue == "hidden" and not replay_back_s) else 0.0
        self._hidden_entry_done = False
        self._entry_cue = entry_cue
        self._entry_rewind = entry_rewind_s if not replay_back_s else 0.0
        why = self._why = {}
        try:
            out = await self._execute(target, replay_back_s, may_cut, replay_id, issued_at)
            entry_rewind_s = self._entry_rewind   # [EXP-741 B] a tap may arm its rewind at the cut (arm_entry_rewind)
            if self._hidden_entry_done and out.ok:
                out.detail += f"; rewound {entry_rewind_s:.0f}s to the dead ball before the flip"
            elif entry_rewind_s and out.ok and not out.dry_run and not replay_back_s and self.main is not None:
                if not await self._rewind(self.main, entry_rewind_s):
                    out = Outcome(False, target, out.took, "entry rewind failed; position unknown", False,
                                  path=out.path)
                else:
                    self._mark("rewind_done")
                    out.detail += f"; rewound {entry_rewind_s:.0f}s to the dead ball"
                    if self.seek_log_path is not None and target:
                        pb = await self.main.playback(self._package(self.main, target))
                        self._seek_sample(self.main, target, entry_rewind_s, pb)
                        if playback_ready(pb):
                            self.main.remember(target, pb)
        finally:
            self._marks = None
            self._hidden_entry = 0.0
            self._entry_rewind = 0.0
            self._why = None
        marks.append(("done", time.time()))
        out.marks = marks
        out.provenance = self._provenance(out, marks, why)
        return out

    def _provenance(self, out: Outcome, marks: list, why: dict) -> dict:
        """[provenance] The controller's part of a switch's provenance (redzone/trace.py): which path
        and stick carried it, how the TV input changed and the game came back, and every playbook
        that fired on the way ("playbook:<id>" marks). Serves: provenance traces (Cycle 06 #2). Owner: lane 3."""
        control = {"path": out.path, **why}
        return {"control": control,
                "playbook": [n.split(":", 1)[1] for n, _ in marks if n.startswith("playbook:")]}

    def _note(self, key: str, value) -> None:
        """[provenance] Record how one step of the switch now running went. Preloads don't note."""
        if self._why is not None:
            self._why[key] = value

    def _mark(self, step: str) -> None:
        """Stamp one step of the switch now running (the trace). Preloads and the watchdogs don't mark."""
        marks = getattr(self, "_marks", None)
        if marks is not None:
            marks.append((step, time.time()))

    async def _execute(self, target: str | None, replay_back_s: float | None = None,
                       may_cut: Callable[[], bool] | None = None, replay_id: str | None = None,
                       issued_at: float | None = None) -> Outcome:
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
        if getattr(self, "_preloaded_only", False) and not replay_back_s \
                and not (self.two_stick and self._live_on_hidden(target)):
            # [jump mode, overseer Nov 7 20:01:50] a jump tuned on the way (29 s) and lost its dead ball
            return Outcome(False, target, 0.0, STANDBY_NOT_READY, False, path="refused", screen_unchanged=True)
        mv = self.prime_mv
        if mv is not None and not replay_back_s and self.main is not None and mv.has_tile(self.main.host, target):
            out = await self._execute_tile(mv, target, may_cut, t0)
            if out is not None:
                return out
        if self.two_stick:
            out = await self._execute_two(target, replay_back_s, may_cut, t0, replay_id, back_at)
            if out is not None:
                return out
        # Recheck after waiting for the controller lock and after a failed
        # HDMI attempt: the safe gap may have closed during those operations.
        self._mark("safe_start")
        if await self._wait_safe(may_cut) is None:
            return Outcome(False, target, time.time() - t0,
                           "no safe moment to cut — kept the current game", False, screen_unchanged=True)
        self._mark("safe_done")
        if not self.live_control:
            return Outcome(False, target, 0.0, "TV control was disabled", True)
        return await self._execute_one(target, replay_back_s, t0, back_at)

    async def _execute_tile(self, mv: PrimeMultiviewTuner, target: str, may_cut, t0: float) -> Outcome | None:
        """The game is a tile of the Prime multiview on screen: at a safe moment, make it main
        (~1 s, no relaunch). None = nothing was pressed, so the caller's normal path runs instead."""
        main = self.main
        if mv.is_main(main.host, target) and await self._still_on_visible(target):
            return Outcome(True, target, 0.0, "already on it", False, time.time(), path="already")
        self._mark("safe_start")
        waited = await self._wait_safe(may_cut)
        if waited is None:
            return Outcome(False, target, time.time() - t0, "no safe moment to cut — kept the current game", False,
                           path="prime_tile", waited=MAX_SAFE_WAIT_S, screen_unchanged=True)
        self._mark("safe_done")
        if not self.live_control:
            return Outcome(False, target, 0.0, "TV control was disabled", True)
        self.on_target = None
        self._mark("tune_start")
        res = await mv.switch(main, target, may_cut)
        self._mark("tune_done")
        if res.ok:
            self.latency.record("prime_tile", res.took)
            self.on_target = target
            self._visible_bad_since = None
            return Outcome(True, target, res.took, "; ".join(res.steps[-2:]), False, time.time(), path="prime_tile",
                           waited=waited, stick=main.host)
        if res.screen_changed is False:
            return None
        # Keys were pressed and the main tile isn't the one we wanted (or can't be read). The session is
        # forgotten, so the next decision takes a normal path; never chain a 30 s tune into this cut.
        return Outcome(False, target, time.time() - t0, res.error, False, path="prime_tile", waited=waited,
                       stick=main.host)

    async def _execute_one(self, target, replay_back_s, t0, back_at=None) -> Outcome:
        if replay_back_s and self._replay_scrubs(target):
            # [Nick, Nov 7] the viewer never sees a rewind: a YouTube TV replay is only ever scrubbed on the hidden stick
            return Outcome(False, target, 0.0, "replay needs the hidden stick (never rewound on screen)", False,
                           path="single", screen_unchanged=True)
        if not replay_back_s and await self._still_on_visible(target):
            return Outcome(True, target, 0.0, "already on it", False, time.time(), path="already")
        main = self.main
        self.on_target = None   # unknown until this tune is confirmed (a cancelled tune leaves it unknown)
        self._mark("tune_start")
        res = await self._tune(main, target, self._exit_check)
        self._mark("tune_done")
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
        if replay and self._replay_scrubs(target) and not self._parked_for(target, replay_id):
            why = self._scrub_park_refusal(target, replay_back_s)
            if why:
                # [Nick, Nov 8 live] a replay that can't be parked never tunes the hidden stick: refused, untouched
                return Outcome(False, target, time.time() - t0, f"replay refused: {why}", False, path="refused",
                               screen_unchanged=True)
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
        elif not replay and self._left_for(hid, target) is not None and await self._left_paused(hid, target):
            path = "left hold"   # playbook=STK-07: paused at the dead ball we left it on; it plays after the flip
        elif not replay and self._live_on_hidden(target) and await self._steady_playing(hid, target) is not None:
            path = "preloaded"
        elif getattr(self, "_preloaded_only", False):
            # [jump mode] the ready standby stopped playing: no re-tune on a jump (it would miss the dead ball)
            return Outcome(False, target, time.time() - t0, STANDBY_NOT_READY + " (stopped playing)", False,
                           path="refused", screen_unchanged=True)
        else:
            # A standby that stopped (a stick that lost the TV input can pause its app) gets tuned
            # again here instead of failing the switch at flip time.
            path = "tuned"
            br = getattr(hid, "breaker", None)
            if br is not None and br.is_open:
                # Hung hidden stick: nothing is sent, so the screen is untouched (not an error toward
                # safe mode); the breaker paces the retries.
                return Outcome(False, target, time.time() - t0, f"{STICK_HUNG}: {hid.host}, retry in "
                               f"{br.retry_in():.0f}s", False, path=path, screen_unchanged=True)
            sleeping = await self._sleeping(hid)
            if sleeping:
                # Waking a Fire TV makes it take the TV input (CEC): only ever at a safe moment.
                if await self._wait_safe(may_cut) is None:
                    return Outcome(False, target, time.time() - t0, no_moment, False, path=path,
                                   waited=MAX_SAFE_WAIT_S, screen_unchanged=True)
                visible_untouched = False  # waking can already change the TV input via CEC
            self._set_hidden(target, False)
            self._mark("tune_start")
            res = await self._tune(hid, target, may_cut if sleeping else None)
            self._mark("tune_done")
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
                self._mark("park_done")
        if not replay and path in ("tuned", "preloaded") and self._scrub_wanted(target) \
                and hid.scrubbed(target) is None and not self._buffered_jump():
            # [buffered jump] not inside a switch: tune + banner wait + scrub + the safe-moment wait held the command
            # lock 156 s (overseer Nov 8 22:15, stuck_lock). Buffered scrubs run on the standby (preload/buffer_standby);
            # this picture lands live-edge and the next bootstrap cut moves the screen onto a buffered one.
            # [scrub entry, item 4] seek the hidden stick into the buffer BEFORE the flip, with the old game still
            # on screen: the picture comes up B s behind live and the safe-moment wait below then judges the
            # dead ball on that delayed picture (the engine's may_cut reads the delay book, which _set_held
            # shifted by the measured label). Never a flip to a stream whose offset we didn't read.
            b = self._buffer_for(hid, target, TuneResult(True, target, 0.0, [], ""))
            if b > 0:
                self._mark("scrub_start")
                ok = await self._scrub_into(hid, target, b)
                self._mark("scrub_done")
                self._note("scrub", self.held.get(target) if ok else "failed")
                if not ok and not (self._scrub_fallback_live() and self._live_on_hidden(target)):
                    return Outcome(False, target, time.time() - t0, "buffer entry failed; kept the current game",
                                   False, path=path, screen_unchanged=visible_untouched)
        self.latency.note_preloaded(target.split(":", 1)[0], path != "tuned")
        work = time.time() - t0
        # Wait for a safe moment on what's visible, THEN flip. This is where "never mid-play" is enforced
        # to the second, because the cut happens at the flip.
        self._mark("safe_start")
        waited = await self._wait_safe(may_cut)
        if waited is None:
            return Outcome(False, target, work, no_moment, False, path=path, waited=MAX_SAFE_WAIT_S,
                           screen_unchanged=visible_untouched)
        self._mark("safe_done")
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
            elif path == "left hold":
                if not await self._left_paused(hid, target):
                    # playbook=SAFE-08 for a left-game hold (STK-07): it must still be paused where we
                    # left it (not resumed, not past the cap) right before the flip.
                    log.info("playbook=SAFE-08 the left-game hold of %s on %s is gone: flip skipped, kept the "
                             "current game", target, hid.host)
                    self._left = None
                    self._set_hidden(None, False)
                    return Outcome(False, target, time.time() - t0, "standby stream is no longer confirmed", False,
                                   path=path, screen_unchanged=visible_untouched)
            else:
                back = getattr(self, "_hidden_entry", 0.0)
                if back:
                    # [seek model] rewind the hidden stick to the dead ball before the flip, then take
                    # its position as the stream's new anchor (a seek breaks still_on's clock).
                    was = hid.scrubbed(target)   # [scrub entry] read before the rewind's presses clear it
                    if not await self._rewind(hid, back):
                        self._set_hidden(None, False)
                        return Outcome(False, target, time.time() - t0, "entry rewind failed; kept current game",
                                       False, path=path, screen_unchanged=visible_untouched)
                    self._mark("rewind_done")
                    pb0 = await hid.playback(self._package(hid, target))
                    self._seek_sample(hid, target, back, pb0)
                    if playback_ready(pb0):
                        hid.remember(target, pb0, scrubbed_s=None if was is None else was + back)
                    self._hidden_entry_done = True
                if self._snap_park is not None and self._snap_park.target == target:
                    # [buffered jump] the hidden stick holds the line-up before its snap: PLAY, then flip
                    if not await self._snap_roll(hid, target):
                        self._set_hidden(None, False)
                        return Outcome(False, target, time.time() - t0, "snap park lost; kept current game", False,
                                       path=path, screen_unchanged=visible_untouched)
                    self._mark("snap_roll")
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
                if hid.still_on(target, pb) and (self._note_steady(hid, target, pb) or 0.0) < FLIP_STEADY_S:
                    # [SYNC 1] Its state changed since the check (a buffering blip): steady again first.
                    pb = await self._steady_playing(hid, target) or {}
                if not hid.still_on(target, pb):
                    # playbook=SAFE-08: not steady PLAYING right before the flip. Skip it and stay on the
                    # current game; the next decision re-tunes it and tries again at the next dead ball.
                    self._mark("playbook:SAFE-08")
                    log.info("playbook=SAFE-08 %s on %s isn't steady playing (%s): flip skipped, kept the current game",
                             target, hid.host, pb.get("state"))
                    self._set_hidden(None, False)
                    return Outcome(False, target, time.time() - t0, "standby stream is no longer confirmed", False,
                                   path=path, screen_unchanged=visible_untouched)
            # Readback and replay reseeking may have consumed the whole gap.
            if may_cut is not None and not may_cut():
                return Outcome(False, target, time.time() - t0, "safe exit window closed during preparation", False,
                               path=path, screen_unchanged=visible_untouched)
            self._mark("flip_start")
            flipped = await self._flip_to(hid)
            self._mark("flip_done")
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
        t_flip = time.time()
        back = self._left if path == "left hold" else None   # STK-07: it's on screen now, so no longer a hold
        self._left = None
        self.active = self.sticks.index(hid)
        self._flip_gen += 1
        self.on_target = None if replay else target
        self._replay_shown = (target, hid, self._flip_gen) if replay else None
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
        hold_left = bool(old_target and not replay and self._holds_left(old_target, old_stick))
        if hold_left:
            # [buffer.hold_left] playbook=STK-07: keep the game we left paused at this dead ball instead of
            # reloading it at the live edge, so going back is a flip plus a catch-up from right here.
            self._refresh_task = asyncio.create_task(self._hold_left(old_target, old_stick, t_flip))
        flip_s, took = self._flip_s, work + (time.time() - t1)
        detail = f"flipped to {self.hdmi.get(hid.host)} ({path})"
        if self._flip_how.startswith("tv:"):
            detail += f" by the TV's input ({self._flip_how[3:]})"
        resume_s, how, stall = None, None, None
        home = self._home_flipped and not replay
        tv_flip = self._flip_how.startswith("tv:") and not replay
        if tv_flip:
            # Nothing was pressed on this stick, so its game should simply still be playing. Check
            # it right away (it's on screen now, so pressing play can't cut anything): a player that
            # paused anyway gets MEDIA_PLAY / one fresh link, the same ladder as after HOME.
            r0 = time.monotonic()
            self._flipping = True
            self.stage.update(stage="resuming", reason="checking the game is still playing after the input change")
            try:
                pkg = self._package(hid, target)
                # A left-game hold (STK-07) is paused on purpose: play it at once, no wait for it to play.
                pb = None if back else await self._await_playing(hid, target, pkg, TV_PLAYING_WAIT_S)
                if pb is not None:
                    how = "auto"
                    stall = self._book_lag(hid, target, pb)
                else:
                    log.info("%s isn't playing %s right after the TV input change; resuming it", hid.host, target)
                    how = await self._resume_playback(hid, target, 0.0)
            except Exception:
                log.exception("playing check after the TV input change crashed")
            finally:
                self._flipping = False
            resume_s = time.monotonic() - r0
            if how is None:
                detail += "; game not confirmed playing yet (watchdog will retry)"
                self._visible_bad_since = time.time()
            elif how != "auto":
                detail += f"; resumed by {how.replace('_', ' ')}"
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
            if how is not None:
                self._mark("resume_done")
            if how is None:
                # The input DID change (that's the cut); the picture isn't confirmed. The visible-stick
                # watchdog keeps retrying, and the next switch re-tunes instead of trusting it.
                detail += "; game not confirmed playing yet (watchdog will retry)"
                self._visible_bad_since = time.time()
            elif how != "auto":
                detail += f"; resumed by {how.replace('_', ' ')}"
        settled = self._book_flip(hid, target, flip_s, resume_s, how)
        self._note("stick", self.hdmi.get(hid.host))
        self._note("flip", self._flip_how or None)
        self._note("resume", how)
        self._note("settled", settled)
        if back is not None and how in ("auto", "media_play", "prompt"):
            # It played on from the dead ball we left it on: that far behind live now (the sync shifts by it).
            behind = back.behind_s(time.time())
            self._set_held(target, behind)
            self._buffer_log("left_resumed", target, hid.host, behind, None)
            log.info("playbook=STK-07 %s resumed at the dead ball we left it on: %.1fs behind live", target, behind)
            detail += f"; resumed where we left it ({behind:.0f}s behind live)"
        if stall:
            detail += f"; {stall:.1f}s of buffering added to its delay"
        if old_target and not replay and not hold_left:
            self._refresh_task = asyncio.create_task(self._refresh_left(old_target, old_stick, time.time()))
        self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
        return Outcome(True, target, took, detail, False, time.time(), path=path, waited=waited,
                       flip_s=None if flip_s is None else round(flip_s, 2),
                       resume_s=None if resume_s is None else round(resume_s, 2),
                       resumed=(how is not None) if (home or tv_flip) else None,
                       settled=settled, stall_s=stall or None)

    async def _flip_to(self, stick: Stick) -> bool:
        """Make the TV show this stick.

        1. The TV's own input change (local Samsung remote or SmartThings, whichever tv_setup.py
           proved): nothing is pressed on either stick, so both keep playing. ~1 s.
        2. HOME on the stick (it announces <Active Source> over CEC and the TV follows; no tokens
           needed). HOME backgrounds the app, so it is relaunched and the caller confirms the game
           is playing again (_resume_playback). A TV command whose delivery was ambiguous may already
           have switched: HOME on the SAME destination stick can only push the TV the same way.
        3. If the TV path wasn't set up: the TV's network remote / SmartThings as before.
        Records self._flip_s (command -> confirmed on that stick) and self._flip_how."""
        t0 = time.monotonic()
        self.stage.update(stage="verifying_input", reason="requesting and verifying the display input")
        self._home_flipped = False
        self._flip_s = None
        self._flip_how = ""
        ok = False
        requested = self.hdmi.get(stick.host)
        if not requested:
            self.tv.last_error = "no input configured for this stick"
            return False
        if self._exit_check is not None and not self._exit_check():
            self.tv.last_error = "safe exit window closed before CEC command"
            return False
        tried_tv = False
        paths = self.tv.paths() if self.tv.ready() else []
        if paths:
            tried_tv = True
            self.stage.update(stage="verifying_input", reason=f"switching the TV to {requested}")
            ok = await self.tv.set_input(requested, methods=paths)
            ev = self.tv.last_input
            if ok:
                took = ev.total_s if ev and ev.total_s is not None else time.monotonic() - t0
                self._flip_s = took
                self._flip_how = f"tv:{ev.source if ev else 'tv'}"
                self.latency.record("flip", took)
                self.latency.record("tv_flip", took)
                self._awake_at[stick.host] = time.time()
                if took > TV_FLIP_MAX_S:
                    log.warning("TV input change to %s took %.1fs (%s)", requested, took, ev.how if ev else "")
                return True
            log.warning("TV input change to %s not confirmed (%s: %s); falling back to HOME on %s",
                        requested, ev.source if ev else "?", self.tv.last_error or (ev.detail if ev else ""),
                        stick.host)
            if self._exit_check is not None and not self._exit_check() and not (ev and ev.accepted is None):
                # Nothing reached the TV and the moment passed: nothing changed on screen either.
                self.tv.last_error = "safe exit window closed before display command"
                return False
        # Fire OS has no shell CEC command and blocks the system call (HDMI_CEC permission),
        # but HOME makes the stick announce <Active Source> over the HDMI cable, and the TV
        # follows. HOME backgrounds the preloaded app, so it is relaunched right after; the
        # caller then confirms the game is really playing again (_resume_playback).
        # Always tried before onetouchplay: it's the only stick-side path that works on these
        # sticks, so a rejected onetouchplay below must never bar it.
        h0 = time.monotonic()
        if await self._home_flip_cec(stick):
            took = self._flip_s if self._flip_s is not None else time.monotonic() - h0
            self._flip_s = took
            self.latency.record("flip", took)
            self.latency.record("home_flip", took)
            self._awake_at[stick.host] = time.time()
            self._home_flipped = True
            self._flip_how = "home"
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
        if not ok and not tried_tv:
            if self._exit_check is not None and not self._exit_check():
                self.tv.last_error = "safe exit window closed before display command"
                return False
            ok = await self.tv.set_input(requested)
            if ok:
                self._flip_how = f"tv:{self.tv.last_input.source if self.tv.last_input else 'tv'}"
        if ok:
            self._flip_s = time.monotonic() - t0
            self.latency.record("flip", self._flip_s)
            self._awake_at[stick.host] = time.time()
        return ok

    async def _home_flip_cec(self, stick: Stick, target: str | None = None) -> bool:
        """HOME flip, also onto a stick whose HDMI-CEC we turned off (scripts/stick_cec.py): CEC goes
        back on for this one flip (HOME can't take the TV without it) and off again once the TV shows
        it, so it still never pauses when it loses the input later."""
        if not self.cec_off(stick):
            return await self._home_flip(stick, target)
        log.info("%s has HDMI-CEC off: turning it on for a HOME fallback", stick.host)
        r = await stick.shell(CEC_SET_CMD.format(v=1), timeout=4)
        if not r.ok:
            return False
        self._cec_on[stick.host] = True
        await asyncio.sleep(CEC_REENABLE_S)
        ok = await self._home_flip(stick, target)
        old = self._cec_restore.pop(stick.host, None)
        if old and not old.done():
            old.cancel()

        async def restore():
            await asyncio.sleep(3.0)   # it IS the TV's input now: turning CEC off changes nothing on screen
            r = await stick.shell(CEC_SET_CMD.format(v=0), timeout=4)
            if r.ok:
                self._cec_on[stick.host] = False
                log.info("%s: HDMI-CEC off again after the HOME fallback", stick.host)
        if self.cfg.get("two_stick.keep_cec_off", True):
            self._cec_restore[stick.host] = asyncio.create_task(restore())
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
        the game's page (Watch + Watch Live again, ~30 s). So: HOME (twice if the first didn't switch
        the TV), wait for <Active Source>, THEN bring Prime's screen back to the front
        (REORDER_TO_FRONT, or the game's link where that is refused). Prime usually shows the
        game's detail page there, not the player: _resume_playback presses through it."""
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
        if self._dialog_back(stick, target, last, pkg):
            # playbook=APP-04: still paused after MEDIA_PLAY and no prompt read: YouTube TV draws an
            # "update available" window over its player that only BACK dismisses (it pauses the
            # player and ignores play). One BACK, then the re-link below if that wasn't it.
            log.info("playbook=APP-04 %s: %s still paused after play; one BACK for a dialog", stick.host, target)
            await stick.key("KEYCODE_BACK")
            pb = await self._await_playing(stick, target, pkg, RESUME_PLAY_WAIT_S)
            if pb is not None:
                stick.remember(target, pb)
                return "back"
            last = await stick.playback(pkg)
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

    def _standby_blocked(self, hid: Stick, target: str) -> bool:
        """[APP-04] A flip pauses the stick it leaves once (STK-05). The same standby pausing again and
        again off screen, with no flip, is the app blocked behind a window that keeps coming back (a
        forced update): stop re-tuning it there for PRELOAD_BACKOFF_S x 5 and say so on the phone."""
        now = time.time()
        times = [x for x in self._repaused.get((hid.host, target), []) if now - x < STANDBY_REPAUSE_S] + [now]
        self._repaused[(hid.host, target)] = times
        if len(times) < STANDBY_REPAUSE_N:
            return False
        self._repaused.pop((hid.host, target), None)
        self._failed[target] = now + 5 * PRELOAD_BACKOFF_S
        self.note = (f"{target.split(':', 1)[0]} keeps stopping on {getattr(hid, 'name', '') or hid.host} (an app update "
                     "window?); not pre-tuning it there for 10 min")
        log.warning("playbook=APP-04 standby %s on %s paused %d times in %.0f min: blocked there for %.0f s",
                    target, hid.host, len(times), STANDBY_REPAUSE_S / 60, 5 * PRELOAD_BACKOFF_S)
        return True

    @staticmethod
    def _dialog_back(stick: Stick, target: str, pb: dict, pkg: str | None) -> bool:
        """[APP-04] Worth one BACK: YouTube TV is in front and paused (an update dialog over its player
        reads exactly so). Not Prime (BACK leaves its player for the detail page) and not the home screen."""
        return (target.startswith("youtubetv:") and pb.get("state") == "paused"
                and (not pkg or pb.get("pkg") in (None, pkg)))

    async def restore_visible(self, target: str, relink: bool = False, why: str = "") -> str | None:
        """The supervisor saw the stick on screen showing the Fire TV home screen or an app page
        instead of the game, twice, 20 s apart. Put the game back: a fresh deep link (relink, for
        the home screen) or the usual resume ladder. Only called when the game is clearly NOT
        playing there, so no live play can be cut. Returns how it came back, or None."""
        if not self.live_control or self.input_unknown or self._flipping:
            return None
        # Runs as the controller's resume task: any real switch (submit) or Pause cancels it, so a
        # restore can never hold the controller for its whole 25-55 s relink.
        if self._resume_task and not self._resume_task.done():
            return None
        self._resume_task = asyncio.create_task(self._restore_visible(target, relink, why))
        try:
            return await asyncio.shield(self._resume_task)
        except asyncio.CancelledError:
            if self._resume_task.cancelled():
                return None
            raise

    async def _restore_visible(self, target: str, relink: bool, why: str) -> str | None:
        async with self._lock:
            stick = self.main
            if stick is None or self.input_unknown:
                return None
            self.stage = {"stage": "resuming", "target": target, "reason": why or "the screen isn't showing the game"}
            self._visible_fix_at = time.time()
            r0 = time.monotonic()
            how: str | None
            if relink:
                try:
                    limit = 55.0 if target.startswith("prime:") else RELINK_MAX_S
                    res = await asyncio.wait_for(self._tune(stick, target), limit)
                    how = "relink" if res.ok else None
                except asyncio.TimeoutError:
                    how = None
            else:
                how = await self._resume_playback(stick, target, 0.0)
            if how:
                self.on_target = target
                self._visible_bad_since = None
                self.latency.record("resume", time.monotonic() - r0)
                self.note = ""
                self.stage = {"stage": "confirmed", "target": target, "reason": f"restored by {how}"}
            else:
                self.note = f"The TV isn't showing {target}; couldn't restore it yet"
                self.stage = {"stage": "failed", "target": target, "reason": self.note}
            return how

    def _book_flip(self, stick: Stick, target: str | None, flip_s: float | None, resume_s: float | None,
                   how: str | None) -> bool | None:
        """The latency book for the phone: input change and picture-back, measured separately.
        Returns whether the flip settled (PLAYING within FLIP_SETTLE_S); None = nothing was checked."""
        if resume_s is not None and how is not None:
            self.latency.record("resume", resume_s)
        settled = None if resume_s is None else (how is not None and resume_s <= FLIP_SETTLE_S)
        if settled is not None:
            self.settle_book["n"] += 1
            self.settle_book["ok"] += int(settled)
            if not settled:
                log.info("flip to %s on %s didn't settle within %.0fs (%s after %.1fs)", target, stick.host,
                         FLIP_SETTLE_S, how or "unconfirmed", resume_s)
        self.last_flip = {"at": time.time(), "stick": stick.host, "hdmi": self.hdmi.get(stick.host),
                          "target": target, "flip_s": None if flip_s is None else round(flip_s, 2),
                          "resume_s": None if resume_s is None else round(resume_s, 2),
                          "resumed": how is not None, "how": how or "unconfirmed", "settled": settled,
                          "settle_rate": dict(self.settle_book)}
        return settled

    # --- [SYNC 1] steady before the flip, stalls booked into the delay -------------------

    def _note_steady(self, stick: Stick, target: str, pb: dict) -> float | None:
        """Seconds this stick has provably played `target` with no state change: still_on (the stream
        we tuned, no pause or seek) and the same `updated` stamp (the app's last state change) as a
        read that long ago. None = not on it; 0.0 = on it, but a state change (buffering -> playing)
        just now."""
        if not stick.still_on(target, pb):
            self._steady.pop(stick.host, None)
            return None
        now, key = time.monotonic(), (target, pb.get("updated"))
        if stick.scrubbed(target) is not None:
            # [scrub entry] after a seek media_session's stamp and position crawl on: steady = playing since the
            # scrub's own playing check (still_on above), not an unchanged `updated` stamp
            key = (target, ("scrub", (stick.tuned or {}).get("scrubbed_at")))
        seen = self._steady.get(stick.host)
        if seen is None or seen[0] != key:
            self._steady[stick.host] = (key, now)
            return 0.0
        return now - seen[1]

    async def _steady_playing(self, stick: Stick, target: str) -> dict | None:
        """The hidden stick's report once it has been PLAYING `target`, steady, FLIP_STEADY_S; None if
        it isn't within FLIP_STEADY_WAIT_S, or says paused/stopped (believed at once: re-tune it).
        A stall it went through while hidden is booked into the delay (_book_lag)."""
        pkg = self._package(stick, target)
        end = time.monotonic() + FLIP_STEADY_WAIT_S
        while True:
            pb = await stick.playback(pkg)
            age = self._note_steady(stick, target, pb)
            if age is not None and age >= FLIP_STEADY_S:
                self._book_lag(stick, target, pb)
                return pb
            if age is None and pb.get("state") in ("paused", "stopped"):
                return None
            left = end - time.monotonic()
            if left <= 0:
                log.info("standby %s on %s not steady within %.0fs (%s): re-tuning", target, stick.host,
                         FLIP_STEADY_WAIT_S, pb.get("state"))
                return None
            await asyncio.sleep(min(left, max(RESUME_POLL_S, FLIP_STEADY_S - (age or 0.0))))

    def _book_lag(self, stick: Stick, target: str, pb: dict) -> float:
        """Buffering since the tune anchor, booked once into the target's delay. The app stamps each
        report with the stick's clock: lag = clock elapsed - position elapsed. A live stream that
        buffered 2 s plays 2 s further behind the edge for the rest of the game, so the delay book
        (via on_behind) moves by exactly that, and the stream is re-anchored so it's booked once.
        Returns the seconds booked (0.0 = none)."""
        t = stick.tuned or {}
        a = t.get("report") or {}
        if t.get("target") != target or t.get("scrubbed_s") is not None:
            return 0.0   # [scrub entry] a scrubbed stream's position crawls (a reporting quirk): never a stall
        pa, ua, pn, un = a.get("position"), a.get("updated"), pb.get("position"), pb.get("updated")
        if None in (pa, ua, pn, un) or pa <= 0 or pn <= 0:
            return 0.0
        lag = ((un - ua) - (pn - pa)) / 1000.0
        if lag < STALL_MIN_S:
            return 0.0      # no stall (a live player catching up runs slightly fast: never booked)
        if lag > STALL_MAX_S:
            log.info("%s on %s is %.1fs behind its tune anchor: not a stall, not booked", target, stick.host, lag)
            return 0.0
        self._set_held(target, round(self.held.get(target, 0.0) + lag, 1))
        stick.remember(target, pb)
        log.info("%s on %s buffered %.1fs: added to its delay", target, stick.host, lag)
        return round(lag, 1)

    # --- the hidden stick between switches ---------------------------------------------

    def preload(self, target: str | None, back_s: float | None = None, replay_id: str | None = None,
                *, refresh: bool = False, user: bool = False, urgent: bool = False, rotate: bool = False) -> bool:
        """Put the hidden stick on `target`: live, or parked on a replay (back_s from the live edge,
        replay_id). Returns True if a preload started. WHAT to preload is the engine's call
        (standby.Planner); this only refuses what can't or shouldn't happen right now.
        refresh=True is _refresh_left reloading the game we just left: it skips the preload gap and
        doesn't use it up, and the planner wins over it — a planner preload of another target
        cancels it, even mid-tune. user=True is a game opened on the phone (engine._peek): it skips the
        preload gap and replaces a planner preload still loading, never a switch."""
        if not (self.live_control and self.two_stick and target) or self.input_unknown:
            return self._refused("no two-stick control" if not self.input_unknown else "TV input unknown")
        # [buffered jump] the rotation may replace a scrubbed standby (every stick is scrubbed): its own rotate_due
        # (redzone/decider/buffered.py) decides when, so a ping-pong partner isn't re-tuned every tick
        if not user and not rotate and self._scrub_protected(target):
            self.preload_why = "a scrubbed standby is kept"
            # [scrub entry] Nick's live test (Nov 7): a preload overwrote a scrubbed hidden stick. A scrubbed standby
            # is kept until it's flipped to (or its anchor expires / its label goes stale); only a game you open on
            # the phone replaces it.
            return False
        if self._steal_task and not self._steal_task.done():
            return self._refused("TV-04 steal recovery holds the hidden stick")
        if replay_id is None and (target == self.on_target or self._live_on_hidden(target)):
            return self._refused("already there" if target != self.on_target else "that game is on screen")
        if replay_id is not None and self._parked_for(target, replay_id):
            return self._refused("already parked")
        now = time.time()
        if not refresh and self.can_tune(target) and self._plays(self.hidden, target):
            self._wish = (target, now)   # what the planner wants there, even if refused just now
        displace = None
        if not refresh and self._refreshing not in (None, target) and self._preload_task \
                and not self._preload_task.done():
            displace = self._preload_task   # a refresh of the game we left: the planner's pick wins
        elif user and self._preload_task and not self._preload_task.done() and self._preloading != (target, None):
            displace = self._preload_task   # what you opened on the phone wins over the planner's guess
        busy = bool(self._task and not self._task.done()) if displace else self._busy()
        if busy or now < self._asleep_until or not self.can_tune(target) or not self._plays(self.hidden, target) \
                or (not refresh and not user and not urgent and now - self._last_preload < MIN_PRELOAD_GAP_S):
            # [jump mode] why the standby didn't refill (data/jumps.jsonl standby rows)
            return self._refused("a switch or preload is running" if busy else "standby stick asleep"
                                 if now < self._asleep_until else "can't tune it now (failed tune backoff)"
                                 if not self.can_tune(target) else "the hidden stick can't play it"
                                 if not self._plays(self.hidden, target) else "preload gap")
        if displace is not None:
            log.info("planner wants %s on the hidden stick: dropping the refresh of %s", target, self._refreshing)
            displace.cancel()
        if not refresh:
            self._last_preload = now
            self._cancel_refresh()
        self.preload_why = ""
        self._preloading = (target, replay_id)
        self._refreshing = target if refresh else None
        self._preload_task = asyncio.create_task(self._preload(target, back_s, replay_id, now))
        return True

    async def _preload(self, target: str, back_s: float | None, replay_id: str | None, t0: float) -> None:
        hold = 0.0
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
                        if res.error.startswith(STICK_HUNG):
                            return   # the stick's fault, not the channel's: the breaker paces retries
                        self._failed[target] = time.time() + self._preload_backoff(res)
                        self._blackout_check(target, res)
                        if self.on_preload_fail:
                            self.on_preload_fail(target, res.error + " | " + "; ".join(res.steps[-3:]),
                                                 host=hid.host)
                        return
                    self._set_hidden(target, True)
                    # [controls] The tune's confirming read is the first of the two steady reads the
                    # flip asks for: a "go now" right after this preload flips after one more read.
                    self._note_steady(hid, target, (hid.tuned or {}).get("report") or {})
                    await self._maybe_build_multiview(hid, target, res)
                    hold = 0.0 if replay_id is not None else self._buffer_for(hid, target, res)
                    if hold and self._scrub_wanted(target):
                        # [scrub entry] the overlay scrub, off screen, before anything can flip to it; a pause
                        # doesn't hold on YouTube TV (Nick, Nov 6). Fails closed at the live edge or not ready.
                        await self._scrub_into(hid, target, hold)   # sets the hidden stick's state itself
                        hold = 0.0
                    if hold:
                        self._set_hidden(target, False)   # not flippable until it's held
                if replay_id is not None:
                    # A stream already held behind live is that much closer to the play.
                    back = (back_s or 0.0) + (time.time() - t0) - self.held.get(target, 0.0)
                    if not await self._park(hid, target, max(0.0, back), replay_id):
                        self._failed[target] = time.time() + PRELOAD_BACKOFF_S
                        return
                self._verified_at = time.time()
            if replay_id is None and hold:
                await self._hold(hid, target, hold)
                if self.hidden is hid and self.hidden_target == target:
                    self._set_hidden(target, True)
        except asyncio.CancelledError:
            self._set_hidden(None, False)   # stopped mid-tune: we don't know what it's showing
            raise
        finally:
            if self._preload_task is asyncio.current_task():   # not a preload that displaced this one
                self._preloading, self._refreshing = None, None

    def _buffer_for(self, stick: Stick, target: str, res: TuneResult) -> float:
        """Seconds to hold this fresh hidden-stick tune behind live (0 = none)."""
        if not (config.any_league(self.cfg, "timing.behind_live_s.enabled")
                or config.any_league(self.cfg, "timing.behind_live_s.rc8_on") or self._buffered_jump()):
            return 0.0   # [league overrides] behind_of (the engine) says 0 for a league that doesn't run it
        app = self._ref(target)[0]
        if app not in (self.cfg.get("timing.behind_live_s.apps") or ("youtubetv", "primevideo")):
            return 0.0
        mv = self.prime_mv
        if res.path == "tile" or (mv is not None and mv.session(stick.host) is not None):
            return 0.0    # a multiview tile is one stream of several: pausing it pauses them all
        try:
            b = float(self.behind_of(target) or 0.0)
        except Exception:
            log.exception("buffer: behind_of(%s)", target)
            return 0.0
        return max(0.0, min(b, float(self.cfg.get("timing.behind_live_s.max_s", 90.0))))

    # --- [scrub entry] item 4 of Nick's Nov 6 order: seek the hidden stick before the flip --------------

    def _scrub_wanted(self, target: str | None) -> bool:
        """Is this target buffered by the overlay scrub (redzone/tuners/yttv_seek.enter_buffer) instead of a pause?
        Needs yttv_seek.enabled (the tool) and timing.behind_live_s.scrub_entry for the target's league (engine:
        scrub_of), a YouTube TV target and two sticks. Ships off: nothing changes until both are on."""
        if not target or target.split(":", 1)[0] not in SCRUB_APPS or not self.two_stick:
            return False
        if not self.cfg.get("yttv_seek.enabled", False) and not self._buffered_jump():
            return False
        try:
            if self.scrub_of is not None:
                return bool(self.scrub_of(target))
        except Exception:
            log.exception("scrub: scrub_of(%s)", target)
            return False
        return bool(self.cfg.get("timing.behind_live_s.scrub_entry", False))

    def _buffered_jump(self) -> bool:
        """[buffered jump] jump_mode + buffered_jump on: both sticks scrubbed behind live (the engine's scrub_of /
        behind_of say which targets)."""
        return bool(self.cfg.get("jump_mode.enabled", False) and self.cfg.get("buffered_jump.enabled", False))

    # --- [buffered jump] park the hidden stick just before its next snap (Nick 21:05 via the overseer) ---------
    # The hidden stick plays behind live (scrubbed, label read); the engine asks for a PAUSE timed from the play-by-play
    # (redzone/decider/buffered.park_in) so its picture holds on the line-up; the next dead ball on screen flips to it
    # and PLAY rolls the snap. Each pause adds its length to the stick's behind-live (held + the delay book).

    def snap_parked(self, target: str | None) -> float | None:
        """Wall time the hidden stick paused on `target` (its picture is frozen since), or None."""
        sp = self._snap_park
        return sp.at if sp is not None and target and sp.target == target and self.hidden_target == target \
            and self.hidden_ready else None

    def snap_park(self, target: str, in_s: float) -> bool:
        """Pause the hidden stick (playing `target`, scrubbed) in_s from now. True = scheduled."""
        if not (self.live_control and self.two_stick and self._buffered_jump()) or self.hidden is None:
            return False
        if self.hidden_target != target or not self.hidden_ready or self.parked is not None \
                or self._snap_park is not None or self.hidden.scrubbed(target) is None:
            return False
        if self._busy() or self._flipping or (self._snap_task is not None and not self._snap_task.done()):
            return False
        self._snap_task = asyncio.create_task(self._do_snap_park(self.hidden, target, time.time() + max(0.0, in_s)))
        return True

    async def _do_snap_park(self, hid: Stick, target: str, at: float) -> None:
        def ours() -> bool:
            return self.hidden is hid and self.hidden_target == target and self.hidden_ready and not self._flipping \
                and self.parked is None
        try:
            while time.time() < at:
                if not ours():
                    return
                await asyncio.sleep(min(0.1, max(0.0, at - time.time())))
            if not ours() or self._busy():
                return
            async with self._lock:
                if not ours():
                    return
                t_key = time.time()
                sent = await hid.key("KEYCODE_MEDIA_PAUSE")
                t_pause = (t_key + time.time()) / 2.0
                if not sent or not getattr(sent, "ok", False):
                    log.info("buffered jump: PAUSE not sent to %s on %s", target, hid.host)
                    return
                pkg = self._package(hid, target)
                end, pb = time.time() + float(self.cfg.get("replay.pause_settle_s", 3.0)), {}
                while True:
                    pb = await hid.playback(pkg)
                    if pb.get("state") == "paused" or time.time() >= end:
                        break
                    await asyncio.sleep(float(self.cfg.get("replay.pause_poll_s", 0.4)))
                if pb.get("state") != "paused":
                    # the key may or may not have landed: the picture's place is unknown, so it's no buffered standby
                    log.info("buffered jump: %s on %s reads %s after PAUSE: dropped", target, hid.host, pb.get("state"))
                    self._set_hidden(None, False)
                    return
                self._snap_park = Parked("snap", target, t_pause, True)
                self.latency.record("snap_park_key", round(time.time() - t_key, 2))
                log.info("buffered jump: %s parked on %s (late %.2f s)", target, hid.host, t_pause - at)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("buffered jump: park of %s", target)

    async def _snap_roll(self, hid: Stick, target: str) -> bool:
        """PLAY a snap-parked hidden stick: its pause joins its behind-live (held, the delay book, the stick's anchor
        for still_on). Called with the control lock held (the flip, or an expired park). False: not paused there."""
        sp = self._snap_park
        if sp is None or sp.target != target:
            return True
        self._snap_park = None
        pkg = self._package(hid, target)
        pb = await hid.playback(pkg)
        if pb.get("state") != "paused":
            log.info("buffered jump: the park of %s on %s reads %s, not paused", target, hid.host, pb.get("state"))
            return False
        sent = await hid.key("KEYCODE_MEDIA_PLAY")
        if not sent or not getattr(sent, "ok", False):
            return False
        paused = max(0.0, time.time() - sp.at)
        behind = round(self.held.get(target, 0.0) + paused, 1)
        self._set_held(target, behind)
        if self.on_label is not None:
            try:
                self.on_label(target, behind, None, time.time())
            except Exception:
                log.exception("buffered jump: on_label(%s)", target)
        pb = await hid.playback(pkg)
        if playback_ready(pb):
            hid.remember(target, pb, scrubbed_s=behind)
        self._buffer_log("snap_roll", target, hid.host, behind, None, paused_s=round(paused, 1))
        return True

    def snap_park_expire(self) -> bool:
        """A park older than buffered_jump.park_max_age_s is rolled on the hidden stick (a long YouTube TV pause
        can lose its place, Nov 6) and parks again at its next snap. True = rolling."""
        sp = self._snap_park
        if sp is None or self._busy() or self._flipping or self.hidden is None:
            return False
        if time.time() - sp.at <= float(self.cfg.get("buffered_jump.park_max_age_s", 45.0)):
            return False
        hid, target = self.hidden, sp.target

        async def roll() -> None:
            async with self._lock:
                if self.hidden is hid and self._snap_park is sp and not await self._snap_roll(hid, target):
                    self._set_hidden(None, False)
        self._snap_task = asyncio.create_task(roll())
        return True

    def _scrub_fallback_live(self) -> bool:
        """After a failed scrub: flip to the stream at the live edge (today's behavior, graded at B=0) or keep
        the current game ("keep", the default: no picture of unproven delay goes on screen)."""
        return str(self.cfg.get("timing.behind_live_s.scrub_fallback", "keep")) == "live"

    def _preload_backoff(self, res: TuneResult) -> float:
        """Seconds before a failed standby target is preloaded again. [Nick's live test, Nov 7] the standby kept
        choosing a Prime game whose page said UNAVAILABLE: with two_stick.unavailable_backoff_s > 0 an
        account / payment / unavailable screen keeps that target off the standby that long (0 = as before)."""
        text = (" ".join(res.steps) + " " + res.error).lower()
        long = float(self.cfg.get("two_stick.unavailable_backoff_s", 0) or 0)
        return max(PRELOAD_BACKOFF_S, long) if long and "unavailable" in text else PRELOAD_BACKOFF_S

    def _scrub_protected(self, target: str | None = None) -> bool:
        """[scrub entry] The hidden stick is a ready, scrubbed standby (label-read behind live) of a game other than
        `target`: nothing but its flip or a fresh redo (label_stale) may replace it."""
        hid, have = self.hidden, self.hidden_target
        return bool(hid is not None and have and have != target and self.hidden_ready and self.parked is None
                    and hid.scrubbed(have) is not None and self._scrub_wanted(have))

    def _scrub_cost(self, target: str | None) -> float:
        """Seconds the scrub adds before a flip to `target` (0 = none needed): about 10 s of presses plus one
        label read (Nick, Nov 6), measured once we have scrubs (latency key yttv_scrub, p90)."""
        if not self._scrub_wanted(target):
            return 0.0
        hid = self.hidden
        if hid is not None and self._live_on_hidden(target) and hid.scrubbed(target) is not None:
            return 0.0
        samples = sorted(self.latency.samples.get("yttv_scrub") or ())
        p90 = samples[min(len(samples) - 1, math.ceil(len(samples) * .9) - 1)] if samples else 0.0
        return max(float(self.cfg.get("timing.behind_live_s.scrub_cost_s", 28.0)), p90)

    async def _scrub_into(self, stick: Stick, target: str, seconds: float) -> bool:
        """[scrub entry] Put the hidden stick's fresh tune of `target` about `seconds` behind live by the overlay
        scrub (CENTER, N x LEFT, CENTER), off screen. Called with the control lock held. True = playing that far
        behind live as READ off the overlay's time label: `held` and the delay book get the measured value
        (never media_session, which lies after a seek), and the stick is a ready standby.

        Fails closed: no label read, a reading past max_s, a stick that is no longer the hidden one, or any
        error -> the stream is re-tuned at the live edge (a known position) and is ready only with
        scrub_fallback "live". A failed target isn't scrubbed again for scrub_retry_s (no key storms)."""
        t0 = time.monotonic()
        pkg = self._package(stick, target)
        cap = float(self.cfg.get("timing.behind_live_s.max_s", 90.0))
        read_s = float(self.cfg.get("timing.behind_live_s.scrub_read_s", 8.0))
        # Each label read adds ~8 s (Nick, Nov 6: 60 s asked -> 67 s read), so ask that much less.
        ask = max(15.0, min(float(seconds), cap) - read_s)
        self._set_hidden(target, False)

        def still_hidden() -> bool:
            return self.hidden is stick and self.hidden_target == target and not self._flipping

        if self.label_probe is None and self._buffered_jump():
            try:   # [buffered jump] turned on at runtime: every scrub is label-read (a buffer is never placed blind)
                from redzone.tuners.yttv_label import LabelProbe
                self.label_probe = LabelProbe(self.cfg)
            except Exception:
                log.exception("buffered jump: no label probe")
        # [overseer Nov 8 21:55] YouTube TV's LIVE/title banner sits up to ~30 s after a tune and swallows the overlay keys
        # ('focus guard: no scrub label after the LEFT', 62 s): let it clear before the first key (stick tests waited)
        settle = float(self.cfg.get("timing.behind_live_s.scrub_after_tune_s", 22.0)) \
            - (time.time() - self._played_at.get(stick.host, 0.0))
        while settle > 0:
            if not still_hidden():
                return False   # flipped to or replaced while the banner cleared: no key was pressed
            await asyncio.sleep(min(0.25, settle))
            settle -= 0.25
        res, why = None, ""
        if time.time() < self._scrub_backoff.get(target, 0.0):
            why = "backoff after a failed scrub"
        else:
            try:
                from redzone.tuners import yttv_seek
                kw = {"read_behind": self.read_behind} if self.read_behind is not None else {}
                if self.label_probe is not None:
                    # [label reader] one screencap feeds the focus guard and the label; the probe at the live edge
                    # is the at-live anchor, and one read after the commit measures behind-live (yttv_label)
                    if self.label_probe.arm(stick):
                        kw = {"read_overlay": self.label_probe.overlay_reader, "read_behind": self.label_probe}
                    # not armed (#391: the stick is still behind on a target) -> no read_behind: the scrub runs
                    # unmeasured and is refused below (fails closed)
                res = await yttv_seek.enter_buffer(stick, ask, cfg=self.cfg, pkg=pkg, still_hidden=still_hidden, **kw)
            except asyncio.CancelledError:
                raise
            except ImportError:
                why = "yttv_seek not installed"
            except Exception as e:   # the seek helper never raises by contract; a bug must still fail closed
                log.exception("scrub: enter_buffer(%s) on %s", target, stick.host)
                why = f"crashed: {e}"
        took = round(time.monotonic() - t0, 1)
        if res is None or getattr(res, "presses", 1) or getattr(res, "committed", True) or why.startswith("crashed"):
            if not why.startswith("backoff"):
                stick.behind_on = target   # keys may have moved it: the next tune of target must reset first
        behind = getattr(res, "behind_s", None) if res is not None and getattr(res, "ok", False) else None
        if behind is not None and getattr(res, "measured", True) is False:
            behind = None   # an estimate (behind_before + presses x 15 s), not a label reading: never trusted
        if res is not None and not why:
            why = str(getattr(res, "reason", "") or ("no label read" if behind is None else ""))
        if behind is not None and not (0.0 < float(behind) <= cap + float(
                self.cfg.get("timing.behind_live_s.scrub_over_s", 15.0))):
            why, behind = f"label read {float(behind):.0f}s behind live, outside (0, max_s]", None
        if behind is not None and still_hidden():
            pb = await stick.playback(pkg)
            if pb.get("state") == "playing" and playback_ready(pb) and still_hidden():
                behind = round(float(behind), 1)
                stick.remember(target, pb, scrubbed_s=behind)
                self._set_held(target, behind)
                self._set_hidden(target, True)
                self.latency.record("yttv_scrub", took)
                if self.on_label is not None:
                    try:
                        self.on_label(target, behind, None, time.time())   # the delay model's measured behind_live
                    except Exception:
                        log.exception("scrub: on_label(%s)", target)
                self._buffer_log("scrubbed", target, stick.host, behind, round(behind - float(seconds), 1), took=took,
                                 asked_s=round(ask, 1))
                log.info("scrub: %s on %s is %.1fs behind live by the overlay label (%.1fs)", target, stick.host,
                         behind, took)
                return True
            why = f"not playing after the scrub ({pb.get('state')})"
        if not why:
            why = "the stick is no longer the hidden one"
        self._buffer_log("scrub_failed", target, stick.host, 0.0, None, took=took, why=why[:120])
        log.warning("scrub: %s on %s failed (%s): fail closed", target, stick.host, why)
        if not why.startswith("backoff"):
            # a banner / focus miss says nothing about the channel: retry it soon, not after the full backoff
            banner = why.startswith("focus guard")
            self._scrub_backoff[target] = time.time() + float(self.cfg.get(
                "timing.behind_live_s.scrub_banner_retry_s" if banner else "timing.behind_live_s.scrub_retry_s",
                30.0 if banner else 120.0))
        if not still_hidden() and self.hidden is not stick:
            return False   # it's on screen (or gone): never pressed or re-tuned from here
        self._set_held(target, 0.0)
        if res is not None or stick.tuned is None or stick.tuned.get("target") != target:
            # keys were (or may have been) pressed: the position is unknown. A fresh live tune is a known one.
            again = await self._tune(stick, target)
            if not again.ok:
                self._set_hidden(None, False)
                return False
        self._set_hidden(target, self._scrub_fallback_live())
        return False

    def buffer_standby(self, target: str) -> bool:
        """[buffered jump bootstrap, overseer Nov 8 21:47] A ready hidden stick on `target` that was tuned before the flag
        went on (never scrubbed) is re-tuned and scrubbed off screen, so the next flip puts a buffered picture on screen.
        True = started. Never touches the stick on screen."""
        hid = self.hidden
        # [overseer Nov 8 22:29: fox ready, unscrubbed, no re-scrub line] the first refusing guard is named (jump_status
        # 'bj_buffer' and one log line each time it changes)
        why = ("flag_off" if not self._buffered_jump() else "no_hidden_stick" if hid is None
               else "hidden_holds_other" if self.hidden_target != target
               else "already_scrubbed" if hid.scrubbed(target) is not None
               else "not_ready" if not self.hidden_ready else "replay_parked" if self.parked is not None
               else "switch_running" if self._task is not None and not self._task.done()
               else "preload_running" if self._preload_task is not None and not self._preload_task.done()
               else "lock_held" if self._lock.locked() else "flipping" if self._flipping
               else "scrub_not_wanted" if not self._scrub_wanted(target)
               else "scrub_backoff" if time.time() < self._scrub_backoff.get(target, 0.0) else "start")
        if why != getattr(self, "bj_buffer_why", ""):
            log.info("buffered jump: standby %s re-scrub check: %s", target, why)
        self.bj_buffer_why = why
        if why != "start":
            return False
        log.info("buffered jump: %s on the hidden stick %s isn't scrubbed: re-tune + scrub", target, hid.host)
        self._set_hidden(target, False)
        self._preloading = (target, None)
        self._preload_task = asyncio.create_task(self._rescrub(hid, target))
        return True

    def drop_stuck_standby(self, target: str, stuck_s: float) -> bool:
        """[buffered jump, overseer Nov 8 21:47: 'MIA @ ND' sat not-ready 10+ min] A hidden stick that has held `target`
        not ready, with nothing running on it, for stuck_s is let go (no key pressed) and the target backs off, so the
        rotation's next pick (a code channel first) is tuned instead. True = dropped."""
        if self.hidden_target != target or self.hidden_ready or self._busy() or self._flipping:
            self._stuck_since = None
            return False
        now = time.time()
        if self._stuck_since is None or self._stuck_since[0] != target:
            self._stuck_since = (target, now)
            return False
        if now - self._stuck_since[1] < stuck_s:
            return False
        log.info("buffered jump: standby %s not ready for %.0f s with nothing running: dropped", target,
                 now - self._stuck_since[1])
        self._stuck_since = None
        self._set_hidden(None, False)
        self._failed[target] = now + PRELOAD_BACKOFF_S
        self._last_preload = 0.0
        return True

    def label_stale(self, target: str) -> bool:
        """[scrub entry] The delay model says this target's label reading went stale (stalls widened it, #376
        delays.on_label_stale). On the hidden stick: drop it from ready and redo it off screen (fresh live tune,
        then the scrub), so no flip trusts a stale offset. The stick on screen is never pressed. True = redoing."""
        hid = self.hidden
        if hid is None or self.hidden_target != target or hid.scrubbed(target) is None or self._busy() \
                or not self._scrub_wanted(target):
            return False
        self._set_hidden(target, False)
        self._preloading = (target, None)
        self._preload_task = asyncio.create_task(self._rescrub(hid, target))
        return True

    async def _rescrub(self, hid: Stick, target: str) -> None:
        try:
            async with self._lock:
                if self.hidden is not hid or self.hidden_target != target:
                    return
                res = await self._tune(hid, target)
                if not res.ok:
                    self._set_hidden(None, False)
                    return
                b = self._buffer_for(hid, target, res)
                if b:
                    await self._scrub_into(hid, target, b)
                else:
                    self._set_hidden(target, True)
        except asyncio.CancelledError:
            self._set_hidden(None, False)
            raise
        finally:
            if self._preload_task is asyncio.current_task():
                self._preloading = None

    def plan_prime_multiview(self, tiles: list[str]) -> None:
        """The engine's pick of Prime NBA games for a multiview, best first (prime_multiview.plan_tiles)."""
        self.prime_plan = list(tiles)

    async def _maybe_build_multiview(self, hid: Stick, target: str, res: TuneResult) -> None:
        """After a normal Prime tune on the HIDDEN stick (off screen), open a multiview of the planned
        games around it, so the next switches among them are ~1 s tile switches."""
        mv = self.prime_mv
        plan = list(getattr(self, "prime_plan", ()) or ())
        if mv is None or res.path == "tile" or target not in plan or len(plan) < 2:
            return
        if not pmv.needs_rebuild(mv.session(hid.host), plan):
            return
        try:
            built = await mv.build(hid, [target] + [t for t in plan if t != target])
        except asyncio.CancelledError:
            raise
        except Exception:   # a multiview bug must never cost the preload it rides on
            log.exception("prime multiview build failed on %s", hid.host)
            mv.forget(hid.host)
            built = False
        s = mv.session(hid.host)
        log.info("prime multiview on %s: %s", hid.host, s.tiles if built and s else "not built")
        if not built or s is None or s.main_target != target:
            # Where the player stands is unknown now: the next switch re-tunes it.
            self._set_hidden(None, False)
        else:
            pb = await hid.playback(self._package(hid, target))
            if playback_ready(pb):
                hid.remember(target, pb)
            else:
                self._set_hidden(None, False)

    def _maybe_reseek(self) -> None:
        """A parked replay that didn't hold still drifts forward: pull it back every ~20s (from
        keepalive), so the fix-up at the flip is a press or two, not a long run of rewinds."""
        p = self.parked
        step = float(self.cfg.get("replay.seek_step_s", 10))
        if not (self.live_control and self.two_stick) or self.input_unknown or p is None or p.paused or self._busy() \
                or time.time() - p.at < 2 * step or not self._parked_for(p.target, p.replay_id):
            return
        if self._replay_scrubs(p.target):
            return   # a YouTube TV park is never re-scrubbed from behind live (unmeasured); _unpark refuses it
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
        if pb.get("read_ok") is not False and parked is None:
            # [controls] The same steadiness evidence the flip asks for (no state change between two
            # reads >= FLIP_STEADY_S apart), gathered here in the background: a "watch this now" on
            # the preloaded game then flips after one read instead of waiting a second for a second.
            self._note_steady(hid, target, pb)
        if pb.get("read_ok") is False:
            # adb didn't answer: that says nothing about the stream. Ask again soon, keep it.
            self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
            return
        state = pb.get("state")
        if state in ("playing", "buffering", "paused"):
            self._awake_at[hid.host] = time.time()
        sp = self._snap_park
        if sp is not None and sp.target == target and parked is None:
            if state != "paused":   # [buffered jump] paused on purpose; anything else means its place is unknown
                log.info("buffered jump: the park of %s reads %s: dropped", target, state)
                self._set_hidden(None, False)
            return
        lh = self._left_for(hid, target)
        if lh is not None and parked is None:
            self._watch_left(hid, target, pb, lh)   # STK-07: paused on purpose
            return
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
        if state in expect and self._stuck_loading(hid, target, state):
            log.info("standby %s has been %s too long — it'll be reloaded", target, state)
            self._loading_since.pop((hid.host, target), None)
            self._set_hidden(None, False)
            return
        if parked is None and (state == "paused" or (state == "playing" and pb.get("active") is False)):
            # playbook=STK-05: the hidden stick paused off screen (HDMI-CEC pauses the stick a flip
            # leaves, live lab Nov 4) or reads a stale PLAYING on an inactive session. Resuming would
            # come back behind live: drop it now so the planner re-tunes it at the live edge before
            # any flip, instead of the flip finding it (SAFE-08 would skip that flip).
            log.info("playbook=STK-05 standby %s on %s is %s: re-syncing it before any flip", target, hid.host,
                     "paused" if state == "paused" else "a stale PLAYING (active=false)")
            self._set_hidden(None, False)
            self._last_preload = 0.0   # re-tune at once, not after the preload gap
            if state == "paused" and self._standby_blocked(hid, target):
                self._last_preload = time.time()
            return
        if state not in expect:
            log.info("standby %s is %s, not %s — it'll be reloaded", target, state, "/".join(expect))
            self._set_hidden(None, False)
        elif parked is None and state == "playing" and self.cfg.get("buffer.watchdog", False) \
                and self._buffer_watch(hid, target, pb):
            return   # playbook=STK-06: a rebuild is under way (or waits for the next dead ball)
        elif prime_live and self._jumped(hid, target, pb):
            # Every Prime game reads "PrimeVideo" on a wall-clock position, so "playing" alone can't
            # tell live from a player that paused and resumed behind the live edge: its position
            # must still keep pace with the stick's clock (~1% drift is normal; 2% is allowed).
            log.info("standby %s is playing but fell off the live edge — it'll be reloaded", target)
            self._set_hidden(None, False)

    def buffer_error(self, stick: Stick, target: str, pb: dict) -> tuple[float, float] | None:
        """[buffer.watchdog] (behind live now, its error vs the target hold) for a held stream on the
        hidden stick, from its own clock: how far its position ran ahead of (or fell behind) the stick's
        clock since the hold re-anchored it. None = no hold to judge, or no usable reading. An unheld
        standby whose buffer is wanted, with no hold or preload under way, reads as 0 s behind."""
        held = self.held.get(target, 0.0)
        try:
            want = max(0.0, min(float(self.behind_of(target) or 0.0),
                                float(self.cfg.get("timing.behind_live_s.max_s", 90.0))))
        except Exception:
            return None
        t = stick.tuned or {}
        a = t.get("report") or {}
        if self._scrub_wanted(target) and (t.get("scrubbed_s") is not None
                                           or time.time() < self._scrub_backoff.get(target, 0.0)):
            # [scrub entry] after a seek media_session's position crawls, so position vs clock would read a
            # scrubbed stream as "fell behind" every tick; the overlay label is its meter (Nick, Nov 6). And a
            # target whose scrub just failed stays at the live edge until scrub_retry_s, never re-tuned in a loop.
            return None
        if (held <= 0 and want > 0 and t.get("target") == target and playback_ready(pb)
                and self._holding is None and not (self._preload_task and not self._preload_task.done())
                and self._buffer_for(stick, target, TuneResult(True, target, 0.0, [], "")) > 0):
            # A standby at the live edge whose buffer is wanted and that nobody is holding: a switch
            # tuned it itself and then found no safe moment (path "tuned"), so no preload ever held it
            # (lane 2, #132's night). Judged as 0 s behind: the rebuild below holds it.
            return 0.0, round(-want, 1)
        if not (held > 0 and want > 0) or t.get("target") != target or not playback_ready(pb):
            return None
        pa, ua, pn, un = a.get("position"), a.get("updated"), pb.get("position"), pb.get("updated")
        if None in (pa, ua, pn, un) or un < ua:
            return None
        ahead = ((pn - pa) - (un - ua)) / 1000.0      # > 0: it jumped toward live; < 0: it fell behind
        now = max(0.0, held - ahead)
        return round(now, 1), round(now - want, 1)

    def _buffer_watch(self, hid: Stick, target: str, pb: dict) -> bool:
        """playbook=STK-06 (buffer.watchdog): the hold on the hidden stick is lost (it resumed or
        jumped to live) or drifted more than BUFFER_DRIFT_S from its target. Rebuild it at the target's
        next dead ball (self.dead_ball(target)), or after BUFFER_WAIT_S without one (the stick is off
        screen and not flippable while it rebuilds, so nothing on the TV is cut): too close to live -> pause the difference (with the calibrated
        hold offset, timing.behind_live_s.hold_cal_s); too far behind -> reload at the live edge and hold
        again. True = it isn't flippable until that's done. An intentional hold pause is not STK-05:
        that check only runs on a stick whose hold already finished."""
        got = self.buffer_error(hid, target, pb)
        if got is None or abs(got[1]) <= BUFFER_DRIFT_S:
            self._buffer_bad.pop(target, None)
            return False
        now_s, err = got
        first = target not in self._buffer_bad
        self._buffer_bad.setdefault(target, time.time())
        if first:
            log.info("playbook=STK-06 buffer on %s (%s) is %.1fs behind live, %+.1fs off its hold: rebuilding it "
                     "at the next dead ball", target, hid.host, now_s, err)
            self._buffer_log("lost" if now_s < BUFFER_DRIFT_S else "drift", target, hid.host, now_s, err)
        try:
            dead = self.dead_ball(target)
        except Exception:
            log.exception("buffer: dead_ball(%s)", target)
            dead = False
        waited = time.time() - self._buffer_bad[target]
        if self._busy() or not (dead or waited >= BUFFER_WAIT_S):
            self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
            return False   # keep it as it is (still a usable stream) until the next dead ball
        self._set_hidden(target, False)
        self._preloading = (target, None)
        self._preload_task = asyncio.create_task(self._rebuild_buffer(hid, target, now_s, err))
        return True

    async def _rebuild_buffer(self, hid: Stick, target: str, now_s: float, err: float) -> None:
        cal = float(self.cfg.get("timing.behind_live_s.hold_cal_s", -1.5))
        since = self._buffer_bad.get(target, time.time())
        ok = False
        try:
            if self._scrub_wanted(target):
                # [scrub entry] a pause doesn't hold on YouTube TV: rebuild by the overlay scrub (it re-tunes at
                # the live edge itself when it fails)
                async with self._lock:
                    if self.hidden is not hid or self.hidden_target != target:
                        return
                    hold = self._buffer_for(hid, target, TuneResult(True, target, 0.0, [], ""))
                    if hold:
                        ok = await self._scrub_into(hid, target, hold)
                    else:
                        self._set_hidden(target, True)   # no buffer wanted now: the live-edge stream is the standby
                return
            if err < 0:
                ok = await self._hold(hid, target, max(0.5, -err + cal), base_s=now_s)
            else:
                async with self._lock:
                    if self.hidden is not hid or self.hidden_target != target:
                        return
                    res = await self._tune(hid, target)
                    if not res.ok:
                        self._set_hidden(None, False)
                        return
                hold = self._buffer_for(hid, target, res)
                ok = bool(hold) and await self._hold(hid, target, max(0.5, hold + cal))
            if self.hidden is hid and self.hidden_target == target:
                self._set_hidden(target, True)
        except asyncio.CancelledError:
            self._set_hidden(None, False)
            raise
        finally:
            if self._preload_task is asyncio.current_task():
                self._preloading = None
            self._buffer_bad.pop(target, None)
            self._buffer_log("recovered" if ok else "rebuild_failed", target, hid.host, self.held.get(target, 0.0),
                             None, took=round(time.time() - since, 1))
            log.info("playbook=STK-06 buffer on %s %s: %.1fs behind live", target,
                     "rebuilt" if ok else "NOT rebuilt (it runs at the live edge until the next tune)",
                     self.held.get(target, 0.0))

    def _buffer_log(self, kind: str, target: str, host: str, behind_s: float, err: float | None, **kw) -> None:
        """data/buffer.jsonl: every STK-06 detection and rebuild (collect_live_test -> northstar)."""
        if self.buffer_log_path is None:
            return
        try:
            with self.buffer_log_path.open("a") as f:
                f.write(json.dumps({"ts": round(time.time(), 2), "kind": kind, "target": target,
                                    "stick": self.hdmi.get(host), "behind_s": behind_s, "err_s": err, **kw}) + "\n")
        except OSError:
            log.exception("buffer log")

    def force_live_hidden(self, target: str) -> bool:
        """[SYNC B, EXP-801, flag sync.edge.force_live] The hidden stick plays `target` "live" (no hold the app
        asked for) but the live-edge reader (sync_edge.py) measured it landed behind the edge: re-tune it at
        the edge, as a refresh preload (any switch or the planner's own preload cancels it). The stick on
        screen is never touched. False = not now (busy, parked, held on purpose, unknown input)."""
        if not (self.live_control and self.two_stick) or self.input_unknown or self.hidden is None:
            return False
        if self.hidden_target != target or not self.hidden_ready or self.parked is not None \
                or self.on_target == target or self.held.get(target, 0.0) > 0 or self._busy() \
                or time.time() < self._asleep_until or not self.can_tune(target):
            return False
        log.info("sync: %s on the hidden stick landed behind its live edge: re-tuning it at the edge", target)
        self._cancel_refresh()
        self.hidden_ready = False
        self._preloading, self._refreshing = (target, None), target
        self._preload_task = asyncio.create_task(self._preload(target, None, None, time.time()))
        return True

    def relink_visible_live(self, target: str) -> bool:
        """[SYNC B, flag sync.edge.force_live_visible] The stick on screen plays `target` behind its live edge
        without a hold the app asked for (a stall, a pause): re-link it to live with a fresh deep link (2.5 s
        on YouTube TV, overseer Nov 5). Only on a dead ball, so no play is cut; any switch cancels it."""
        if not self.live_control or self.input_unknown or self._flipping or self.on_target != target \
                or self.held.get(target, 0.0) > 0 or self._busy() or not self.dead_ball(target):
            return False
        log.info("sync: %s on screen plays behind its live edge: re-linking it to live", target)
        asyncio.get_running_loop().create_task(self.restore_visible(target, relink=True, why="behind live edge"))
        return True

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
        elif "unavailable" in text:
            self.note = f"{target} shows UNAVAILABLE here"

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
