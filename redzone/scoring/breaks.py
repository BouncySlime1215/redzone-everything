"""Break predictor: will this game go to a commercial in about the next minute of STREAM time, and
for how long?

The data runs ~25-50s ahead of the screen, so "a break is in the data now" is a break coming to
your screen, and "a break is due at the next dead ball" is usually one or two whistles past that.
Rules (sourced, Nov 2 2026 research) and lengths (MEASURED from data/archive: wallclock of the
break to the next live play, restart included):

  NFL    a TD/FG -> ~160s before the kickoff; a punt -> ~150s before the next snap (73% of NFL
         breaks are these two). End of Q1/Q3 ~200s, two-minute warning ~167s. A TEAM timeout
         ("Timeout #1 by MIN") is ~50s and usually no commercial; "Official Timeout" is one.
  NBA    the first two timeouts of every quarter are the long commercial ones (~188s), whoever
         calls them; #3+ are short (~107s). The scorer forces #1 at the first dead ball under
         7:00 and #2 under 3:00 if nobody has called one. End of Q1/Q3 ~171s.
  CBB    media timeouts at the first dead ball under 16/12/8/4 of each half (~184s); a team
         timeout within 30s before a mark, or after it, becomes it.
  WCBB   one media timeout per quarter at the first dead ball under 5:00 (~174s).
  NHL    TV timeouts at the first stoppage after 6, 10 and 14 minutes ELAPSED (~149s), never
         after a goal or icing, on a power play, in the last 30s of P1/P2 or the last 2 min of P3.
  MLB    two outs: the next out ends the half-inning (~175s).

BreakBook keeps the per-period list of long breaks each game has had (so it knows which
NBA/college/NHL mark is still owed); forecast() is pure given that list.

With the learned brain on (brain.enabled), a forecast that isn't already in the data blends in
the brain's break_soon_p (redzone/scoring/brain.py): blend() below. A break already in the data
is never second-guessed.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from redzone.models import REAL_BREAKS, GameState, League, Phase, Stoppage, pro_period

# Measured median dead time (seconds) a viewer sees, restart included.
LEN = {
    "nfl_score": 160.0, "nfl_punt": 150.0, "nfl_turnover": 143.0, "nfl_2min": 167.0, "nfl_quarter": 202.0,
    "nfl_half": 857.0, "nfl_team_to": 50.0,
    "nba_long": 188.0, "nba_short": 107.0, "nba_quarter": 171.0, "nba_half": 933.0,
    "cbb_media": 184.0, "cbb_half": 980.0, "wcbb_media": 174.0, "wcbb_quarter": 186.0, "wcbb_half": 983.0,
    "nhl_tv": 149.0, "nhl_goal": 48.0, "nhl_intermission": 1131.0,
    "mlb_inning": 175.0, "mlb_pitching": 178.0,
}
CBB_MARKS = (960.0, 720.0, 480.0, 240.0)   # 16:00, 12:00, 8:00, 4:00 left in each half
NHL_MARKS = (840.0, 600.0, 360.0)          # 6, 10, 14 minutes elapsed
NBA_MARKS = (420.0, 180.0)                 # mandatory timeouts: under 7:00, then under 3:00
WCBB_MARK = 300.0
MARK_SLACK_S = 30.0                        # a timeout this close before a mark uses it (college)
VISIT_MIN_S = 90.0                         # shorter than this isn't worth a trip
HOLD_S = 75.0                              # a break seen in the data is still coming to the screen this long


@dataclass(frozen=True)
class Forecast:
    p: float           # chance a commercial reaches the screen within about a minute
    expect_s: float    # its expected length if it does
    why: str           # plain English, for the phone
    now: bool = False  # already in the data (not a guess)
    key: str = ""      # which length (a LEN key) expect_s is: the one-brain break table is measured per key

    @property
    def visit(self) -> bool:
        """Long enough to spend somewhere else (a 50s team timeout or a goal isn't)."""
        return self.expect_s >= VISIT_MIN_S


NONE = Forecast(0.0, 0.0, "")
MIN_N = 30          # a measured length replaces the prior only with at least this many breaks behind it


def _f(p: float, key: str, lens: dict, why: str, now: bool = False) -> Forecast:
    return Forecast(p, float(lens[key]), why, now, key)


def lengths_from(table) -> dict:
    """LEN with every key the one-brain break table measured well (n >= MIN_N) replaced by its median
    (redzone/contracts/breaks.BreakTable). Unmeasured keys (no archive for that league) keep the prior."""
    if table is None:
        return LEN
    out = dict(LEN)
    for key in LEN:
        m = table.rule(key)
        if m is not None and m.n >= MIN_N:
            out[key] = m.q50
    return out

# What a learned-only forecast expects a break to last (median long break, per pro league).
BRAIN_LEN = {League.NFL: 150.0, League.NBA: 180.0, League.NHL: LEN["nhl_tv"], League.MLB: LEN["mlb_inning"]}


def _mix_logit(a: float, b: float, w: float) -> float:
    """(1-w)*logit(a) + w*logit(b), back to a probability (each side clipped off 0 and 1)."""
    def lo(x: float) -> float:
        x = min(max(x, 1e-4), 1 - 1e-4)
        return math.log(x / (1 - x))
    z = (1 - w) * lo(a) + w * lo(b)
    return 1 / (1 + math.exp(-z))


def mix(rules_p: float, brain_p: float, w=0.5) -> float:
    """The rules' and the brain's break odds as one probability. No rule evidence (rules_p == 0):
    the brain alone. Otherwise in log-odds: w = (a, b, c), fitted per league on held-out nights
    (scripts/eval_break_blend.py), gives logit q = a + b*logit(brain) + c*logit(rules); a plain
    number w is the brain's weight, (0, w, 1-w) (= _mix_logit, for leagues nobody has fitted)."""
    if rules_p <= 0:
        return float(brain_p)
    if isinstance(w, (int, float)):
        return _mix_logit(rules_p, brain_p, float(w))
    a, b, c = (float(x) for x in w)
    def lo(x: float) -> float:
        x = min(max(x, 1e-4), 1 - 1e-4)
        return math.log(x / (1 - x))
    z = a + b * lo(brain_p) + c * lo(rules_p)
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))


