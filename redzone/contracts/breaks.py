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
from pathlib import Path
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


# [one brain, step 5] ONE measured break-length table for every reader (breaks.LEN, BRAIN_LEN, and for lane 4 to
# wire: decider BREAK_LEN_S, cuts MEDIAN_S, screen MIN_BREAK_S, yaml break_lengths_s). Written by a lab script
# (scripts/measure_break_table.py) from the archive's non-locked nights, with its provenance; read behind
# ``breaks.one_model``. Two key spaces: ``rules`` = breaks.LEN keys (what the forecast says), ``stoppage`` =
# models.Stoppage values per league (what the feed says). Lengths are feed seconds, break play -> next live play.
BREAK_TABLE_PATH = Path(__file__).resolve().parents[1] / "scoring" / "break_table.v1.json"


@dataclass(frozen=True)
class BreakLength:
    n: int
    q10_s: float
    q50_s: float
    q90_s: float

    @property
    def q50(self) -> float:
        return self.q50_s


@dataclass(frozen=True)
class BreakTable:
    version: str
    provenance: dict
    rules: dict            # LEN key -> BreakLength
    stoppage: dict         # league value -> Stoppage value -> BreakLength

    def rule(self, key: str) -> BreakLength | None:
        return self.rules.get(key)

    def by_stoppage(self, league: str, stoppage: str) -> BreakLength | None:
        return (self.stoppage.get(league) or {}).get(stoppage)


def _len(d: dict) -> BreakLength:
    return BreakLength(int(d["n"]), float(d["q10_s"]), float(d["q50_s"]), float(d["q90_s"]))


def load_table(path=None) -> BreakTable:
    """The committed table (BREAK_TABLE_PATH). A missing or bad file raises: the flag is off by default, and a
    flag that is on must not silently fall back to the old numbers."""
    import json
    d = json.loads(Path(path or BREAK_TABLE_PATH).read_text())
    return BreakTable(str(d["version"]), dict(d["provenance"]), {k: _len(v) for k, v in d["rules"].items()},
                      {lg: {k: _len(v) for k, v in t.items()} for lg, t in d["stoppage"].items()})
