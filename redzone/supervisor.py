"""Supervisor: the live system watches itself, so nobody has to.

Why: real nights fail in repeated known ways, so every ~10 s it checks them and fixes what it can, rate-limited, with nobody watching (commit 2026-11-03).

Every ~10 s it checks a short list of things that have gone wrong on real nights and fixes the
ones it can, on its own. Each rule waits until a problem has lasted long enough to be real, every
fix is rate-limited (a minimum gap and a cap per hour), and every observation and fix is written
to data/supervisor.jsonl.

Rules (name: what it notices -> what it does):
  screen_not_game   The TV shows the Fire TV home screen, or the app's own home/detail page,
                    instead of the game (foreground activity + two screenshots) -> resume the
                    game on the stick on screen (home screen: a fresh deep link). The ONLY rule
                    that presses keys on the visible stick, and only when the game is clearly
                    not playing there.
  stuck_switch      A switch still "in flight" well past control.max_switch_s -> cancel it and
                    report it failed, so decisions start again.
  idle_with_live    Live, tunable games but the decider has nothing on for minutes -> clear the
                    start back-off and short failure cool-downs; then refresh the feeds.
  repeat_failures   The same target fails again and again -> back off 10 min after 2 failures,
                    mute it for the night after 3, mute the whole app after 2 muted targets
                    (or 5 failures), and route each game to another source when one exists.
  safe_mode         Safe mode for 3+ minutes -> mute the target behind the errors (if one is),
                    then leave safe mode (at most 3 times an hour; after that it stays and says so).
  stale_feeds       Live games whose data stopped moving -> drop the fetch back-off; later a fresh
                    HTTP client and push-feed restart.
  adb               A stick offline 2+ min -> drop its adb transport and reconnect it; adb itself
                    down -> start the adb server. Never presses a key.
  delay_doubt       A channel's delay uncertainty stays wide for 15 min -> rebuild it from its
                    samples, or fall back to the safe defaults (config delay.default/sigma_default).
                    With delay.edge_model.enabled a MEASURED channel (label read, tonight's edge,
                    calibrated edge) is re-anchored on D_edge + behind_live instead, never reset.
  finished_hold     "Watch this" / lock / focus left on a game that ended -> hand back to autopilot.
  log_growth        Logs, JSONL files, the image cache and the database WAL kept under their caps.

Plus: key moments (big plays from play-by-play) judged seen/missed as they air, and a nightly
report at data/reports/YYYY-MM-DD.md (switches, failures by cause, key moments, delays measured,
what it fixed). Optional phone push through ntfy.sh (no account) when supervisor.ntfy_topic is set.

Every switch carries a trace (redzone/trace.py: decision -> command -> picture, with step times); ~90 s
after each cut the supervisor grades it against the feed (did we leave or land mid-play?) into
data/supervisor_cuts.jsonl. The report rolls traces, grades, safe-mode minutes and the watchdog's actions
into "what it showed / missed / broke / fixed" and a 7-night error budget (redzone/observability.py),
saves the numbers next to it as YYYY-MM-DD.json, and pushes five lines to the phone.

Read-only report from the saved data:  .venv/bin/python -m redzone.supervisor report [YYYY-MM-DD] [--push]
Check the phone push works:            .venv/bin/python -m redzone.supervisor push-test
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shutil
import statistics
import time
from collections import Counter, OrderedDict, defaultdict, deque
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from redzone import cheap_labels
from redzone import observability as obs
from redzone.fsm import MachineMap
from redzone.machines.platform import (SUPERVISOR_APP, SUPERVISOR_CONDITION, SUPERVISOR_LOOP, SUPERVISOR_SAFE_EPISODE,
                                       SUPERVISOR_SCREEN, SUPERVISOR_TARGET)
from redzone.platform.health import PUSH as PUSH_FAULTS
from redzone import trace

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NIGHT_STARTS_H = 6          # a "night" runs 6:00 AM ET to 6:00 AM ET the next day, named by its first date
REPORT_AFTER_S = 300.0      # ...and its report is written 5 minutes after it ends
REPORT_RETRY_S = 600.0      # a report that crashed is tried again this much later...
REPORT_TRIES = 4            # ...up to this many times
CUT_JUDGE_AFTER_S = 90.0    # a cut is graded this long after it happened (the feed has caught up by then)
CUT_LOOKBACK_S = 900.0      # switch rows this recent are looked at (a switch's row lands when it finishes)
NTFY_TOPIC_RX = re.compile(r"^[-_A-Za-z0-9]{1,64}$")

PLAYING = ("playing", "buffering", "connecting")
# Fire OS's own screens: the launcher, settings, the app store, profiles, screensaver.
HOME_PREFIXES = ("com.amazon.tv.", "com.amazon.venezia", "com.amazon.firetv.screensaver", "com.amazon.vizzini")
# An app's non-player screens, from the activity name (used only together with a still screen).
PAGE_RX = re.compile(r"(browse|home|detail|landing|main|launch|search|guide|library|profile|splash)", re.I)
APP_PACKAGES = {
    "youtubetv": ("com.amazon.firetv.youtube.tv", "com.google.android.youtube.tvunplugged"),
    "prime": ("com.amazon.firebat", "com.amazon.avod.thirdpartyclient", "com.amazon.amazonvideo.livingroom",
              "com.amazon.avod"),
    "espn": ("com.espn.gtv", "com.espn.score_center", "com.espn.androidtv", "com.espn.espnplus"),
}

# Defaults (each can be overridden under `supervisor:` in the config).
DEFAULTS = {
    "enabled": True,
    "every_s": 10.0,
    "screen_confirm_s": 20.0,      # the TV must look wrong on two checks this far apart
    "screen_frame_gap_s": 2.0,     # two screenshots this far apart: identical = a still page, not video
    "screen_after_fix_s": 45.0,    # the controller's own watchdog repaired something this recently: wait
    "stuck_grace_s": 30.0,         # a switch older than control.max_switch_s + this is stuck
    "idle_s": 180.0,
    "fail_window_s": 1800.0,
    "backoff_after": 2, "backoff_s": 600.0,
    "mute_after": 3, "app_mute_targets": 2, "app_mute_failures": 5,
    "safe_s": 180.0, "safe_resumes_per_hour": 3,
    "stale_s": 120.0, "stale_hard_s": 420.0,
    "adb_offline_s": 120.0, "adb_server_s": 60.0,
    "doubt_s": 900.0,              # delay doubt wider than doubt_max_s for this long -> reset
    "doubt_max_s": None,           # None = delay.sigma_default + 5
    "finished_hold_s": 180.0,
    "log_every_s": 600.0, "max_log_bytes": 20 * 1024 ** 2, "keep_log_bytes": 2 * 1024 ** 2,
    "max_jsonl_bytes": 10 * 1024 ** 2, "keep_jsonl_lines": 20000,
    "max_imgcache_bytes": 200 * 1024 ** 2, "keep_imgcache_bytes": 150 * 1024 ** 2,
    "max_wal_bytes": 64 * 1024 ** 2,
    "paused_alert_s": 600.0,       # auto-switching paused this long with a live game -> toast + push (P-11)
    "watchdog_beat_s": 300.0,      # the watchdog's heartbeat file older than this with a live game -> push (P-02)
    "watchdog_alert_repeat_s": 1800.0,
    "ntfy_topic": "",              # set to push the nightly summary to the ntfy app (ntfy.sh/<topic>)
    "ntfy_server": "https://ntfy.sh",
    "budget": {},                  # error budget objectives (redzone/observability.BUDGET_DEFAULTS)
    "mac_health": {},              # Mac health alerts (redzone/platform/mac_health.py; enabled: false)
    "night_ops": {},               # sync + health section of the nightly report (redzone/platform/night_ops.py)
}

# How often each fix may run: (minimum gap in seconds, at most this many per hour), per key.
LIMITS = {
    "screen_not_game": (90.0, 6), "stuck_switch": (60.0, 6), "idle_with_live": (300.0, 4),
    "idle_refresh": (600.0, 3), "repeat_failures": (0.0, 30), "safe_mode": (600.0, 3),
    "stale_feeds": (300.0, 4), "stale_feeds_hard": (900.0, 2), "adb": (300.0, 6), "adb_server": (900.0, 2),
    "delay_doubt": (1800.0, 2), "finished_hold": (60.0, 10), "log_growth": (600.0, 6),
}
from redzone.platform.agent_fix import LIMITS as _AGENT_LIMITS  # noqa: E402  (watchdog-requested fixes)
LIMITS.update(_AGENT_LIMITS)

# Switch outcomes that say nothing about the target (same idea as engine.NOT_THE_TARGETS_FAULT).
NOT_THE_TARGET = ("no safe moment", "safe exit window closed", "switch cancelled", "tv input is unknown",
                  "tv control was disabled", "no channel", "standby stream is no longer confirmed", "dry run",
                  "replay seek failed", "replay resume failed", "two-stick mode off", "crashed:",
                  "stick not responding", "buffer entry failed")   # a scrub miss (tune banner), not the channel

# Failure detail -> a short cause name for the nightly report. First match wins.
CAUSES = (
    ("stick not responding", "stick hung"),
    ("replay seek", "replay seek"), ("replay resume", "replay seek"), ("two-stick mode off", "TV input"),
    ("timed out", "timed out"), ("timeout", "timed out"), ("never started", "never started playing"),
    ("couldn't open the link", "link rejected"), ("did not accept", "link rejected"),
    ("signed out", "signed out"), ("sign in", "signed out"), ("not subscribed", "not subscribed"),
    ("blacked out", "blackout"), ("blackout", "blackout"),
    ("profile", "profile picker"), ("still watching", "still-watching prompt"),
    ("no safe moment", "no safe moment"), ("safe exit window", "no safe moment"),
    ("cancelled", "cancelled"), ("input", "TV input"), ("flip", "TV input"),
    ("not playing", "never started playing"), ("playback not confirmed", "never started playing"),
    ("not confirmed", "never started playing"), ("restore", "screen restore failed"),
    ("crashed", "app/tuner crash"), ("no tuner", "no tuner"), ("link", "channel link"),
    ("offline", "stick offline"), ("unauthorized", "stick offline"),
)


# Failures that say something about the SOURCE (this link/app can't play here). Everything else
# (timeouts, a stream that didn't start, TV input, no safe moment) can be Wi-Fi, the stick or the
# controller: those only back a target off for a while, never mute it or its app for the night.
SOURCE_CAUSES = {"link rejected", "signed out", "not subscribed", "blackout", "no tuner", "channel link"}


def failure_cause(detail: str | None) -> str:
    d = (detail or "").lower()
    for needle, name in CAUSES:
        if needle in d:
            return name
    return "other"


def night_of(ts: float) -> date:
    """The night a moment belongs to: 1:30 AM on the 3rd is still the night of the 2nd."""
    return (datetime.fromtimestamp(ts, ET) - timedelta(hours=NIGHT_STARTS_H)).date()


def night_window(night: date) -> tuple[float, float]:
    start = datetime(night.year, night.month, night.day, NIGHT_STARTS_H, tzinfo=ET)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def night_end(ts: float) -> float:
    """When tonight's mutes expire: the end of the night `ts` is in."""
    return night_window(night_of(ts))[1]


