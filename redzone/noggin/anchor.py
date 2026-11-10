"""[anchor.enabled, off] Anchor mode's planner: which crunch game holds the screen, how much of its gap is surely dead, what fills it.

Why: Nick, Nov 9 night: "the OT game has the most weight, so we would hang there for a bit. In any available gaps we would jump to
and catch up on clips" (docs/plans/2026-11-09-anchor-mode.md). The screen stick never leaves the anchor, so coming back is one
input flip, and every risk reduces to one number: how much dead time the anchor surely has left.

    a = pick_anchor(games, current, cfg)                 # the anchor's game id, or None (hysteresis keeps the show from ping-ponging)
    b = gap_budget("nfl", "timeout", 0.0, cfg)           # away seconds this stoppage surely holds: q10 - 2 x flip_max - margin
    p = plan_gap(b, ready_at, now, clips, visits, cfg)   # the exact best ordered fill; p.away_s <= b always
    keep, last, dropped = late_queue(clips, now, history, cfg)   # past 180 s: dropped, unless big (then it airs after the anchor)

Time model of one gap (real seconds; `now` is when the flip out may begin):
  * the picture of the hidden stick is up `flip` after the flip out; content starts no sooner;
  * the hidden stick serves one item at a time: a clip after anything needs `clip_prep` from that item's end, a visit needs a
    tune (`tune_max`); only the first clip may already be loaded (`hidden_ready_at`);
  * between two items the screen flips back to the anchor and out again (2 flips);
  * `away_s` = the last item's end - (now + flip): the plan never takes more than the budget, so the flip back lands at
    now + 2 x flip + budget = now + guaranteed dead time - margin at the latest (`back_at`).

Plain between-play gaps and basketball / hockey whistles budget 0 in v1: a two-minute drill snaps in about 15 s and that next snap
is the moment the anchor exists for (spec section 3). They unlock only once snap-to-snap times in crunch are measured.

Pure: no clock, no I/O, no decider import. `cfg` is config-like (`get(dotted, default)`) or None (the priors below).
"""
from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from redzone import measured_timing
from redzone.decider import buffered as bj
from redzone.moments import value2
from redzone.noggin.catchup import HARD_CAP_S, Route
from redzone.scoring.dead_left import guaranteed_left

FLAG = "anchor.enabled"
# The priors (default_config.yaml anchor.*), used when a config lacks a key. None of them is fitted yet.
PRIORS = {"hysteresis": 1.3, "margin_s": 3.0, "quantile": 0.10, "late_big_pct": 5.0, "max_items": 6, "lead_doubt_s": 0.0}
NO_GAP_KINDS = frozenset({"none", "whistle", "unknown", ""})   # v1: not used, whatever the table says (spec section 3)


def on(cfg) -> bool:
    return bool(cfg is not None and hasattr(cfg, "get") and cfg.get(FLAG, False))


