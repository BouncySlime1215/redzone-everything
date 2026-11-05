"""WorldState: ONE immutable snapshot per tick, passed to every module.

Owner: INTEGRATION. Built once per decision tick by the engine (redzone/engine.py Engine.decide)
from the Feeds snapshot, model outputs, sync estimates, switch paths and health. No module
re-fetches or recomputes its own copy during the tick: everything it needs is in here, so every
module sees the same `now`, the same delays and the same costs.

Build order inside a tick (each step only reads what earlier steps produced):
  1 clock + feeds   2 moments, breaks (per live game)   3 sync (per target)   4 paths (per target)
  5 scored (Scorer, reads 1-2)   6 reasoner views (cached)   7 reel offer   8 health
Then the Decider builds core.CoreState via contracts/views.py and runs core.DecisionCore + guards.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from redzone.contracts.breaks import BreakForecast
from redzone.contracts.clock import Clock
from redzone.contracts.feeds import GameFeed
from redzone.contracts.health import HealthReport
from redzone.contracts.moment import MomentPrediction
from redzone.contracts.reasoner import ReasonerView
from redzone.contracts.reel import ReelOffer
from redzone.contracts.switching import SwitchPath
from redzone.contracts.sync import SyncEstimate


@dataclass(frozen=True)
class GameView:
    """Everything known about one game this tick."""
    feed: GameFeed
    scored: object | None = None            # redzone.scoring.engine.Scored (kept untyped to avoid a cycle)
    target: str | None = None               # where it's on, None = not tunable
    moment: MomentPrediction | None = None
    brk: BreakForecast | None = None
    reasoner: ReasonerView | None = None

    @property
    def game_id(self) -> str:
        return self.feed.state.game_id


@dataclass(frozen=True)
class Screen:
    """What is on the TV right now, per the controller (not per the decider's wish)."""
    game_id: str | None          # game, "redzone", "reel", "replay" or None (black / unknown)
    target: str | None
    since: float                 # wall s it came up (picture confirmed)
    confirmed: bool              # picture confirmed vs. command accepted only
    switch_in_flight: str | None = None   # switch_id of a pending switch


@dataclass(frozen=True)
class WorldState:
    clock: Clock
    games: Mapping[str, GameView]
    screen: Screen
    sync: Mapping[str, SyncEstimate] = field(default_factory=dict)        # target -> estimate
    paths: Mapping[str, tuple[SwitchPath, ...]] = field(default_factory=dict)  # target -> paths
    reel: ReelOffer | None = None
    redzone_target: str | None = None       # NFL RedZone channel while on the air
    health: HealthReport | None = None
    user: object | None = None              # redzone.decider.decider.UserState (copy)
    flags: Mapping[str, bool] = field(default_factory=dict)
    tick: int = 0

    @property
    def now(self) -> float:
        return self.clock.now

    def live(self) -> dict[str, GameView]:
        from redzone.models import Phase
        return {k: v for k, v in self.games.items() if v.feed.state.phase == Phase.LIVE}

    def flag(self, name: str, default: bool = True) -> bool:
        return bool(self.flags.get(name, default))


def freeze(d: dict) -> Mapping:
    """Read-only view for building a WorldState from mutable dicts."""
    return MappingProxyType(dict(d))
