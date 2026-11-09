"""Queue core: readiness R0-R5, prep time, who gets the hidden stick, and what a failed tune does next.

Why: P-QUEUE Q3, Q4 (pure part), Q10 and build plan WP-02. Ten code paths write to the hidden stick and
three choose what it plays; this is the one pure answer to "what should it hold". It lives in noggin/
because contract rule K4 allows ranking only on the decision path.

Pure on purpose: no engine import, no clock, no I/O, standard library only. The caller passes `now`, the
stick's facts and a config getter, so a replay is exact. Nothing here flips a stick: every cut still goes
through the commit gate. Wiring it in is WP-12, behind `queue.enabled` (off).

Fails closed. A bad number, a missing route or a flagged route means "not reachable, not flippable, keep
what is there", never an exception and never a guess. A walled route (purchase or sign-in) is never
pressed again.

Authority, top wins (build plan decision 12: a Pin beats the brain and rotation, only a Lock beats
everything): LOCK (a Lock or a tap) > NEXT (committed switch) > TIER1 known and reachable > PIN > TIER2
known and reachable > WARNING (long-lead route, prep fits) > BRAIN > PLANNER.

Time words. `join_in_s` is the join point's real time minus now, so it is <= 0 once the feed has it, and
that point airs `join_in_s + lookahead_s` from now. `prep_s` is seconds to the route's goal level and
already includes the stick's current job, so `reach` must not add `busy_s` again.
"""
from __future__ import annotations

import dataclasses as dc
import math
from collections.abc import Callable, Mapping
from enum import Enum, IntEnum
from typing import NamedTuple

FAST = "fast"
LONG_LEAD = "long_lead"
LIVE_EDGE = "live_edge"
_NEVER = math.inf
_ALL_TRIES = 1 << 30


class Level(IntEnum):
    """How ready one game is (P-QUEUE section 3)."""
    R0 = 0   # on the slate, no route
    R1 = 1   # app + ref, not walled or flagged
    R2 = 2   # played on a stick tonight, or a same-app check passed
    R3 = 3   # playing at the live edge on the hidden stick
    R4 = 4   # playing B seconds behind, position read
    R5 = 5   # paused at the line-up


class Rung(IntEnum):
    """Authority ladder; a lower number outranks a higher one."""
    LOCK = 1
    NEXT = 2
    TIER1 = 3
    PIN = 4
    TIER2 = 5
    WARNING = 6
    BRAIN = 7
    PLANNER = 8
    NOBODY = 9


_OWNERS = (Rung.LOCK, Rung.NEXT, Rung.PIN)   # these ignore reach, sunk cost and value

# What the phone may say. One word for both tiers and no join time: the feed runs ahead of the screen,
# so a tier or a countdown would spoil the play.
_REASONS = {
    Rung.LOCK: "you picked it", Rung.NEXT: "next cut", Rung.TIER1: "up soon", Rung.PIN: "pinned",
    Rung.TIER2: "up soon", Rung.WARNING: "getting close", Rung.BRAIN: "worth a look",
    Rung.PLANNER: "next in rotation", Rung.NOBODY: "waiting",
}
_OWNER_WORD = {
    Rung.LOCK: "lock", Rung.NEXT: "next", Rung.TIER1: "moment", Rung.PIN: "pin", Rung.TIER2: "moment",
    Rung.WARNING: "warning", Rung.BRAIN: "brain", Rung.PLANNER: "planner", Rung.NOBODY: "none",
}


