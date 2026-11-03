"""SwitchPaths contract: every way to put a target on screen, each with a measured cost.

The path TYPE is the decision core's redzone/decider/core.py `SwitchPath(kind, cost_s, cost_p90_s,
preloaded)`; this file defines who produces them. Owner: SWITCHING ("Switch games instantly via TV
input"). Implementation today: redzone/control.py Controller (switch_costs / deadline_cost /
standby). Called while building the WorldState, once per target per tick; costs update after each
completed switch (LatencyBook). Units: seconds from command sent to picture confirmed.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from redzone.decider.core import SwitchPath

# core.SwitchPath.kind values
TV_FLIP, HOME_FLIP, IN_APP_TUNE, REPLAY_SEEK, NONE = "tv_flip", "home_flip", "in_app_tune", "replay_seek", "none"
# A tile of a YouTube TV multiview expanded to full screen (redzone/tuners/multiview.py; flag multiview.enabled).
MULTIVIEW = "multiview_expand"
KINDS = (TV_FLIP, HOME_FLIP, IN_APP_TUNE, REPLAY_SEEK, MULTIVIEW, NONE)


@runtime_checkable
class SwitchPaths(Protocol):
    def paths(self, target: str, now: float) -> tuple[SwitchPath, ...]:
        """Ready paths only (stick online, not busy); empty = can't reach it now."""
        ...

    def busy(self) -> bool:
        """A switch is in flight (the 'no overlap' guard reads this)."""
        ...


def deadline(paths: tuple[SwitchPath, ...]) -> float:
    """The planning bound the entry/trip guards budget: the cheapest path's p90 (cost_s if unset)."""
    if not paths:
        return float("inf")
    p = min(paths, key=lambda x: x.cost_s)
    return p.cost_p90_s or p.cost_s