def weight_for(w, g: GameState):
    """A per-league blend setting ({league: {a, b, c}, "*": default weight}) for this game; a plain
    number or (a, b, c) passes through."""
    if not isinstance(w, dict):
        return w
    got = w.get(g.league.value) or w.get(g.league.base.value)
    if isinstance(got, dict):
        return (got.get("a", 0.0), got.get("b", 1.0), got.get("c", 0.0))
    return got if got is not None else w.get("*", 0.5)


def blend(f: Forecast, p: float | None, g: GameState, w=0.5, why: list[str] | None = None,
          v3=None, lengths: bool = True) -> Forecast:
    """Mix the rules' forecast with the brain's chance of a break in the next minute (w: a brain
    weight, fitted (a, b, c), or a per-league dict; see mix / weight_for).

    A break already in the data (f.now) keeps its odds; with Brain v3 (`v3`, a brain3.V3 about the
    newest play) its length becomes the model's time to the restart instead of the fixed LEN table
    (halftime / intermission keep the table: the decider plans those trips by it). When the rules
    see nothing coming, the brain alone can raise one, with v3's type-weighted length (else the
    league's median long break)."""
    if f.now:
        if v3 is not None and lengths and f.expect_s < 600 and _about_now(v3, g):
            e50 = v3.entry[1]
            if 0 < e50 < 600:
                return Forecast(f.p, e50, f.why, True)
        return f
    if p is None:
        return f
    if f.p > 0:
        # Both have evidence: mix in log-odds (research 02 §C18: the old 50/50 mix in probability
        # space took the live ECE from <=0.10 for the brain alone to 0.12-0.17), with the per-league
        # coefficients fitted on held-out nights (brain.break_blend; mix / weight_for below).
        return Forecast(mix(f.p, p, weight_for(w, g)), f.expect_s, f.why)
    # The rules see nothing coming: that is no evidence, not a forecast of 0, so the brain stands
    # alone instead of being halved.
    q = p
    if q <= 0:
        return f
    reason = "the model expects a break soon" + (f" ({why[0]})" if why else "")
    length = v3.expected_len() if v3 is not None and lengths else None
    return Forecast(q, length if length and 0 < length < 1500 else BRAIN_LEN.get(g.league.base, 150.0), reason)


def _league_in(setting, g: GameState) -> bool:
    """A per-league switch: a list of leagues (a game's own league or its pro base), or a bool."""
    if isinstance(setting, (list, tuple, set)):
        return g.league.value in setting or g.league.base.value in setting
    return bool(setting)


def _about_now(v3, g: GameState) -> bool:
    """The v3 reading is about the play now in the data (not an older one)."""
    lp = g.last_play
    at = getattr(v3, "at", None)
    return at is not None and lp is not None and lp.wallclock is not None and abs(float(lp.wallclock) - at) <= 1.0


def _long_break_play(league: League, kind: str, text: str) -> bool:
    """A play that is one of the period's owed media / mandatory / TV timeouts (NBA: any timeout,
    since the first two of a quarter are the long ones whoever calls them)."""
    k, t = kind.lower(), text.lower()
    if league == League.NHL:
        return "tv timeout" in t
    if league.base == League.NBA:
        return "timeout" in k or "timeout" in t
    return False


