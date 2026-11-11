"""Circuit breaker: stop waiting on something that keeps hanging.

A Fire TV stick that stops answering makes every adb command wait out its full timeout (8 s for a
deep link, 4 s for a playback read). Fault drill, before this: one switch to a hung hidden stick
took 12 s, and every later switch, preload and verify paid the same again.

States:
  closed     calls go through. `threshold` hangs in a row -> open.
  open       calls fail at once (0 s) for `backoff` seconds (doubling per re-open, up to `max_backoff`).
  half-open  after that, ONE probe call goes through (others still fail fast). Success -> closed and
             the backoff resets; another hang -> open again for twice as long.

Only hangs (a call that ran to its timeout) count. Any other answer, even a fast, clear failure
("device offline", a launch the app refused), costs nothing, is handled where it happens, and counts
as `success()` here: the thing responds again.

Why: first added in 31b5b0eb (2026-11-03) as "Platform: fault drill, per-stick circuit breaker, wall-clock jumps can't age a switch"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

THRESHOLD = 2
BACKOFF_S = 10.0       # the adb keepalive reconnects every ~10 s: one probe per tick
MAX_BACKOFF_S = 120.0


@dataclass
class Breaker:
    name: str
    threshold: int = THRESHOLD
    backoff_s: float = BACKOFF_S
    max_backoff_s: float = MAX_BACKOFF_S
    clock: Callable[[], float] = time.monotonic
    hangs: int = 0                 # in a row
    opened_at: float | None = None
    until: float = 0.0
    trips: int = 0                 # times it opened (for Health and the nightly report)
    _next_backoff: float = field(default=0.0, repr=False)
    _probing: bool = field(default=False, repr=False)

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "open" if self.clock() < self.until else "half-open"

    @property
    def is_open(self) -> bool:
        return self.state == "open"

    def allow(self) -> bool:
        """May a call go through now? In half-open, only the first caller gets to probe."""
        s = self.state
        if s == "closed":
            return True
        if s == "open" or self._probing:
            return False
        self._probing = True
        return True

    def success(self) -> None:
        """Any answer that wasn't a hang (even a fast, clear failure): the thing responds again."""
        self.hangs = 0
        self.opened_at = None
        self._probing = False
        self._next_backoff = 0.0

    def probe_now(self) -> None:
        """Outside evidence it may be back (adb connect just succeeded): skip the rest of the wait
        and let the next call probe, so a long outage doesn't add up to max_backoff of recovery time."""
        if self.opened_at is not None:
            self.until = min(self.until, self.clock())

    def release(self) -> None:
        """The call never finished (cancelled): neither a hang nor an answer. Frees the probe slot,
        so a cancelled probe can't leave the breaker refusing everything forever."""
        self._probing = False

    def hang(self) -> None:
        if self.state == "open":
            # A command that started before it opened, hanging out its own timeout: same outage,
            # count once (as sources/http.py does for a burst of failed fetches).
            self.hangs += 1
            return
        self._probing = False
        self.hangs += 1
        if self.opened_at is not None or self.hangs >= self.threshold:
            now = self.clock()
            self._next_backoff = min(self.max_backoff_s, max(self.backoff_s, self._next_backoff * 2))
            self.opened_at = self.opened_at if self.opened_at is not None else now
            self.until = now + self._next_backoff
            self.trips += 1

    def retry_in(self) -> float:
        return max(0.0, self.until - self.clock())

    def view(self) -> dict:
        return {"name": self.name, "state": self.state, "hangs": self.hangs, "trips": self.trips,
                "retry_in_s": round(self.retry_in(), 1)}
