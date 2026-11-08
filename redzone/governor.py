"""Resource governor: keep the Mac cool, quiet and with free disk while RedZone runs.

Why: the app shares an 8 GB MacBook Air that also goes to school, so extra work must keep it cool, quiet and with free disk (docstring; CLAUDE.md).

The Mac is an 8 GB MacBook Air with ~5 GB of free disk that also goes to school. RedZone shares
it with everything else, so the extra work (catch-up reel downloads and trims, the lab, the
archive, score-bug OCR, the local model) backs off when the Mac is busy, hot, on battery, short
of memory or short of disk. Live switching is never slowed: decisions, tunes and hot polling
run the same at every level.

Levels (computed from one Sample every `every_s` seconds):
  normal    everything runs
  eco       on battery, Low Power Mode, busy CPU, memory pressure "warn", swap GROWING while memory
            is tight, thermal warning, or disk under disk_low_mb:  no reel downloads, no lab, no
            archive; idle polling at half rate; the local model still answers new errors. Score-bug
            OCR keeps its full rate in eco: it is the live delay/sync measurement (P-07).
  critical  battery low, memory pressure "critical", swap nearly full, throttled CPU, disk under
            disk_critical_mb: as eco, plus no score-bug OCR and no local model (rules only); idle
            polling at 1/4. Only critical may touch OCR.
A swap TOTAL is not a signal (some swap use is normal on an 8 GB Mac, P-07): growth while pressure
is at least "warn", and a share of the total for critical, are.
A level goes up at once and comes down only after `calm_s` seconds below it (no flapping).

Memory caps (macOS has no enforced RSS limit for launchd user agents, so they are enforced here):
  - a model process calls apply_mlx_limits() before loading: MLX's own memory and cache limits
  - the watchdog (agent/watchdog.py) restarts the app when its resident memory stays over
    service_hard_mb (never during crunch time), and drops its own model when idle or when the
    model processes together go over models_mb

Stdlib only (plus redzone.fsm / redzone.machines, which are stdlib too): the watchdog's separate venv
imports it. The level is the explicit machine governor.level (redzone/machines/platform.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from redzone.machines.platform import GOVERNOR_LEVEL

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
LEVELS = ("normal", "eco", "critical")

DEFAULTS: dict = {
    "enabled": True,
    "every_s": 30,             # one look at the Mac this often
    "calm_s": 180,             # a level must be clear this long before stepping down
    "busy_load": 0.85,         # 5-min load average per CPU core above this: the Mac is busy
    "battery_eco": True,       # running on battery: eco
    "battery_low_pct": 20,     # on battery and under this: critical
    "swap_growth_mb": 300,     # swap grew by more than this in swap_window_s while memory pressure >= warn: eco
    "swap_window_s": 600,      # ...measured over this long
    "swap_critical_pct": 90,   # swap this much of its total (vm.swapusage): critical
    "disk_low_mb": 3000,       # free disk under this: eco (no new downloads)
    "disk_critical_mb": 1500,  # free disk under this: critical
    "service_soft_mb": 700,    # the app's resident memory: warn above this
    "service_hard_mb": 1200,   # ...the watchdog restarts the app above this (never in crunch time)
    "models_mb": 1500,         # all local model processes together (watchdog + game reasoner)
}

# What each kind of optional work may do at each level. Anything not listed is always allowed.
ALLOWED: dict[str, tuple[str, ...]] = {
    "reel": ("normal",),          # clip downloads + ffmpeg trims
    "lab": ("normal",),           # weekly auto-tune search (minutes of CPU)
    "archive": ("normal",),       # nightly game archive download
    "ocr": ("normal", "eco"),     # score-bug screenshots + Vision OCR
    "llm": ("normal", "eco"),     # the local model (watchdog / game reasoner)
}
# Interval multipliers for loops that may slow down.
SCALE: dict[str, dict[str, float]] = {
    "ocr": {"normal": 1.0, "eco": 1.0, "critical": 5.0},   # eco never slows the score bug (P-07)
    "idle_poll": {"normal": 1.0, "eco": 2.0, "critical": 4.0},
}

# Processes the governor measures (pattern in the command line -> name).
PROCESSES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"scripts/service_runtime\.py|-m redzone(\s|$)"), "service"),
    (re.compile(r"agent[./]llm_server"), "llm_server"),
    # the plist runs it as `python -u -c "...runpy.run_module('agent.watchdog'..."` (P-08)
    (re.compile(r"agent\.watchdog|agent/watchdog\.py"), "watchdog"),
)
MODEL_PROCESSES = ("llm_server", "watchdog")


@dataclass
class Sample:
    """One look at the Mac. None = couldn't tell (not a Mac, command missing): never a reason."""
    at: float = 0.0
    on_battery: bool | None = None
    battery_pct: int | None = None
    low_power: bool | None = None
    thermal_level: int | None = None      # pmset thermal/performance warning level, 0 = none
    cpu_speed_limit: int | None = None    # percent (Intel Macs report it; 100 = not throttled)
    mem_pressure: int | None = None       # kern.memorystatus_vm_pressure_level: 1 normal, 2 warn, 4 critical
    swap_used_mb: float | None = None
    swap_total_mb: float | None = None
    swap_growth_mb: float | None = None   # swap_used now minus its lowest in the last swap_window_s (Governor fills it)
    load_per_core: float | None = None    # 5-minute load average / CPU count
    disk_free_mb: float | None = None
    procs: dict[str, float] = field(default_factory=dict)   # name -> resident MB (summed per name)
    footprint: dict[str, float] = field(default_factory=dict)   # name -> phys_footprint MB (`footprint -p`; macOS only)


