"""Resource governor: keep the Mac cool, quiet and with free disk while RedZone runs.

The Mac is an 8 GB MacBook Air with ~5 GB of free disk that also goes to school. RedZone shares
it with everything else, so the extra work (catch-up reel downloads and trims, the lab, the
archive, score-bug OCR, the local model) backs off when the Mac is busy, hot, on battery, short
of memory or short of disk. Live switching is never slowed: decisions, tunes and hot polling
run the same at every level.

Levels (computed from one Sample every `every_s` seconds):
  normal    everything runs
  eco       on battery, Low Power Mode, busy CPU, memory pressure "warn", swapping, thermal
            warning, or disk under disk_low_mb:  no reel downloads, no lab, no archive; score-bug
            OCR and idle polling at half rate; the local model still answers new errors
  critical  battery low, memory pressure "critical", throttled CPU, disk under disk_critical_mb:
            as eco, plus no score-bug OCR and no local model (rules only); idle polling at 1/4
A level goes up at once and comes down only after `calm_s` seconds below it (no flapping).

Memory caps (macOS has no enforced RSS limit for launchd user agents, so they are enforced here):
  - a model process calls apply_mlx_limits() before loading: MLX's own memory and cache limits
  - the watchdog (agent/watchdog.py) restarts the app when its resident memory stays over
    service_hard_mb (never during crunch time), and drops its own model when idle or when the
    model processes together go over models_mb

Stdlib only: the watchdog's separate venv imports it too.
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
    "swap_eco_mb": 1500,       # swap in use above this: eco (8 GB Mac is short of memory)
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
    "ocr": {"normal": 1.0, "eco": 2.0, "critical": 5.0},
    "idle_poll": {"normal": 1.0, "eco": 2.0, "critical": 4.0},
}

# Processes the governor measures (pattern in the command line -> name).
PROCESSES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"scripts/service_runtime\.py|-m redzone(\s|$)"), "service"),
    (re.compile(r"agent[./]llm_server"), "llm_server"),
    (re.compile(r"-m agent\.watchdog|agent/watchdog\.py"), "watchdog"),
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
    load_per_core: float | None = None    # 5-minute load average / CPU count
    disk_free_mb: float | None = None
    procs: dict[str, float] = field(default_factory=dict)   # name -> resident MB (summed per name)


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
    s.swap_used_mb = parse_swap(run(["sysctl", "-n", "vm.swapusage"]))
    try:
        s.load_per_core = round(os.getloadavg()[1] / (os.cpu_count() or 1), 2)
    except (OSError, AttributeError):
        pass
    try:
        s.disk_free_mb = round(shutil.disk_usage(root).free / 1e6)
    except OSError:
        pass
    s.procs = parse_ps(run(["ps", "-axo", "pid=,rss=,command="]))
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
    if s.swap_used_mb is not None and s.swap_used_mb > opts["swap_eco_mb"]:
        eco.append(f"{s.swap_used_mb / 1000:.1f} GB swapped out")
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
        self.level = "normal"
        self.reasons: list[str] = []
        self.last: Sample | None = None
        self.since = clock()
        self._calm_from: float | None = None   # when the raw level first dropped below self.level
        self._checked = -1e18
        self.changes: list[tuple[float, str, str]] = []   # (when, level, why), last 20
        self._source: Callable[[], dict] | None = None

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
        if not self.opts["enabled"]:
            self.level, self.reasons = "normal", []
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

    def _set(self, level: str, reasons: list[str], now: float) -> None:
        old = self.level
        self.level, self.reasons, self.since = level, reasons, now
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
        return (self.last.procs.get("service") if self.last else None)

    def models_mb(self) -> float:
        return sum((self.last.procs.get(n, 0.0) for n in MODEL_PROCESSES), 0.0) if self.last else 0.0

    def view(self) -> dict:
        s = self.last
        return {"level": self.level, "why": self.why(), "since": self.since,
                "paused": sorted(k for k in ALLOWED if not self.allow(k)),
                "sample": asdict(s) if s else None,
                "service_mb": self.service_mb(), "service_soft_mb": self.opts["service_soft_mb"],
                "models_mb": round(self.models_mb(), 1), "models_cap_mb": self.opts["models_mb"]}


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
