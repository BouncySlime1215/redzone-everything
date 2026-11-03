"""Watchdog/Health contract: faults the system sees and the fixes applied.

Owner: LOCAL AI for the watchdog model (agent/watchdog.py, agent/policy.py); PLATFORM for the
in-process supervisor (redzone/supervisor.py), governor and network awareness. Both report in
these shapes. Called by: DecisionCore reads HealthReport (safe mode, two-stick, away from home);
Trace and the nightly report read faults/fixes. Cadence: supervisor ~10 s, watchdog ~60 s.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class Severity(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"        # degrades switching
    CRITICAL = "critical"  # nothing will switch until fixed


@dataclass(frozen=True)
class Fault:
    code: str              # stable id: "stick_offline", "stale_feed", "away_from_home", "tailscale_overlap", ...
    severity: Severity
    component: str         # "stick:<serial>", "tv", "feed:espn", "llm", "disk", "network"
    detail: str
    since: float           # epoch s first seen
    count: int = 1


@dataclass(frozen=True)
class Fix:
    fault_code: str
    action: str            # agent/policy.py ACTIONS or a supervisor rule name
    target: str
    ok: bool
    at: float
    source: str = "rule"   # rule | model | guard | supervisor
    note: str = ""


@dataclass(frozen=True)
class HealthReport:
    at: float
    faults: tuple[Fault, ...] = field(default_factory=tuple)
    fixes: tuple[Fix, ...] = field(default_factory=tuple)
    safe_mode: bool = False
    two_stick: bool = False
    away_from_home: bool = False
    ram_mb: float | None = None       # service + LLM server resident
    disk_free_mb: float | None = None

    def worst(self) -> Severity | None:
        order = list(Severity)
        return max((f.severity for f in self.faults), key=order.index, default=None)


@runtime_checkable
class Health(Protocol):
    def report(self, now: float) -> HealthReport: ...