def options(over: dict | None = None) -> dict:
    """Defaults + overrides. A value of the wrong type is ignored (the watchdog reads the saved file
    without the app's validation), and the thresholds are kept in order."""
    out = dict(DEFAULTS)
    for k, v in (over or {}).items() if isinstance(over, dict) else ():
        d = DEFAULTS.get(k)
        if d is None:
            continue
        if isinstance(d, bool):
            if isinstance(v, bool):
                out[k] = v
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    out["disk_critical_mb"] = min(out["disk_critical_mb"], out["disk_low_mb"])
    out["service_soft_mb"] = min(out["service_soft_mb"], out["service_hard_mb"])
    return out


def load_options(root: Path = ROOT) -> dict:
    """The governor section of the saved config (defaults + data/config.yaml), for processes that
    don't hold a Config (the watchdog). Never raises: a missing yaml module means defaults."""
    try:
        import yaml
    except ImportError:
        return options()
    over: dict = {}
    for path in (root / "redzone" / "default_config.yaml", root / "data" / "config.yaml"):
        try:
            d = yaml.safe_load(path.read_text()) or {}
            g = d.get("governor") if isinstance(d, dict) else None
            if isinstance(g, dict):
                over.update(g)
        except Exception:   # unreadable or damaged: the defaults still keep the Mac safe
            continue
    return options(over)


# ---- measuring ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 5.0) -> str | None:
    if not shutil.which(cmd[0]) and not os.path.exists(cmd[0]):
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def parse_batt(text: str | None) -> tuple[bool | None, int | None]:
    """`pmset -g batt`: ("Now drawing from 'Battery Power'", " 85%; discharging")."""
    if not text:
        return None, None
    on_batt = None
    m = re.search(r"drawing from '([^']+)'", text)
    if m:
        on_batt = "battery" in m.group(1).lower()
    p = re.search(r"(\d{1,3})%", text)
    return on_batt, (int(p.group(1)) if p else None)


def parse_lowpower(text: str | None) -> bool | None:
    """`pmset -g`: a ' lowpowermode 1' line (absent on Macs/OS versions without it)."""
    if not text:
        return None
    m = re.search(r"^\s*lowpowermode\s+(\d+)", text, re.M)
    return (m.group(1) != "0") if m else None


def parse_therm(text: str | None) -> tuple[int | None, int | None]:
    """`pmset -g therm`: warning levels (Apple Silicon says 'No thermal warning level has been
    recorded' when cool) and, on Intel, CPU_Speed_Limit."""
    if not text:
        return None, None
    level = 0
    for m in re.finditer(r"(?:thermal|performance) warning level[^\d\n]*(\d+)", text, re.I):
        level = max(level, int(m.group(1)))
    s = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", text)
    return level, (int(s.group(1)) if s else None)


def parse_swap(text: str | None) -> float | None:
    """`sysctl -n vm.swapusage`: 'total = 2048.00M  used = 1023.25M  free = ...'."""
    m = re.search(r"used\s*=\s*([\d.]+)([MG])", text or "")
    if not m:
        return None
    return float(m.group(1)) * (1024 if m.group(2) == "G" else 1)


def parse_swap_total(text: str | None) -> float | None:
    """`sysctl -n vm.swapusage`: the 'total = ...' part."""
    m = re.search(r"total\s*=\s*([\d.]+)([MG])", text or "")
    if not m:
        return None
    return float(m.group(1)) * (1024 if m.group(2) == "G" else 1)