def _owed(clock: float, marks: tuple, clocks: tuple) -> bool:
    """A mark has passed and no break has used it yet. Each break (in game order) uses the
    earliest open mark it is at most MARK_SLACK_S before; one long before every mark uses none."""
    open_ = list(marks)
    for c in clocks:
        if c is None:
            continue
        m = next((m for m in open_ if c <= m + MARK_SLACK_S), None)
        if m is not None:
            open_.remove(m)
    return any(clock <= m for m in open_)


def _now(g: GameState, n: int, lens: dict = LEN) -> Forecast | None:
    """A break already in the data: no guess, just how long."""
    s = g.stoppage
    if s not in REAL_BREAKS:
        return None
    lg, base = g.league, g.league.base
    text = (g.last_play.text if g.last_play else "").lower()
    kind = (g.last_play.kind if g.last_play else "").lower()
    if s == Stoppage.HALFTIME:
        key = "nfl_half" if base == League.NFL else "cbb_half" if lg == League.CBB else \
            "wcbb_half" if lg == League.WCBB else "nba_half"
        return _f(1.0, key, lens, "halftime", True)
    if s == Stoppage.INTERMISSION:
        return _f(1.0, "nhl_intermission", lens, "intermission", True)
    if base == League.NFL:
        if s == Stoppage.TIMEOUT:
            if kind == "timeout" or "timeout #" in text:
                return _f(0.15, "nfl_team_to", lens, "team timeout (usually no commercial)", True)
            return _f(1.0, "nfl_score", lens, "TV timeout", True)
        if s == Stoppage.SCORE_BREAK:
            return _f(0.95, "nfl_score", lens, "score: commercial before the kickoff", True)
        if s == Stoppage.TWO_MINUTE_WARNING:
            return _f(1.0, "nfl_2min", lens, "two-minute warning", True)
        if s == Stoppage.END_PERIOD:
            return _f(1.0, "nfl_quarter", lens, "end of the quarter", True)
        return Forecast(0.4, 120.0, "stoppage", True)
    if base == League.NBA:
        if s == Stoppage.TIMEOUT:
            if lg.college and "tv" in kind + " " + text:
                return _f(1.0, "cbb_media" if lg == League.CBB else "wcbb_media", lens, "media timeout", True)
            if lg == League.NBA:
                # n already counts this one (observe runs first): #1 and #2 of a quarter are the long ones.
                return _f(1.0, "nba_long" if n <= 2 else "nba_short", lens,
                                "long timeout" if n <= 2 else "short timeout", True)
            return Forecast(0.8, 140.0, "timeout", True)
        if s == Stoppage.END_PERIOD:
            return _f(1.0, "wcbb_quarter" if lg == League.WCBB else "nba_quarter", lens, "end of the quarter", True)
        return Forecast(0.5, 90.0, "review", True)
    if lg == League.NHL:
        if s == Stoppage.TIMEOUT:
            return _f(1.0, "nhl_tv", lens, "TV timeout", True)
        if s == Stoppage.SCORE_BREAK:
            return _f(0.3, "nhl_goal", lens, "goal (no TV timeout after one)", True)
        return Forecast(0.5, 90.0, "review", True)
    if lg == League.MLB:
        if s == Stoppage.PITCHING_CHANGE:
            return _f(0.7, "mlb_pitching", lens, "pitching change", True)
        if s == Stoppage.END_PERIOD:
            return _f(1.0, "mlb_inning", lens, "between innings", True)
    return Forecast(0.6, 120.0, "stoppage", True)


