"""Excitement score, 0-100, with a plain-English reason.

    final = min(100, neutral_cap * min(100, M * S) + P)

M = live moment (0-100), S = stakes (0.8-1.2), P = personal boosts (capped).
A neutral game tops out at 90; only games you care about reach 90-100.

Every input is optional. Missing data lowers confidence, never crashes.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from redzone.models import PERIOD_S, REAL_BREAKS, REGULATION, ByLeague, GameState, League, Phase, Stoppage, pro_period
from redzone import crunch
from redzone.scoring import garbage, postseason
from redzone.scoring.brain import Brain
from redzone.reasoner import brief as rbrief
from redzone.reasoner.reasoner import Reasoner, lock_points

# --- closeness: how competitive the score is, 0..1 --------------------------

_K = ByLeague({
    # (max deficit, late value, early value)
    League.NFL: [(3, 1.0, 1.0), (8, 0.9, 0.9), (16, 0.6, 0.85), (24, 0.3, 0.6), (999, 0.1, 0.1)],
    League.NBA: [(3, 1.0, 1.0), (5, 0.95, 0.95), (8, 0.8, 0.8), (12, 0.55, 0.85), (20, 0.3, 0.65), (999, 0.05, 0.05)],
    League.NHL: [(0, 1.0, 1.0), (1, 0.9, 0.9), (2, 0.5, 0.8), (999, 0.15, 0.5)],
    League.MLB: [(1, 1.0, 1.0), (2, 0.85, 0.85), (3, 0.65, 0.65), (5, 0.35, 0.35), (999, 0.1, 0.1)],
})


def _q(g: GameState) -> int:
    """Period in pro terms (men's college halves read as NBA quarters)."""
    return pro_period(g.league, g.period, g.clock_s)


def is_late(g: GameState) -> bool:
    p = _q(g)
    return {League.NFL: p >= 4, League.NBA: p >= 4, League.NHL: p >= 3, League.MLB: p >= 7}[g.league.base]


def closeness(g: GameState) -> float:
    d = g.score_diff
    if d is None:
        return 0.7
    late = is_late(g)
    for limit, late_v, early_v in _K[g.league]:
        if abs(d) <= limit:
            return late_v if late else early_v
    return 0.1


ONE_SCORE = {League.NFL: crunch.ONE_SCORE_NFL}


def crunch_time(g: GameState) -> bool:
    """Crunch time, stoppages included — flagged a little BEFORE the graded window so the decider
    can get there at a stoppage before the first crunch play (basketball and hockey only have
    clean cuts at whistles, so arriving late means missing possessions):
      NFL  final 2:00 of a one-score game, or OT (the two-minute warning is the way in)
      NBA  final 3:00 within 7, or OT          (graded: final 2:00 within 5)
      NHL  final 3:00 of the 3rd within 1, or OT / shootout
      MLB  8th inning on, within 2 runs (extra innings always)"""
    if g.phase != Phase.LIVE:
        return False
    if g.stoppage == Stoppage.HALFTIME:
        # College halftime can arrive as "period 2, 0:00" (OSU@BYU, Nov 2): never the end of a game.
        return False
    return crunch.inside(crunch.row("run_up", g.league.base), _q(g), g.clock_s, g.score_diff)


# --- per-game memory (runs, WP swings, smoothing) ---------------------------

@dataclass
class History:
    last_home: int | None = None
    last_away: int | None = None
    run_team: str | None = None
    run_pts: int = 0
    run_at: float | None = None
    max_deficit: dict[str, int] = field(default_factory=dict)
    last_wp: float | None = None
    swing_at: float | None = None
    shown: float = 0.0
    shown_at: float = 0.0
    garbage: bool = False
    comeback_at: float | None = None
    leader: int | None = None          # +1 home, -1 away, 0 tied (last seen)
    lead_changes: int = 0              # lead changes + ties-from-behind this game
    lead_change_at: float | None = None
    pp_team: str | None = None         # [nhl chase] the power play we're tracking...
    pp_start_clock: float | None = None  # ...and the game clock (period, seconds left) it started at
    pp_start_period: int | None = None
    imminent_at: float | None = None   # last poll a goal chance was imminent (see nhl_goal_imminent)

    def observe(self, g: GameState, now: float, wp_threshold: float) -> None:
        hs, as_ = g.home.score, g.away.score
        if hs is not None and as_ is not None:
            if self.last_home is not None and self.last_away is not None:
                dh, da = hs - self.last_home, as_ - self.last_away
                if dh > 0 and da == 0:
                    self._run(g.home.abbr, dh, now)
                elif da > 0 and dh == 0:
                    self._run(g.away.abbr, da, now)
                elif dh > 0 and da > 0:
                    self.run_team, self.run_pts = None, 0
            self.last_home, self.last_away = hs, as_
            diff = hs - as_
            lead = (diff > 0) - (diff < 0)
            # A lead change, or the trailing team tying it: both are "anyone's game" moments.
            if self.leader is not None and self.leader != 0 and lead != self.leader:
                self.lead_changes += 1
                self.lead_change_at = now
            self.leader = lead
            if diff < 0:
                self.max_deficit[g.home.abbr] = max(self.max_deficit.get(g.home.abbr, 0), -diff)
            elif diff > 0:
                self.max_deficit[g.away.abbr] = max(self.max_deficit.get(g.away.abbr, 0), diff)
        wp = g.home_win_prob
        if wp is not None:
            if self.last_wp is not None and abs(wp - self.last_wp) >= wp_threshold:
                self.swing_at = now
            self.last_wp = wp

    def _run(self, team: str, pts: int, now: float) -> None:
        if self.run_team == team:
            self.run_pts += pts
        else:
            self.run_team, self.run_pts = team, pts
        self.run_at = now


def _fade(at: float | None, now: float, fade_s: float) -> float:
    if at is None:
        return 0.0
    return max(0.0, 1.0 - (now - at) / fade_s)


# --- result ------------------------------------------------------------------

@dataclass
class Scored:
    game_id: str
    league: League
    score: float            # what the decider uses (smoothed, unrounded)
    raw: float              # this poll's value before smoothing
    moment: float
    stakes: float
    personal: float
    reason: str
    tags: list[str]
    is_break: bool
    lock: str | None        # a moment the decider shouldn't cut out of
    pre_moment: str | None  # a big moment about to start: worth pre-switching to
    closeness: float
    garbage: bool = False   # decided game (bets/fantasy can still keep it interesting)
    late: bool = False      # final period (NFL/NBA Q4+, NHL P3+, MLB 7th+): your team here is never left for a lesser game
    crunch: bool = False    # crunch time (see crunch_time); unlike lock, kept through breaks
    crunch_now: bool = False  # inside the graded crunch window itself (NHL: last 2:00, not the 3:00 run-up)
    imminent: bool = False  # [nhl chase] a goal chance was imminent here within the last ~half minute
    factor: str = ""        # short human reason for the single biggest factor right now
    factors: dict[str, float] = field(default_factory=dict)  # context points by name (debug/UI)
    brain: dict = field(default_factory=dict)  # learned brain's reading (hotness, key/break odds, reasons)
    reasoner: dict = field(default_factory=dict)  # live game reasoner: lock_in, odds, the model's storyline
    # HEAT 2.0 (redzone/contracts/watch.py), behind scoring.watch_value: None = off / no model (use score)
    watch_value: float | None = None   # 0..100 calibrated: 50 = league-median live moment, 90 = top 5%
    watch_why: str = ""                # one line for the UI
    watch_terms: dict = field(default_factory=dict)   # p_big, impact, leverage, stakes_mult, story, garbage
    watch_rising: float | None = None  # d(watch_value)/dt, points per minute
    crunch_state: str | None = None    # learned crunch STATE: "entering" | "in" | "leaving" | None (contracts/watch.py)
    stakes_line: str | None = None     # e.g. "World Series - Game 7 · winner takes it"; None = not computed

    @property
    def display(self) -> int:
        return int(round(self.score))


@dataclass
class Personal:
    """Boost inputs that come from you, not the feeds."""
    my_teams: dict[League, set[str]] = field(default_factory=dict)
    bet_sweat: dict[str, float] = field(default_factory=dict)       # game_id -> 0..1
    bet_labels: dict[str, str] = field(default_factory=dict)
    fantasy: dict[str, tuple[float, str]] = field(default_factory=dict)  # game_id -> (points, label)
    boosted: set[str] = field(default_factory=set)


class Scorer:
    def __init__(self, cfg, clock=time.time, brain=None, reasoner=None):
        self.cfg = cfg
        self._clock = clock
        self.hist: dict[str, History] = {}
        self._live_leagues: dict[str, float] = {}   # league -> last time one of its games was live
        # The learned brain (redzone/scoring/brain.py): read every poll while brain.enabled is on;
        # with it off or a league unmodeled, scores are the hand-written ones, unchanged.
        self.brain = brain if brain is not None else Brain(version=int(cfg.get("brain.version", 3) or 3),
                                                           v3_score=cfg.get("brain.v3_score", None),
                                                           v3_break_odds=cfg.get("brain.v3_break_odds", None))
        # The live game reasoner (redzone/reasoner): bounded lock_in points while reasoner.enabled is on.
        self.reasoner = reasoner if reasoner is not None else Reasoner(cfg, clock=clock)
        self._mine_live: dict[str, float] = {}   # your team's live, not-decided games -> last seen
        self.watch = None                          # HEAT 2.0 WatchValue, loaded on first use (flag on)

    def _watch(self, g: GameState, me: Personal, reading, is_garbage: bool, now: float):
        """HEAT 2.0: the learned watch value. The brain TRACK feeds it (win probability, swings, runs), so it
        is observed here even with the brain's score points off. Stakes are a preference multiplier, never a label."""
        from redzone.noggin.watch import WatchValue
        if self.watch is None:
            self.watch = WatchValue()
        if not self.watch.available(g.league.value):
            return None
        if reading is None:
            self.brain.read(g)          # keeps the tracker current; its score points stay off
        tr = self.brain._track.get(g.game_id)
        ctx = rbrief.Context(mine=g.involves(set(me.my_teams.get(g.league, set()))), garbage=is_garbage,
                             crunch=crunch_time(g), late=is_late(g))
        f = rbrief.facts(g, tr.row if tr is not None else None, None, ctx)
        c = self.cfg
        mult = 1.0
        if ctx.mine:
            mult *= float(c.get("scoring.watch_stakes.your_team", 1.25))
        if me.bet_sweat.get(g.game_id):
            mult *= 1.0 + float(c.get("scoring.watch_stakes.bet", 0.15)) * float(me.bet_sweat[g.game_id])
        if g.game_id in me.fantasy:
            mult *= float(c.get("scoring.watch_stakes.fantasy", 1.1))
        mult *= 1.0 + postseason.stakes_bonus(g, c)      # #163: elimination / decider / round (0 unless its flag)
        return self.watch.read(g.game_id, g.league.value, f, now, mult)

    def brain_on(self) -> bool:
        return bool(self.cfg.get("brain.enabled", False))

    def score(self, g: GameState, me: Personal | None = None) -> Scored:
        me = me or Personal()
        now = self._clock()
        c = self.cfg
        h = self.hist.setdefault(g.game_id, History())
        h.observe(g, now, c.get("score.nfl.wp_swing.threshold", 0.15))

        k = closeness(g)
        is_break = g.stoppage in REAL_BREAKS
        if g.phase != Phase.LIVE:
            return Scored(g.game_id, g.league, 0.0, 0.0, 0.0, 1.0, 0.0,
                          _phase_reason(g), [], True, None, None, k)

        fn = {League.NFL: _nfl, League.NBA: _nba, League.NHL: _nhl, League.MLB: _mlb}[g.league.base]
        m, parts, lock, pre = fn(g, h, k, now, c)
        if g.league.base == League.NHL and nhl_goal_imminent(g, h, c):
            h.imminent_at = now
        ctx, ctx_parts, factors = context(g, h, k, now, c)
        m += ctx
        parts.extend(ctx_parts)
        reading = self.brain.read(g) if self.brain_on() else None
        if reading is not None and reading.hotness is not None:
            # College leagues get their own settings when set (never silently the pro league's).
            key = g.league.value if c.get(f"brain.{g.league.value}") is not None else g.league.base.value
            pts, part = brain_points(reading, c, key)
            m += pts
            factors["brain"] = round(pts, 1)
            if part:
                parts.append(part)
        rr = None
        r_pts = 0.0
        if self.reasoner is not None and self.reasoner.on():
            tr = self.brain._track.get(g.game_id)
            mine = g.involves(set(me.my_teams.get(g.league, set())))
            rctx = rbrief.Context(mine=mine, garbage=garbage.is_garbage(g, h.garbage), crunch=crunch_time(g),
                                  late=is_late(g), bet=me.bet_labels.get(g.game_id, ""))
            rr = self.reasoner.read(g, tr.row if tr is not None else None, reading, rctx)
            key = g.league.value if c.get(f"reasoner.{g.league.value}") is not None else g.league.base.value
            pts, part = lock_points(rr, c, key)
            if is_break and not c.get("reasoner.during_breaks", False):
                pts, part = 0.0, None   # a break is the break logic's call: points here only buy commercials
            if mine and not rctx.garbage:
                self._mine_live[g.game_id] = now
                if len(self._mine_live) > 50:
                    self._mine_live = {k: t for k, t in self._mine_live.items() if now - t <= self.MINE_LIVE_S}
            elif pts and c.get("reasoner.yield_to_your_team", True) and any(
                    now - t <= self.MINE_LIVE_S for k, t in self._mine_live.items() if k != g.game_id):
                pts, part = 0.0, None   # your team is on: a reasoner call never pulls you off it
            if pts:
                r_pts = pts             # added after smoothing (below): never lingers into a break
                factors["reasoner"] = round(pts, 1)
                if part:
                    parts.append(part)
        # Garbage time: decided games stop competing for the TV — your team's included.
        was = h.garbage
        h.garbage = garbage.is_garbage(g, was)
        if was and not h.garbage and g.phase == Phase.LIVE:
            h.comeback_at = now  # it got close again: that's a story worth seeing
        if h.garbage:
            m *= c.get("score.garbage.moment_mult", 0.12)
            lock = pre = None
        comeback = _fade(h.comeback_at, now, c.get("score.garbage.comeback_s", 240)) * \
            c.get("score.garbage.comeback_boost", 12)
        if comeback > 0:
            m += comeback
            parts.append((comeback + 25, "comeback — it's close again"))
        m = max(0.0, min(100.0, m))
        s = self._stakes(g)
        p, tags = self._personal(g, me, k, h.garbage)

        lw = self._league_weight(g.league, now)
        factors["league"] = lw
        raw = min(100.0, c.get("score.neutral_cap", 0.9) * min(100.0, m * s * lw) + p)
        # Fast attack, slow release: jump up instantly, drift down with a half-life.
        hl = c.get("score.smoothing_half_life_s", 30)
        decayed = h.shown * 0.5 ** ((now - h.shown_at) / hl) if h.shown_at else 0.0
        shown = max(raw, decayed)
        h.shown, h.shown_at = shown, now
        if r_pts:
            # lock_in points ride on top of the smoothed score, unsmoothed: they're there while the
            # reasoner says so and gone the poll it stops (a break, garbage time, the moment passed)
            shown = min(100.0, shown + r_pts)
            raw = min(100.0, raw + r_pts)

        reason = _reason(g, parts, is_break)
        factor = _top_factor(parts)
        if h.garbage:
            reason = f"garbage time — {reason}"
            tags = tags + ["garbage time"]
        wr = self._watch(g, me, reading, h.garbage, now) if c.get("scoring.watch_value", False) else None
        return Scored(g.game_id, g.league, shown, raw, m, s, p, reason, tags,
                      is_break, None if is_break else lock, pre, k, garbage=h.garbage, crunch=crunch_time(g),
                      watch_value=wr.watch_value if wr else None, watch_why=wr.watch_why if wr else "",
                      watch_terms=dict(wr.watch_terms) if wr else {}, watch_rising=wr.watch_rising if wr else None,
                      crunch_state=wr.crunch_state if wr else None,
                      stakes_line=(postseason.reason(g) or None) if wr and postseason.enabled(c) else None,
                      late=is_late(g),
                      factor="garbage time" if h.garbage else factor, factors=factors,
                      crunch_now=crunch_window(g),
                      imminent=h.imminent_at is not None
                      and now - h.imminent_at <= c.get("score.nhl.imminent_hold_s", -1),
                      brain=reading.as_dict() if reading is not None and reading.hotness is not None else {},
                      reasoner=rr.as_dict() if rr is not None and rr.p is not None else {},
                      stakes_line=postseason.reason(g) or None)   # [#163] the phone's stakes line (display only)

    MINE_LIVE_S = 60.0      # your team's game counts as on this long after its last live, close poll
    LEAGUE_LIVE_S = 300.0   # a league counts as "on tonight" this long after its last live game

    def _league_weight(self, league: League, now: float) -> float:
        """Your league order, relative to the other leagues live right now. The best league on is
        1.0, so a night of one sport is scored exactly as without an order; a lower league only
        gives way when a league you like more is actually on."""
        self._live_leagues[league.value] = now
        weights = self.cfg.get("score.league_weight") or {}

        def w(name: str) -> float:
            try:
                return max(0.5, min(1.5, float(weights.get(name, 1.0))))
            except (TypeError, ValueError):
                return 1.0
        on = [n for n, t in self._live_leagues.items() if now - t <= self.LEAGUE_LIVE_S]
        top = max((w(n) for n in on), default=w(league.value))
        return w(league.value) / top if top > 0 else 1.0

    def _stakes(self, g: GameState) -> float:
        c = self.cfg
        s = 1.0
        hp, ap = g.home.win_pct, g.away.win_pct
        if hp is not None and ap is not None:
            if hp >= c.get("score.stakes.both_good_pct") and ap >= c.get("score.stakes.both_good_pct"):
                s += c.get("score.stakes.both_good")
            if hp <= c.get("score.stakes.both_bad_pct") and ap <= c.get("score.stakes.both_bad_pct"):
                s += c.get("score.stakes.both_bad")
        if g.spread is not None:
            if abs(g.spread) <= c.get("score.stakes.close_spread"):
                s += c.get("score.stakes.close_spread_bonus")
            elif abs(g.spread) >= c.get("score.stakes.big_spread"):
                s += c.get("score.stakes.big_spread_penalty")
        if g.national_tv:
            s += c.get("score.stakes.national_tv", 0)
        s += postseason.stakes_bonus(g, c)   # [#163 playoff stakes] 0 unless score.postseason.enabled
        return max(c.get("score.stakes.min"), min(c.get("score.stakes.max"), s))

    def _personal(self, g: GameState, me: Personal, k: float, garbage_time: bool = False) -> tuple[float, list[str]]:
        c = self.cfg
        p, tags = 0.0, []
        mine = me.my_teams.get(g.league, set())
        if g.involves(mine):
            # Your team still gets the tag, but no boost once the game is decided.
            rank = float(c.get(f"score.personal.my_team_league.{g.league.value}", 1.0))
            p += 0.0 if garbage_time else c.get("score.personal.my_team") * rank * k
            tags.append(next(t for t in (g.home.abbr, g.away.abbr) if t in mine))
        sweat = me.bet_sweat.get(g.game_id, 0.0)
        if sweat > 0:
            p += c.get("score.personal.bet_max") * max(0.0, min(1.0, sweat))
            tags.append(me.bet_labels.get(g.game_id, "your bet"))
        if g.game_id in me.fantasy:
            pts, label = me.fantasy[g.game_id]
            p += pts
            tags.append(label)
        if g.game_id in me.boosted:
            p += c.get("score.personal.manual_boost")
            tags.append("boosted")
        return min(p, c.get("score.personal_cap")), tags


# --- cross-league context: more variables, all from data the feeds already carry -------------
# Each term is small, only fires when the game is competitive (k), and is scaled by how late it
# is, so it reorders close games without ever lifting a blowout. Garbage time still multiplies
# the whole moment down afterwards.

_LATE_FRAC = REGULATION


def game_progress(g: GameState) -> float:
    """0 at kickoff/tip/puck drop/first pitch, 1 at the end of regulation (OT stays 1)."""
    p = g.period or 0
    reg = _LATE_FRAC[g.league]
    if p <= 0:
        return 0.0
    if p > reg:
        return 1.0
    if g.league == League.MLB:
        return min(1.0, (p - 1 + (0.5 if g.half == "bottom" else 0.0)) / reg)
    length = PERIOD_S[g.league]
    clk = g.clock_s if g.clock_s is not None else length / 2
    return min(1.0, max(0.0, (p - 1 + (1 - min(clk, length) / length)) / reg))


def context(g: GameState, h: History, k: float, now: float, c) -> tuple[float, list, dict]:
    """Extra moment points shared by every league: (points, reason parts, factors by name)."""
    f: dict[str, float] = {}
    prog = game_progress(g)
    late = prog ** 2  # most of the weight in the last third
    lg = g.league.base.value.lower() if hasattr(g.league, "value") else str(g.league).lower()

    # 1. Win-probability leverage: a coin flip late is worth more than a coin flip early.
    #    (NFL/NBA already have an uncertainty term, so NHL/MLB get the bigger share.)
    wp = g.home_win_prob
    if wp is not None:
        coin = 1 - abs(2 * wp - 1)
        f["wp_leverage"] = c.get(f"score.context.{lg}.wp_leverage",
                                 {"nfl": 0, "nba": 0, "nhl": 15, "mlb": 6}.get(lg, 4)) * coin * late

    # 2. Lead changes / ties: a back-and-forth game is more watchable, and one that JUST flipped
    #    is the freshest story on the board.
    if h.lead_changes:
        f["lead_changes"] = min(c.get("score.context.lead_change_cap", 8),
                                c.get(f"score.context.{lg}.lead_change_each", {"nhl": 2.0}.get(lg, 0)) * h.lead_changes) * k
    flip = _fade(h.lead_change_at, now, c.get("score.context.lead_change_fade_s", 150))
    if flip > 0:
        f["fresh_flip"] = c.get("score.context.fresh_flip", 0) * flip * (0.4 + 0.6 * late) * k

    # 3. Upset brewing: the pregame underdog is leading (or tied) in the second half.
    d = g.score_diff
    if g.spread is not None and d is not None and abs(g.spread) >= c.get("score.context.upset_min_spread", 3) \
            and prog >= 0.5:
        dog_home = g.spread > 0  # home spread positive = home is the underdog
        dog_margin = d if dog_home else -d
        if dog_margin >= 0:
            size = min(1.0, abs(g.spread) / c.get("score.context.upset_full_spread", 10))
            f["upset"] = c.get(f"score.context.{lg}.upset", {"nfl": 0}.get(lg, 8)) * size * (0.5 + 0.5 * late) * k

    # 4. Late one-score windows the base clock buckets don't single out: NHL within a goal in the
    #    last 5:00 of the 3rd (the extra-attacker window), NBA within 6 under 3:00.
    p, clk = _q(g), g.clock_s
    if d is not None and clk is not None:
        if g.league == League.NHL and p == 3 and clk <= 300 and abs(d) <= 1:
            f["late_one_score"] = c.get("score.context.nhl.late_one_goal", 10)
        elif g.league.base == League.NBA and p == 4 and clk <= 180 and abs(d) <= 6:
            f["late_one_score"] = c.get("score.context.nba.late_within_6", 4)

    # 5. Overtime / extras: sudden death or close to it, every league.
    reg = _LATE_FRAC[g.league]
    if (g.period or 0) > reg:
        f["overtime"] = c.get("score.context.overtime", 4)

    pts = sum(f.values())
    parts = []
    if f.get("fresh_flip", 0) >= 3:
        parts.append((f["fresh_flip"] + 18, "lead just changed hands"))
    elif f.get("lead_changes", 0) >= 4.5:
        parts.append((f["lead_changes"] + 6, f"{h.lead_changes} lead changes"))
    if f.get("upset", 0) >= 3:
        dog = g.home.abbr if g.spread > 0 else g.away.abbr
        parts.append((f["upset"] + 12, f"upset brewing: {dog} (+{abs(g.spread):g}) "
                                       f"{'tied' if d == 0 else 'leading'}"))
    return pts, parts, {n: round(v, 1) for n, v in f.items() if v}


def brain_points(r, c, league: str = "") -> tuple[float, tuple[float, str] | None]:
    """Moment points from the learned brain: game flow above/below a median moment, plus how much
    likelier than usual (for this league) a key moment is in the next few minutes.
    (points, reason part or None)"""
    def knob(name: str, default: float) -> float:   # brain.<league>.<name> beats brain.<name>
        v = c.get(f"brain.{league}.{name}") if league else None
        return float(v if v is not None else c.get(f"brain.{name}", default))

    hot = (r.hotness - 50.0) / 50.0 * knob("hot_points", 0)
    base = r.key_rate if r.key_rate is not None else 0.3
    lift = max(0.0, ((r.key_soon_p or 0.0) - base) / max(1e-6, 1 - base))
    key = lift * knob("key_points", 6)
    pts = hot + key
    part = None
    if r.hotness >= c.get("brain.hot_reason_min", 85):
        why = f": {r.reasons[0]}" if r.reasons else ""
        part = (max(pts, 0) + 6, f"game flow {round(r.hotness)}/100{why}")
    elif key >= 3:
        part = (key, "big moment likely soon")   # ranks below the specific reasons (upset, run, ...)
    return pts, part


def _top_factor(parts: list[tuple[float, str]]) -> str:
    """The single heaviest phrase (the situation line is parts[0] and is usually not a 'why')."""
    rest = [p for p in parts[1:] if p[1]]
    if not rest:
        return parts[0][1] if parts else ""
    return max(rest, key=lambda p: p[0])[1]


# --- league moment models ------------------------------------------------------
# Each returns (M, parts, lock, pre_moment). parts = [(points, phrase)] for the reason.

def _clock_str(s: float | None) -> str:
    if s is None:
        return ""
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


def _period_name(g: GameState, q: int) -> str:
    if q >= 5:
        return "OT"
    if REGULATION[g.league] == 2:
        return f"H{g.period or 1}"
    return f"Q{q}"


def _nfl(g, h, k, now, c):
    q = g.period or 0
    clk = g.clock_s
    w = c.get("score.nfl.time")
    if q >= 5:
        t = w["ot"]
    elif q == 4:
        t = w["q4_early"] if (clk is None or clk > 480) else w["q4_mid"] if clk > 120 else w["q4_late"]
    elif q == 3:
        t = w["q3"]
    elif q == 2:
        t = w["q2_two_min"] if clk is not None and clk <= 120 else w["q2"]
    else:
        t = w["q1"]

    sit = g.situation
    fw = c.get("score.nfl.field")
    f = 0.0
    ytg = sit.yards_to_goal
    if ytg is not None and sit.possession:
        f = (fw["own"] if ytg > 50 else fw["opp_mid"] if ytg > 35 else fw["opp_deep"] if ytg > 20
             else fw["red_zone"] if ytg > 10 else fw["inside_10"])
        if sit.goal_to_go:
            f += fw["goal_to_go"]

    # Score from the offense's point of view.
    off_diff = None
    if g.score_diff is not None and sit.possession:
        off_diff = g.score_diff if sit.possession == g.home.abbr else -g.score_diff

    dw = c.get("score.nfl.down")
    d = 0.0
    if sit.down == 3 and sit.distance is not None and sit.distance <= 3:
        d = dw["third_short"]
    elif sit.down == 4:
        go = (ytg is not None and ytg < 50 and (sit.distance or 99) <= 2) or (
            q >= 4 and off_diff is not None and off_diff < 0)
        if go:
            d = dw["fourth_go"]

    drill = 0.0
    if crunch.trailing_drill(q, clk, off_diff):
        drill = c.get("score.nfl.two_minute_drill")

    wp = g.home_win_prob
    u_scale = 1.0 if q >= 4 else 0.5
    u = c.get("score.nfl.uncertainty") * (1 - abs(2 * wp - 1) if wp is not None else k * 0.6) * u_scale
    sw = c.get("score.nfl.wp_swing")
    wsw = sw["points"] * _fade(h.swing_at, now, sw["fade_s"])
    gp = c.get("score.nfl.player_going_off") if g.player_flags else 0.0

    m = k * (t + f + d + drill) + u + wsw + gp

    parts = []
    if sit.down and sit.possession and ytg is not None:
        if sit.goal_to_go:
            spot = f"{_ordinal(sit.down)} & goal at the {ytg}"
        else:
            where = f"opp {ytg}" if ytg < 50 else ("midfield" if ytg == 50 else f"own {100 - ytg}")
            spot = f"{_ordinal(sit.down)} & {sit.distance} at {where}"
        parts.append((f + d + 1, f"{sit.possession} {spot}"))
    if off_diff is not None:
        parts.append((t, _margin_phrase(off_diff, "")))
    elif g.score_diff is not None:
        parts.append((t, _lead_phrase(g)))
    parts.append((t, f"{_clock_str(clk)} left in {'OT' if q >= 5 else f'Q{q}'}" if clk is not None else f"Q{q}"))
    if drill:
        parts.append((drill + 20, "two-minute drill"))
    if wsw > 5:
        parts.append((wsw, "big momentum swing"))
    for flag in g.player_flags[:1]:
        parts.append((gp, flag))

    lock = pre = None
    if sit.possession and ytg is not None and ytg <= 20 and k >= 0.6:
        lock = "red zone drive"
    if q >= 4 and clk is not None and clk <= 120 and k >= 0.9:
        lock = "final two minutes"
    if sit.down == 4 and d:
        pre = "4th down"
    if sit.goal_to_go and sit.down in (3, 4):
        pre = f"{_ordinal(sit.down)} & goal"
    if q >= 4 and clk is not None and clk <= 60 and k >= 0.9:
        pre = "final minute, one score"
    if q >= 4 and g.stoppage == Stoppage.TWO_MINUTE_WARNING and k >= 0.9:
        pre = "two-minute warning, one score"  # the last natural stoppage before crunch: get there now
    return m, parts, lock, pre


def _nba(g, h, k, now, c):
    q = _q(g)
    clk = g.clock_s
    w = c.get("score.nba.time")
    if q >= 5:
        t = w["ot"]
    elif q == 4:
        t = (w["q4"] if clk is None or clk > 300 else w["q4_5min"] if clk > 120
             else w["q4_2min"] if clk > 30 else w["q4_30s"])
    elif q == 3:
        t = w["q3"]
    else:
        t = w["early"]
    diff = g.score_diff
    last = c.get("score.nba.last_shot") if (q >= 4 and clk is not None and clk <= 24
                                            and diff is not None and abs(diff) <= 3) else 0.0
    run = 0.0
    if h.run_pts >= 14:
        run = c.get("score.nba.run_14")
    elif h.run_pts >= 10:
        run = c.get("score.nba.run_10")
    run *= _fade(h.run_at, now, c.get("score.nba.run_fade_s"))
    comeback = 0.0
    comeback_team = None
    if q >= 4 and diff is not None and abs(diff) <= 5:
        trailing = g.home.abbr if diff < 0 else g.away.abbr if diff > 0 else None
        for team, deficit in h.max_deficit.items():
            if deficit >= 10 and (trailing is None or team == trailing):
                comeback, comeback_team = c.get("score.nba.comeback"), team
    wp = g.home_win_prob
    u = c.get("score.nba.uncertainty") * ((1 - abs(2 * wp - 1)) if wp is not None else k * 0.6) * (
        1.0 if q >= 4 else 0.5)
    gp = c.get("score.nba.player_going_off") if g.player_flags else 0.0
    m = k * (t + last) + run + comeback + u + gp

    parts = [(t, _lead_phrase(g)),
             (t, f"{_clock_str(clk)} left in {_period_name(g, q)}" if clk is not None else _period_name(g, q))]
    if run:
        parts.append((run + 10, f"{h.run_team} on a {h.run_pts}-0 run"))
    if comeback:
        parts.append((comeback + 10, f"{comeback_team} came back from {h.max_deficit[comeback_team]} down"))
    if last:
        parts.append((last + 30, "last-shot possession"))
    for flag in g.player_flags[:1]:
        parts.append((gp + 5, flag))
    lock = "crunch time" if q >= 4 and clk is not None and clk <= 120 and k >= 0.95 else None
    pre = "final minute, one possession" if q >= 4 and clk is not None and clk <= 60 and diff is not None and abs(diff) <= 3 else None
    return m, parts, lock, pre


def _nhl(g, h, k, now, c):
    p = g.period or 0
    clk = g.clock_s
    w = c.get("score.nhl.time")
    if g.period_type == "SO":
        t = w["shootout"]
    elif p >= 4:
        t = w["ot"]
    elif p == 3:
        t = (w["p3_early"] if clk is None or clk > 600 else w["p3_mid"] if clk > 300
             else w["p3_5min"] if clk > 120 else w["p3_2min"])
    elif p == 2:
        t = w["p2"]
    else:
        t = w["p1"]
    sit = g.situation
    diff = g.score_diff
    pp = 0.0
    if sit.power_play:
        pp = c.get("score.nhl.power_play")
        if p == 3 and diff is not None and abs(diff) <= 1:
            pp = c.get("score.nhl.power_play_p3_close")
        if sit.strength in ("5v3", "3v5"):
            pp = max(pp, c.get("score.nhl.five_on_three"))
    en = c.get("score.nhl.empty_net") if sit.empty_net else 0.0
    m = k * t + pp + en + (8.0 if g.player_flags else 0.0)

    if g.period_type == "SO":
        when = "shootout"
    elif p >= 4:
        when = f"{_clock_str(clk)} left in OT" if clk is not None else "OT"
    else:
        when = f"{_clock_str(clk)} left in P{p}" if clk is not None else f"P{p}"
    parts = [(t, _lead_phrase(g)), (t, when)]
    if sit.power_play:
        parts.append((pp + 20, f"{sit.power_play} power play" + (" (5-on-3)" if sit.strength in ("5v3", "3v5") else "")))
    if sit.empty_net:
        parts.append((en + 20, f"{sit.empty_net} pulled the goalie"))
    for flag in g.player_flags[:1]:
        parts.append((10, flag))
    lock = None
    if g.period_type == "SO" or p >= 4:
        lock = "overtime"      # [nhl pp] sudden death: only crunch time elsewhere takes the TV (decider)
    elif sit.empty_net:
        lock = "empty net"
    elif sit.power_play and diff is not None and abs(diff) <= c.get("score.nhl.power_play_lock_within", 2):
        lock = "power play"    # [nhl pp] within 2 goals; garbage time clears it (score())
    pre = "power play" if sit.power_play else ("empty net" if sit.empty_net else None)
    return m, parts, lock, pre


def crunch_window(g: GameState) -> bool:
    """The graded crunch window. Same as crunch_time except NHL, whose flag goes up at 3:00 so we
    can get there at a stoppage: inside 2:00 (or OT) every second counts, before it a whistle is
    worth waiting for on the way in."""
    return crunch_time(g) and crunch.inside(crunch.row("window", g.league.base), _q(g), g.clock_s, g.score_diff)


def nhl_goal_imminent(g, h: History, c) -> bool:
    """[nhl chase] Is a goal chance imminent right now? A 5-on-3, the first 30 s (game clock) of
    a power play, an empty net in the last 2:00, or a penalty shot. Only then may a chase into
    hockey skip the whistle hold on the way in: anywhere else, landing mid-play costs more than
    the goal it might catch (PR #3 review: mid-play entries 11 -> 14)."""
    sit = g.situation
    p, clk = g.period or 0, g.clock_s
    if sit.power_play != h.pp_team:
        h.pp_team = sit.power_play
        h.pp_start_period, h.pp_start_clock = (p, clk) if sit.power_play else (None, None)
    if sit.power_play and sit.strength in ("5v3", "3v5"):
        return True
    if sit.power_play and h.pp_start_clock is not None and clk is not None and h.pp_start_period == p \
            and h.pp_start_clock - clk <= c.get("score.nhl.imminent_pp_s", 30):
        return True
    if sit.empty_net and p >= 3 and clk is not None and clk <= 120:
        return True
    return any((x.kind or "").startswith("ps-") for x in (g.recent_plays or [])[-3:])


def estimate_li(inning: int, diff_for_batting: int, outs: int, runners: tuple[bool, bool, bool]) -> float:
    """Crude leverage index when the feed doesn't give one. ~1.0 is average."""
    base = 0.9 + 0.2 * runners[0] + 0.45 * runners[1] + 0.45 * runners[2]
    ad = abs(diff_for_batting)
    close = 1.7 if ad <= 1 else 1.2 if ad == 2 else 0.9 if ad == 3 else 0.4 if ad <= 5 else 0.15
    late = 1 + max(0, inning - 5) * 0.25
    outs_f = 1.1 if outs == 2 else 1.0
    return min(6.0, base * close * late * outs_f)


def _mlb(g, h, k, now, c):
    inning = g.period or 1
    sit = g.situation
    runners = sit.runners or (False, False, False)
    outs = sit.outs if sit.outs is not None else 0
    diff = g.score_diff or 0
    batting_home = g.half == "bottom"
    diff_bat = diff if batting_home else -diff
    li = sit.leverage_index
    if li is None:
        li = estimate_li(inning, diff_bat, outs, runners)
    im = c.get("score.mlb.inning_mult")
    mult = im["early"] if inning <= 5 else im["mid"] if inning <= 7 else im["eighth"] if inning == 8 else im["ninth_plus"]
    base = mult * 100 * (1 - math.exp(-li / c.get("score.mlb.leverage_scale")))
    on = sum(runners)
    walkoff = c.get("score.mlb.walk_off_spot") if (
        batting_home and inning >= 9 and diff <= 0 and -diff <= on + 1) else 0.0
    nh = 0.0
    nhw = c.get("score.mlb.no_hitter")
    if sit.no_hitter_for:
        nh = nhw["ninth"] if inning >= 9 else nhw["through_7"] if inning >= 8 else nhw["through_6"] if inning >= 7 else 0.0
    m = base + walkoff + nh + (10.0 if g.player_flags else 0.0)

    half = "Bot" if batting_home else "Top"
    bases = _bases_phrase(runners)
    parts = [(base, _lead_phrase(g)), (base, f"{half} {_ordinal(inning)}, {outs} out" + (f", {bases}" if bases else ""))]
    if walkoff:
        parts.append((walkoff + 40, "walk-off spot"))
    if nh:
        parts.append((nh + 30, f"{sit.no_hitter_for} no-hitter watch"))
    for flag in g.player_flags[:1]:
        parts.append((15, flag))
    inning_over = outs >= 3  # the half-inning is done: nothing to lock onto until the next one
    lock = "high-leverage at-bat" if li >= 2.0 and on >= 1 and not inning_over else None
    pre = "bases loaded, late" if on == 3 and inning >= 7 and li >= 2.5 else None
    if walkoff and not inning_over:
        pre = "walk-off spot"
    if inning_over:
        pre = None
    return m, parts, lock, pre


# --- reason text -------------------------------------------------------------

def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def _bases_phrase(r: tuple[bool, bool, bool]) -> str:
    if all(r):
        return "bases loaded"
    names = [n for on, n in zip(r, ("1st", "2nd", "3rd")) if on]
    return ("runners on " + " & ".join(names)) if len(names) > 1 else (f"runner on {names[0]}" if names else "")


def _margin_phrase(diff: int, who: str) -> str:
    if diff == 0:
        return "tied"
    return f"{who}{'up' if diff > 0 else 'down'} {abs(diff)}".strip()


def _lead_phrase(g: GameState) -> str:
    d = g.score_diff
    if d is None:
        return g.matchup
    if d == 0:
        return f"tied {g.home.score}-{g.away.score}"
    leader, trailer = (g.home, g.away) if d > 0 else (g.away, g.home)
    return f"{leader.abbr} up {abs(d)} ({leader.score}-{trailer.score})"


def _phase_reason(g: GameState) -> str:
    return {Phase.PRE: "not started", Phase.POST: "final"}.get(g.phase, "status unknown")


_BREAK_TEXT = {
    Stoppage.TIMEOUT: "timeout", Stoppage.TWO_MINUTE_WARNING: "two-minute warning",
    Stoppage.END_PERIOD: "break between periods", Stoppage.HALFTIME: "halftime",
    Stoppage.REVIEW: "under review", Stoppage.INJURY: "injury stoppage",
    Stoppage.PITCHING_CHANGE: "pitching change", Stoppage.SCORE_BREAK: "break after a score",
    Stoppage.WHISTLE: "whistle", Stoppage.INTERMISSION: "intermission",
}


def _reason(g: GameState, parts: list[tuple[float, str]], is_break: bool) -> str:
    # Situation first (the most specific phrase), then the two most important others.
    seen, out = set(), []
    if parts:
        first = parts[0]
        rest = sorted(parts[1:], key=lambda p: -p[0])
        for _, text in [first] + rest:
            if text and text not in seen:
                seen.add(text)
                out.append(text)
            if len(out) == 3:
                break
    text = " · ".join(out)
    if is_break:
        text = f"{_BREAK_TEXT.get(g.stoppage, 'break')} — {text}"
    return text
