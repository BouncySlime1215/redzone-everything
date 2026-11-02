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
- Two-stick mode drops to single-stick automatically if an input flip fails.
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
from redzone.tuners.adb import Stick, adb_available, playback_ready
from redzone.tuners.tuners import TUNERS, TuneResult
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
TWO_STICK_RETRY_S = 300.0 # a failed flip turns two-stick mode off this long (once the input is known again)
AWAKE_TRUST_S = 120.0      # a stick seen playing this recently is awake: skip the sleep check
ASLEEP_RECHECK_S = 60.0    # a sleeping hidden stick: don't ask again (or preload) for this long
VERIFY_SOON_S = 3.0        # after a flip, check the stick we left within a few seconds
SEEK_LATE_OK_S = 1.0       # a rewind may land this far past its target (Missed.back_s leads the snap by 3s)

LATENCY_KEEP = 15
# What a real measurement can look like; outside this it's a glitch (a fake, a hung adb), not a switch.
PLAUSIBLE = {"flip": (0.02, 30.0), "seek": (0.05, 60.0)}
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
        self._exit_check = None

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
        on = []
        for i, s in enumerate(self.sticks):
            if not s.controllable:
                continue
            r = await s.shell("dumpsys hdmi_control | grep -E -c 'mIsActiveSource: true'", timeout=5)
            if r.ok and r.out.strip().startswith("1"):
                on.append(i)
        if len(on) != 1:
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
            self.input_unknown = False
        return changed

    async def close(self) -> None:
        """App shutdown: close each stick's always-open adb shell."""
        tasks = [t for t in (self._task, self._preload_task, self._resume_task) if t and not t.done()]
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
        if not self.two_stick_ok and time.time() >= self._two_stick_retry_at and not self.input_unknown:
            self.two_stick_ok = True
            log.info("Two-stick mode back on (the TV input is known again)")
        await self._stick_watchdog()
        await self.verify_standby()
        self._maybe_reseek()

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
                self.on_target = None   # it may have stopped the stream: next switch re-tunes

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
        stick = self.hidden or self.main
        return stick is None or bool(stick.packages.get(getattr(tuner, "role", app)))

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
                "path": "flip" if self.two_stick else "tune"}

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
            self.stage = {"stage": "tuning", "target": target, "reason": "confirming the requested stream"}
            try:
                out = await self.execute(target, replay_back_s, may_cut, replay_id, issued_at)
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
        if self.on_target != target or self.main is None:
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
        if replay and self._parked_for(target, replay_id):
            path = "parked replay"
        elif not replay and self._live_on_hidden(target):
            path = "preloaded"
        else:
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
            self.input_unknown = True
            self.on_target = None
            return Outcome(False, target, time.time() - t0, self.note + "; visible input is unconfirmed", False,
                           path=path)
        old_target, old_stick = self.on_target, self.main
        self.active = self.sticks.index(hid)
        self.on_target = None if replay else target
        # Flip-back: the stick we just left is still playing its game live, so "back"/"home" is one
        # more flip. Checked again in a few seconds: losing the input can pause a stick over CEC.
        self._set_hidden(old_target, old_target is not None)
        if old_stick is not None:
            self._awake_at[old_stick.host] = time.time()
        self._verified_at = time.time() - self._verify_every() + VERIFY_SOON_S
        if self._home_flipped and not replay:
            self._check_resumed_soon(hid, target)
        return Outcome(True, target, work + (time.time() - t1), f"flipped to {self.hdmi.get(hid.host)} ({path})",
                       False, time.time(), path=path, waited=waited)

    async def _flip_to(self, stick: Stick) -> bool:
        """Make the TV show this stick. CEC one-touch-play from the stick first (no tokens needed),
        then the TV's own network remote, then SmartThings. A stick whose CEC just failed goes
        straight to the TV for a while: no wasted round trip on every flip."""
        t0 = time.monotonic()
        self.stage.update(stage="verifying_input", reason="requesting and verifying the display input")
        self._home_flipped = False
        ok = False
        requested = self.hdmi.get(stick.host)
        if not requested:
            self.tv.last_error = "no input configured for this stick"
            return False
        if time.time() >= self._cec_bad.get(stick.host, 0.0):
            if self._exit_check is not None and not self._exit_check():
                self.tv.last_error = "safe exit window closed before CEC command"
                return False
            # Fire OS has no shell CEC command and blocks the system call (HDMI_CEC permission),
            # but HOME makes the stick announce <Active Source> over the HDMI cable, and the TV
            # follows. HOME backgrounds the preloaded app, so bring it straight back: it resumes
            # the stream it was already playing (measured on the 4K/4K Plus: ~2.5s to video).
            if await self._home_flip(stick):
                self.latency.record("flip", time.monotonic() - t0)
                self._awake_at[stick.host] = time.time()
                self._home_flipped = True
                return True
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
            self.latency.record("flip", time.monotonic() - t0)
            self._awake_at[stick.host] = time.time()
        return ok

    def _check_resumed_soon(self, stick: Stick, target: str) -> None:
        """After a HOME flip the app was relaunched; make sure it really resumed the game. Runs as
        background work, so the switch reports at once; any newer command cancels it."""
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._resume_task = asyncio.create_task(self._check_resumed(stick, target))

    async def _check_resumed(self, stick: Stick, target: str) -> None:
        async with self._lock:
            if self.main is not stick or self.on_target != target:
                return
            pkg = self._package(stick, target)
            end = time.monotonic() + RESUME_WAIT_S
            while time.monotonic() < end:
                pb = await stick.playback(pkg)
                if playback_ready(pb):
                    stick.remember(target, pb)   # resumed the stream we confirmed before the flip
                    return
                await asyncio.sleep(0.5)
            # On screen and not playing (stuck on the app's home page, a profile picker...):
            # nothing to protect any more, so load the game again.
            log.warning("after the flip %s didn't resume %s; loading it again", stick.host, target)
            self.on_target = None
            res = await self._tune(stick, target)
            if res.ok and self.main is stick:
                self.on_target = target
            else:
                log.warning("re-load after the flip failed: %s", res.error)

    async def _home_flip(self, stick: Stick) -> bool:
        """HOME -> <Active Source> -> TV switches to this stick; then resume its app.
        Verified on the stick's own CEC state (the TV isn't reachable over Wi-Fi)."""
        app = (self.hidden_target or "").split(":", 1)[0] if stick is self.hidden else None
        pkg = stick.packages.get(app or "") if app else None
        resume = f"; sleep 0.6; am start -n $(cmd package resolve-activity --brief {pkg} | tail -1) >/dev/null 2>&1" \
            if pkg else ""
        before = await stick.shell("dumpsys hdmi_control | grep -c 'Active Source> '", timeout=5)
        r = await stick.shell(f"input keyevent KEYCODE_HOME{resume}", timeout=8)
        if not r.ok:
            return False
        for _ in range(12):
            st = await stick.shell("dumpsys hdmi_control | grep -E -c 'mIsActiveSource: true'", timeout=5)
            if st.out.strip().startswith("1"):
                return True
            await asyncio.sleep(0.25)
        log.warning("HOME flip on %s: stick never became active source (before=%s)", stick.host,
                    before.out.strip())
        return False

    # --- the hidden stick between switches ---------------------------------------------

    def preload(self, target: str | None, back_s: float | None = None, replay_id: str | None = None) -> bool:
        """Put the hidden stick on `target`: live, or parked on a replay (back_s from the live edge,
        replay_id). Returns True if a preload started. WHAT to preload is the engine's call
        (standby.Planner); this only refuses what can't or shouldn't happen right now."""
        if not (self.live_control and self.two_stick and target):
            return False
        if replay_id is None and (target == self.on_target or self._live_on_hidden(target)):
            return False
        if replay_id is not None and self._parked_for(target, replay_id):
            return False
        now = time.time()
        if self._busy() or now < self._asleep_until or now - self._last_preload < MIN_PRELOAD_GAP_S \
                or not self.can_tune(target):
            return False
        self._last_preload = now
        self._preloading = (target, replay_id)
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
            self._preloading = None

    def _maybe_reseek(self) -> None:
        """A parked replay that didn't hold still drifts forward: pull it back every ~20s (from
        keepalive), so the fix-up at the flip is a press or two, not a long run of rewinds."""
        p = self.parked
        step = float(self.cfg.get("replay.seek_step_s", 10))
        if not (self.live_control and self.two_stick) or p is None or p.paused or self._busy() \
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
        state = pb.get("state")
        if state in ("playing", "buffering", "paused"):
            self._awake_at[hid.host] = time.time()
        expect = ("paused",) if parked and parked.paused else ("playing", "buffering")
        if state not in expect:
            log.info("standby %s is %s, not %s — it'll be reloaded", target, state, "/".join(expect))
            self._set_hidden(None, False)

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
            return not self.cfg.get("mode.dry_run", True) and (may_run is None or may_run())

        async with self._lock:
            if not allowed():
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
