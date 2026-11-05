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
    CBB = "cbb"    # men's college basketball: NBA rules, two 20-minute halves
    WCBB = "wcbb"  # women's college basketball: NBA rules, four 10-minute quarters
    CFB = "cfb"    # college football: NFL rules

    @property
    def base(self) -> "League":
        """The pro league whose game rules (dead balls, breaks, scoring) this one plays by."""
        return _BASE.get(self, self)

    @property
    def college(self) -> bool:
        return self in _BASE


_BASE = {League.CBB: League.NBA, League.WCBB: League.NBA, League.CFB: League.NFL}

# [one brain, step 1] THE league order (North Star: NFL > CFB > NBA > CBB > NHL > MLB; Nick, Nov 3:
# overlapping crunch times, first listed wins after your team / bet). Every ranking reads it through
# league_order(cfg), which honours the config key core.league_priority. Never copy it.
# WCBB is not one of Nick's leagues (Nick, Nov 4 7:55 PM ET): off by default and unlisted, so it ranks last.
LEAGUE_ORDER: tuple[League, ...] = (League.NFL, League.CFB, League.NBA, League.CBB, League.NHL, League.MLB)


def league_order(cfg=None) -> tuple[str, ...]:
    """League values, first = highest priority: core.league_priority when set, else LEAGUE_ORDER."""
    got = cfg.get("core.league_priority") if cfg is not None else None
    return tuple(str(x).lower() for x in got) if got else tuple(lg.value for lg in LEAGUE_ORDER)


def league_index(league, cfg=None, order: tuple[str, ...] | None = None) -> int:
    """0 = first in the league order; an unlisted league comes after every listed one."""
    lp = order if order is not None else league_order(cfg)
    v = getattr(league, "value", league)
    return lp.index(v) if v in lp else len(lp)
# Periods in regulation and seconds per period (MLB: innings, untimed).
REGULATION = {League.NFL: 4, League.NBA: 4, League.NHL: 3, League.MLB: 9,
              League.CBB: 2, League.WCBB: 4, League.CFB: 4}
PERIOD_S = {League.NFL: 900, League.NBA: 720, League.NHL: 1200, League.MLB: 0,
            League.CBB: 1200, League.WCBB: 600, League.CFB: 900}


class ByLeague(dict):
    """A per-league table keyed by the pro leagues: a college league reads its pro league's row
    unless it has its own."""

    def __missing__(self, key):
        base = getattr(key, "base", key)
        if base is key:
            raise KeyError(key)
        return self[base]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return dict.__contains__(self, key) or dict.__contains__(self, getattr(key, "base", key))


def pro_period(league: League, period: int | None, clock_s: float | None) -> int:
    """The period in the pro league's terms, so NBA rules read college halves right: a men's
    college half counts as two NBA quarters (2nd half with 10:00 or less left = "Q4", 1st OT = 5).
    Every other league already plays the pro structure."""
    p = period or 0
    if REGULATION.get(league) != 2 or p <= 0:
        return p
    if p > 2:
        return p + 2
    half = PERIOD_S[league] / 2
    return 2 * p - (1 if clock_s is None or clock_s > half else 0)


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
    HALFTIME = "halftime"
    INTERMISSION = "intermission"  # NHL break after the 1st/2nd period (~18 min)
    REVIEW = "review"
    INJURY = "injury"
    PITCHING_CHANGE = "pitching_change"
    SCORE_BREAK = "score_break"    # after a TD/goal: XP, kickoff, celebration, ads
    WHISTLE = "whistle"            # brief dead ball (foul, icing, offside): safe to cut, too short to visit
    UNKNOWN = "unknown"


# Stoppages long enough to go watch something else during.
REAL_BREAKS = {Stoppage.TIMEOUT, Stoppage.TWO_MINUTE_WARNING, Stoppage.END_PERIOD, Stoppage.HALFTIME,
               Stoppage.INTERMISSION,
               Stoppage.REVIEW, Stoppage.INJURY, Stoppage.PITCHING_CHANGE, Stoppage.SCORE_BREAK}


@dataclass
class Team:
    abbr: str
    name: str = ""
    score: int | None = None
    id: str = ""
    logo: str = ""
    color: str = ""
    logo_dark: str = ""
    alt_color: str = ""
    wins: int | None = None
    losses: int | None = None
    ties: int | None = None        # NFL ties / NHL OT losses
    rank: int | None = None        # college poll rank (top 25), None when unranked

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
    score: tuple[int, int] | None = None  # (home, away) right after this play, when the feed says
    # [cut timing] the game clock (seconds LEFT in the period) and period at this entry, and the
    # feed's raw type text: basketball's restart is worked back from the first clock-moving event.
    clock: float | None = None
    period: int | None = None
    type_text: str = ""


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
    leaders: list[dict] = field(default_factory=list)   # top performers: label, name, short, headshot, stat, team
    spotlight: dict | None = None   # player in the latest big play: name, short, headshot, team, text
    stakes: object | None = None    # scoring.postseason.Stakes: series / round / ranks (None = nothing to say)
    venue_info: dict = field(default_factory=dict)      # id, name, city, image, indoor
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