def parse_footprint(text: str | None) -> float | None:
    """`footprint -p <pid>`: 'name [pid]: 64-bit    Footprint: 357 MB (16384 bytes per page)' -> MB.
    macOS counts a process by this (compressed + swapped pages included), not by RSS (research rec 1)."""
    m = re.search(r"Footprint:\s*([\d.]+)\s*([KMG]B)", text or "")
    if not m:
        return None
    return float(m.group(1)) * {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}[m.group(2)]


def parse_ps_pids(text: str | None) -> dict[str, list[int]]:
    """Same lines and patterns as parse_ps: {name: [pid, ...]}."""
    out: dict[str, list[int]] = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit() or "python" not in parts[2].lower():
            continue
        for rx, name in PROCESSES:
            if rx.search(parts[2]):
                out.setdefault(name, []).append(int(parts[0]))
                break
    return out


def parse_ps(text: str | None) -> dict[str, float]:
    """`ps -axo pid=,rss=,command=` -> {name: MB}; rss is in KiB. The governor's own `ps` and any
    shell wrapping a match are skipped by requiring a python interpreter on the line."""
    out: dict[str, float] = {}
    for line in (text or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        cmd = parts[2]
        if "python" not in cmd.lower():
            continue
        for rx, name in PROCESSES:
            if rx.search(cmd):
                out[name] = round(out.get(name, 0.0) + int(parts[1]) / 1024, 1)
                break
    return out


def sample(root: Path = ROOT, run: Callable[[list[str]], str | None] = _run,
           clock: Callable[[], float] = time.time) -> Sample:
    """One look at the Mac: a few cheap commands (~20 ms together). Blocking: call it from a thread."""
    s = Sample(at=clock())
    s.on_battery, s.battery_pct = parse_batt(run(["pmset", "-g", "batt"]))
    s.low_power = parse_lowpower(run(["pmset", "-g"]))
    s.thermal_level, s.cpu_speed_limit = parse_therm(run(["pmset", "-g", "therm"]))
    p = (run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]) or "").strip()
    s.mem_pressure = int(p) if p.isdigit() else None
    swap = run(["sysctl", "-n", "vm.swapusage"])
    s.swap_used_mb, s.swap_total_mb = parse_swap(swap), parse_swap_total(swap)
    try:
        s.load_per_core = round(os.getloadavg()[1] / (os.cpu_count() or 1), 2)
    except (OSError, AttributeError):
        pass
    try:
        s.disk_free_mb = round(shutil.disk_usage(root).free / 1e6)
    except OSError:
        pass
    ps = run(["ps", "-axo", "pid=,rss=,command="])
    s.procs = parse_ps(ps)
    if sys.platform == "darwin":   # the app's own footprint (one `footprint` call per matching pid)
        for name, pids in parse_ps_pids(ps).items():
            got = [parse_footprint(run(["footprint", "-p", str(pid)], 10.0)) for pid in pids]
            if any(g is not None for g in got):
                s.footprint[name] = round(sum(g for g in got if g is not None), 1)
    return s


# ---- judging ------------------------------------------------------------------------------

def assess(s: Sample, opts: dict) -> tuple[str, list[str]]:
    """The level this sample calls for, and the reasons (plain words, for the phone)."""
    crit: list[str] = []
    eco: list[str] = []
    if s.disk_free_mb is not None:
        if s.disk_free_mb < opts["disk_critical_mb"]:
            crit.append(f"only {s.disk_free_mb / 1000:.1f} GB free on disk")
        elif s.disk_free_mb < opts["disk_low_mb"]:
            eco.append(f"only {s.disk_free_mb / 1000:.1f} GB free on disk")
    if s.mem_pressure is not None:
        if s.mem_pressure >= 4:
            crit.append("memory pressure is critical")
        elif s.mem_pressure >= 2:
            eco.append("memory is tight")
    # Swap SIZE says little (5.9 of 7 GB was this Mac's normal state, P-07). Growth while memory is
    # tight is the signal; a nearly full swap is the only absolute line, and it is critical.
    if s.swap_used_mb is not None and s.swap_total_mb:
        if s.swap_used_mb / s.swap_total_mb * 100 >= opts["swap_critical_pct"]:
            crit.append(f"swap is {s.swap_used_mb / s.swap_total_mb * 100:.0f}% full")
    if (s.swap_growth_mb is not None and s.swap_growth_mb > opts["swap_growth_mb"]
            and s.mem_pressure is not None and s.mem_pressure >= 2):
        eco.append(f"swap grew {s.swap_growth_mb:.0f} MB in {int(opts['swap_window_s'] / 60)} min while memory is tight")
    if s.cpu_speed_limit is not None and s.cpu_speed_limit < 70:
        crit.append(f"the CPU is throttled to {s.cpu_speed_limit}%")
    if s.thermal_level:
        (crit if s.thermal_level >= 2 else eco).append("the Mac is running hot")
    if s.on_battery:
        if s.battery_pct is not None and s.battery_pct < opts["battery_low_pct"]:
            crit.append(f"battery at {s.battery_pct}%")
        elif opts["battery_eco"]:
            eco.append("on battery")
    if s.low_power:
        eco.append("Low Power Mode is on")
    if s.load_per_core is not None and s.load_per_core > opts["busy_load"]:
        eco.append("the Mac is busy")
    if crit:
        return "critical", crit + eco
    if eco:
        return "eco", eco
    return "normal", []