@dc.dataclass(frozen=True)
class Params:
    """Every number the queue uses. Defaults are P-QUEUE's measured or target values; `tune_s` and
    `buffer_s` are fallbacks until the stick has medians. The eviction and tier-value numbers are
    uncalibrated. Read `queue.<name>` from config with `from_config`."""
    tune_s: Mapping[str, float] = dc.field(default_factory=lambda: {
        "youtubetv": 4.0, "youtubetv:search": 13.0, "prime": 45.0, "espn": 20.0, "other": 30.0})
    buffer_s: Mapping[str, float] = dc.field(default_factory=lambda: {"youtubetv": 56.0, "other": 56.0})
    start_budget_s: Mapping[str, float] = dc.field(default_factory=lambda: {
        "youtubetv": 12.0, "youtubetv:search": 30.0, "prime": 55.0, "espn": 20.0, "other": 30.0})
    hold_apps: tuple[str, ...] = ("prime",)   # the buffer is a pause, so B behind costs B seconds
    retry_apps: tuple[str, ...] = ("youtubetv",)   # one fresh retry when the video never started
    behind_s: float = 75.0
    reach_margin_s: float = 2.0
    evict_margin: float = 5.0        # value points, 0-100 scale
    evict_per_sunk_s: float = 0.1    # value points per second of prep the eviction throws away
    tier1_value: float = 100.0
    tier2_value: float = 60.0
    scrub_budget_s: float = 45.0
    banner_retry_s: float = 30.0
    backoff_s: float = 120.0
    up_next: int = 5
    r3_flippable: bool = False

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> Params:
        """Build from a `cfg.get(dotted, default)` getter. A bad value keeps its default."""
        base, out = cls(), {}
        for f in dc.fields(cls):
            old, new = getattr(base, f.name), get(f"queue.{f.name}", None)
            if isinstance(old, Mapping):
                good = {k: float(v) for k, v in new.items() if _cost(v) is not None} \
                    if isinstance(new, Mapping) else {}
                out[f.name] = {**old, **good}
            elif isinstance(old, bool):
                out[f.name] = new if isinstance(new, bool) else old
            elif isinstance(old, tuple):
                ok = isinstance(new, (list, tuple)) and all(isinstance(a, str) for a in new)
                out[f.name] = tuple(new) if ok else old
            elif isinstance(old, int):
                out[f.name] = new if type(new) is int and new >= 0 else old
            else:
                out[f.name] = float(new) if _cost(new) is not None else old
        return cls(**out)


QUEUE_PARAMS = Params()


@dc.dataclass(frozen=True, slots=True)
class Route:
    app: str
    ref: str
    klass: str = FAST        # an unknown class is handled as long_lead
    search: bool = False     # reached only by in-app search
    flagged: bool = False    # walled, "may be blacked out", ...: never used


@dc.dataclass(frozen=True, slots=True)
class Entry:
    """One game, as the caller knows it this tick. `routes` is in preference order."""
    game: str
    routes: tuple[Route, ...] = ()
    level: int = Level.R0
    user: str = ""                    # "lock" (a Lock or a tap), "pin" or ""
    committed: bool = False           # it is the decider's next switch
    tier: int | None = None           # a known moment, 1 or 2
    join_in_s: float | None = None    # see the module docstring; None = no deadline
    lookahead_s: float | None = None
    lead_s: float | None = None       # expected lead from a warning or a scheduled block
    brain_rank: float | None = None
    planner_rank: float | None = None
    value: float = 0.0                # 0-100


@dc.dataclass(frozen=True, slots=True)
class Stick:
    """The hidden stick. `job_game` is what its running job is bringing to level `job_to`; the job ends
    at `busy_until`. `tune_s` and `buffer_s` are this stick's measured medians by app."""
    busy_until: float = 0.0
    job_game: str | None = None
    job_to: int | None = None
    tune_s: Mapping[str, float] = dc.field(default_factory=dict)
    buffer_s: Mapping[str, float] = dc.field(default_factory=dict)


def _num(x) -> float | None:
    """x as a finite float, else None. A bool is not a number here."""
    t = type(x)
    if t is float:
        return x if math.isfinite(x) else None
    if t is int:
        try:
            x = float(x)
        except OverflowError:
            return None
        return x if math.isfinite(x) else None
    return None


def _cost(x) -> float | None:
    v = _num(x)
    return v if v is not None and v >= 0 else None


def _usable(r) -> bool:
    """A route with a blank or odd field counts as flagged."""
    return type(r) is Route and type(r.app) is str and type(r.ref) is str and bool(r.app and r.ref) \
        and r.flagged is False


def _route(e: Entry) -> Route | None:
    """The first usable route, or None."""
    rs = e.routes
    if type(rs) in (tuple, list):
        for r in rs:
            if _usable(r):
                return r
    return None


def _live_routes(e: Entry) -> list[Route]:
    return [r for r in e.routes if _usable(r)] if type(e.routes) in (tuple, list) else []


def level(e: Entry) -> Level:
    """The level that counts: R0 for a bad level or when no usable route is left."""
    v = e.level
    if type(v) not in (int, Level) or not 0 <= v <= 5 or _route(e) is None:
        return Level.R0
    return Level(v)


