"""One clock: wall time, feed (data) time and per-channel screen time.

Owner: INTEGRATION. Every module that talks about "when" (breaks, entries, reels, replays) converts
through this, never with its own `+ delay`. All values are epoch seconds.

  wall    the Mac's clock right now (time.time(), or the replay clock in backtests)
  feed    when an event happened per the feed's wallclock (Play.wallclock)
  screen  when that event is (or was) on a given channel's picture: feed + delay(target)

The clock is frozen per tick: WorldState carries one Clock so every module sees the same `now`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from redzone.contracts.sync import SyncEstimate


@dataclass(frozen=True)
class Clock:
    now: float                                         # wall time of this tick
    sync: Callable[[str], SyncEstimate] | None = None  # target -> estimate (frozen for the tick)
    default_delay_s: float = 30.0
    default_sigma_s: float = 15.0
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    def estimate(self, target: str) -> SyncEstimate:
        if target not in self._cache:
            self._cache[target] = (self.sync(target) if self.sync else
                                   SyncEstimate(target, self.default_delay_s, self.default_sigma_s, "default"))
        return self._cache[target]

    def to_screen(self, feed_t: float, target: str) -> float:
        return feed_t + self.estimate(target).delay_s

    def to_feed(self, screen_t: float, target: str) -> float:
        return screen_t - self.estimate(target).delay_s

    def on_screen_now(self, target: str) -> float:
        """Feed time of what `target` is showing right now."""
        return self.to_feed(self.now, target)

    def screen_window(self, feed_t: float, target: str, k: float = 2.0) -> tuple[float, float]:
        """(earliest, latest) screen time of a feed event at k sigma."""
        e = self.estimate(target)
        return feed_t + e.delay_s - k * e.sigma_s, feed_t + e.delay_s + k * e.sigma_s
