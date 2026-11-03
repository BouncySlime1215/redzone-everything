"""Runtime invariants and the Health report (contracts/health.py), checked every supervisor tick.

The supervisor's rules FIX things; this module only SEES them, in the contract's shapes, so the
decision core, the phone, the nightly report and the watchdog model all read one list of faults.

Invariants (code: what must always hold):
  orphan_switch           the controller runs a switch the engine isn't waiting for (lost bookkeeping)
  lost_switch_callback    the engine waits for a switch the controller isn't running (done never fired)
  screen_unknown          what's on screen is unknown, and the controller has said so (input_unknown)
  screen_unknown_silent   ...unknown for 30 s+ and NOT flagged: nothing else will repair it
  stuck_lock              the controller's command lock held past control.max_switch_s
  stuck_flip              a flip (not cancellable by design) running for 60 s+
  stick_offline           a saved stick isn't connected (not a fault while away from home)
  stick_hung              a stick's circuit breaker is open (redzone/platform/breaker.py)
  stale_feed              live games whose score feed went dark (redzone/sources/feeds.py)

Each condition must hold for its grace period before it is a fault (`since` = first seen), so a
switch in its first seconds or a one-tick race never shows up.
"""
from __future__ import annotations

import resource
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from redzone.contracts.health import Fault, Fix, HealthReport, Severity

GRACE_S = {
    "orphan_switch": 10.0, "lost_switch_callback": 10.0, "screen_unknown": 0.0, "screen_unknown_silent": 30.0,
    "stuck_lock": 0.0, "stuck_flip": 60.0, "stick_offline": 30.0, "stick_hung": 0.0, "stale_feed": 0.0,
}
SEVERITY = {
    "orphan_switch": Severity.ERROR, "lost_switch_callback": Severity.ERROR, "screen_unknown": Severity.WARN,
    "screen_unknown_silent": Severity.ERROR, "stuck_lock": Severity.CRITICAL, "stuck_flip": Severity.CRITICAL,
    "stick_offline": Severity.WARN, "stick_hung": Severity.WARN, "stale_feed": Severity.WARN,
}


def _alive(task: Any) -> bool:
    return task is not None and not task.done()