class Governor:
    """Holds the current level. `allow(kind)` and `scale(kind)` are cheap and safe to call from any
    loop; `run()` refreshes the level in the background. A broken probe means "normal" — the
    governor can slow optional work down, never break the app."""

    def __init__(self, opts: dict | None = None, *, root: Path = ROOT,
                 probe: Callable[[], Sample] | None = None, clock: Callable[[], float] = time.time):
        self.opts = options(opts)
        self.root = root
        self._probe = probe or (lambda: sample(root, clock=clock))
        self._clock = clock
        self.m = GOVERNOR_LEVEL.new(clock=clock)   # normal | eco | critical: the one place the level lives
        self.reasons: list[str] = []
        self.last: Sample | None = None
        self._calm_from: float | None = None   # when the raw level first dropped below self.level
        self._checked = -1e18
        self.changes: list[tuple[float, str, str]] = []   # (when, level, why), last 20
        self._source: Callable[[], dict] | None = None
        self._swap_hist: list[tuple[float, float]] = []   # (when, swap used MB) inside swap_window_s

    @classmethod
    def from_config(cls, cfg, **kw) -> "Governor":
        def read() -> dict:
            try:
                over = cfg.get("governor", {}) or {}
            except Exception:
                over = {}
            return over if isinstance(over, dict) else {}
        g = cls(read(), **kw)
        g._source = read   # settings changed on the phone apply at the next look
        return g

    @property
    def level(self) -> str:
        return self.m.state

    @property
    def since(self) -> float:
        return self.m.since

    # -- what callers ask --
    def allow(self, kind: str) -> bool:
        if not self.opts["enabled"]:
            return True
        ok = ALLOWED.get(kind)
        return ok is None or self.level in ok

    def scale(self, kind: str) -> float:
        if not self.opts["enabled"]:
            return 1.0
        return SCALE.get(kind, {}).get(self.level, 1.0)

    def why(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "the Mac has room to spare"

    # -- updating --
    def update(self, s: Sample) -> str:
        """Fold one sample in (with hysteresis) and return the level."""
        self.last = s
        self._note_swap(s)
        if not self.opts["enabled"]:
            self.m.to("normal", "governor disabled")
            self.reasons = []
            return self.level
        raw, reasons = assess(s, self.opts)
        now = self._clock()
        if LEVELS.index(raw) >= LEVELS.index(self.level):
            self._calm_from = None
            if raw != self.level:
                self._set(raw, reasons, now)
            else:
                self.reasons = reasons
        else:
            if self._calm_from is None:
                self._calm_from = now
            if now - self._calm_from >= float(self.opts["calm_s"]):
                self._calm_from = None
                self._set(raw, reasons, now)
        return self.level

    def _note_swap(self, s: Sample) -> None:
        """Swap growth over swap_window_s: now minus the lowest value seen inside the window."""
        if s.swap_used_mb is None:
            return
        now = self._clock()
        window = float(self.opts["swap_window_s"])
        self._swap_hist = [(t, mb) for t, mb in self._swap_hist if now - t <= window]
        self._swap_hist.append((now, s.swap_used_mb))
        s.swap_growth_mb = round(s.swap_used_mb - min(mb for _, mb in self._swap_hist), 1)

    def _set(self, level: str, reasons: list[str], now: float) -> None:
        old = self.level
        self.reasons = reasons
        self.m.to(level, self.why(), now)
        self.changes = (self.changes + [(now, level, self.why())])[-20:]
        log.info("governor: %s -> %s (%s)", old, level, self.why())

    def refresh(self, force: bool = False) -> str:
        """Re-measure if `every_s` has passed (blocking; ~20 ms of subprocesses)."""
        now = self._clock()
        if not force and now - self._checked < float(self.opts["every_s"]):
            return self.level
        self._checked = now
        if self._source is not None:
            try:
                self.opts = options(self._source())
            except Exception:
                log.exception("governor: couldn't re-read settings")
        try:
            s = self._probe()
        except Exception:
            log.exception("governor: probe failed")
            return self.level
        return self.update(s)

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.refresh, True)
            except Exception:
                log.exception("governor: refresh failed")
            await asyncio.sleep(max(5.0, float(self.opts["every_s"])))

    def service_mb(self) -> float | None:
        """The app's memory: the larger of its resident size and its footprint (RSS under-reads once
        the compressor and swap hold its pages, P-08)."""
        if not self.last:
            return None
        rss, fp = self.last.procs.get("service"), self.last.footprint.get("service")
        got = [v for v in (rss, fp) if v is not None]
        return max(got) if got else None

    def models_mb(self) -> float:
        return sum((self.last.procs.get(n, 0.0) for n in MODEL_PROCESSES), 0.0) if self.last else 0.0

    def view(self) -> dict:
        s = self.last
        return {"level": self.level, "why": self.why(), "since": self.since, "machine": self.m.view(history=0),
                "paused": sorted(k for k in ALLOWED if not self.allow(k)),
                "sample": asdict(s) if s else None,
                "service_mb": self.service_mb(), "service_rss_mb": s.procs.get("service") if s else None,
                "service_footprint_mb": s.footprint.get("service") if s else None, "service_soft_mb": self.opts["service_soft_mb"],
                "models_mb": round(self.models_mb(), 1), "models_cap_mb": self.opts["models_mb"]}