def level_of(route_ok, verified, standby: str | None = None, buffered=False) -> Level:
    """The level from `machines/control.STANDBY`. Any state that is not in STANDBY_READY stays below R3,
    so a stick still loading or scrubbing can never look flippable. Replay footage is not readiness."""
    if route_ok is not True:
        return Level.R0
    if standby in ("snap_parked", "left_hold"):
        return Level.R5
    if standby == "ready":
        return Level.R4 if buffered is True else Level.R3
    return Level.R2 if verified is True else Level.R1


def flippable(e: Entry, p: Params = QUEUE_PARAMS) -> bool:
    """May a flip show it? R4 and R5 yes; R3 only with `queue.r3_flippable` (and the strict live gate)."""
    v = level(e)
    return v >= Level.R4 or (v == Level.R3 and p.r3_flippable is True)


def busy_s(stick: Stick, now) -> float:
    """Seconds until the stick's current job ends; infinite when that can't be known."""
    t = _num(now)
    if t is None or type(stick) is not Stick:
        return _NEVER
    if stick.busy_until is None:
        return 0.0
    b = _num(stick.busy_until)
    return _NEVER if b is None else max(0.0, b - t)


def _key_cost(measured, shipped: Mapping[str, float], route: Route) -> float:
    keys = (f"{route.app}:search", route.app) if route.search else (route.app,)
    if isinstance(measured, Mapping):
        for k in keys:
            v = _cost(measured.get(k))
            if v is not None:
                return v
    for k in keys:
        if k in shipped:
            return shipped[k]
    return shipped.get("other", _NEVER)


def _work(route: Route, frm: int, to: int, stick: Stick | None, p: Params) -> float:
    """Seconds of stick work to go from level frm to level to on this route."""
    total = 0.0
    if frm < Level.R3 <= to:
        total += _key_cost(stick.tune_s if stick else None, p.tune_s, route)
    if frm < Level.R4 <= to:
        cost = _key_cost(stick.buffer_s if stick else None, p.buffer_s, route)
        total += max(cost, p.behind_s) if route.app in p.hold_apps else cost
    return total


def _prep(e: Entry, lv: Level, route: Route | None, stick, now, p: Params) -> float:
    if lv == Level.R0 or route is None:
        return _NEVER
    goal = Level.R3 if route.klass == LIVE_EDGE else Level.R4
    if lv >= goal:
        return 0.0
    if type(stick) is not Stick:
        return _NEVER
    busy = busy_s(stick, now)
    if busy == _NEVER:
        return _NEVER
    start: int = lv
    to = stick.job_to
    if stick.job_game == e.game and type(to) in (int, Level) and Level.R3 <= to <= Level.R5:
        start = max(lv, to)
    return busy + _work(route, start, goal, stick, p)


def prep_s(e: Entry, stick: Stick, now, p: Params = QUEUE_PARAMS) -> float:
    """Seconds until this game reaches its goal level (R4, or R3 on a live-edge route), including the
    stick's current job. 0 when it is already there, infinite when it can't get there."""
    return _prep(e, level(e), _route(e), stick, now, p)


def _reach(e: Entry, route: Route | None, prep: float, p: Params) -> bool:
    if prep == _NEVER or route is None:
        return False
    if e.join_in_s is None:
        if route.klass in (FAST, LIVE_EDGE):
            return True
        lead = _num(e.lead_s)
        return lead is not None and lead >= prep
    j, ahead = _num(e.join_in_s), _cost(e.lookahead_s)
    if j is None or ahead is None or not math.isfinite(j + ahead):
        return False
    return j + ahead >= prep + p.reach_margin_s


def reach(e: Entry, stick: Stick, now, p: Params = QUEUE_PARAMS) -> bool:
    """Can the stick be ready in time? Exactly on time counts. No deadline is reachable, except that a
    long-lead route needs at least prep_s of expected lead. If not, the moment becomes a rewind."""
    return _reach(e, _route(e), prep_s(e, stick, now, p), p)


def _rung(e: Entry, lv: Level, route: Route | None, prep: float, ok: bool) -> Rung:
    if lv == Level.R0 or route is None:
        return Rung.NOBODY
    if e.user == "lock":
        return Rung.LOCK
    if e.committed is True:
        return Rung.NEXT
    # Inside the look-ahead means the join point is already in the feed: join_in_s <= 0.
    j = _num(e.join_in_s)
    known = ok and j is not None and j <= 0 and type(e.tier) is int
    if known and e.tier == 1:
        return Rung.TIER1
    if e.user == "pin":
        return Rung.PIN
    if known and e.tier == 2:
        return Rung.TIER2
    if route.klass not in (FAST, LIVE_EDGE):
        lead = _num(e.lead_s)
        if lead is not None and lead >= prep:
            return Rung.WARNING
    if _num(e.brain_rank) is not None:
        return Rung.BRAIN
    if _num(e.planner_rank) is not None:
        return Rung.PLANNER
    return Rung.NOBODY