def append_jsonl(path: Path, rec: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        log.exception("could not write %s", path)


def read_jsonl(path: Path, t0: float = 0.0, t1: float = float("inf")) -> list[dict]:
    out = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and t0 <= float(rec.get("ts") or 0) < t1:
                    out.append(rec)
    except OSError:
        pass
    return out


# --- screenshots ------------------------------------------------------------------------

def frames_verdict(a: bytes, b: bytes, luma: Callable[[bytes], Any] | None = None) -> str | None:
    """Two screenshots a couple of seconds apart: "still" (a page, not video), "black" (DRM video or
    nothing), "moving" (video), or None (no screenshot). Identical bytes = identical pixels."""
    if not a or not b:
        return None
    la = lb = None
    if luma is not None:
        try:
            la, lb = luma(a), luma(b)
        except Exception:
            la = lb = None
    if la is not None and lb is not None:
        try:
            mean_a, mean_b = float(la.mean()), float(lb.mean())
            if max(mean_a, mean_b) < 12.0:
                return "black"
            if la.shape == lb.shape and float(abs(la - lb).mean()) < 0.8:
                return "still"
            return "moving"
        except Exception:
            pass
    # Pixels unreadable: never call it a frozen page from bytes alone. Identical frames are what a
    # DRM-blacked player (YouTube TV, Peacock) always returns while the game plays fine (a 1080p
    # black PNG is ~10 KB), and treating that as "still" would press keys on the playing stick.
    if a and b and max(len(a), len(b)) < 40_000:
        return "black"
    return None


def png_luma_bytes(png: bytes):
    """Luminance of a PNG (macOS sips). None if it can't be decoded."""
    import tempfile
    from redzone.tuners.screen_prompts import png_luma
    with tempfile.TemporaryDirectory(prefix="rz-sup-") as d:
        p = os.path.join(d, "f.png")
        with open(p, "wb") as f:
            f.write(png)
        return png_luma(p)


# --- rate limiting --------------------------------------------------------------------------

class Limiter:
    """A fix may run once per `gap` seconds and `per_hour` times an hour, per key."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=64))

    def allow(self, key: str, gap: float, per_hour: int) -> bool:
        now = self.clock()
        h = self.hist[key]
        if h and now - h[-1] < gap:
            return False
        return sum(1 for t in h if now - t < 3600) < per_hour

    def note(self, key: str) -> None:
        self.hist[key].append(self.clock())


@dataclass
class Fix:
    rule: str
    key: str
    action: str
    ok: bool
    detail: str = ""
    at: float = 0.0


class _UntilView(MutableMapping):
    """The old `{key: muted until}` dict over the supervisor.target / supervisor.app machines, so callers
    and tests that read or set `muted_targets[...]` keep working. Only night-muted keys are listed."""

    def __init__(self, machines: MachineMap, until: dict, mute: Callable[[str, float, str], None],
                 unmute: Callable[[str, str], None]):
        self._m, self._until, self._mute, self._unmute = machines, until, mute, unmute

    def __getitem__(self, key: str) -> float:
        m = self._m.get(key)
        if m is None or not m.is_("muted_night"):
            raise KeyError(key)
        return self._until.get(key, 0.0)

    def __setitem__(self, key: str, until: float) -> None:
        self._mute(key, float(until), "muted (set directly)")

    def __delitem__(self, key: str) -> None:
        if key not in self:
            raise KeyError(key)
        self._unmute(key, "unmuted (deleted directly)")

    def __iter__(self):
        return iter(self._m.in_state("muted_night"))

    def __len__(self) -> int:
        return len(self._m.in_state("muted_night"))


class Supervisor:
    def __init__(self, engine: Any, root: Path | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Any] = asyncio.sleep, luma: Callable[[bytes], Any] | None = png_luma_bytes,
                 push: Callable[[str, str], Any] | None = None):
        self.e = engine
        self.root = Path(root) if root else DATA
        self.log_path = self.root / "supervisor.jsonl"
        self.moments_path = self.root / "supervisor_moments.jsonl"
        self.cuts_path = self.root / "supervisor_cuts.jsonl"
        self.reports = self.root / "reports"
        self.clock = clock
        self.sleep = sleep
        self.luma = luma
        self.push = push
        self.limiter = Limiter(clock)
        # Watched conditions (supervisor.condition, one per key): `since` is the old dict view of it.
        self.conditions = MachineMap(SUPERVISOR_CONDITION, clock=clock)
        self.fixes: deque[Fix] = deque(maxlen=200)
        self.counts: Counter = Counter()                # rule -> fixes tonight (status)
        # Per target / per app availability (supervisor.target / supervisor.app). Memory only on purpose:
        # a restart forgets night mutes (the decider's own short cool-downs still apply), and the
        # nightly report already records every mute. `muted_targets` / `muted_apps` are dict views.
        self.targets = MachineMap(SUPERVISOR_TARGET, clock=clock)
        self.apps = MachineMap(SUPERVISOR_APP, clock=clock)
        self._target_until: dict[str, float] = {}        # target -> end of its back-off / night mute
        self._app_until: dict[str, float] = {}           # app -> end of its night mute
        self._forgiven: dict[str, float] = {}            # target -> failures up to here don't count for back-off
        self.failures: deque[tuple[float, str, str]] = deque(maxlen=500)   # (ts, target, cause): an event log
        self._sw_seen = clock()                          # switches before start were judged last run
        self.screen = SUPERVISOR_SCREEN.new(clock=clock)  # the two-look confirm of rule_screen
        self._screen_kind_target: tuple[str, str] | None = None   # (kind, target) the suspect look saw
        self._screen_first = 0.0                          # when the look being restored was first seen
        self._routed: dict[str, str] = {}                # game -> alternate target we routed it to
        self._judged: set[tuple[str, str]] = set()
        self._last_logs = 0.0
        self._limited: set[str] = set()                  # fixes whose rate limit we already logged
        self._observed: dict[str, str] = {}              # condition -> last logged observation
        self._report_checked: date | None = None
        self._report_failed = 0                          # crashes building this night's report
        self._report_retry_at = 0.0
        self._cut_floor = clock()                        # switches before start were graded last run
        self._cuts_judged: OrderedDict[str, None] = OrderedDict()
        self.safe_episode = SUPERVISOR_SAFE_EPISODE.new(clock=clock)   # safe mode as recorded episodes
        self.last_step = 0.0
        self.last_beat = 0.0                             # the loop's heartbeat: /api/health health.supervisor.heartbeat_age_s (P-03)
        self._paused_alerted = 0.0                       # when the "paused with a live game" alert last went out
        self._wd_alerted = 0.0                           # ... and the "watchdog isn't running" one
        self.loop = SUPERVISOR_LOOP.new(clock=clock)    # is run() alive, and is it acting?
        # Runtime invariants + the contract's HealthReport (redzone/platform/health.py): sees, never fixes.
        from redzone.platform.health import PlatformHealth
        self.health = PlatformHealth(engine, fixes=self.contract_fixes, data_dir=self.root)
        self._faults_logged: set[tuple[str, str]] = set()
        self.last_faults: list = []
        self.mac_health = None                           # redzone/platform/mac_health.MacHealth, made when enabled
        self._wake = None

    # --- config / helpers -------------------------------------------------------------

    def opt(self, key: str) -> Any:
        v = None
        try:
            v = self.e.cfg.get(f"supervisor.{key}", None)
        except Exception:
            v = None
        return DEFAULTS.get(key) if v is None else v

    # --- explicit state (redzone/machines/platform.py) ----------------------------------------

    @property
    def running(self) -> bool:
        """The background loop is alive (supervisor.loop is not 'stopped')."""
        return not self.loop.is_("stopped")

    @property
    def since(self) -> dict[str, float]:
        """condition -> when it started holding (a read-only view of supervisor.condition)."""
        return {k: m.since for k, m in self.conditions.items() if m.is_("held")}

    def _rearm(self, key: str, why: str, now: float | None = None) -> None:
        """Start a condition's clock over (after a fix ran, or the reason to wait went away)."""
        m = self.conditions.get(key)
        if m is not None:
            m.to("clear", why, now)
        self.conditions.prune()

    @property
    def muted_targets(self) -> _UntilView:
        return _UntilView(self.targets, self._target_until, self._mute_target, self._free_target)

    @property
    def muted_apps(self) -> _UntilView:
        return _UntilView(self.apps, self._app_until, self._mute_app, self._free_app)

    def _mute_target(self, target: str, until: float, why: str) -> None:
        self.targets[target].to("muted_night", why)
        self._target_until[target] = until

    def _free_target(self, target: str, why: str) -> None:
        m = self.targets.get(target)
        if m is not None:
            m.to("available", why)
        self._target_until.pop(target, None)
        self.targets.prune()

    def _mute_app(self, app: str, until: float, why: str) -> None:
        self.apps[app].to("muted_night", why)
        self._app_until[app] = until

    def _free_app(self, app: str, why: str) -> None:
        m = self.apps.get(app)
        if m is not None:
            m.to("available", why)
        self._app_until.pop(app, None)
        self.apps.prune()

    def _until(self, target: str) -> float:
        """When a muted target may be tried again (its own mute or its app's, whichever ends later)."""
        t = self._target_until.get(target, 0.0) if self.targets.state(target) == "muted_night" else 0.0
        a = self.app(target)
        return max(t, self._app_until.get(a, 0.0) if self.apps.state(a) == "muted_night" else 0.0)

    def _expire(self, now: float) -> None:
        """Back-offs and night mutes whose time is up go back to available."""
        for t, m in self.targets.items():
            if not m.is_("available") and self._target_until.get(t, 0.0) <= now:
                self._free_target(t, "back-off over" if m.is_("backed_off") else "night over")
        for a in self.apps.in_state("muted_night"):
            if self._app_until.get(a, 0.0) <= now:
                self._free_app(a, "night over")

    def _held(self, key: str, cond: bool, now: float) -> float:
        """How long `cond` has been true without a break (0 when false; resets on false)."""
        if not cond:
            m = self.conditions.get(key)
            if m is not None:
                m.to("clear", "condition cleared", now)
                self.conditions.prune()
            self._observed.pop(key, None)
            return 0.0
        m = self.conditions[key]
        m.to("held", "condition true", now)
        return now - m.since

    def _record(self, rec: dict) -> None:
        rec = {"ts": round(self.clock(), 3), **rec}
        append_jsonl(self.log_path, rec)

    def observe(self, key: str, what: str, **extra) -> None:
        """Log a condition once (until it clears), not every 10 s."""
        if self._observed.get(key) == what:
            return
        self._observed[key] = what
        self._record({"kind": "observe", "rule": key.split(":")[0], "key": key, "what": what, **extra})

    async def fix(self, rule: str, key: str, action: str, fn: Callable[[], Any], limit: str | None = None) -> bool | None:
        """Run one fix if its rate limit allows. None = rate-limited (not run)."""
        gap, per_hour = LIMITS.get(limit or rule, (60.0, 6))
        lk = f"{limit or rule}:{key}"
        if not self.limiter.allow(lk, gap, per_hour):
            if lk not in self._limited:
                self._limited.add(lk)
                self._record({"kind": "limited", "rule": rule, "key": key, "action": action})
            return None
        self._limited.discard(lk)
        self.limiter.note(lk)
        ok, detail = True, ""
        try:
            res = fn()
            if inspect.isawaitable(res):
                res = await res
            if isinstance(res, tuple):
                res, detail = res[0], str(res[1])
            ok = res is not False   # None (a fix with nothing to report) counts as done
        except Exception as ex:   # a fix must never take the supervisor down
            log.exception("supervisor fix %s failed", rule)
            ok, detail = False, f"crashed: {ex}"
        f = Fix(rule, key, action, bool(ok), detail, self.clock())
        self.fixes.append(f)
        self.counts[rule] += 1
        self._record({"kind": "fix", "rule": rule, "key": key, "action": action, "ok": bool(ok), "detail": detail})
        (log.info if ok else log.warning)("supervisor %s: %s (%s)%s", rule, action, key,
                                          f" -> {detail}" if detail else "")
        return bool(ok)

    def _dry(self) -> bool:
        return bool(self.e.cfg.get("mode.dry_run", True))

    def _games(self) -> dict:
        return getattr(getattr(self.e, "merger", None), "games", {}) or {}

    def _live(self) -> list:
        from redzone.models import Phase
        try:
            lgs = set(self.e._leagues())
        except Exception:
            lgs = None
        return [g for g in self._games().values() if g.phase == Phase.LIVE and (lgs is None or g.league in lgs)]

    @staticmethod
    def app(target: str | None) -> str:
        return (target or "").split(":", 1)[0]

    def muted(self, target: str | None, now: float | None = None) -> bool:
        if not target:
            return False
        now = self.clock() if now is None else now
        return self._until(target) > now

    def _toast(self, text: str, level: str = "info") -> None:
        try:
            self.e.toast(text, level)
        except Exception:
            pass

    # --- loop ------------------------------------------------------------------------------

    async def run(self) -> None:
        self.loop.to("running", "loop started")
        try:
            while True:
                try:
                    await self.step()
                except Exception:
                    log.exception("supervisor step failed")
                await self.sleep(float(self.opt("every_s")))
        finally:   # cancelled at shutdown (or crashed): say so instead of claiming to run forever
            self.loop.to("stopped", "loop ended")

    RULES = ("rule_stuck_switch", "rule_screen", "rule_failures", "rule_safe_mode", "rule_idle", "rule_feeds",
             "rule_adb", "rule_delay_doubt", "rule_finished_hold", "rule_logs", "track_moments", "track_episodes",
             "grade_cuts", "maybe_report", "rule_invariants", "rule_mac_health", "rule_paused_alert",
             "rule_watchdog_beat")

    def _gate(self, state: str, why: str) -> None:
        if not self.loop.is_("stopped"):   # step() called on its own (tests, one-shot) has no loop to show
            self.loop.to(state, why)

    async def step(self) -> None:
        self.last_beat = self.clock()   # before any gate: the loop is alive even when the supervisor is off or yielded
        if not self.opt("enabled"):
            self._gate("disabled", "supervisor.enabled is off")
            return
        rec = getattr(self.e, "recorded", None)
        if rec is not None and getattr(rec, "active", False):
            self._gate("yielded", "a recorded session drives the screen")
            return   # a recorded session drives the screen itself
        self._gate("running", "acting on its rules")
        self.last_step = self.clock()
        for name in self.RULES:
            self.last_beat = self.clock()   # one slow rule must block the loop for the whole stale limit to look hung
            try:
                await getattr(self, name)(self.clock())
            except Exception:
                log.exception("supervisor rule %s failed", name)

    # --- routing hook (engine.decide) -------------------------------------------------------

    def route(self, gid: str, where: Any) -> Any:
        """A game whose source is muted tonight goes to another way to watch it, when one exists."""
        target = getattr(where, "target", None)
        if not self.muted(target):
            self._routed.pop(gid, None)
            return where
        for alt in getattr(where, "alternates", None) or ():
            t = getattr(alt, "target", None)
            if t and t != target and getattr(alt, "tunable", False) and not getattr(alt, "blackout", False) \
                    and not self.muted(t):
                if self._routed.get(gid) != t:
                    self._routed[gid] = t
                    self._record({"kind": "fix", "rule": "repeat_failures", "key": gid,
                                  "action": f"route {target} -> {t}", "ok": True, "detail": ""})
                try:
                    from redzone.discovery.providers import Pick
                    return Pick(t, getattr(alt, "label", t), getattr(alt, "confidence", "likely"), True,
                                provider=getattr(alt, "provider", self.app(t)),
                                reasons=[f"{target} keeps failing tonight; using {getattr(alt, 'label', t)}"])
                except Exception:
                    from redzone.channels import Where
                    return Where(t, getattr(alt, "label", t), getattr(alt, "confidence", "likely"), True)
        return where

    # --- rules ----------------------------------------------------------------------------

    async def rule_stuck_switch(self, now: float) -> None:
        e, ctl = self.e, self.e.control
        limit = float(e.cfg.get("control.max_switch_s", 150)) + float(self.opt("stuck_grace_s"))
        p = getattr(e, "_pending_decision", None)
        if p is not None:
            self._held("ctl_task", False, now)   # a command with a switch behind it isn't the orphan the clock below times
            age = (e.pending_age(now) if hasattr(e, "pending_age")
                   else now - float(getattr(e, "_pending_since", now) or now))
            if age < limit:
                return
            self.observe("stuck_switch", f"switch to {p.target} stuck {age:.0f}s")

            def unstick():
                from redzone.control import Outcome
                ctl.cancel_pending()
                done = getattr(e, "_pending_done", None)
                if done is not None:
                    done(Outcome(False, None, age, f"switch timed out after {age:.0f}s (supervisor); "
                                 "playback not confirmed", False))
                if getattr(e, "_pending_decision", None) is p:
                    e._pending_decision = None
                    e._pending_done = None
                return True, f"gave up on the switch to {p.target} after {age:.0f}s"
            await self.fix("stuck_switch", str(p.target), "cancel stuck switch", unstick)
            return
        task = getattr(ctl, "_task", None)
        running = task is not None and not task.done() and not getattr(ctl, "_flipping", False)
        if self._held("ctl_task", running, now) >= limit:
            await self.fix("stuck_switch", "controller", "cancel stuck controller command",
                           lambda: (ctl.cancel_pending(), True)[1])
            self._rearm("ctl_task", "cancelled the stuck command", now)

    async def _screen_kind(self, stick: Any, target: str) -> tuple[str | None, str]:
        """("home" | "app_page" | "foreign" | "ok" | None, foreground)."""
        ctl = self.e.control
        fg = await stick.foreground()
        front = fg.split("/")[0] if fg else ""
        if not front:
            return None, fg
        if front.startswith(HOME_PREFIXES):
            return "home", fg
        pkg = ctl._package(stick, target) if hasattr(ctl, "_package") else None
        app = self.app(target)
        ours = {p for p in (pkg, *APP_PACKAGES.get(app, ())) if p}
        try:
            from redzone.tuners.providers import BY_KEY
            if app in BY_KEY:
                ours |= set(BY_KEY[app].packages)
        except Exception:
            pass
        if front not in ours:
            return "foreign", fg
        pb = await stick.playback(pkg)
        if pb.get("read_ok") is False:
            return None, fg
        if pb.get("state") in PLAYING:
            return "ok", fg
        a = await stick.screencap()
        await self.sleep(float(self.opt("screen_frame_gap_s")))
        b = await stick.screencap()
        verdict = frames_verdict(a, b, self.luma)
        activity = fg.split("/", 1)[1] if "/" in fg else ""
        if verdict == "still" or (verdict in (None, "black") and PAGE_RX.search(activity)):
            return "app_page", fg
        return None, fg

    def _screen_blocked(self) -> str | None:
        e, ctl, dec = self.e, self.e.control, self.e.decider
        if self._dry():
            return "dry run"
        if not getattr(ctl, "live_control", False):
            return "no TV control"
        if getattr(ctl, "input_unknown", False):
            return "TV input unknown"
        if ctl._busy() or getattr(ctl, "_flipping", False):
            return "controller busy"
        if getattr(e, "_pending_decision", None) is not None:
            return "switch in flight"
        if getattr(dec, "replaying", None) or getattr(dec, "after_replay", False):
            return "replay"
        rt = getattr(ctl, "_resume_task", None)
        if rt is not None and not rt.done():
            return "controller watchdog working"
        if self.clock() - float(getattr(ctl, "_visible_fix_at", 0.0) or 0.0) < float(self.opt("screen_after_fix_s")):
            return "controller watchdog just acted"
        return None

    @property
    def _screen_obs(self) -> tuple[str, str, float] | None:
        """(kind, target, first seen) while the screen check is suspect or restoring; else None."""
        if self.screen.is_("clear") or self._screen_kind_target is None:
            return None
        kind, target = self._screen_kind_target
        return kind, target, self.screen.since if self.screen.is_("suspect") else self._screen_first

    def _screen_clear(self, why: str, now: float | None = None) -> None:
        self.screen.to("clear", why, now)
        self._screen_kind_target = None

    async def rule_screen(self, now: float) -> None:
        from redzone.models import Phase
        e, ctl, dec = self.e, self.e.control, self.e.decider
        blocked = self._screen_blocked()
        if blocked:
            self._screen_clear(f"blocked: {blocked}", now)
            return
        stick, target = ctl.main, ctl.on_target
        g = self._games().get(getattr(dec, "current", None) or "")
        if stick is None or not target or g is None or g.phase != Phase.LIVE:
            self._screen_clear("no live game on screen to check", now)
            return
        kind, fg = await self._screen_kind(stick, target)
        if kind not in ("home", "app_page"):
            if kind == "foreign":
                self.observe("screen_not_game", f"another app in front: {fg}")
            self._screen_clear(f"screen reads {kind or 'unknown'}", now)
            return
        obs = self._screen_obs
        if obs is None or obs[1] != target:
            if obs is not None:
                self._screen_clear(f"target changed to {target}", now)
            self.screen.to("suspect", f"{kind} on screen instead of {target}", now)
            self._screen_kind_target = (kind, target)
            self.observe("screen_not_game", f"{kind} on screen instead of {target}", foreground=fg)
            return
        if now - obs[2] < float(self.opt("screen_confirm_s")):
            return
        # The reads above awaited: re-check nothing started meanwhile before touching the stick.
        if self._screen_blocked() or ctl.main is not stick or ctl.on_target != target:
            self._screen_clear("something started while the screen was read", now)
            return
        use = target
        if self.muted(target):   # decide() already routed the game elsewhere: restore THAT source
            alt = getattr((getattr(e, "where", {}) or {}).get(dec.current), "target", None)
            use = alt if alt and not self.muted(alt) else target
        relink = kind == "home"

        async def restore():
            how = await ctl.restore_visible(use, relink=relink, why=f"{kind} on screen instead of the game")
            return (bool(how), how or "still not playing")
        self._screen_first = obs[2]
        self.screen.to("restoring", f"confirmed twice: restoring {use}", now)
        ok = await self.fix("screen_not_game", use, f"restore {use} ({'deep link' if relink else 'resume'}; {kind})",
                            restore)
        if ok is False:
            self.note_failure(use, "screen restore failed", now)
        if ok is not None:
            self._screen_clear("restored" if ok else "restore failed", now)
        else:   # rate-limited: still wrong, keep the first look so the next allowed check acts at once
            self.screen.to("suspect", "restore rate-limited; still wrong", now=obs[2])

    def note_failure(self, target: str, cause: str, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.failures.append((now, target, cause))

    def _ingest_switches(self) -> None:
        store = getattr(self.e, "store", None)
        if store is None:
            return
        try:
            rows = store.recent_switches(50)
        except Exception:
            return
        newest = self._sw_seen
        for r in rows:
            ts = float(r.get("ts") or 0)
            if ts <= self._sw_seen:
                continue
            newest = max(newest, ts)
            if r.get("ok") or r.get("dry_run") or not r.get("target"):
                continue
            detail = r.get("detail") or ""
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail).get("detail", "") if detail.startswith("{") else detail
                except ValueError:
                    pass
            if str(detail).lower().startswith(NOT_THE_TARGET):
                continue
            self.failures.append((ts, r["target"], failure_cause(str(detail))))
        self._sw_seen = newest

    async def rule_failures(self, now: float) -> None:
        self._ingest_switches()
        self._expire(now)
        win = float(self.opt("fail_window_s"))
        recent = [(t, tg, c) for t, tg, c in self.failures if now - t < win]
        by_target = Counter(tg for _, tg, _ in recent)
        # Back-off counts only failures after the last idle_with_live unstick forgave them (else the
        # unstick and this rule take turns every tick); night mutes count the whole window.
        backoff_n = Counter(tg for t, tg, _ in recent if t > self._forgiven.get(tg, -1e18))
        source_fails = Counter(tg for _, tg, c in recent if c in SOURCE_CAUSES)
        cool = getattr(self.e, "_fail_cool", None)
        until = night_end(now)
        for target, n in by_target.items():
            if self.targets.state(target) == "muted_night" and self._target_until.get(target, 0) > now:
                continue
            if source_fails[target] >= int(self.opt("mute_after")):
                def mute(target=target, n=n):
                    self._mute_target(target, until, f"{source_fails[target]} source failures in {win / 60:.0f} min")
                    if cool is not None:
                        cool[target] = until
                    self._toast(f"{target} failed {n} times — skipping it for the rest of the night")
                    return True, f"{n} failures in {win / 60:.0f} min"
                await self.fix("repeat_failures", target, "mute target for the night", mute)
            elif backoff_n[target] >= int(self.opt("backoff_after")) and cool is not None \
                    and cool.get(target, 0) < now + 60:
                def back_off(target=target):
                    end = max(cool.get(target, 0), now + float(self.opt("backoff_s")))
                    cool[target] = end
                    self.targets[target].to("backed_off", f"{backoff_n[target]} failures in {win / 60:.0f} min", now)
                    self._target_until[target] = end
                    return True, f"no retry for {float(self.opt('backoff_s')) / 60:.0f} min"
                await self.fix("repeat_failures", f"backoff:{target}", "back off target", back_off)
        by_app: dict[str, set] = defaultdict(set)
        for t in self.targets.in_state("muted_night"):
            if self._target_until.get(t, 0) > now:
                by_app[self.app(t)].add(t)
        app_fails = Counter(self.app(tg) for _, tg, c in recent if c in SOURCE_CAUSES)
        for app in set(by_app) | set(app_fails):
            if self.apps.state(app) == "muted_night" and self._app_until.get(app, 0) > now:
                continue
            if len(by_app.get(app, ())) >= int(self.opt("app_mute_targets")) or \
                    app_fails[app] >= int(self.opt("app_mute_failures")):
                def mute_app(app=app):
                    self._mute_app(app, until, f"{app_fails[app]} source failures, "
                                               f"{len(by_app.get(app, ()))} muted targets")
                    self._enforce_mutes(now)
                    self._toast(f"{app} keeps failing — using other apps for the rest of the night")
                    return True, f"{app_fails[app]} failures, {len(by_app.get(app, ()))} muted targets"
                await self.fix("repeat_failures", f"app:{app}", "mute app for the night", mute_app)
        self._enforce_mutes(now)

    def _enforce_mutes(self, now: float) -> None:
        """The muted state is the authority: every target of a muted app (including one that only showed
        up after the app was muted) and every muted target keeps an engine cool-down until its mute
        ends, even after the engine overwrote it with a short failure cool-down that then expired."""
        cool = getattr(self.e, "_fail_cool", None)
        if cool is None:
            return
        targets = {getattr(w, "target", None) for w in (getattr(self.e, "where", {}) or {}).values()}
        targets |= set(self.targets.in_state("muted_night"))
        for t in targets:
            if not t:
                continue
            end = self._until(t)
            if end > now and cool.get(t, 0) < end:
                cool[t] = end

    async def rule_safe_mode(self, now: float) -> None:
        dec = self.e.decider
        held = self._held("safe_mode", bool(getattr(dec, "safe_mode", False)), now)
        if (held < float(self.opt("safe_s")) or getattr(self.e, "_pending_decision", None) is not None
                or getattr(self.e.control, "input_unknown", False)):
            return   # a switch in flight or an unknown TV input: let those settle first
        recent = Counter(tg for t, tg, _ in self.failures if now - t < 600)
        culprit = next((tg for tg, n in recent.most_common(1) if n >= 2), None)
        if not self.limiter.allow("safe_mode:night", 0, int(self.opt("safe_resumes_per_hour"))):
            self.observe("safe_mode", "safe mode keeps coming back — leaving it on; needs a look")
            return

        def leave():
            if culprit and not self.muted(culprit):
                self._mute_target(culprit, night_end(now), "behind a safe mode that kept coming back")
                cool = getattr(self.e, "_fail_cool", None)
                if cool is not None:
                    cool[culprit] = night_end(now)
            dec.auto_resume(0)
            self._toast("Safe mode cleared on its own" + (f" (skipping {culprit} tonight)" if culprit else ""))
            return True, f"after {held:.0f}s" + (f"; muted {culprit}" if culprit else "")
        if await self.fix("safe_mode", "decider", "leave safe mode", leave):
            self.limiter.note("safe_mode:night")
            self._rearm("safe_mode", "left safe mode", now)

    def _watchable(self, now: float) -> list[str]:
        e = self.e
        muted_by_user = getattr(getattr(e.decider, "user", None), "muted", set()) or set()
        dry = self._dry()
        out = []
        for g in self._live():
            w = (getattr(e, "where", {}) or {}).get(g.game_id)
            t = getattr(w, "target", None)
            if not t or not (getattr(w, "tunable", False) or dry):
                continue
            if t in (getattr(e, "_fail_cool", {}) or {}) or self.muted(t, now) or g.game_id in muted_by_user:
                continue
            try:
                if e.merger.is_stale(g.game_id):
                    continue
            except Exception:
                pass
            out.append(g.game_id)
        return out

    async def rule_idle(self, now: float) -> None:
        from redzone.decider.decider import State
        e, dec = self.e, self.e.decider
        u = dec.user
        busy = (u.paused or u.locked_game or getattr(e, "hold", None) or dec.safe_mode
                or getattr(e, "_pending_decision", None) is not None)
        d = getattr(e, "last_decision", None)
        nothing_on = dec.current is None or (d is not None and getattr(d, "state", None) == State.IDLE)
        if d is not None and str(getattr(d, "reason", "")).startswith("waiting for your team"):
            nothing_on = False   # idle on purpose: your game tips soon
        watchable = self._watchable(now) if not busy and nothing_on else []
        held = self._held("idle_with_live", bool(watchable), now)
        if held < float(self.opt("idle_s")):
            return
        self.observe("idle_with_live", f"{len(watchable)} watchable live games but nothing on",
                     reason=getattr(d, "reason", ""))

        def unstick():
            dec.start_retry_at = 0.0
            dec.start_fails = 0
            cool = getattr(e, "_fail_cool", None)
            cleared = 0
            if cool is not None:
                for t in [t for t in cool if not self.muted(t, now)]:
                    cool.pop(t, None)
                    self._forgiven[t] = now   # these failures were forgiven: don't back off on them again
                    if self.targets.state(t) == "backed_off":
                        self._free_target(t, "back-off cleared: live games but nothing on")
                    cleared += 1
            failed = getattr(e.control, "_failed", None)
            if isinstance(failed, dict):
                failed.clear()
            return True, f"start back-off reset, {cleared} cool-downs cleared"
        await self.fix("idle_with_live", "decider", "clear start back-off and cool-downs", unstick)
        if held >= 2 * float(self.opt("idle_s")) and hasattr(e, "reset_feeds"):
            await self.fix("idle_with_live", "feeds", "refresh feeds", lambda: e.reset_feeds(hard=False),
                           limit="idle_refresh")

    async def rule_feeds(self, now: float) -> None:
        e = self.e
        live = self._live()
        try:
            stale = [g.game_id for g in live if e.merger.is_stale(g.game_id)]
        except Exception:
            stale = []
        cur = getattr(e.decider, "current", None)
        bad = bool(live) and (len(stale) == len(live) or (cur in stale))
        held = self._held("stale_feeds", bad, now)
        if held < float(self.opt("stale_s")) or not hasattr(e, "reset_feeds"):
            return
        self.observe("stale_feeds", f"{len(stale)}/{len(live)} live games stale")
        if held >= float(self.opt("stale_hard_s")):
            await self.fix("stale_feeds", "http", "new HTTP client + push feed restart",
                           lambda: e.reset_feeds(hard=True), limit="stale_feeds_hard")
        else:
            await self.fix("stale_feeds", "backoff", "drop fetch back-off", lambda: e.reset_feeds(hard=False))

    async def rule_adb(self, now: float) -> None:
        ctl = self.e.control
        if self._dry():
            return
        from redzone.tuners import adb as adbmod
        if self._held("adb_server", not getattr(ctl, "adb_ok", False) and bool(getattr(ctl, "sticks", [])),
                      now) >= float(self.opt("adb_server_s")):
            async def start_server():
                await adbmod._adb("start-server", timeout=10)
                ctl.adb_ok = await adbmod.adb_available()
                return ctl.adb_ok, "adb answering" if ctl.adb_ok else "adb still not answering"
            await self.fix("adb", "server", "start adb server", start_server, limit="adb_server")
            return
        net = getattr(getattr(self.e, "network", None), "state", None)
        if net is not None and (net.away or net.routes_broken):
            # Away from home, or the home routes go into a tunnel: reconnecting can't help, and the
            # sticks aren't broken. Say so once; the clocks start fresh when it's fixed.
            self.observe("network", net.message)
            for s in list(getattr(ctl, "sticks", []) or []):
                self._held(f"adb:{s.host}", False, now)
            return
        self._observed.pop("network", None)
        for s in list(getattr(ctl, "sticks", []) or []):
            state = getattr(s, "state", "unknown")
            held = self._held(f"adb:{s.host}", state != "connected", now)
            if state == "unauthorized" and held:
                self.observe(f"adb:{s.host}", "stick needs the adb prompt approved on the TV")
                continue
            if held < float(self.opt("adb_offline_s")):
                continue
            if getattr(self.e, "_pending_decision", None) is not None:
                continue   # one adb owner: never drop a transport under a switch in flight (P-09); next step

            async def reconnect(s=s):
                s.drop_session()
                s._next_retry = 0.0
                await adbmod._adb("disconnect", s.host, timeout=6)
                ok = await s.connect()
                if ok:
                    if not s.packages:
                        await s.identify()
                    forget = getattr(ctl, "_forget", None)
                    if forget is not None:
                        forget(s)   # it may have rebooted: what it showed is unknown
                return ok, s.state
            await self.fix("adb", s.host, "drop adb transport and reconnect", reconnect)

    def _doubt_max(self) -> float:
        v = self.opt("doubt_max_s")
        if v is not None:
            return float(v)
        return float(self.e.cfg.get("delay.sigma_default", 15) or 15) + 5.0

    async def rule_delay_doubt(self, now: float) -> None:
        delays = getattr(self.e, "delays", None)
        if delays is None:
            return
        cap = self._doubt_max()
        for target, d in list(delays.book.items()):
            try:
                sig = d.current_sigma(now)
            except Exception:
                continue
            held = self._held(f"delay_doubt:{target}", sig > cap, now)
            if held < float(self.opt("doubt_s")):
                continue

            if getattr(delays, "edge_model", False) and delays.edge_measured(target):
                # [edge model, Nick Nov 6 item 3] a measured channel is re-anchored on its edge + behind_live,
                # never reset to the defaults (that dropped a scrubbed tune's 60 s behind_live)
                def reanchor(target=target, old=(d.mu, sig)):
                    nd = delays.reanchor(target)
                    if nd is None:
                        return False, "no edge to re-anchor on"
                    return True, (f"{old[0]:.0f}±{old[1]:.0f}s -> {nd.mu:.0f}±{nd.sigma:.0f}s "
                                  f"(re-anchored: edge + behind {nd.behind:.0f}s, {nd.source})")
                if await self.fix("delay_doubt", target, "re-anchor measured delay", reanchor):
                    self._rearm(f"delay_doubt:{target}", "delay rebuilt", now)
                continue

            def reset(target=target, old=(d.mu, sig)):
                delays.book.pop(target, None)
                nd = delays.get(target)   # rebuilt from its samples (taps / score bug / show changes)
                how = "rebuilt from samples"
                if nd.current_sigma(now) > cap:
                    defaults = self.e.cfg.get("delay.default", {}) or {}
                    nd.mu = float(defaults.get(self.app(target), 40.0))
                    nd.sigma = float(self.e.cfg.get("delay.sigma_default", 15) or 15)
                    nd.calibrated_at, nd.source, nd.live = None, "default", None
                    how = "safe defaults"
                return True, f"{old[0]:.0f}±{old[1]:.0f}s -> {nd.mu:.0f}±{nd.sigma:.0f}s ({how})"
            if await self.fix("delay_doubt", target, "reset delay estimate", reset):
                self._rearm(f"delay_doubt:{target}", "delay rebuilt", now)

    async def rule_finished_hold(self, now: float) -> None:
        from redzone.models import Phase
        from redzone.decider.decider import REDZONE, FocusMode
        e, u = self.e, self.e.decider.user
        games = self._games()

        def over(gid: str | None) -> bool:
            if not gid:
                return False
            if gid == REDZONE:
                try:
                    from redzone.channels import redzone_on
                    return not redzone_on(games.values())
                except Exception:
                    return False
            g = games.get(gid)
            return (g is None and bool(games)) or (g is not None and g.phase == Phase.POST)

        h = getattr(e, "hold", None)
        gid = h.get("game_id") if isinstance(h, dict) else None
        wait = float(self.opt("finished_hold_s"))
        if self._held("finished_hold:hold", over(gid), now) >= wait:
            def release():
                e._release_hold("")
                self._toast("That game is over — autopilot is back")
                return True, f"released hold on {gid}"
            await self.fix("finished_hold", f"hold:{gid}", "release hold on finished game", release)
            return
        if not gid and self._held("finished_hold:lock", over(u.locked_game), now) >= wait:
            def unlock():
                was, u.locked_game = u.locked_game, None
                return True, f"unlocked {was}"
            await self.fix("finished_hold", f"lock:{u.locked_game}", "clear lock on finished game", unlock)
        if self._held("finished_hold:focus", over(u.focus_game), now) >= wait:
            def unfocus():
                was = u.focus_game
                u.focus_game, u.focus_mode = None, FocusMode.AUTOPILOT
                return True, f"focus on {was} -> autopilot"
            await self.fix("finished_hold", f"focus:{u.focus_game}", "clear focus on finished game", unfocus)

    async def rule_logs(self, now: float) -> None:
        if now - self._last_logs < float(self.opt("log_every_s")):
            return
        self._last_logs = now
        done = []
        for f in sorted(self.root.glob("*.log")):
            if _size(f) > int(self.opt("max_log_bytes")):
                done.append(("log", f))
        for f in sorted(self.root.glob("*.jsonl")):
            if _size(f) > int(self.opt("max_jsonl_bytes")):
                done.append(("jsonl", f))
        cache = self.root / "imgcache"
        if cache.is_dir() and _dir_size(cache) > int(self.opt("max_imgcache_bytes")):
            done.append(("imgcache", cache))
        wal = self.root / "redzone.db-wal"
        if _size(wal) > int(self.opt("max_wal_bytes")):
            done.append(("wal", wal))
        for kind, path in done:
            if kind == "log":
                fn = lambda path=path: rotate_log(path, int(self.opt("keep_log_bytes")))
            elif kind == "jsonl":
                fn = lambda path=path: trim_jsonl(path, int(self.opt("keep_jsonl_lines")))
            elif kind == "imgcache":
                fn = lambda path=path: prune_dir(path, int(self.opt("keep_imgcache_bytes")))
            else:
                fn = self._checkpoint
            await self.fix("log_growth", path.name, f"shrink {path.name}", fn)
        try:
            free = shutil.disk_usage(self.root if self.root.exists() else ROOT).free
        except OSError:
            free = None
        if free is not None and free < 1024 ** 3:
            self.observe("disk", f"only {free / 1024 ** 3:.1f} GB free on the disk")

    def _checkpoint(self):
        store = getattr(self.e, "store", None)
        if store is None or not hasattr(store, "checkpoint"):
            return False, "no store"
        store.checkpoint()
        return True, f"WAL now {_size(self.root / 'redzone.db-wal') / 1024 ** 2:.1f} MB"

    # --- key moments + nightly report -----------------------------------------------------

    async def track_moments(self, now: float) -> None:
        """Each big play, once it has aired: did the screen show it (seen) or not (missed)?"""
        from redzone.decider.screen import PLAY_LEN_S
        dec, e = self.e.decider, self.e
        for gid, tl in list(getattr(dec, "timelines", {}).items()):
            w = (getattr(e, "where", {}) or {}).get(gid)
            target = getattr(w, "target", None) or getattr(dec, "_known_target", {}).get(gid) or "unknown"
            try:
                mu = e.delays.get(target).mu
            except Exception:
                mu = 40.0
            play_len = PLAY_LEN_S.get(tl.league) or 8.0
            for ev in list(tl.events):
                if ev.kind != "play" or not ev.big:
                    continue
                key = (gid, ev.pid or ev.label)
                air_end = ev.seen + mu
                if key in self._judged or now < air_end + 3:
                    continue
                self._judged.add(key)
                seen = bool(dec._saw(gid, air_end - play_len, air_end))
                g = self._games().get(gid)
                append_jsonl(self.moments_path, {
                    "ts": round(ev.seen, 2), "game": gid, "matchup": getattr(g, "matchup", gid),
                    "league": getattr(tl.league, "value", str(tl.league)), "label": ev.label[:160],
                    "kind": ev.play_kind, "seen": seen, "on_screen": dec.on_screen_at(air_end),
                    "dry_run": self._dry()})
        if len(self._judged) > 5000:
            self._judged = set(list(self._judged)[-2000:])

    async def track_episodes(self, now: float) -> None:
        """Safe mode on/off as episodes, so the report (and the error budget) can count its minutes."""
        on = bool(getattr(getattr(self.e, "decider", None), "safe_mode", False))
        first = self.safe_episode.is_("unknown")
        changed = self.safe_episode.to("on" if on else "off", "decider safe mode " + ("on" if on else "off"), now)
        if changed and not (first and not on):
            self._record({"kind": "episode", "rule": "safe_mode", "state": "on" if on else "off"})

    @property
    def _safe_on(self) -> bool | None:
        """Last safe-mode state recorded as an episode (None = not looked yet)."""
        return None if self.safe_episode.is_("unknown") else self.safe_episode.is_("on")

    async def grade_cuts(self, now: float) -> None:
        """Each live cut, once the feed has caught up: was a play on screen in the game we left, and in
        the one we landed on? Uses the delays the switch planned with (saved in its trace)."""
        store = getattr(self.e, "store", None)
        if store is None:
            return
        rows = _switch_rows(store, max(self._cut_floor, now - CUT_LOOKBACK_S), now)
        timelines = getattr(getattr(self.e, "decider", None), "timelines", {}) or {}
        for r in rows:
            t = trace.of_row(r)
            if not t or t.get("dry_run") or t.get("cut_at") is None or t.get("id") in self._cuts_judged:
                continue
            if now < float(t["cut_at"]) + CUT_JUDGE_AFTER_S:
                continue
            self._cuts_judged[t["id"]] = None
            g = grade_cut(t, timelines)
            labels = getattr(self.e, "cheap_labels", None)
            if cheap_labels.is_on(getattr(self.e, "cfg", None)):   # [cheap labels] off: no new rows, no new fields
                if g is not None:
                    g.update(cheap_labels.cut_fields(labels, t))
                cheap_labels.write_outcome(self.root / "switch_outcomes.jsonl", t, r, g)
            if g is not None:
                append_jsonl(self.cuts_path, g)
                if g.get("leave") == "play":
                    log.warning("cut at %s left %s mid-play (%s)", _clock_et(float(t["cut_at"])),
                                g.get("from_label") or g.get("from"), g.get("leave_why"))
        while len(self._cuts_judged) > 2000:
            self._cuts_judged.popitem(last=False)

    async def maybe_report(self, now: float) -> None:
        prev = night_of(now) - timedelta(days=1)
        if self._report_checked == prev:
            return
        if now < night_window(prev)[1] + REPORT_AFTER_S:
            return
        if now < self._report_retry_at:
            return
        self._report_checked = prev
        self.collect_live_night(prev)
        path = self.reports / f"{prev.isoformat()}.md"
        if path.exists():
            return
        try:
            rep = night_report(prev, getattr(self.e, "store", None), self.root, now=now,
                               budget_cfg=self.opt("budget"), ops_cfg=self.opt("night_ops"))
            write_report(path, rep["text"])
            write_metrics(path.with_suffix(".json"), rep["metrics"])
        except Exception:
            # A crash must not cost the night its report: look again later, a few times, then give up.
            self._report_failed += 1
            if self._report_failed < REPORT_TRIES:
                self._report_checked = None
                self._report_retry_at = now + REPORT_RETRY_S
            raise
        self._report_failed, self._report_retry_at = 0, 0.0
        self._record({"kind": "report", "rule": "report", "key": prev.isoformat(), "path": str(path)})
        spent = rep["budget"]["spent"]
        await self.send_push(f"RedZone night of {prev.isoformat()}", rep["phone"],
                             priority="high" if spent else "default",
                             tags="warning,tv" if spent else "tv")

    def collect_live_night(self, night: date) -> Path | None:
        """[live nights] After each night, with no one's input: its landings (offset vs the dead ball),
        sync anchors (error per stick), TV switch latency and phone press-to-TV, LAN addresses masked,
        into data/live_nights/<night>/ (scripts/collect_live_test.py's format; summary.json is what
        scripts/northstar.py's "Live nights" section reads). Once per night; never raises."""
        out = self.root / "live_nights" / night.isoformat()
        if (out / "summary.json").exists():
            return None
        try:
            import importlib.util
            src = Path(__file__).resolve().parent.parent / "scripts" / "collect_live_test.py"
            spec = importlib.util.spec_from_file_location("rz_collect_live_test", src)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            lo, hi = night_window(night)
            s = mod.collect(self.root, out, lo, hi)
            try:
                # [calib.auto] the night's flip and press-to-TV, judged against the last 5 nights. Written
                # whether or not calib.auto is on (the guard needs history); read back only when it is.
                from redzone import calibration
                calibration.merge(self.root, night, calibration.timing_from_summary(s))
                # lane 2's sync half: the night's seek fit and per-channel delays (seeks.jsonl, sync.jsonl)
                calibration.merge(self.root, night, calibration.sync_from_night(self.root, lo, hi), section="sync")
            except Exception:
                log.exception("calibration write failed for %s", night)
            self._record({"kind": "report", "rule": "live_night", "key": night.isoformat(), "path": str(out),
                          "rows": s.get("rows")})
            return out
        except Exception:
            log.exception("live night collection failed for %s", night)
            return None

    async def send_push(self, title: str, body: str, priority: str = "default", tags: str = "tv") -> bool:
        topic = str(self.opt("ntfy_topic") or "").strip()
        if not topic:
            return False
        return await push_ntfy(str(self.opt("ntfy_server")), topic, title, body, priority, tags, self.push)

    async def rule_invariants(self, now: float) -> None:
        """Check the runtime invariants; log each fault once when it appears and once when it clears."""
        faults = self.health.faults(now)
        self.last_faults = faults
        keys = {(f.code, f.component) for f in faults}
        for f in faults:
            if (f.code, f.component) not in self._faults_logged:
                self._faults_logged.add((f.code, f.component))
                self._record({"kind": "invariant", "code": f.code, "severity": f.severity.value,
                              "component": f.component, "detail": f.detail})
                if f.severity.value in ("error", "critical"):
                    log.warning("invariant broken: %s (%s): %s", f.code, f.component, f.detail)
                if f.code in PUSH_FAULTS:
                    # Only faults a person has to fix (a refused sign-in); once per appearance.
                    try:
                        await self.send_push(f"RedZone: {f.code}", f.detail, priority="high", tags="warning,tv")
                    except Exception:
                        log.exception("couldn't push fault %s", f.code)
        for k in sorted(self._faults_logged - keys):
            self._faults_logged.discard(k)
            self._record({"kind": "invariant_cleared", "code": k[0], "component": k[1]})

    def _mac_snapshot(self, now: float):
        """What the Mac health alerts look at this tick (redzone/platform/mac_health.py)."""
        import sys
        from redzone.platform.mac_health import Snapshot
        from redzone.platform.wake import WakeWatch
        e = self.e
        if self._wake is None:
            mono = time.monotonic if self.clock is time.time else self.clock   # tests: one fake clock
            self._wake = WakeWatch(wall=self.clock, mono=mono, expect_s=float(self.opt("every_s")))
        snap = Snapshot(live_games=len(self._live()), slept_s=self._wake.check())
        s = getattr(getattr(e, "governor", None), "last", None)
        if s is not None:
            snap.on_battery, snap.battery_pct = s.on_battery, s.battery_pct
            snap.mem_pressure, snap.swap_used_mb = s.mem_pressure, s.swap_used_mb
        if sys.platform == "darwin" and hasattr(e, "_caffeinate"):
            c = e._caffeinate
            snap.keep_awake = c is not None and c.poll() is None
        u = getattr(getattr(e, "decider", None), "user", None)
        snap.paused = bool(getattr(u, "paused", False))
        snap.held = bool(getattr(u, "locked_game", None) or getattr(e, "hold", None))
        store = getattr(e, "store", None)
        if store is not None:
            try:
                rows = store.recent_switches(1)
                snap.last_switch_ts = float(rows[0]["ts"]) if rows else None
            except Exception:
                snap.last_switch_ts = None
        return snap

    async def rule_mac_health(self, now: float) -> None:
        """Mac health alerts to the phone (battery, sleep, memory, paused, no switches). OFF by default."""
        from redzone.platform import mac_health as mh
        opts = mh.options(self.opt("mac_health"))
        if not opts["enabled"]:
            self.mac_health = None
            return
        if self.mac_health is None:
            self.mac_health = mh.MacHealth(opts)
        self.mac_health.opts = opts
        for a in self.mac_health.check(self._mac_snapshot(now), now):
            self._record(a.row())
            if a.cleared:
                self._toast(a.text, "info")
                continue
            if a.level == "info":
                continue                      # logged for the night report only
            self._toast(a.text, "error" if a.level in mh.PUSH_LEVELS else "warn")
            if a.level in mh.PUSH_LEVELS and opts["push"]:
                try:
                    await self.send_push(f"RedZone Mac: {a.code.replace('_', ' ')}", a.text,
                                         priority="urgent" if a.level == "urgent" else "high", tags="warning,computer")
                except Exception:
                    log.exception("couldn't push Mac alert %s", a.code)

    async def rule_paused_alert(self, now: float) -> None:
        """Auto-switching paused while a game is live for paused_alert_s: say so (P-11). One forgotten tap on
        Saturday used to leave Sunday on one channel with nothing but the phone banner saying so. Skipped when
        the Mac-health alerts are on (they carry the same alert, mac_health.paused)."""
        if self.mac_health is not None:
            return
        u = getattr(getattr(self.e, "decider", None), "user", None)
        live = self._live()
        held = self._held("paused_live", bool(getattr(u, "paused", False)) and bool(live), now)
        if held < float(self.opt("paused_alert_s")):
            self._paused_alerted = 0.0
            return
        if self._paused_alerted and now - self._paused_alerted < float(self.opt("watchdog_alert_repeat_s")):
            return
        self._paused_alerted = now
        text = (f"Auto-switching has been paused {held / 60:.0f} min with {len(live)} live game"
                f"{'s' if len(live) != 1 else ''}. Tap Resume to put it back on autopilot.")
        self._record({"kind": "paused_alert", "rule": "paused", "minutes": round(held / 60, 1), "live": len(live)})
        self._toast(text, "warn")
        try:
            await self.send_push("RedZone: paused", text, priority="high", tags="warning,tv")
        except Exception:
            log.exception("couldn't push the paused alert")

    def watchdog_beat_age(self, now: float | None = None) -> float | None:
        """Seconds since the watchdog last touched data/agent.heartbeat; None when it never has."""
        try:
            return round((self.clock() if now is None else now) - (self.root / "agent.heartbeat").stat().st_mtime, 1)
        except OSError:
            return None

    async def rule_watchdog_beat(self, now: float) -> None:
        """The watchdog is a separate launchd job nothing watched: it ran no check from Nov 3 to Nov 8 (P-02).
        With a live game, a heartbeat file older than watchdog_beat_s (or none) is a push."""
        age = self.watchdog_beat_age(now)
        stale = age is None or age > float(self.opt("watchdog_beat_s"))
        held = self._held("watchdog_down", stale and bool(self._live()), now)
        if held < 60.0:
            self._wd_alerted = 0.0
            return
        if self._wd_alerted and now - self._wd_alerted < float(self.opt("watchdog_alert_repeat_s")):
            return
        self._wd_alerted = now
        text = ("The watchdog has never run (no heartbeat). Install it: python -m agent.install"
                if age is None else f"The watchdog has not checked in for {age / 60:.0f} min: it is not running")
        self._record({"kind": "watchdog_down", "rule": "watchdog", "age_s": age})
        self._toast(text, "warn")
        try:
            await self.send_push("RedZone: watchdog down", text, priority="high", tags="warning,computer")
        except Exception:
            log.exception("couldn't push the watchdog alert")

    def contract_fixes(self) -> list:
        """Tonight's fixes in the contract's shape (contracts/health.py Fix)."""
        from redzone.contracts.health import Fix as HFix
        return [HFix(fault_code=f.rule, action=f.action, target=f.key, ok=f.ok, at=f.at, source="supervisor",
                     note=f.detail) for f in self.fixes]

    def report(self, now: float | None = None):
        """contracts.health.Health.report: faults (invariants), fixes, safe mode, two-stick, away, RAM, disk."""
        return self.health.report(self.clock() if now is None else now)

    def status(self) -> dict:
        now = self.clock()
        return {"running": self.running, "last_step": self.last_step or None,
                "heartbeat_age_s": round(now - self.last_beat, 1) if self.last_beat else None,
                "switch_in_flight": getattr(self.e, "_pending_decision", None) is not None,
                "watchdog_beat_age_s": self.watchdog_beat_age(now),
                "fixes_tonight": dict(self.counts),
                "recent": [{"rule": f.rule, "action": f.action, "ok": f.ok, "detail": f.detail}
                           for f in list(self.fixes)[-5:]],
                "muted_targets": sorted(t for t, u in self.muted_targets.items() if u > now),
                "muted_apps": sorted(a for a, u in self.muted_apps.items() if u > now),
                "backed_off": sorted(t for t in self.targets.in_state("backed_off")
                                     if self._target_until.get(t, 0) > now),
                "watching": sorted(k for k in self.since),
                "machines": {"loop": self.loop.view(now, history=0), "screen": self.screen.view(now, history=0),
                             "targets": self.targets.view(now), "apps": self.apps.view(now)},
                "faults": [{"code": f.code, "severity": f.severity.value, "component": f.component,
                            "detail": f.detail} for f in self.last_faults],
                "mac_health": self.mac_health.active() if self.mac_health is not None else None}


# --- file housekeeping ----------------------------------------------------------------------

def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _dir_size(d: Path) -> int:
    total = 0
    for root, _, files in os.walk(d):
        for f in files:
            total += _size(Path(root) / f)
    return total


def rotate_log(path: Path, keep_bytes: int) -> tuple[bool, str]:
    """Keep the last keep_bytes in <name>.1 and empty the log in place (writers append, so they
    simply continue at the new end)."""
    size = _size(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - keep_bytes))
        tail = f.read()
    old = path.with_name(path.name + ".1")
    old.write_bytes(tail)
    with open(path, "r+b") as f:
        f.truncate(0)
    return True, f"{size / 1024 ** 2:.1f} MB -> 0 (last {len(tail) / 1024 ** 2:.1f} MB in {old.name})"


