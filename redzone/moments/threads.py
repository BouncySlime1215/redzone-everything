"""The thread ledger: each live game's one open question, when it should resolve, and how a hold or a cut reads it.

Why: Nick's "our edge... the most invested part" (D-ADDICT, docs/agents/context/2026-11-09-addicting-show.md sections 3, 4
and 11, mechanic M1). Today's show paces cuts and tells no story; a cut is good when it closes a question or opens a
better one, so the show needs to know which question each game is asking. This file only keeps the ledger and answers
two questions: should this optional cut wait, and how much is this game's about-to-close thread worth. The chain in
`redzone/noggin/show.py` asks them; Rule #1, the landing rules and the arbiter's floor are never read or changed here.

    ledger = Ledger()
    ledger.tick(states, on_screen=gid, now=t, params=p, air_lag_s=35.0)     # every decider tick
    closure_bonus(ledger, gid, now=t, params=p)                             # points for a thread about to close
    hold_for(ledger, gid, league, now=t, facts=HoldFacts(...), params=p)    # the thread's kind, or None: don't hold

What a thread is, per sport (play-by-play state only):
    NFL, CFB   rz_trip, fourth_down, two_minute, go_ahead
    NBA, CBB   last_possessions, tying_shot, close_run (ledger only: never holds, never ranks)
    NHL        power_play, extra_attacker
    MLB        risp_late, save_situation

Limits this file keeps (decisions.md, Nov 4 and 2026-11-09; report sections 7 and 8):
  - a run is a fact, not a forecast: `close_run` is tracked and worth nothing to the hold or the cut
  - no bets, odds, spreads, fantasy, streaks, session length or time of night go in here (the guard test scans this file)
  - nothing is delayed, teased or invented: the hold only keeps a live game that is already on screen
  - a thread cannot hold past its sport's reach, and a chain of threads cannot hold past twice that
  - a game with a thread never outranks a must-show or a crunch game: the bonus is capped and the caller checks those
  - past the viewer's own chosen end (The Close), the ledger keeps its counts and everything that acts stops

Pure: reads the GameState it is handed; no clock, no I/O, no decider import. Flags `threads.enabled` (ledger) and
`threads.closure_first` (holds and bonus), both code default OFF. Off, the decider never calls this file's logic.
"""
from __future__ import annotations

import dataclasses as dc
from collections import Counter, deque
from collections.abc import Callable, Mapping

from redzone.models import REAL_BREAKS, GameState, League, Phase, pro_period

# Kinds a viewer can be held on. `close_run` is left out on purpose: runs are mostly chance (report 2.6).
HOLDABLE = frozenset({"rz_trip", "fourth_down", "two_minute", "go_ahead", "last_possessions", "tying_shot",
                      "power_play", "extra_attacker", "risp_late", "save_situation"})
# The cuts a hold may delay: every other kind (a user's, a crunch, a must-see, a chase, a return home, a replay) goes.
OPTIONAL_CUTS = frozenset({"better", "jump", "break_visit"})


@dc.dataclass(frozen=True)
class Params:
    """Every number the ledger uses, read as `threads.<name>`. All are judgments, not fitted: see decisions.md."""
    enabled: bool = False
    closure_first: bool = False
    max_hold_nfl_s: float = 150.0     # about three snaps
    max_hold_cfb_s: float = 150.0
    max_hold_nba_s: float = 75.0      # a possession, its inbound and its free throws, twice over
    max_hold_cbb_s: float = 75.0
    max_hold_nhl_s: float = 120.0     # a minor penalty
    max_hold_mlb_s: float = 180.0     # a plate appearance or two
    about_to_close_s: float = 60.0    # a thread earns its bonus only this close to its expected end
    bonus_pts: float = 12.0           # most a thread adds to a game's score: a tie-breaker scale, not a Tier 1 lever
    cut_after_s: float = 20.0         # after the answer airs, the game on screen reads lower this long: cut on the answer
    cut_penalty_pts: float = 8.0
    feed_lag_s: float = 8.0           # the feed knows this long after the play; the picture is later still (see air_lag_s)
    reopen_guard_s: float = 25.0      # no new thread in a game this soon after one closed (a score's extra-point state, label lag)
    flicker_s: float = 8.0            # a power play or a pulled goalie must be gone this long to count as over
    chain_gap_s: float = 30.0         # holds this close together count as one chain (the cap is twice the reach)
    run_pts: int = 8                  # unanswered points that make a close_run (a fact for the ledger, nothing more)

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> Params:
        base, out = cls(), {}
        for f in dc.fields(cls):
            old, new = getattr(base, f.name), get(f"threads.{f.name}", None)
            try:
                if isinstance(old, bool):
                    out[f.name] = old if new is None else bool(new)
                elif new is not None:
                    out[f.name] = type(old)(new)
            except (TypeError, ValueError):
                continue
        return dc.replace(base, **out)

    def reach(self, league: League) -> float:
        """The longest one thread may hold the screen in this sport. A league without a row uses its pro league's."""
        for lg in (league, league.base):
            got = getattr(self, f"max_hold_{lg.value}_s", None)
            if got is not None:
                return float(got)
        return 0.0


