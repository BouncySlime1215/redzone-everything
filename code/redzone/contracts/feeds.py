"""Feeds contract: game state plus how fresh it is.

Owner: PLATFORM (implementation: redzone/sources/*). Contract file owner: INTEGRATION.
Cadence: the engine polls hot games every polling.hot_s (3 s), other live games every
polling.live_s (10 s); FastCast pushes may arrive between polls. Consumers read a snapshot,
never the sources directly.

Units: epoch seconds for times, seconds for ages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from redzone.models import GameState


@dataclass(frozen=True)
class GameFeed:
    state: GameState
    fetched_at: float          # epoch s: last fresh data for this game from any source
    source: str                # "espn" | "fastcast" | "merged" ...
    stale: bool = False        # the merger says this game's data stopped moving

    def age_s(self, now: float) -> float:
        return max(0.0, now - self.fetched_at)


@dataclass(frozen=True)
class FeedSnapshot:
    at: float                                     # epoch s the snapshot was taken
    games: dict[str, GameFeed] = field(default_factory=dict)   # game_id -> feed

    def live(self) -> dict[str, GameFeed]:
        from redzone.models import Phase
        return {k: v for k, v in self.games.items() if v.state.phase == Phase.LIVE}


@runtime_checkable
class Feeds(Protocol):
    def snapshot(self, now: float) -> FeedSnapshot: ...