def trim_jsonl(path: Path, keep_lines: int) -> tuple[bool, str]:
    """Keep the last `keep_lines`. Other processes append to some of these files (the watchdog, deploy.py): rows
    written while the rewrite was being prepared are carried over instead of lost (P-16)."""
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    kept = lines[-keep_lines:]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(b"".join(kept))
    try:
        with path.open("rb") as f:   # whatever was appended since the read above
            f.seek(len(raw))
            late = f.read()
    except OSError:
        late = b""
    if late:
        with tmp.open("ab") as f:
            f.write(late)
    tmp.replace(path)
    return True, f"{len(lines)} -> {len(kept)} lines" + (f" (+{len(late)} bytes written meanwhile)" if late else "")


def prune_dir(d: Path, keep_bytes: int) -> tuple[bool, str]:
    files = []
    for root, _, names in os.walk(d):
        for n in names:
            p = Path(root) / n
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
    total = sum(s for _, s, _ in files)
    removed = 0
    for _, s, p in sorted(files):
        if total <= keep_bytes:
            break
        try:
            p.unlink()
            total -= s
            removed += 1
        except OSError:
            pass
    return True, f"removed {removed} oldest files; {total / 1024 ** 2:.0f} MB left"


# --- the nightly report -----------------------------------------------------------------------