def authority(e: Entry, stick: Stick, now, p: Params = QUEUE_PARAMS) -> Rung:
    """Which ladder rung this game holds right now."""
    lv, route = level(e), _route(e)
    prep = _prep(e, lv, route, stick, now, p)
    return _rung(e, lv, route, prep, _reach(e, route, prep, p))


class Row(NamedTuple):
    entry: Entry
    rung: Rung
    level: Level
    prep: float
    reach: bool
    route: Route | None


def _row(e: Entry, stick, now, p: Params) -> Row:
    lv, route = level(e), _route(e)
    prep = _prep(e, lv, route, stick, now, p)
    ok = _reach(e, route, prep, p)
    return Row(e, _rung(e, lv, route, prep, ok), lv, prep, ok, route)


def _value(row: Row, p: Params) -> float | None:
    v = _num(row.entry.value)
    if v is None:
        return None
    if row.rung == Rung.TIER1:
        return max(v, p.tier1_value)
    return max(v, p.tier2_value) if row.rung == Rung.TIER2 else v


def _spent(row: Row, stick, p: Params) -> float:
    """Seconds of prep this game already holds: what evicting it throws away."""
    if row.route is None:
        return 0.0
    return _work(row.route, Level.R1, row.level, stick if type(stick) is Stick else None, p)


def _swap(hidden: Row | None, want: Row, stick, p: Params) -> tuple[bool, str]:
    if want.level == Level.R0 or want.route is None:
        return False, "want_has_no_route"
    if hidden is not None and hidden.entry.game == want.entry.game:
        return False, "already_hidden"
    if hidden is None or hidden.level == Level.R0 or hidden.route is None:
        return (True, "stick_free") if want.rung in _OWNERS or want.reach else (False, "unreachable")
    if hidden.rung < want.rung:
        return False, "hidden_outranks"
    if want.rung in _OWNERS:
        return True, "owner"
    if not want.reach:
        return False, "unreachable"
    a, b = _value(want, p), _value(hidden, p)
    if a is None or b is None:
        return False, "bad_value"
    needed = p.evict_margin + p.evict_per_sunk_s * _spent(hidden, stick, p)
    return (True, "worth_it") if a - b > needed else (False, "not_worth_sunk_cost")


def replace(hidden: Entry | None, want: Entry, stick: Stick, now, p: Params = QUEUE_PARAMS) -> bool:
    """Should the hidden stick drop what it holds for `want`? A strictly better-ranked occupant is never
    evicted. Lock, committed next and Pin need nothing else. Anyone else must be reachable and worth more
    than the margin plus the prep thrown away; exactly equal is not enough."""
    held = _row(hidden, stick, now, p) if type(hidden) is Entry else None
    return _swap(held, _row(want, stick, now, p), stick, p)[0]


@dc.dataclass(frozen=True, slots=True)
class Plan:
    """One tick's answer. `rows` is in authority order and leaves out the on-screen game."""
    rows: tuple[Row, ...]
    want: Row | None
    swap: bool
    why: str
    on_screen: Row | None
    hidden: Row | None
    p: Params


def _order_key(row: Row) -> tuple:
    e, rung = row.entry, row.rung
    if rung in (Rung.TIER1, Rung.TIER2):   # the one that airs first
        rank = e.join_in_s + e.lookahead_s
    elif rung == Rung.WARNING:
        rank = _num(e.lead_s)
    elif rung == Rung.BRAIN:
        rank = _num(e.brain_rank)
    elif rung == Rung.PLANNER:
        rank = _num(e.planner_rank)
    else:
        rank = 0.0
    return (rung, _NEVER if rank is None else rank, e.game)