# ---- keep the Mac awake for games that are about to start (P-10) --------------------------

def keep_awake_wanted(games, now: float, soon_s: float = 900.0, planned: bool = False) -> bool:
    """Should caffeinate run? While any game is live, inside its `soon_s` pre-game window, or planned for
    tonight (a plan_*.json exists). The idle poll can sleep for up to an hour: a Mac that slept before
    kickoff never notices the game is live (P-10). `planned` is the caller's "a planned game starts within
    the next hour" (a plan_*.json), so it does not hold the Mac awake all day. `games` need .phase (a str or enum with .value) and
    .start_time."""
    for g in games:
        phase = getattr(getattr(g, "phase", ""), "value", getattr(g, "phase", ""))
        if str(phase).lower() == "live":
            return True
        t = getattr(g, "start_time", None)
        if str(phase).lower() == "pre" and t and 0 < t - now < soon_s:
            return True
    return bool(planned)


# ---- helpers for heavy work ---------------------------------------------------------------

TASKPOLICY = "/usr/sbin/taskpolicy"


def background_cmd(cmd: list[str]) -> list[str]:
    """Run a heavy child process at background QoS: efficiency cores, throttled disk I/O
    (macOS `taskpolicy -b`); `nice -n 10` elsewhere. Never for anything a switch waits on."""
    if sys.platform == "darwin" and os.path.exists(TASKPOLICY):
        return [TASKPOLICY, "-b", *cmd]
    if shutil.which("nice"):
        return ["nice", "-n", "10", *cmd]
    return list(cmd)


def apply_mlx_limits(model_mb: float, cache_mb: float = 64.0) -> bool:
    """Cap MLX in this process before a model loads: a memory limit (MLX frees its cache and waits
    rather than growing past it) and a small buffer cache (MLX otherwise keeps freed GPU buffers
    around, which is most of an idle model's extra memory). True when the limits took."""
    try:
        import mlx.core as mx
    except ImportError:
        return False
    limit, cache = int(model_mb * 1024 * 1024), int(cache_mb * 1024 * 1024)
    done = False
    for mod in (mx, getattr(mx, "metal", None)):   # newer MLX: mx.set_*; older: mx.metal.set_*
        if mod is None or done:
            continue
        try:
            mod.set_memory_limit(limit)
            mod.set_cache_limit(cache)
            done = True
        except (AttributeError, TypeError, ValueError):
            continue
    return done


def free_mlx_cache() -> None:
    try:
        import mlx.core as mx
    except ImportError:
        return
    for mod in (mx, getattr(mx, "metal", None)):
        fn = getattr(mod, "clear_cache", None) if mod is not None else None
        if fn:
            try:
                fn()
                return
            except Exception:
                continue