def knob(cfg, key: str) -> float:
    """anchor.<key> from the config, else its prior."""
    v = cfg.get(f"anchor.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return float(PRIORS[key] if v is None else v)


@dataclass(frozen=True)
class Costs:
    flip: float        # one input flip, planned at the slowest measured one
    prep: float        # the hidden stick loads a clip (tune + wait + seek)
    tune: float        # the hidden stick tunes a live game for a one-play visit


def costs(cfg) -> Costs:
    """timing.measured on: the TV's measured flip max, clip load and tune max. Off: the route's existing flip
    (buffered_jump.flip_s) and clip load (catchup.Route.prep_s); the tune has no older number, so it is the measured max."""
    if measured_timing.on(cfg):
        return Costs(measured_timing.value(cfg, "flip_max_s"), measured_timing.value(cfg, "clip_prep_s"),
                     measured_timing.value(cfg, "tune_max_s"))
    return Costs(float(bj.cfg_get(cfg, "flip_s")), float(Route.prep_s), measured_timing.MEASURED["tune_max_s"])


# ----- which game is the anchor ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Game:
    """One live game as the anchor sees it."""
    gid: str
    league: str             # "nfl", "nba", ...
    margin: int             # points between the teams
    left_s: float           # regulation seconds left (the overtime period's own clock in overtime); -1 unknown
    overtime: bool = False
    crunch: bool = False    # inside its crunch window (crunch.py's graded window, Scored.crunch_now)
    mine: bool = False      # your team plays (or your bet)
    stakes: float = 1.0     # value2 stakes; 1.0 when unknown
    league_rank: int = 0    # core.league_rank: only breaks a tie
    ended: bool = False

    @property
    def eligible(self) -> bool:
        return not self.ended and (self.crunch or self.overtime)


def anchor_score(g: Game) -> float:
    """Leverage x stakes. A league value v2 does not grade yet (hockey, baseball) reads 1.0, its overtime the leverage cap."""
    try:
        lev = value2.leverage(g.league, g.margin, g.left_s, g.overtime)
    except KeyError:
        lev = float(value2.config()["leverage"]["cap"]) if g.overtime else 1.0
    return lev * g.stakes


def pick_anchor(games: Iterable[Game], current: str | None, cfg=None) -> str | None:
    """The anchor: the eligible game first by your team, then by score (league order breaks a tie). A challenger takes an eligible
    anchor only if it is yours and the anchor is not, or (same side) its score beats the anchor's by `anchor.hysteresis`.
    The anchor ends when its game ends or leaves crunch (for NFL that is a two-score margin: the window is one score)."""
    pool = [g for g in games if g.eligible]
    if not pool:
        return None
    key = {g.gid: (int(g.mine), anchor_score(g), g.league_rank) for g in pool}
    best = max(pool, key=lambda g: (*key[g.gid], g.gid))
    if current is None or current not in key or best.gid == current:
        return best.gid
    (bm, bs, _), (cm, cs, _) = key[best.gid], key[current]
    if bm != cm:
        return best.gid if bm > cm else current
    return best.gid if bs >= knob(cfg, "hysteresis") * cs and bs > cs else current


# ----- how much of a gap is surely dead -------------------------------------------------------------------------------------------

def guaranteed_s(league: str, stoppage_kind: str, elapsed_s: float, cfg=None, sigma_s: float = 0.0) -> float:
    """Seconds of this stoppage surely still to come on screen: its `anchor.quantile` length less what already ran, less 2 sigma
    of the stream-delay estimate. 0 for a plain dead ball or a whistle (v1)."""
    kind = str(getattr(stoppage_kind, "value", stoppage_kind) or "")
    if kind in NO_GAP_KINDS:
        return 0.0
    return max(0.0, guaranteed_left(league, kind, elapsed_s, knob(cfg, "quantile")) - 2.0 * max(0.0, sigma_s))


def gap_budget(league: str, stoppage_kind: str, elapsed_s: float, cfg=None, sigma_s: float = 0.0) -> float:
    """Away seconds one gap holds: guaranteed dead time - flip out - flip back - anchor.margin_s, never below 0."""
    g = guaranteed_s(league, stoppage_kind, elapsed_s, cfg, sigma_s)
    return max(0.0, g - 2.0 * costs(cfg).flip - knob(cfg, "margin_s")) if g > 0 else 0.0


# ----- what fills a gap ------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Clip:
    """A queued catch-up clip: the lead-in, the whole play and a second (spec section 4.1)."""
    id: str
    value: float            # replay value
    seconds: float          # on screen
    deadline: float         # the latest its picture may come up (180 s cap and the stick's reach)
    big: bool = False       # top anchor.late_big_pct of replay values: may air after the anchor, past its deadline


@dataclass(frozen=True)
class Visit:
    """A one-play live visit: another game's coming snap, watched whole (spec section 4.2)."""
    id: str
    value: float            # live value of that snap
    snap_at: float          # when the snap airs on that game's picture (the early side)
    play_s: float = 8.0     # the play, to its dead ball


@dataclass(frozen=True)
class Plan:
    items: tuple = ()                       # Clip / Visit, in airing order
    starts: tuple[float, ...] = ()          # when each item's picture is up
    ends: tuple[float, ...] = ()
    value: float = 0.0
    away_s: float = 0.0                     # last end - (now + flip): never above the budget
    back_at: float = 0.0                    # the anchor is back on screen by here
    why: str = ""


def _walk(order: Sequence, now: float, ready_at: float, c: Costs) -> tuple[list[float], list[float]] | None:
    """Start and end of each item in this order, or None when one cannot make its deadline or its snap."""
    cursor, starts, ends, prev_end = now + c.flip, [], [], None
    for i, it in enumerate(order):
        if isinstance(it, Visit):
            ready = (now if prev_end is None else prev_end) + c.tune
        else:
            ready = ready_at if i == 0 else prev_end + c.prep
        up = max(cursor, ready)
        if isinstance(it, Visit):
            if up > it.snap_at:
                return None                 # the picture would come up after the snap: never join mid-play
            end = it.snap_at + it.play_s
        else:
            if up > it.deadline:
                return None
            end = up + it.seconds
        starts.append(up)
        ends.append(end)
        prev_end, cursor = end, end + 2.0 * c.flip
    return starts, ends


def plan_gap(budget_s: float, hidden_ready_at: float, now: float, queue: Sequence[Clip], visits: Sequence[Visit] = (), cfg=None) -> Plan:
    """The exact best ordered fill of one gap: the most value whose last item ends within the budget (ties: back sooner, fewer
    items). Only the `anchor.max_items` most valuable items are searched (every order of every subset). Empty when nothing fits."""
    c = costs(cfg)
    if budget_s <= 0:
        return Plan(back_at=now, why="no budget")
    limit = now + c.flip + budget_s
    pool = sorted([*queue, *visits], key=lambda x: (-x.value, x.id))[:max(0, int(knob(cfg, "max_items")))]
    best: tuple | None = None
    for k in range(1, len(pool) + 1):
        for order in itertools.permutations(pool, k):
            w = _walk(order, now, hidden_ready_at, c)
            if w is None or w[1][-1] > limit + 1e-9:
                continue
            val = sum(x.value for x in order)
            key = (round(val, 9), -w[1][-1], -k)
            if best is None or key > best[0]:
                best = (key, order, w)
    if best is None:
        return Plan(back_at=now, why="nothing fits the budget")
    _, order, (starts, ends) = best
    return Plan(tuple(order), tuple(starts), tuple(ends), sum(x.value for x in order), ends[-1] - (now + c.flip),
                ends[-1] + c.flip, f"{len(order)} item(s) in a {budget_s:.0f} s budget")


# ----- plays the gaps cannot hold ------------------------------------------------------------------------------------------------

def big_line(history: Sequence[float], cfg=None) -> float:
    """The replay value at the top `anchor.late_big_pct` percent of the queue's history; inf with no history (nothing is big yet)."""
    vals = sorted(float(v) for v in history)
    if not vals:
        return float("inf")
    pct = min(100.0, max(0.0, knob(cfg, "late_big_pct")))
    i = min(len(vals) - 1, max(0, int(len(vals) * (1.0 - pct / 100.0))))
    return vals[i]


def late_queue(queue: Sequence[Clip], now: float, history: Sequence[float], cfg=None) -> tuple[list[Clip], list[Clip], list[Clip]]:
    """(still on time, past the deadline but big: air last after the anchor ends, past the deadline and small: dropped)."""
    line = big_line(history, cfg)
    keep, last, dropped = [], [], []
    for x in queue:
        if now <= x.deadline:
            keep.append(x)
        elif x.big or x.value >= line:
            last.append(x)
        else:
            dropped.append(x)
    return keep, last, dropped


def clip_deadline(aired: float, play_s: float, lead_s: float, reach_s: float) -> float:
    """The latest a clip's picture may come up: its first second must be under 180 s late and inside the stick's reach."""
    return aired - play_s - lead_s + min(reach_s, HARD_CAP_S - 1.0)