def tick(entries, on_screen, hidden, stick: Stick, now, p: Params = QUEUE_PARAMS) -> Plan:
    """Rank every game and answer "what should the hidden stick hold, and should it change". Pure: the
    caller acts on `swap`. A hidden game missing from `entries` is only displaced by an owner."""
    rows, seen, screen, held = [], set(), None, None
    for e in entries if type(entries) in (tuple, list) else ():
        if type(e) is Entry and type(e.game) is str and e.game:
            rows.append(_row(e, stick, now, p))
    rows.sort(key=_order_key)
    kept = []
    for r in rows:   # the best-ranked copy of a game wins
        g = r.entry.game
        if g in seen:
            continue
        seen.add(g)
        if g == on_screen:
            screen = r
            continue
        if g == hidden:
            held = r
        kept.append(r)
    want = next((r for r in kept if r.rung != Rung.NOBODY and (r.rung in _OWNERS or r.reach)), None)
    if want is None:
        swap, why = False, "nothing_to_queue"
    elif type(hidden) is str and hidden and held is None:
        swap, why = want.rung in _OWNERS, "hidden_unknown"
    else:
        swap, why = _swap(held, want, stick, p)
    return Plan(tuple(kept), want, swap, why, screen, held, p)


def _card(row: Row, p: Params) -> dict:
    e = row.entry
    return {
        "game": e.game, "level": row.level.name, "app": row.route.app if row.route else None,
        "prep_s": None if row.prep == _NEVER else round(row.prep, 1), "owner": _OWNER_WORD[row.rung],
        "reason": _REASONS[row.rung], "flippable": flippable(e, p),
    }


def view(plan: Plan) -> dict:
    """`snapshot["game_queue"]`: JSON-safe and spoiler-safe (no tier, join time or value)."""
    p, used = plan.p, {plan.hidden.entry.game} if plan.hidden else set()
    live = [r for r in plan.rows if r.level != Level.R0 and r.entry.game not in used]
    screen = plan.on_screen
    return {
        "on_screen": _card(screen, p) if screen else None,
        "hidden": _card(plan.hidden, p) if plan.hidden else None,
        # the stick we flip away from keeps this game buffered: flipping back costs only the flip
        "partner": screen.entry.game if screen and screen.level >= Level.R4 else None,
        "up_next": [_card(r, p) for r in live[:p.up_next]],
        "cant_reach": [r.entry.game for r in plan.rows if r.level == Level.R0],
        "want": plan.want.entry.game if plan.want else None,
        "swap": plan.swap, "why": plan.why,
    }


# ---- when a tune fails (P-QUEUE section 9) ---------------------------------------------------------


class Failure(str, Enum):
    STICK_HUNG = "stick_hung"
    LINK_REFUSED = "link_refused"
    WALLED = "walled"                 # purchase or sign-in screen
    BLACKOUT = "blackout"
    NEVER_STARTED = "never_started"
    SCRUB_BANNER_MISS = "scrub_banner_miss"
    SCRUB_FAILED = "scrub_failed"
    HOLD_FAILED = "hold_failed"
    NO_ROUTE = "no_route"
    UNKNOWN = "unknown"


class Step(str, Enum):
    WAIT_STICK = "wait_stick"      # the stick's fault: try again once its breaker closes
    NEXT_ROUTE = "next_route"
    STOP = "stop"                  # no more keys on this route; the queue moves on
    RETRY = "retry"                # one fresh attempt on the same route
    STAY_LIVE = "stay_live"        # keep it at the live edge (R3)
    RETUNE = "retune"              # one re-tune at the live edge
    NEXT_GAME = "next_game"


class _Rule(NamedTuple):
    step: Step
    route_off: bool = False    # off for the night
    app_off: bool = False      # ...along with this game's other routes on that app
    advance: bool = True       # nothing waits on this attempt: the queue looks again now
    press_ok: bool = True      # False: no further key on this route
    drop: bool = False         # this attempt's route leaves the entry
    to_r3: bool = False        # it stays playing at the live edge
    reads: int = 0
    phone: str = ""


FAILURE_RULES: dict[Failure, _Rule] = {
    Failure.STICK_HUNG: _Rule(Step.WAIT_STICK, advance=False),
    Failure.LINK_REFUSED: _Rule(Step.NEXT_ROUTE, route_off=True, drop=True),
    Failure.WALLED: _Rule(Step.STOP, route_off=True, app_off=True, press_ok=False, drop=True,
                          phone="Needs a sign-in or purchase. Skipped tonight."),
    Failure.BLACKOUT: _Rule(Step.NEXT_ROUTE, route_off=True, drop=True, reads=1, phone="Blacked out on this app."),
    Failure.NEVER_STARTED: _Rule(Step.NEXT_ROUTE, drop=True),
    Failure.SCRUB_BANNER_MISS: _Rule(Step.STAY_LIVE, to_r3=True, advance=False),
    Failure.SCRUB_FAILED: _Rule(Step.STAY_LIVE, to_r3=True),
    Failure.HOLD_FAILED: _Rule(Step.RETUNE, to_r3=True, advance=False),
    Failure.NO_ROUTE: _Rule(Step.NEXT_GAME, phone="No way to reach this game tonight."),
    Failure.UNKNOWN: _Rule(Step.NEXT_GAME, press_ok=False),
}


