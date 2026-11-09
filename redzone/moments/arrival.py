"""Thread-aware arrival: points for a game whose open question is about to be answered on its picture.

Why: the thread replay (D-ADDICT, 2026-11-09) found the show loses payoffs by not being on the game when a question
resolves, not by leaving early: closures seen NFL 47.2%, NHL 45.5%, and most proposed cuts had no open question on screen.
Report docs/agents/context/2026-11-09-addicting-show.md, sections 3 and 4. So the show should get there first, with enough
lead for the buildup: a few seconds to a play or two before the answer airs.

    book = Book()
    before = dict(ledger.open); ledger.tick(...); book.tick(before, ledger, states, now)        # every decider tick
    arrival_points(book, ledger, g, now, air_s=35.0, tune_s=5.0, on_screen=False, params=p)     # 0 .. bonus_pts

What it uses, all of it known at the tick (the hindsight trap: a replay knows when questions resolve; this file must not):
    the state the feed has delivered, how long that state has stood, when the thread opened, and the stick's picture delay.
A forecast of the answer comes from the state alone. An answer the feed has already given and the picture has not aired
counts too (`answered`): the feed runs ahead of the picture, so the show can still get there before it airs.

Limits this file keeps (decisions.md, Nov 4 and 2026-11-09; report section 8):
  - nothing is delayed, teased or invented: these are points for a game that is live anyway
  - a run is a fact, not a forecast: `close_run` earns nothing (threads.HOLDABLE)
  - no bets, odds, spreads, fantasy, streaks, session length or time of night go in here (tests scan this file)
  - points only. Rule #1, the landing rules, the leave rules and the arbiter's floor are the chain's, and redzone/noggin/show.py
    caps these points below every must-show, crunch or your-team game

Pure: reads the GameState it is handed; no clock, no I/O, no decider import. Flag `threads.arrival.enabled`, code default OFF.
"""
from __future__ import annotations

import dataclasses as dc
from collections.abc import Callable, Mapping

from redzone.models import GameState, League
from redzone.moments.threads import HOLDABLE, Ledger, Thread

# Answers worth a cut when the feed already has them. An expiry, a stranded runner or a period end is an answer but no payoff.
PAYOFF = frozenset({"score", "possession", "first_down"})


@dc.dataclass(frozen=True)
class Params:
    """Every number here, read as `threads.arrival.<name>`. All are judgments, not fitted: see decisions.md."""
    enabled: bool = False
    bonus_pts: float = 25.0        # most a question adds to a game's score: above switch_margin, below a crunch game's lead
    min_lead_s: float = 4.0        # arriving later than this before the answer loses the buildup: nothing for that
    full_lead_nfl_s: float = 50.0  # up to one snap of lead earns the full points
    full_lead_cfb_s: float = 50.0
    full_lead_nba_s: float = 25.0  # a possession
    full_lead_cbb_s: float = 25.0
    full_lead_nhl_s: float = 30.0  # a shift's worth of a power play
    full_lead_mlb_s: float = 50.0  # a pitch or two
    fade_s: float = 45.0           # past the full lead the points fall to zero over this long: too early means dead air
    use_answered: bool = True      # also count an answer the feed has that the picture has not aired
    snap_s: float = 38.0           # NFL, CFB: one play to the next
    yards_a_snap: float = 7.0
    possession_s: float = 18.0     # NBA, CBB
    at_bat_s: float = 75.0         # MLB
    hazard_s: float = 15.0         # NHL: a goal can come any moment, so an open power play always reads this close

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> Params:
        base, out = cls(), {}
        for f in dc.fields(cls):
            old, new = getattr(base, f.name), get(f"threads.arrival.{f.name}", None)
            try:
                if isinstance(old, bool):
                    out[f.name] = old if new is None else bool(new)
                elif new is not None:
                    out[f.name] = type(old)(new)
            except (TypeError, ValueError):
                continue
        return dc.replace(base, **out)

    def full_lead(self, league: League) -> float:
        for lg in (league, league.base):
            got = getattr(self, f"full_lead_{lg.value}_s", None)
            if got is not None:
                return float(got)
        return 0.0


def _key(g: GameState) -> tuple:
    """What counts as the game having moved: a new play shows up in one of these."""
    s = g.situation
    return (g.period, g.half, g.clock_s, g.home.score, g.away.score, s.possession, s.down, s.distance, s.yards_to_goal,
            s.outs, s.runners, s.power_play, s.empty_net)


