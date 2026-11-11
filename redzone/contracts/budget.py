"""Per-tick latency budget, split across modules. Enforced and logged.

Owner: INTEGRATION (numbers) / PLATFORM (enforcement + logging). The decision tick runs every
polling.hot_s (3 s). A module over its budget is logged and counted in the error budget; one that
raises returns its degraded result for this tick. Three overruns in a row of the same part let the
supervisor turn that part's flag off (flags.py) and say so on the phone. Async
parts (reasoner, watchdog model, reel builds) are never on the tick.

Why: first added in 418ffc56 (2026-11-03) as "Architecture contract v1: interfaces, stubs, contract tests, ownership, status board"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# milliseconds per tick (Mac M2, service at background QoS)
BUDGET_MS: dict[str, float] = {
    "feeds": 50.0,          # snapshot from already-fetched data (fetching is async)
    "models": 150.0,        # MomentModel + BreakModel for all live games
    "sync": 10.0,
    "paths": 20.0,
    "reasoner": 5.0,        # read the cached view only
    "decision": 100.0,      # DecisionCore + Guard
    "command": 250.0,       # sending the first command (completion is async)
}
TICK_MS = sum(BUDGET_MS.values())


@dataclass
class TickTimer:
    over: dict[str, float] = field(default_factory=dict)
    spent: dict[str, float] = field(default_factory=dict)

    def run(self, part: str, fn, *args, fallback=None, **kw):
        """Run fn; if it takes longer than its budget, keep the result but record the overrun.
        Exceptions return `fallback` (the degraded result) and are recorded as overruns of inf."""
        t0 = time.perf_counter()
        try:
            out = fn(*args, **kw)
        except Exception:
            self.over[part] = float("inf")
            return fallback
        ms = (time.perf_counter() - t0) * 1000.0
        self.spent[part] = self.spent.get(part, 0.0) + ms
        if ms > BUDGET_MS.get(part, TICK_MS):
            self.over[part] = ms
        return out