@dc.dataclass(frozen=True, slots=True)
class Move:
    """What to do about one failure. `entry` is the game afterwards; None if the input was not an Entry."""
    failure: Failure
    step: Step
    entry: Entry | None
    route_off: tuple[Route, ...] = ()   # off for the night
    advance: bool = True
    press_ok: bool = True
    budget_s: float = 0.0               # 0 = instant or fail fast
    reads: int = 0
    retry_after_s: float = 0.0
    backoff_s: float = 0.0              # the failed route may come back after this long
    phone: str = ""


def parse_failure(x) -> Failure:
    """A Failure from a log string; anything unrecognised is UNKNOWN."""
    try:
        return Failure(x)
    except ValueError:
        return Failure.UNKNOWN


def _budget(kind: Failure, route: Route | None, p: Params) -> float:
    if route is None:
        return 0.0
    if kind == Failure.NEVER_STARTED:
        return _key_cost(None, p.start_budget_s, route)
    if kind in (Failure.SCRUB_BANNER_MISS, Failure.SCRUB_FAILED):
        return p.scrub_budget_s
    return _key_cost(None, p.tune_s, route) if kind == Failure.HOLD_FAILED else 0.0


def on_failure(entry: Entry, failure, attempt=0, p: Params = QUEUE_PARAMS) -> Move:
    """The typed answer to a failed step. `attempt` counts earlier tries of this step; a bad value means
    no retries are left. Budgets are per attempt, and every path ends by moving the queue."""
    kind = parse_failure(failure)
    tries = attempt if type(attempt) is int and attempt >= 0 else _ALL_TRIES
    rule = FAILURE_RULES[kind]
    if type(entry) is not Entry:
        return Move(kind, Step.NEXT_GAME, None, press_ok=rule.press_ok, phone=rule.phone)
    route = _route(entry)
    if kind == Failure.NEVER_STARTED and route is not None and route.app in p.retry_apps and tries < 1:
        return Move(kind, Step.RETRY, entry, advance=False, budget_s=_budget(kind, route, p))
    if kind == Failure.SCRUB_BANNER_MISS and tries >= 1:
        kind, rule = Failure.SCRUB_FAILED, FAILURE_RULES[Failure.SCRUB_FAILED]
    if kind == Failure.HOLD_FAILED and tries >= 1:
        rule = rule._replace(step=Step.STAY_LIVE, advance=True)
    backoff = p.backoff_s if kind in (Failure.NEVER_STARTED, Failure.UNKNOWN) else 0.0
    retry = p.banner_retry_s if kind == Failure.SCRUB_BANNER_MISS else 0.0
    move = Move(kind, rule.step, entry, advance=rule.advance, press_ok=rule.press_ok, reads=rule.reads,
                budget_s=_budget(kind, route, p), retry_after_s=retry, backoff_s=backoff, phone=rule.phone)
    if rule.to_r3:
        return dc.replace(move, entry=dc.replace(entry, level=Level.R3 if route is not None else Level.R0))
    if not rule.drop:
        return dc.replace(move, entry=dc.replace(entry, level=min(level(entry), Level.R2)))
    routes = _live_routes(entry)
    gone = [r for r in routes if rule.app_off and route is not None and r.app == route.app] or routes[:1]
    left = tuple(r for r in routes if r not in gone)
    move = dc.replace(move, entry=dc.replace(entry, routes=left, level=Level.R1 if left else Level.R0),
                      route_off=tuple(gone) if rule.route_off else ())
    if left or rule.step != Step.NEXT_ROUTE:
        return move
    # A route that only backed off may come back, so don't tell the phone it is gone for the night.
    return dc.replace(move, step=Step.NEXT_GAME, phone=move.phone or ("" if backoff else FAILURE_RULES[Failure.NO_ROUTE].phone))