@dc.dataclass(frozen=True)
class Spec:
    """What the game's state says is open right now."""
    kind: str
    question: str     # a template with no names, ready for a phone line later
    eta_s: float      # expected seconds from opening to the answer
    stake: float      # 0..1, from the margin and the clock only


@dc.dataclass
class Thread:
    """One open (or just closed) question. `anchor` is what `closes_on` compares against."""
    id: str
    game_id: str
    league: League
    kind: str
    question: str
    opened_at: float
    eta_s: float
    stake: float
    anchor: dict
    closes_on: str = ""
    closed_at: float | None = None
    aired_at: float | None = None    # when the answer reaches the picture: the feed runs ahead of the screen
    seen_open: bool = False          # the game was on screen at some tick while this was open
    closed_on_screen: bool = False
    first_hold: float | None = None
    gone_since: float | None = None  # for flicker: when the state stopped showing the power play / pulled goalie

    @property
    def open(self) -> bool:
        return self.closed_at is None


@dc.dataclass(frozen=True)
class HoldFacts:
    """What the hold needs to know about the proposed cut. Built by the caller from the chain's own decision."""
    cut_kind: str
    on_break: bool           # the game on screen is on a real break: no reason to wait through it
    must_elsewhere: bool     # a must-show (the arbiter's floor) is owed on another game
    target_big: bool         # the cut goes to crunch, high leverage, your team or a crunch lock


def _margin(g: GameState) -> int | None:
    d = g.score_diff
    return abs(d) if d is not None else None


def _stake(g: GameState, late: bool, base: float) -> float:
    m = _margin(g)
    close = 0.0 if m is None else max(0.2, 1.0 - m / base)
    return round(close * (1.0 if late else 0.7), 3)


def _nfl(g: GameState) -> Spec | None:
    s, p, left, m = g.situation, g.period or 0, g.clock_s, _margin(g)
    if s.possession is None or m is None:
        return None
    down, ytg, late = s.down, s.yards_to_goal, p >= 4
    behind = _deficit(g, s.possession)
    if down == 4 and ytg is not None and ytg <= 45:
        return Spec("fourth_down", "Do they convert this 4th down?", 45.0, _stake(g, late, 17.0))
    if ytg is not None and ytg <= 20:
        return Spec("rz_trip", "Can they punch it in?", 45.0 * max(1, 5 - (down or 1)), _stake(g, late, 17.0))
    if p in (2, 4) and left is not None and left <= 120 and m <= 8:
        return Spec("two_minute", "What does this two-minute drive get them?", 120.0, _stake(g, p == 4, 17.0))
    if late and left is not None and left <= 480 and behind is not None and 0 <= behind <= 8 and (ytg is None or ytg <= 70):
        return Spec("go_ahead", "Can they drive for the lead?", 240.0, _stake(g, True, 17.0))
    return None


def _nba(g: GameState, run: tuple[str, int] | None, p: Params) -> Spec | None:
    q, left, m = pro_period(g.league, g.period, g.clock_s), g.clock_s, _margin(g)
    if m is None or left is None:
        return None
    if q > 4 or (q == 4 and left <= 120 and m <= 6):
        if left <= 60 and 1 <= m <= 3:
            return Spec("tying_shot", "Can they tie it?", 15.0, _stake(g, True, 15.0))
        return Spec("last_possessions", "Who makes the last big play?", 25.0, _stake(g, True, 15.0))
    if q >= 3 and m <= 8 and run is not None and run[1] >= p.run_pts and _deficit(g, run[0]) not in (None, 0):
        return Spec("close_run", "A run has made this close", 90.0, _stake(g, q >= 4, 15.0))   # a fact; never holds or ranks
    return None


def _nhl(g: GameState) -> Spec | None:
    s = g.situation
    if s.empty_net:
        return Spec("extra_attacker", "Can they tie it with the extra attacker?", 75.0, _stake(g, True, 3.0))
    if s.power_play:
        return Spec("power_play", "Do they score on this power play?", 120.0, _stake(g, (g.period or 0) >= 3, 3.0))
    return None


