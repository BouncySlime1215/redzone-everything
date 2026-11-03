"""BreakModel contract: when a stoppage starts, what kind it is, how long it runs, when play restarts.

Owner: MOMENT/BREAK MODEL. Implementation today: redzone/scoring/breaks.py BreakBook + forecast()
(wrapped by legacy.LegacyBreaks, which only knows the median length).
Called by: DecisionCore (break visits, drive-takeover dead-air check, reel timing) and Guard
(a trip must fit: cost there + back + exit <= length_q10_s for a sure-fit visit). Cadence: every
feed update per live game.

Units: seconds of SCREEN-INDEPENDENT feed time for lengths; next_restart_at is epoch s in FEED
wallclock (add SyncEstimate.delay_s for screen time). p over the next ~60 s.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from redzone.models import GameState


class BreakKind(str, Enum):
    NONE = "none"
    COMMERCIAL = "commercial"      # TV timeout / media timeout / after score or punt
    TEAM_TIMEOUT = "team_timeout"  # usually no commercial (NFL ~50 s)
    QUARTER = "quarter"            # end of quarter/period
    HALFTIME = "halftime"          # halftime / intermission
    REVIEW = "review"              # replay review / injury: unknown length
    INNINGS = "innings"            # MLB half-inning change


@dataclass(frozen=True)
class BreakForecast:
    game_id: str
    at: float                     # epoch s of the feed state
    p_start: float                # P(a break reaches the feed within ~60 s); 1.0 when in_data_now
    kind: BreakKind
    in_data_now: bool             # the break is already in the feed (not a guess)
    length_q10_s: float           # 10th / 50th / 90th percentile of total dead time, restart included
    length_q50_s: float
    length_q90_s: float
    next_restart_at: float | None = None   # feed-time epoch s of the next live play, if known
    why: str = ""

    @property
    def visit_worthy(self) -> bool:
        """Long enough to spend somewhere else (breaks.VISIT_MIN_S = 90 s, on the median)."""
        return self.length_q50_s >= 90.0


NO_BREAK = dict(p_start=0.0, kind=BreakKind.NONE, in_data_now=False,
                length_q10_s=0.0, length_q50_s=0.0, length_q90_s=0.0)


@runtime_checkable
class BreakModel(Protocol):
    def observe(self, g: GameState) -> None: ...
    def forecast(self, g: GameState, now: float) -> BreakForecast: ...
