"""Mac health alerts: tell Nick's phone when the Mac itself is about to cost him the night.

Nick's order (Nov 7, item 6): alert on battery, sleep, swap, paused during games, and no switches for
20 min. Every one of these happened on Nov 4-6 and nobody was told until the morning:
  - battery: on battery during games Nov 4 evening, down to 19% at 11:00 PM and 13% at 12:44 AM ET;
    19% again Nov 5 at 7:46 PM ET (the RC6.3 deploy was skipped that night for the same reason).
  - swap: 5.8-10.0 GB swapped out on the 8 GB Mac on every eco step logged Nov 3-7 (10.0 GB Nov 5 8:13 PM ET).
  - no switches: Nov 5 10:12 PM -> 12:37 AM ET (145 min), Nov 6 7:20 -> 8:26 PM (66 min), 8:26 -> 9:35 PM
    (69 min) and 10:19 -> 11:41 PM (82 min) ET, with up to 10 live games, while the supervisor log showed
    stuck command locks (151-158 s) and stale feeds. No switch row at all in those windows.
  - sleep: macOS sleeps on battery with the lid closed even while caffeinate runs (-s holds only on AC).

This module only SEES and SAYS. It never presses a key, never changes a Mac setting, never restarts
anything. The supervisor calls `MacHealth.check(Snapshot, now)` every tick (supervisor.mac_health.enabled,
default OFF) and sends what it returns to the phone (toast on the app; ntfy push for "high" and "urgent").

Alerts (code: when -> level):
  battery_low      on battery at <= battery_low_pct (any time: the Mac may die before the game) -> high;
                   <= battery_critical_pct -> urgent
  on_battery       on battery with live games for battery_hold_s -> warn
  slept            the Mac was away (asleep or stalled) >= sleep_gap_s between two ticks -> high with live
                   games, else info (logged and in the night report, not pushed)
  keep_awake       live games but the app's keep-awake (caffeinate) isn't running for keep_awake_s -> warn
  memory           memory pressure critical for mem_hold_s, or swap >= swap_alert_mb for swap_hold_s with
                   live games -> warn (high when pressure is critical with live games)
  paused           auto-switching paused for paused_alert_s with live games -> warn
  no_switches      >= no_switch_min_live live games, autopilot on (not paused, no lock, no hold), and no
                   switch row (ok, failed or dry run) for no_switch_s -> high
Each alert fires once when its condition has held long enough, again at once if it gets worse (high ->
urgent), again every repeat_s while it still holds, and says "cleared" (toast only) when it ends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": False,             # OFF until Nick turns it on (Nov 7 order: all flags off)
    "battery_low_pct": 20,
    "battery_critical_pct": 10,
    "battery_hold_s": 120.0,
    "sleep_gap_s": 60.0,
    "keep_awake_s": 120.0,
    "mem_hold_s": 60.0,
    "swap_alert_mb": 8000.0,      # 8 GB Mac: 3-6 GB swapped is its normal evening; 8+ is where it fell over
    "swap_hold_s": 300.0,
    "paused_alert_s": 600.0,
    "no_switch_s": 1200.0,        # Nick: "no switches for 20 min"
    "no_switch_min_live": 2,
    "repeat_s": 1800.0,
    "push": True,                 # also push high/urgent alerts through ntfy when supervisor.ntfy_topic is set
}
LEVELS = ("info", "warn", "high", "urgent")
PUSH_LEVELS = ("high", "urgent")


def options(over: Any = None) -> dict:
    """Defaults + overrides of the right type (a bad value keeps the default)."""
    out = dict(DEFAULTS)
    if isinstance(over, dict):
        for k, v in over.items():
            d = DEFAULTS.get(k)
            if d is None:
                continue
            if isinstance(d, bool):
                if isinstance(v, bool):
                    out[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                out[k] = v
    out["battery_critical_pct"] = min(out["battery_critical_pct"], out["battery_low_pct"])
    return out


@dataclass
class Snapshot:
    """What the supervisor sees this tick. None = unknown (never a reason to alert)."""
    live_games: int = 0
    on_battery: bool | None = None
    battery_pct: int | None = None
    mem_pressure: int | None = None       # 1 normal, 2 warn, 4 critical (kern.memorystatus_vm_pressure_level)
    swap_used_mb: float | None = None
    slept_s: float | None = None          # seconds away since the last tick (WakeWatch), None = no gap
    keep_awake: bool | None = None        # the app's caffeinate is running
    paused: bool = False
    held: bool = False                    # a lock / manual hold / focus: staying put is the user's choice
    last_switch_ts: float | None = None   # newest switch row of any kind (ok, failed, dry run)


@dataclass
class Alert:
    code: str
    level: str
    text: str
    at: float
    cleared: bool = False

    def row(self) -> dict:
        return {"kind": "mac_health", "code": self.code, "level": self.level, "text": self.text,
                "cleared": self.cleared}


def _clock_et(ts: float) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("America/New_York")).strftime("%-I:%M %p")


@dataclass
class MacHealth:
    opts: dict = field(default_factory=lambda: dict(DEFAULTS))
    since: dict[str, float] = field(default_factory=dict)       # condition -> first seen
    fired: dict[str, float] = field(default_factory=dict)       # alert -> last fired at
    fired_level: dict[str, str] = field(default_factory=dict)   # alert -> level it last fired at
    started: float | None = None                                # first check (no "no switches" before start + window)
    _quiet_from: float | None = None

    def _held(self, key: str, cond: bool, now: float) -> float:
        if not cond:
            self.since.pop(key, None)
            return -1.0
        return now - self.since.setdefault(key, now)

    def conditions(self, s: Snapshot, now: float) -> dict[str, tuple[str, str]]:
        """code -> (level, text) for every alert whose condition has held long enough right now."""
        o = self.opts
        if self.started is None:
            self.started = now
        out: dict[str, tuple[str, str]] = {}
        live = int(s.live_games or 0)
        games = f"{live} live game{'s' if live != 1 else ''}"

        low = bool(s.on_battery) and s.battery_pct is not None and s.battery_pct <= o["battery_low_pct"]
        if self._held("battery_low", low, now) >= 0:
            crit = s.battery_pct <= o["battery_critical_pct"]
            out["battery_low"] = ("urgent" if crit else "high",
                                  f"Mac battery at {s.battery_pct}% and not charging: plug it in"
                                  + (f" ({games})" if live else ""))
        on_batt = bool(s.on_battery) and live > 0 and not low
        if self._held("on_battery", on_batt, now) >= o["battery_hold_s"]:
            pct = f" ({s.battery_pct}%)" if s.battery_pct is not None else ""
            out["on_battery"] = ("warn", f"Mac is on battery{pct} with {games}: plug it in, it can sleep "
                                         "with the lid closed")

        if s.slept_s is not None and s.slept_s >= o["sleep_gap_s"]:
            mins = s.slept_s / 60
            out["slept"] = ("high" if live else "info",
                            f"Mac was asleep or frozen for {mins:.0f} min" + (f" with {games}" if live else "")
                            + ": switching stopped meanwhile")

        no_awake = live > 0 and s.keep_awake is False
        if self._held("keep_awake", no_awake, now) >= o["keep_awake_s"]:
            out["keep_awake"] = ("warn", f"{games} but the Mac's keep-awake isn't running: it may sleep")

        crit_mem = s.mem_pressure is not None and s.mem_pressure >= 4
        swap = live > 0 and s.swap_used_mb is not None and s.swap_used_mb >= o["swap_alert_mb"]
        h_mem, h_swap = self._held("mem_critical", crit_mem, now), self._held("swap", swap, now)
        if h_mem >= o["mem_hold_s"] or h_swap >= o["swap_hold_s"]:
            what = []
            if h_mem >= o["mem_hold_s"]:
                what.append("memory pressure is critical")
            if s.swap_used_mb is not None and s.swap_used_mb >= o["swap_alert_mb"]:
                what.append(f"{s.swap_used_mb / 1000:.1f} GB swapped out")
            out["memory"] = ("high" if (h_mem >= o["mem_hold_s"] and live) else "warn",
                             f"Mac is short of memory ({'; '.join(what)}): close other apps")

        paused = bool(s.paused) and live > 0
        hp = self._held("paused", paused, now)
        if hp >= o["paused_alert_s"]:
            out["paused"] = ("warn", f"Auto-switching has been paused {hp / 60:.0f} min with {games}")

        watching = live >= int(o["no_switch_min_live"]) and not s.paused and not s.held
        if watching:
            if self._quiet_from is None:
                self._quiet_from = now
            last = max(s.last_switch_ts or 0.0, self._quiet_from)
            quiet = now - last
            if quiet >= o["no_switch_s"]:
                when = (f" (last at {_clock_et(s.last_switch_ts)})" if s.last_switch_ts
                        and s.last_switch_ts <= now - o["no_switch_s"] else "")
                out["no_switches"] = ("high", f"No switch in {quiet / 60:.0f} min with {games}{when}: "
                                              "switching may be stuck")
        else:
            self._quiet_from = None
        return out

    def check(self, s: Snapshot, now: float) -> list[Alert]:
        """Alerts to send now: new ones, repeats every repeat_s, and 'cleared' for ones that ended."""
        cur = self.conditions(s, now)
        out: list[Alert] = []
        for code, (level, text) in cur.items():
            last = self.fired.get(code)
            worse = LEVELS.index(level) > LEVELS.index(self.fired_level.get(code, level))
            if last is None or worse or now - last >= float(self.opts["repeat_s"]):
                self.fired[code], self.fired_level[code] = now, level
                out.append(Alert(code, level, text, now))
        for code in [c for c in self.fired if c not in cur]:
            del self.fired[code]
            self.fired_level.pop(code, None)
            if code != "slept":       # a sleep is an event, not a state: nothing to clear
                out.append(Alert(code, "info", f"Cleared: {code.replace('_', ' ')}", now, cleared=True))
        return out

    def active(self) -> list[str]:
        return sorted(c for c in self.fired if c != "slept")