def _mlb(g: GameState) -> Spec | None:
    s, p, m = g.situation, g.period or 0, _margin(g)
    if m is None or s.runners is None:
        return None
    if p >= 7 and m <= 2 and (s.outs or 0) < 3 and (s.runners[1] or s.runners[2]):
        return Spec("risp_late", "Do they drive this run in?", 150.0, _stake(g, True, 5.0))
    if p >= 9 and 1 <= m <= 3 and g.half and _batting_trails(g):
        return Spec("save_situation", "Can the closer finish this?", 300.0, _stake(g, True, 5.0))
    return None


DETECTORS = {League.NFL: _nfl, League.NHL: _nhl, League.MLB: _mlb}


def _deficit(g: GameState, team: str) -> int | None:
    """How far `team` is behind (0: tied; negative: ahead). None: unknown."""
    if g.home.score is None or g.away.score is None:
        return None
    own, other = (g.home.score, g.away.score) if team == g.home.abbr else (g.away.score, g.home.score)
    return other - own


def _batting_trails(g: GameState) -> bool:
    d = g.score_diff
    if d is None:
        return False
    return (g.half == "bottom" and d < 0) or (g.half == "top" and d > 0)


def detect(g: GameState, *, run: tuple[str, int] | None = None, params: Params = Params()) -> Spec | None:
    """The question this game is asking now, or None. Priority is each sport's own order."""
    if g.phase != Phase.LIVE or g.stoppage in REAL_BREAKS:
        return None
    if g.league.base == League.NBA:
        return _nba(g, run, params)
    fn = DETECTORS.get(g.league.base)
    return fn(g) if fn is not None else None


def _points(g: GameState) -> tuple[int | None, int | None]:
    return (g.home.score, g.away.score)


def _crosses_half(g: GameState, anchor: dict) -> bool:
    was, now = anchor.get("period") or 0, g.period or 0
    return now > was and ((was <= 2 < now) or now > 4)


def closes_on(t: Thread, g: GameState, now: float, p: Params) -> str:
    """Why this thread is answered in this state, or "". The machine-checkable end of the question."""
    a = t.anchor
    if g.phase == Phase.POST:
        return "game_end"
    if _points(g) != a["score"] and None not in _points(g):
        return "score"
    s = g.situation
    if t.league.base == League.NFL:
        if s.possession is not None and s.possession != a["possession"]:
            return "possession"
        if t.kind == "fourth_down" and s.down is not None and s.down != 4:
            return "first_down"
        return "half_end" if _crosses_half(g, a) else ""
    if t.league.base == League.NBA:
        return "period_end" if (g.period or 0) != a["period"] else ""
    if t.league.base == League.NHL:
        if (g.period or 0) != a["period"]:
            return "period_end"
        gone = (s.power_play != a.get("pp")) if t.kind == "power_play" else not s.empty_net
        if not gone:
            t.gone_since = None
            return ""
        t.gone_since = t.gone_since if t.gone_since is not None else now
        return ("pp_over" if t.kind == "power_play" else "goalie_back") if now - t.gone_since >= p.flicker_s else ""
    if t.league.base == League.MLB:
        if (g.period, g.half) != (a["period"], a["half"]):
            return "inning_end"
        if t.kind == "risp_late" and s.runners is not None and not (s.runners[1] or s.runners[2]):
            return "stranded"
    return ""


def _anchor(g: GameState) -> dict:
    s = g.situation
    return {"score": _points(g), "period": g.period, "half": g.half, "possession": s.possession, "pp": s.power_play}


