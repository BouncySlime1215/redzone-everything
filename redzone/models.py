"""Source-agnostic game model. Every data source normalizes into these.

Every field is optional except the identity fields, because the feeds are
undocumented and any field can disappear mid-season. Code that reads a
GameState must treat None as "unknown", never as zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class League(str, Enum):
    NFL = "nfl"
    NBA = "nba"
    NHL = "nhl"
    MLB = "mlb"


class Phase(str, Enum):
    PRE = "pre"
    LIVE = "live"
    POST = "post"
    UNKNOWN = "unknown"


class Stoppage(str, Enum):
    """Why play is stopped. NONE means the ball/puck is live or about to be."""
    NONE = "none"
    TIMEOUT = "timeout"            # team or TV timeout
    TWO_MINUTE_WARNING = "two_minute_warning"
    END_PERIOD = "end_period"      # end of quarter / period / half-inning
    HALFTIME = "halftime"          # also NHL intermission
    REVIEW = "review"
    INJURY = "injury"
    PITCHING_CHANGE = "pitching_change"
    SCORE_BREAK = "score_break"    # after a TD/goal: XP, kickoff, celebration, ads
    WHISTLE = "whistle"            # brief dead ball (foul, icing, offside): safe to cut, too short to visit
    UNKNOWN = "unknown"


# Stoppages long enough to go watch something else during.
REAL_BREAKS = {Stoppage.TIMEOUT, Stoppage.TWO_MINUTE_WARNING, Stoppage.END_PERIOD, Stoppage.HALFTIME,
               Stoppage.REVIEW, Stoppage.INJURY, Stoppage.PITCHING_CHANGE, Stoppage.SCORE_BREAK}


@dataclass
class Team:
    abbr: str
    name: str = ""
    score: int | None = None
    id: str = ""
    logo: str = ""
    color: str = ""
    wins: int | None = None
    losses: int | None = None
    ties: int | None = None        # NFL ties / NHL OT losses

    @property
    def win_pct(self) -> float | None:
        if self.wins is None or self.losses is None:
            return None
        games = self.wins + self.losses + (self.ties or 0)
        if games == 0:
            return None
        return (self.wins + 0.5 * (self.ties or 0)) / games


@dataclass
class Play:
    """One event from play-by-play. `seen_at` is when WE first saw it (epoch s)."""
    id: str
    text: str = ""
    kind: str = ""                 # normalized: "td", "fg", "turnover", "goal", "hr", ...
    scoring: bool = False
    wallclock: float | None = None # when it happened, per the feed (epoch s)
    seen_at: float | None = None
    source: str = ""


@dataclass
class Situation:
    """League-specific live state. Unused fields stay None."""
    # NFL
    possession: str | None = None  # team abbr
    down: int | None = None
    distance: int | None = None
    yards_to_goal: int | None = None
    goal_to_go: bool | None = None
    # NHL
    power_play: str | None = None  # abbr of team on the PP
    strength: str | None = None    # skaters, e.g. "5v4", "5v3", "6v5"
    empty_net: str | None = None   # abbr of team with goalie pulled
    # MLB
    outs: int | None = None
    runners: tuple[bool, bool, bool] | None = None  # 1st, 2nd, 3rd
    leverage_index: float | None = None
    no_hitter_for: str | None = None  # abbr of team throwing it
    # shared
    timeouts_home: int | None = None
    timeouts_away: int | None = None


@dataclass
class GameState:
    league: League
    game_id: str                   # canonical id (ESPN event id)
    home: Team
    away: Team
    phase: Phase = Phase.UNKNOWN
    period: int | None = None      # quarter / period / inning
    half: str | None = None        # MLB "top" / "bottom"
    period_type: str | None = None # "REG", "OT", "SO"
    clock_s: float | None = None   # seconds left in period (None for MLB)
    stoppage: Stoppage = Stoppage.UNKNOWN
    situation: Situation = field(default_factory=Situation)
    home_win_prob: float | None = None  # 0..1
    spread: float | None = None    # home spread (negative = home favored)
    national_tv: bool = False
    broadcasts: list[str] = field(default_factory=list)
    player_flags: list[str] = field(default_factory=list)  # e.g. "Jokic 38 pts"
    venue: str = ""
    start_time: float | None = None  # scheduled start (epoch s)
    status_text: str = ""           # "Q4 1:12", "Halftime", "Final/OT"
    odds_text: str = ""             # "SEA -3 · O/U 46.5"
    last_play: Play | None = None
    recent_plays: list[Play] = field(default_factory=list)
    updated_at: float = 0.0        # when we last got fresh data (epoch s)
    sources: dict[str, float] = field(default_factory=dict)  # source -> last fresh time

    @property
    def score_diff(self) -> int | None:
        if self.home.score is None or self.away.score is None:
            return None
        return self.home.score - self.away.score

    def involves(self, abbrs: set[str]) -> bool:
        return self.home.abbr in abbrs or self.away.abbr in abbrs

    @property
    def matchup(self) -> str:
        return f"{self.away.abbr} @ {self.home.abbr}"