class PlatformHealth:
    """Health.report(now) for the running engine. `fixes` is the supervisor's fix log, if any."""

    def __init__(self, engine: Any, fixes: Callable[[], list[Fix]] | None = None,
                 data_dir: Path | None = None, mono: Callable[[], float] = time.monotonic):
        self.e = engine
        self.fixes = fixes or (lambda: [])
        self.data_dir = data_dir
        self.mono = mono                      # durations: wall-clock jumps can't fake a stuck lock
        self._since: dict[tuple[str, str], tuple[float, float]] = {}   # (code, component) -> (wall, mono)
        self._lock_mono: float | None = None

    # --- conditions ---------------------------------------------------------------------------

    def conditions(self) -> list[tuple[str, str, str]]:
        """(code, component, detail) for every invariant broken right now (before grace periods)."""
        e = self.e
        ctl = getattr(e, "control", None)
        out: list[tuple[str, str, str]] = []
        pending = getattr(e, "_pending_decision", None)
        if ctl is not None:
            running = _alive(getattr(ctl, "_task", None))
            flipping = bool(getattr(ctl, "_flipping", False))
            if running and pending is None and not flipping:
                out.append(("orphan_switch", "control", "controller runs a switch nobody is waiting for"))
            if pending is not None and not running and not flipping:
                out.append(("lost_switch_callback", "engine",
                            f"waiting on a switch to {getattr(pending, 'target', '?')} the controller isn't running"))
            if getattr(ctl, "input_unknown", False):
                out.append(("screen_unknown", "tv", "TV input unknown (flagged; recovery is running)"))
            elif self._screen_silent(ctl, pending):
                out.append(("screen_unknown_silent", "tv",
                            "nothing confirmed on screen and no recovery flagged"))
            lock = getattr(ctl, "_lock", None)
            limit = float(e.cfg.get("control.max_switch_s", 150)) if getattr(e, "cfg", None) else 150.0
            if lock is not None and lock.locked():
                if self._lock_mono is None:
                    self._lock_mono = self.mono()
                held = self.mono() - self._lock_mono
                if held > limit:
                    out.append(("stuck_lock", "control", f"command lock held {held:.0f}s (limit {limit:.0f}s)"))
            else:
                self._lock_mono = None
            if flipping:
                out.append(("stuck_flip", "control", "a flip is still running"))
            away = self._away()
            for s in getattr(ctl, "sticks", []) or []:
                if not away and getattr(s, "state", "connected") not in ("connected", "unknown"):
                    out.append(("stick_offline", f"stick:{s.host}",
                                f"{s.state}: {getattr(s, 'connect_error', '') or 'no adb connection'}"[:200]))
                br = getattr(s, "breaker", None)
                if br is not None and br.state != "closed":
                    out.append(("stick_hung", f"stick:{s.host}",
                                f"{br.hangs} adb commands hung; {br.state}, retry in {br.retry_in():.0f}s"))
        feeds = getattr(e, "feeds", None)
        merger = getattr(e, "merger", None)
        if feeds is not None and merger is not None:
            from redzone.models import Phase
            try:
                dark = [gid for gid, g in merger.games.items() if g.phase == Phase.LIVE and feeds.stale(gid)]
            except Exception:
                dark = []
            if dark:
                out.append(("stale_feed", "feed:espn", f"{len(dark)} live game(s) with no fresh data: "
                            + ", ".join(sorted(dark)[:5])))
        return out

    def _away(self) -> bool:
        try:
            return bool(getattr(getattr(self.e, "network", None), "away", False))
        except Exception:
            return False

    def _screen_silent(self, ctl: Any, pending: Any) -> bool:
        if pending is not None or not getattr(ctl, "live_control", False):
            return False
        if getattr(ctl, "on_target", None) is not None:
            return False
        dec = getattr(self.e, "decider", None)
        if dec is None or getattr(dec, "current", None) is None:
            return False                          # nothing chosen yet: nothing should be on screen
        if getattr(dec, "replaying", None) or getattr(dec, "after_replay", False):
            return False                          # a replay is on: on_target is None by design
        return True

    # --- report -------------------------------------------------------------------------------

    def faults(self, now: float) -> list[Fault]:
        current = self.conditions()
        keys = {(c, comp) for c, comp, _ in current}
        for k in list(self._since):
            if k not in keys:
                del self._since[k]
        mono = self.mono()
        out = []
        for code, comp, detail in current:
            wall, m0 = self._since.setdefault((code, comp), (now, mono))
            if mono - m0 < GRACE_S.get(code, 0.0):
                continue
            out.append(Fault(code, SEVERITY.get(code, Severity.WARN), comp, detail, since=wall))
        return out

    def report(self, now: float) -> HealthReport:
        e = self.e
        dec = getattr(e, "decider", None)
        ctl = getattr(e, "control", None)
        away = self._away()
        return HealthReport(
            at=now, faults=tuple(self.faults(now)), fixes=tuple(self.fixes()),
            safe_mode=bool(getattr(dec, "safe_mode", False)),
            two_stick=bool(getattr(ctl, "two_stick", False)) if ctl is not None else False,
            away_from_home=away, ram_mb=peak_rss_mb(), disk_free_mb=disk_free_mb(self.data_dir))


def peak_rss_mb() -> float | None:
    try:
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    return round(r / (1 << 20) if sys.platform == "darwin" else r / 1024, 1)   # bytes on macOS, KB on Linux


def disk_free_mb(path: Path | None) -> float | None:
    try:
        return round(shutil.disk_usage(str(path or Path.cwd())).free / (1 << 20), 1)
    except OSError:
        return None