@dc.dataclass
class Ledger:
    """One open thread per live game, plus the closed ones and what the show did about them."""
    open: dict[str, Thread] = dc.field(default_factory=dict)
    closed: deque = dc.field(default_factory=lambda: deque(maxlen=4000))
    opened: Counter = dc.field(default_factory=Counter)
    _seq: int = 0
    _guard: dict[str, float] = dc.field(default_factory=dict)      # gid -> no new thread before this
    _run: dict[str, tuple[str, int, tuple]] = dc.field(default_factory=dict)   # gid -> (team, unanswered points, last score)
    _hold_chain: dict[str, tuple[float, float]] = dc.field(default_factory=dict)  # gid -> (first hold, last hold)

    def tick(self, states: Mapping[str, GameState], on_screen: str | None, now: float, params: Params, air_lag_s: float) -> None:
        """One decider tick: close what the state answered, open what it now asks."""
        for gid, g in states.items():
            t = self.open.get(gid)
            if t is not None:
                t.seen_open = t.seen_open or on_screen == gid
                why = closes_on(t, g, now, params)
                if why:
                    self._close(gid, t, why, now, on_screen, air_lag_s, params)
                    t = None
            if g.phase != Phase.LIVE:
                continue
            run = self._run_tick(gid, g)
            if t is None and now >= self._guard.get(gid, 0.0):
                spec = detect(g, run=run, params=params)
                if spec is not None:
                    self._open(gid, g, spec, now, on_screen)

    def _run_tick(self, gid: str, g: GameState) -> tuple[str, int] | None:
        """Unanswered points by the team that scored last, for basketball's close_run. Reset by the other side scoring."""
        if g.league.base != League.NBA or g.home.score is None or g.away.score is None:
            return None
        team, pts, last = self._run.get(gid, ("", 0, (g.home.score, g.away.score)))
        scored_h, scored_a = g.home.score - last[0], g.away.score - last[1]
        if scored_h > 0 or scored_a > 0:
            who = g.home.abbr if scored_h > scored_a else g.away.abbr
            pts = (pts if who == team else 0) + abs(scored_h - scored_a)
            team = who
        self._run[gid] = (team, pts, (g.home.score, g.away.score))
        return (team, pts) if team else None

    def _open(self, gid: str, g: GameState, spec: Spec, now: float, on_screen: str | None) -> None:
        self._seq += 1
        t = Thread(f"{gid}:{self._seq}", gid, g.league, spec.kind, spec.question, now, spec.eta_s, spec.stake, _anchor(g),
                   seen_open=on_screen == gid)
        self.open[gid] = t
        self.opened[(g.league.value, spec.kind)] += 1

    def _close(self, gid: str, t: Thread, why: str, now: float, on_screen: str | None, air_lag_s: float, p: Params) -> None:
        t.closes_on, t.closed_at, t.aired_at, t.closed_on_screen = why, now, now + max(0.0, air_lag_s), on_screen == gid
        self.closed.append(t)
        del self.open[gid]
        self._guard[gid] = now + p.reopen_guard_s

    def unaired(self, gid: str, now: float) -> Thread | None:
        """The newest closed thread of this game whose answer has not reached the picture yet."""
        for t in reversed(self.closed):
            if t.game_id == gid:
                return t if t.aired_at is not None and now < t.aired_at else None
        return None

    def just_aired(self, gid: str, now: float, within_s: float) -> Thread | None:
        """The newest closed thread of this game whose answer aired within the last `within_s` seconds."""
        for t in reversed(self.closed):
            if t.game_id == gid:
                return t if t.aired_at is not None and t.aired_at <= now <= t.aired_at + within_s else None
        return None

    def note_hold(self, gid: str, now: float, t: Thread, params: Params) -> None:
        """A hold really happened: start this thread's reach clock, and the game's chain clock."""
        t.first_hold = t.first_hold if t.first_hold is not None else now
        first, last = self._hold_chain.get(gid, (now, now))
        self._hold_chain[gid] = (first if now - last <= params.chain_gap_s else now, now)

    def chain_s(self, gid: str, now: float, params: Params) -> float:
        """How long holds have kept this game on screen without a gap."""
        first, last = self._hold_chain.get(gid, (now, now))
        return now - first if now - last <= params.chain_gap_s else 0.0


def closure_bonus(ledger: Ledger, gid: str, now: float, params: Params) -> float:
    """Points for a thread about to close on this game: most when it should resolve now, 0 when not soon or not holdable."""
    t = ledger.open.get(gid)
    if t is None or t.kind not in HOLDABLE or not params.closure_first or params.about_to_close_s <= 0:
        return 0.0
    remaining = max(0.0, t.opened_at + t.eta_s - now)
    if remaining > params.about_to_close_s:
        return 0.0
    urgency = 1.0 - remaining / params.about_to_close_s
    return params.bonus_pts * max(0.0, min(1.0, t.stake)) * (0.5 + 0.5 * urgency)


def fade_after_answer(ledger: Ledger, gid: str, now: float, params: Params) -> float:
    """Points off the game on screen right after its answer aired, so the next better game can take the cut."""
    if not params.closure_first or params.cut_after_s <= 0:
        return 0.0
    return params.cut_penalty_pts if ledger.just_aired(gid, now, params.cut_after_s) is not None else 0.0


def hold_for(ledger: Ledger, gid: str, league: League, now: float, facts: HoldFacts, params: Params) -> tuple[str, Thread] | None:
    """Should this optional cut wait on the thread open on `gid`? (kind, thread), or None. The caller still runs every
    other rule; a hold is only ever a stay on a game already on screen."""
    if not params.closure_first or facts.cut_kind not in OPTIONAL_CUTS or facts.must_elsewhere or facts.target_big or facts.on_break:
        return None
    t = ledger.open.get(gid)
    if t is not None and t.kind not in HOLDABLE:
        t = None
    t = t or ledger.unaired(gid, now)
    if t is None or t.kind not in HOLDABLE:
        return None
    reach = params.reach(league)
    start = t.first_hold if t.first_hold is not None else now
    if now - start >= reach or ledger.chain_s(gid, now, params) >= 2.0 * reach:
        return None
    return (t.kind, t)
