"""MomentModel contract (Brain v3): per-game "is something worth seeing about to happen".

Owner: MOMENT/BREAK MODEL ("Rebuild the brain lean and sharper"). Implementation today:
redzone/scoring/brain.py Brain (wrapped by legacy.LegacyMoment); Hype v2 lives behind the same call.
Called by: the Scorer (redzone/scoring/engine.py) and DecisionCore. Cadence: once per game per
feed update (3-10 s). Must be cheap: < 5 ms per game on the Mac, no network.

Units: probabilities 0..1 over the horizon_s of FEED time; hotness 0..100.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from redzone.models import GameState


@dataclass(frozen=True)
class MomentPrediction:
    game_id: str
    at: float                       # epoch s of the feed state it was computed from
    hotness: float | None           # 0..100, None = no model for this league
    key_soon_p: float | None        # P(a key play: score, turnover, big gain) within horizon_s
    break_soon_p: float | None      # P(a commercial starts within horizon_s); BreakModel owns lengths
    horizon_s: float = 60.0
    home_wp: float | None = None    # home win probability 0..1
    reasons: tuple[str, ...] = ()   # plain English, for the phone
    model_version: str = ""

    @property
    def available(self) -> bool:
        return self.hotness is not None or self.key_soon_p is not None


@runtime_checkable
class MomentModel(Protocol):
    def predict(self, g: GameState, now: float) -> MomentPrediction: ...
    def forget(self, game_id: str) -> None: ...


def empty(game_id: str, at: float) -> MomentPrediction:
    return MomentPrediction(game_id, at, None, None, None)