@dc.dataclass
class Book:
    """What the ledger doesn't keep: how long each game's state has stood, and each game's newest answered question."""
    _since: dict[str, tuple[tuple, float]] = dc.field(default_factory=dict)
    answered: dict[str, Thread] = dc.field(default_factory=dict)

    def tick(self, before: Mapping[str, Thread], ledger: Ledger, states: Mapping[str, GameState], now: float) -> None:
        """After `ledger.tick`. `before` is the ledger's open threads from just before it."""
        for gid, g in states.items():
            k, was = _key(g), self._since.get(gid)
            if was is None or was[0] != k:
                self._since[gid] = (k, now)
        for gid, t in before.items():
            if t.closed_at is not None and ledger.open.get(gid) is not t:
                self.answered[gid] = t

    def stood_s(self, gid: str, now: float) -> float:
        got = self._since.get(gid)
        return max(0.0, now - got[1]) if got is not None else 0.0


def remaining(t: Thread, g: GameState, stood_s: float, now: float, p: Params) -> float:
    """Expected seconds until the feed has the answer, from the state now. Never negative; 0 means it could be any second."""
    s, kind = g.situation, t.kind
    if kind == "fourth_down":
        left = p.snap_s - stood_s
    elif kind == "rz_trip":
        left = p.snap_s * max(1.0, 3.3 - 0.8 * (s.down or 1)) - stood_s
    elif kind in ("two_minute", "go_ahead"):
        left = max(p.snap_s, p.snap_s * (s.yards_to_goal or 40) / p.yards_a_snap) - stood_s
        if kind == "two_minute" and g.clock_s is not None:
            left = min(left, g.clock_s * 1.4)
    elif kind in ("last_possessions", "tying_shot"):
        left = p.possession_s - stood_s
    elif kind in ("power_play", "extra_attacker"):
        left = min(p.hazard_s, t.opened_at + t.eta_s - now)
    elif kind == "risp_late":
        left = p.at_bat_s * min(2.0, 3.0 - (s.outs or 0)) - stood_s
    elif kind == "save_situation":
        left = p.at_bat_s * 3.0 * (3.0 - (s.outs or 0)) - stood_s
    else:
        left = t.opened_at + t.eta_s - now
    return max(0.0, left)


def proximity(lead_s: float, floor_s: float, league: League, p: Params) -> float:
    """0..1: how good a moment this is to be arriving, given `lead_s` of picture before the answer airs."""
    full = p.full_lead(league)
    if lead_s < floor_s or full <= 0.0:
        return 0.0
    if lead_s <= full:
        return 1.0
    return max(0.0, 1.0 - (lead_s - full) / p.fade_s) if p.fade_s > 0 else 0.0


def arrival_points(book: Book, ledger: Ledger, g: GameState, now: float, *, air_s: float, tune_s: float, on_screen: bool,
                   params: Params) -> tuple[float, Thread | None]:
    """(points, the thread earning them). The better of the question still open and the answer about to air.

    `air_s` is how far this game's picture runs behind the feed; `tune_s` how long a cut takes to show a picture. A game
    already on screen needs no tune, and has no lead to lose: being there is the point."""
    p, gid = params, g.game_id
    if not p.enabled or p.bonus_pts <= 0:
        return 0.0, None
    tune, floor = (0.0, 0.0) if on_screen else (max(0.0, tune_s), p.min_lead_s)
    best, who = 0.0, None
    t = ledger.open.get(gid)
    if t is not None and t.kind in HOLDABLE:
        rem = remaining(t, g, book.stood_s(gid, now), now, p)
        best, who = p.bonus_pts * _clamp(t.stake) * proximity(rem + air_s - tune, floor, g.league, p), t
    done = book.answered.get(gid) if p.use_answered else None
    if done is not None and done.kind in HOLDABLE and done.closes_on in PAYOFF and done.closed_at is not None:
        lead = float(done.closed_at) + air_s - now - tune
        pts = p.bonus_pts * _clamp(done.stake) * proximity(lead, floor, g.league, p) if lead >= 0.0 else 0.0
        if pts > best:
            best, who = pts, done
    return best, (who if best > 0.0 else None)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))