def forecast(g: GameState | None, clocks: tuple = (), lens: dict = LEN) -> Forecast:
    """Chance a commercial reaches the screen soon, and its expected length.

    clocks: the game clock at each long break this period has already had (BreakBook).
    lens: the length table (LEN, or the measured one-brain table: lengths_from)."""
    if g is None or g.phase != Phase.LIVE:
        return NONE
    n = len(clocks)
    now = _now(g, n, lens)
    if now is not None:
        return now
    lg, base, clk = g.league, g.league.base, g.clock_s
    if lg == League.MLB:
        outs = g.situation.outs or 0
        return _f(0.6, "mlb_inning", lens, "two outs: the next out ends the inning") if outs >= 2 else NONE
    if clk is None:
        return NONE
    p = pro_period(lg, g.period, clk)
    kind = (g.last_play.kind if g.last_play else "").lower()
    if base == League.NFL:
        if p in (1, 3) and clk <= 60:
            return _f(0.8, "nfl_quarter", lens, "the quarter's about to end")
        if p in (2, 4) and 120 < clk <= 165:
            return _f(0.8, "nfl_2min", lens, "two-minute warning coming")
        if kind == "punt":
            return _f(0.75, "nfl_punt", lens, "punt: commercial before the next snap")
        if kind == "turnover":
            return _f(0.5, "nfl_turnover", lens, "turnover: often a commercial")
        sit = g.situation
        if sit.down == 4 and sit.yards_to_goal is not None:
            if sit.yards_to_goal <= 35:
                return _f(0.55, "nfl_score", lens, "4th down in range: a field goal means a break")
            return _f(0.6, "nfl_punt", lens, "4th down: a punt means a break")
        return NONE
    if lg == League.CBB:
        if g.period == 1 and clk <= 60:
            return _f(0.8, "cbb_half", lens, "halftime's coming")
        if (g.period or 0) <= 2 and _owed(clk, CBB_MARKS, clocks):
            return _f(0.85, "cbb_media", lens, "media timeout due at the next whistle")
        return NONE
    if base == League.NBA:
        wom = lg == League.WCBB
        if p in (1, 3) and clk <= 60:
            return _f(0.8, "wcbb_quarter" if wom else "nba_quarter", lens, "the quarter's about to end")
        if p == 2 and clk <= 60:
            return _f(0.8, "wcbb_half" if wom else "nba_half", lens, "halftime's coming")
        if p > 4:
            return NONE
        if wom:
            if clk <= WCBB_MARK and not clocks:
                return _f(0.8, "wcbb_media", lens, "media timeout due at the next whistle")
            return NONE
        if (n == 0 and clk <= NBA_MARKS[0]) or (n <= 1 and clk <= NBA_MARKS[1]):
            return _f(0.7, "nba_long", lens, "mandatory timeout due at the next dead ball")
        return NONE
    if lg == League.NHL:
        if p in (1, 2) and clk <= 90:
            return _f(0.8, "nhl_intermission", lens, "intermission's coming")
        if p > 3 or g.situation.power_play or (p in (1, 2) and clk <= 30) or (p == 3 and clk <= 120):
            return NONE
        if _owed(clk, NHL_MARKS, clocks):
            return _f(0.7, "nhl_tv", lens, "TV timeout due at the next stoppage")
        return NONE
    return NONE


class BreakBook:
    """Per-game memory the rules need: the long breaks this period has already had, and the last
    break the data showed. The screen runs ~25-50s behind the data, so a break that left the data
    (the kickoff after the TD's ads is already logged) is still on its way to the screen: a
    forecast holds for HOLD_S unless a stronger one replaces it."""

    def __init__(self, clock=None, brain=None, table=None):
        """brain: a callable returning (brain, weight) to blend in, or (None, 0) when off.
        table: the one-brain break table (contracts.breaks.BreakTable, flag breaks.one_model); None = LEN."""
        self._clock = clock or (lambda: time.time())
        self._brain = brain
        self.table = table
        self.lens = lengths_from(table)
        self._last: dict[str, tuple[float, Forecast]] = {}
        self._seen: dict[str, set[str]] = {}
        self._period: dict[str, int | None] = {}
        self._clocks: dict[str, tuple] = {}

    def observe(self, g: GameState) -> None:
        if g.phase != Phase.LIVE:
            return
        gid = g.game_id
        if self._period.get(gid, -1) != g.period:
            self._period[gid], self._clocks[gid] = g.period, ()
        seen = self._seen.setdefault(gid, set())
        for pl in g.recent_plays or ([g.last_play] if g.last_play else []):
            if pl is None or pl.id in seen:
                continue
            seen.add(pl.id)
            if _long_break_play(g.league, pl.kind, pl.text):
                self._clocks[gid] = (*self._clocks[gid], g.clock_s)

    def clocks(self, gid: str, period: int | None) -> tuple:
        return self._clocks.get(gid, ()) if self._period.get(gid, -1) == period else ()

    def forecast(self, g: GameState | None) -> Forecast:
        if g is None:
            return NONE
        f, now = forecast(g, self.clocks(g.game_id, g.period), self.lens), self._clock()
        if self._brain is not None and g.phase == Phase.LIVE:
            got = self._brain()
            b, w = got[0], got[1]
            lengths = _league_in(got[2], g) if len(got) > 2 else True
            if b is not None and (not f.now or lengths):
                r = b.read(g)
                f = blend(f, r.break_soon_p, g, w, r.break_reasons, v3=r.v3, lengths=lengths)
        held = self._last.get(g.game_id)
        if held is not None and now - held[0] < HOLD_S and held[1].p > f.p and g.phase == Phase.LIVE:
            return held[1]
        if f.p > 0:
            self._last[g.game_id] = (now, f)
        return f