def _switch_rows(store: Any, t0: float, t1: float) -> list[dict]:
    if store is None:
        return []
    try:
        if hasattr(store, "switches_between"):
            return store.switches_between(t0, t1)
        return [r for r in store.recent_switches(5000) if t0 <= float(r.get("ts") or 0) < t1]
    except Exception:
        log.exception("could not read switches for the report")
        return []


def _detail(r: dict) -> dict:
    d = r.get("detail")
    if isinstance(d, dict):
        return d
    try:
        v = json.loads(d or "{}")
        return v if isinstance(v, dict) else {"detail": str(v)}
    except ValueError:
        return {"detail": str(d)}


def _short(target: str) -> str:
    """prime:amzn1.dv.gti.1e64e9b4-...-8d1f6cc07a42 -> prime:amzn1.dv.gt…6cc07a42 (readable in a table)."""
    return target if len(target) <= 30 else f"{target[:18]}…{target[-8:]}"


def _clock_et(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%-I:%M %p")


def build_report(night: date, store: Any, root: Path, now: float | None = None) -> tuple[str, str]:
    """(markdown, one-paragraph summary) for one night."""
    rep = night_report(night, store, root, now=now)
    return rep["text"], rep["summary"]


def grade_cut(t: dict, timelines: dict) -> dict | None:
    """Both ends of one live cut from its trace, against the game timelines the decider keeps.
    leave/enter: "dead" | "play" | "unknown" | None (not graded: a replay, RedZone, a reel, no data)."""
    cut, pic = t.get("cut_at"), t.get("picture_at")
    if cut is None:
        return None
    rec = {"ts": cut, "id": t.get("id"), "from": t.get("from"), "from_label": t.get("from_label"),
           "to": t.get("to"), "to_label": t.get("to_label"), "kind": t.get("kind"), "path": t.get("path"),
           # Nick, Nov 4 10:55 PM: a second "Go now" tap switches at once, even mid-play. It is his explicit
           # control, so the graders count it on its own line, never as the decider joining mid-play.
           "forced": bool(t.get("forced")),
           "leave": None, "leave_why": "", "enter": None, "enter_why": ""}

    def land(gid, at, delay, offsets: bool = False):
        tl = timelines.get(gid or "")
        dead = getattr(tl, "dead", None)
        if dead is None or not delay or delay.get("mu") is None:
            return None, ""
        x = float(at) - float(delay["mu"])    # the real (feed) time the screen showed at that moment
        try:
            got = dead.landing(x, x)
        except Exception:
            log.exception("cut grade failed")
            return None, ""
        if offsets:
            # [live nights] Where in the dead ball the picture came up: seconds after it began and
            # seconds before play resumed (negative: it had resumed). The live-night log's landing.
            try:
                w = next((w for w in reversed(dead.windows()) if w.start <= x), None)
                if w is not None:
                    rec["enter_after_dead_s"] = round(x - w.start, 1)
                if getattr(got, "resume", None) is not None:
                    rec["enter_before_play_s"] = round(got.resume - x, 1)
            except Exception:
                log.debug("landing offsets unavailable", exc_info=True)
        return got.state, f"{got.why}{' (' + got.kind + ')' if got.kind else ''}"

    if t.get("from") and not t.get("from_replay"):
        rec["leave"], rec["leave_why"] = land(t["from"], cut, t.get("leave_delay"))
    if pic is not None and t.get("action") != "replay":
        rec["enter"], rec["enter_why"] = land(t.get("to"), pic, t.get("enter_delay"), offsets=True)
    if rec["leave"] is None and rec["enter"] is None:
        return None
    return rec


async def push_ntfy(server: str, topic: str, title: str, body: str, priority: str = "default",
                    tags: str = "tv", fake: Callable[[str, str], Any] | None = None) -> bool:
    """Post to ntfy (free, no account: install the ntfy app and subscribe to the topic). The topic is
    the only secret, so it must be long and random."""
    topic = (topic or "").strip()
    if not NTFY_TOPIC_RX.match(topic):
        log.warning("ntfy topic %r is not valid (letters, digits, - and _, at most 64): no push", topic[:70])
        return False
    try:
        if fake is not None:
            res = fake(title, body)
            if inspect.isawaitable(res):
                await res
            return True
        import httpx
        # Header values must be latin-1: keep the title plain; the body is UTF-8.
        safe_title = title.encode("latin-1", "replace").decode("latin-1")
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(f"{(server or 'https://ntfy.sh').rstrip('/')}/{topic}", content=body.encode(),
                             headers={"Title": safe_title, "Tags": tags, "Priority": priority})
        if r.status_code >= 300:
            log.warning("push refused: HTTP %s %s", r.status_code, r.text[:120])
        return r.status_code < 300
    except Exception as ex:
        log.warning("push failed: %s", ex)
        return False


def write_metrics(path: Path, m: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(m, indent=1, default=str))
    tmp.replace(path)


def night_report(night: date, store: Any, root: Path, now: float | None = None,
                 budget_cfg: dict | None = None, ops_cfg: dict | None = None) -> dict:
    """text (markdown), summary (one line), phone (the push body), metrics (the .json sidecar), budget."""
    budget_cfg = budget_cfg if isinstance(budget_cfg, dict) else {}
    t0, t1 = night_window(night)
    rows = _switch_rows(store, t0, t1)
    live_rows = [r for r in rows if not r.get("dry_run")]
    dry_rows = [r for r in rows if r.get("dry_run")]
    ok = [r for r in live_rows if r.get("ok")]
    failed = [r for r in live_rows if not r.get("ok")]
    held = [r for r in failed if str(_detail(r).get("detail") or "").lower().startswith(NOT_THE_TARGET)]
    causes = Counter(failure_cause(_detail(r).get("detail")) for r in failed)
    cause_targets: dict[str, Counter] = defaultdict(Counter)
    for r in failed:
        cause_targets[failure_cause(_detail(r).get("detail"))][r.get("target") or "?"] += 1
    took = [float(_detail(r).get("took") or 0) for r in ok if _detail(r).get("took")]
    kinds = Counter(r.get("kind") or "?" for r in live_rows)   # real choices only: dry-run replays are not Nick

    moments = read_jsonl(root / "supervisor_moments.jsonl", t0, t1)
    seen = [m for m in moments if m.get("seen")]
    missed = [m for m in moments if not m.get("seen")]

    cals = []
    if store is not None:
        try:
            cals = [c for c in store.calibrations(since=t0) if c[0] < t1]
        except Exception:
            cals = []
    by_target: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for ts, target, sample, kind in cals:
        by_target[target].append((float(sample), kind or "tap"))

    events = read_jsonl(root / "supervisor.jsonl", t0, t1)
    fixes = [x for x in events if x.get("kind") == "fix"]
    fix_rules = Counter(x.get("rule") for x in fixes)
    fix_fail = Counter(x.get("rule") for x in fixes if not x.get("ok"))
    limited = [x for x in events if x.get("kind") == "limited"]
    observed = [x for x in events if x.get("kind") == "observe"]

    lines = [f"# RedZone night report: {night.isoformat()}", ""]
    summary = (f"{len(live_rows)} switches ({len(failed) - len(held)} failed"
               + (f", {len(held)} held back or cancelled" if held else "") + ")"
               + (f", {len(dry_rows)} dry-run" if dry_rows else "")
               + f"; key moments seen {len(seen)}/{len(moments)}"
               + f"; {len(fixes)} auto-fixes"
               + (f"; {len(by_target)} delays measured" if by_target else ""))
    lines += [f"**{summary}.**", "",
              f"Window: {_clock_et(t0)} {night.isoformat()} to {_clock_et(t1)} the next day (ET).", ""]

    end_at = None
    if store is not None and hasattr(store, "last_snapshot_between"):
        try:
            end_at = store.last_snapshot_between(t0, t1)
        except Exception:
            end_at = None
    nm = obs.metrics(night, rows, root, t0, t1, end_at=end_at, held_prefixes=NOT_THE_TARGET, cfg=budget_cfg)
    bud = obs.budget(nm, obs.load_sidecars(root / "reports", night, int(budget_cfg.get(
        "window_nights", obs.BUDGET_DEFAULTS["window_nights"]))), budget_cfg)
    nm["budget"] = {k: v for k, v in bud.items() if isinstance(v, dict)}
    top, rest = obs.report_sections(nm, bud, root, t0, t1, budget_cfg)
    if bud["spent"]:
        lines += ["**Error budget spent: " + ", ".join(obs.NAMES[k] for k in bud["spent"]) + ".**", ""]
    lines += top

    lines += ["## Switches", ""]
    if not rows:
        lines += ["None.", ""]
    else:
        lines += [f"- Live: {len(live_rows)} ({len(ok)} ok, {len(failed) - len(held)} failed, "
                  f"{len(held)} held back/cancelled: not the source's fault)",
                  f"- Dry run: {len(dry_rows)}"]
        if took:
            lines.append(f"- Time to picture: median {statistics.median(took):.1f}s, worst {max(took):.1f}s")
        lines.append("- By kind: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common(5)))
        lines.append("")

    lines += ["## Failures by cause", ""]
    if not causes:
        lines += ["None.", ""]
    else:
        lines += ["| Cause | Count | Worst targets |", "|---|---|---|"]
        for cause, n in causes.most_common(5):
            tg = ", ".join(f"{_short(t)} ({c})" for t, c in cause_targets[cause].most_common(3))
            lines.append(f"| {cause} | {n} | {tg} |")
        lines.append("")

    lines += ["## Key moments", ""]
    if not moments:
        lines += ["No big plays tracked.", ""]
    else:
        lines.append(f"Seen live: {len(seen)} of {len(moments)}.")
        if missed:
            lines += ["", "Missed (most recent first):"]
            for m in sorted(missed, key=lambda m: -float(m.get("ts") or 0))[:5]:
                lines.append(f"- {_clock_et(float(m['ts']))} {m.get('matchup', '')}: {m.get('label', '')}")
        if seen:
            lines += ["", "Seen:"]
            for m in sorted(seen, key=lambda m: -float(m.get("ts") or 0))[:5]:
                lines.append(f"- {_clock_et(float(m['ts']))} {m.get('matchup', '')}: {m.get('label', '')}")
        lines.append("")

    lines += ["## Delays measured", ""]
    if not by_target:
        lines += ["None tonight.", ""]
    else:
        lines += ["| Channel | Samples | Median | How |", "|---|---|---|---|"]
        for target, xs in sorted(by_target.items(), key=lambda kv: -len(kv[1]))[:5]:
            how = ", ".join(f"{k} {n}" for k, n in Counter(k for _, k in xs).most_common())
            lines.append(f"| {_short(target)} | {len(xs)} | {statistics.median(s for s, _ in xs):.1f}s | {how} |")
        lines.append("")

    lines += ["## What it fixed on its own", ""]
    if not fixes:
        lines += ["Nothing needed fixing.", ""]
    else:
        for rule, n in fix_rules.most_common(5):
            last = next(x for x in reversed(fixes) if x.get("rule") == rule)
            bad = f", {fix_fail[rule]} didn't work" if fix_fail[rule] else ""
            lines.append(f"- {rule}: {n}{bad} (last: {last.get('action')}"
                         + (f" -> {last.get('detail')}" if last.get("detail") else "") + ")")
        lines.append("")

    try:
        from redzone.platform.autofix import night_lines
        autofix = night_lines(root, t0, t1)
    except Exception:
        autofix = []
    if autofix:
        lines += ["## Sent to Claude (auto-fix)", ""] + autofix + [""]

    needs = []
    if autofix:
        needs.append(f"{len(autofix)} problem(s) sent to Claude: review the draft PR(s) it opens (see 'Sent to Claude')")
    for x in observed:
        w = str(x.get("what", ""))
        if "approved on the TV" in w or "needs a look" in w or "GB free" in w:
            needs.append(w)
    if limited:
        needs.append(f"{len(limited)} fixes hit their rate limit (problem kept coming back): "
                     + ", ".join(sorted({str(x.get('rule')) for x in limited})[:5]))
    lines += rest
    ops = None
    from redzone.platform import night_ops
    o = night_ops.options(ops_cfg)
    if o["enabled"]:   # joins, fallbacks, drift, errors, Mac health (supervisor.night_ops, default OFF)
        try:
            ops = night_ops.summarize(root, t0, t1, margin=o["join_margin_s"])
            lines += night_ops.lines(ops)
            nm["ops"] = ops
            for code, text in ops["mac_last"].items():
                if code != "slept" or "live game" in text:
                    needs.append(f"Mac {code.replace('_', ' ')}: {text}")
        except Exception:
            log.exception("night ops section failed")
            ops = None
    if nm["left_mid_play"]:
        needs.insert(0, f"{nm['left_mid_play']} cut(s) left a play on screen: see 'Cuts' above")
    lines += ["## Needs you", ""]
    lines += ([f"- {w}" for w in list(dict.fromkeys(needs))[:5]] or ["Nothing."]) + [""]
    phone = obs.phone_body(nm, bud, len(fixes), fix_rules, causes, missed,
                           len(obs.watchdog_actions(root, t0, t1)))
    if autofix:
        phone += f"\nAuto-fix: {len(autofix)} problem(s) sent to Claude; a draft PR follows."
    if ops is not None:
        phone += "\n" + night_ops.phone_line(ops)
    return {"text": "\n".join(lines), "summary": summary, "phone": phone, "metrics": nm, "budget": bud}


def write_report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _cli_cfg() -> tuple[str, str, dict]:
    """(ntfy server, topic, budget settings) from data/config.yaml, the same place the service reads."""
    try:
        from redzone.config import Config
        cfg = Config()
        return (str(cfg.get("supervisor.ntfy_server", None) or DEFAULTS["ntfy_server"]),
                str(cfg.get("supervisor.ntfy_topic", "") or ""), cfg.get("supervisor.budget", {}) or {})
    except Exception as ex:
        print(f"(config not readable: {ex})")
        return DEFAULTS["ntfy_server"], "", {}


def main(argv: list[str] | None = None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    usage = ("usage: python -m redzone.supervisor report [YYYY-MM-DD] [--push]\n"
             "       python -m redzone.supervisor push-test")
    if not args or args[0] not in ("report", "push-test"):
        print(usage)
        return 2
    server, topic, budget_cfg = _cli_cfg()
    if args[0] == "push-test":
        if not topic:
            print("No supervisor.ntfy_topic in data/config.yaml. Pick a long random one, e.g.\n"
                  f"  supervisor:\n    ntfy_topic: redzone-{os.urandom(9).hex()}\n"
                  "then subscribe to it in the free ntfy app (ntfy.sh, no account).")
            return 1
        ok = asyncio.run(push_ntfy(server, topic, "RedZone test", "If you can read this, the nightly report "
                                   "will reach your phone."))
        print("sent" if ok else "push failed (see the log line above)")
        return 0 if ok else 1
    rest = [a for a in args[1:] if not a.startswith("--")]
    night = date.fromisoformat(rest[0]) if rest else night_of(time.time()) - timedelta(days=1)
    from redzone.store import Store
    rep = night_report(night, Store(), DATA, budget_cfg=budget_cfg)
    path = DATA / "reports" / f"{night.isoformat()}.md"
    write_report(path, rep["text"])
    write_metrics(path.with_suffix(".json"), rep["metrics"])
    print(rep["text"])
    print(f"\nSaved {path}")
    if "--push" in args:
        ok = asyncio.run(push_ntfy(server, topic, f"RedZone night of {night.isoformat()}", rep["phone"]))
        print("pushed" if ok else "not pushed (no or invalid supervisor.ntfy_topic, or the network refused)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
