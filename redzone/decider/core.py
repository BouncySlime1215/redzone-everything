"""The decision core: ONE expected-value function that every action is chosen by.

Every part of the app feeds the core through the small views below; the core never reaches into a
feed, a model or a tuner itself. The Decider (decider.py) builds a CoreState each tick, asks the
core for the action with the highest expected value, and lets the guard layer (hard rules: never
cut mid-play, crunch time, your team late, garbage time, your own pause/lock) veto or override it.

    feeds/scorer --> MomentView   (Brain v3 + reasoner: value now, p(big moment soon), must_see, lock_in)
    breaks       --> BreakView    (on a break? remaining p10/p50, restart time, next break soon?)
    reel.py      --> ReelView     (ready reel: length, min length it can be cut to, value)
    switching    --> SwitchPath   (TV flip / HOME flip / in-app tune / replay seek, measured cost, preloaded?)
    sync         --> SyncView     (delay +- error per channel)
    cuts.py      --> EntryView    (land on a dead ball: ok now? rewind? wait?)
    user state   --> Priority     (league order, your teams, bets, focus)

UNITS. Time in seconds; `now` and every `*_at` in epoch seconds of wall time. Values are on the
scorer's 0-100 scale ("points"): what one second of this picture is worth to you. The expected value
of an action is the AVERAGE points per second on screen over the decision window (the horizon),
minus the switching friction (hysteresis) that keeps the TV from flip-flopping:

    ev(a) = (1/H) * sum over the window of  value_on_screen(t) dt   -  friction(a)

    value_on_screen   a live game: its MomentView.value x Priority.weight
                      a commercial (dead air): -dead_air_penalty
                      the switch's own black screen: 0
                      a reel / replay: its value
    friction(a)       0 to stay; switch_margin + cost_s x points_per_switch_s to switch
                      (with the defaults this is exactly today's "lead >= margin + cost" rule)

The guard layer runs AFTER ev: it may only veto an action (turn it into stay / a wait for a safe
moment) or force one (crunch time, your locked game). It never adds value of its own, so every
trade the app makes is visible in `Action.ev` and `Action.ev_stay` in the trace.

KNOBS live under `core.*` in the config so the auto-tuner (redzone/lab/tune.py) can search them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Protocol

# --------------------------------------------------------------------------- views (inputs)

VERBS = ("stay", "switch", "visit", "reel", "replay", "return")


@dataclass(frozen=True)
class MomentView:
    """What this game is worth right now and soon (Brain v3 + the reasoner).

    value        0-100 points per second on screen now (scorer's Scored.score)
    p_big_soon   chance (0-1) a big moment happens here within the horizon (Brain v3 key_soon)
    lock         a moment in progress that must not be cut out of (red-zone drive, power play...)
    crunch, late, garbage, personal: as Scored (personal = your team / bet / fantasy boost, 0-100)
    must_see     the reasoner says this one can't be missed (forces stay / go)
    lock_in      the reasoner's lock-in level 0 (free) .. 3 (never leave): raises friction to leave
    """
    value: float
    p_big_soon: float = 0.0
    lock: str | None = None
    crunch: bool = False
    late: bool = False
    garbage: bool = False
    personal: float = 0.0
    must_see: bool = False
    lock_in: int = 0
    mine_late: bool = False    # your team (or bet) late and close: a hard rule, never left for less


@dataclass(frozen=True)
class BreakView:
    """This game's stoppage as the SCREEN will show it (BreakModel + cuts.DeadWindows).

    on_break         a commercial / dead stretch is on screen now (or will be when a switch lands)
    remaining_p10_s  cautious end: at least this much break is left on screen (0 if not on break)
    remaining_p50_s  median remaining
    restart_at       epoch s the next live play reaches the screen (None: unknown)
    break_soon_p     chance (0-1) a commercial reaches the screen within the horizon (Forecast.p)
    break_soon_s     its expected length if it does (Forecast.expect_s)
    """
    on_break: bool = False
    remaining_p10_s: float = 0.0
    remaining_p50_s: float = 0.0
    restart_at: float | None = None
    break_soon_p: float = 0.0
    break_soon_s: float = 0.0


@dataclass(frozen=True)
class SwitchPath:
    """One way to put this option on screen, with its measured cost (command -> picture)."""
    kind: str                  # "tv_flip" | "home_flip" | "in_app_tune" | "replay_seek" | "none"
    cost_s: float              # median seconds of black/garbage before the picture is up
    cost_p90_s: float = 0.0    # deadline side (0 = same as cost_s)
    preloaded: bool = False    # already playing on the hidden stick
    stick: str | None = None   # which stick this path uses (None: the one on screen / unknown)
    fastest_s: float = 0.0     # quickest the picture has come up this way (entry guard: earliest landing)
    samples: int = 0           # measurements behind cost_s (0 = a configured default, not measured)


@dataclass(frozen=True)
class SyncView:
    """How far this channel runs behind the data (SyncModel)."""
    delay_s: float = 35.0
    sigma_s: float = 3.0
    behind_s: float = 0.0      # extra seconds behind live after a DVR rewind


@dataclass(frozen=True)
class EntryView:
    """Coming in on a dead ball (cuts.plan_entry). ok=False: going now lands mid-play."""
    ok: bool = True
    rewind_s: float = 0.0
    wait_s: float = 0.0
    why: str = ""


@dataclass(frozen=True)
class ReelView:
    """The catch-up reel ready to play (reel.py)."""
    length_s: float
    min_length_s: float = 0.0   # it can be cut down to this (still 5+ clips)
    value: float = 0.0          # sum of unseen clips' importance x freshness, 0-100 scale
    clips: int = 0


@dataclass(frozen=True)
class Priority:
    weight: float = 1.0         # league order x your teams x bets (multiplies MomentView.value)
    focus: bool = False         # your focus game


@dataclass(frozen=True)
class Option:
    """Something the TV can show: a game, RedZone, the reel, or a catch-up replay."""
    id: str
    kind: str = "game"          # "game" | "redzone" | "reel" | "replay"
    target: str | None = None   # tuner target, e.g. "youtubetv:espn"
    moment: MomentView = field(default_factory=lambda: MomentView(0.0))
    brk: BreakView = field(default_factory=BreakView)
    sync: SyncView = field(default_factory=SyncView)
    paths: tuple[SwitchPath, ...] = ()
    entry: EntryView = field(default_factory=EntryView)
    priority: Priority = field(default_factory=Priority)
    reel: ReelView | None = None
    value_override: float | None = None   # a replay's / reel's own value, when it has one

    def best_path(self) -> SwitchPath:
        return min(self.paths, key=lambda p: p.cost_s) if self.paths else SwitchPath("none", 0.0)


@dataclass(frozen=True)
class CoreState:
    """Everything the core sees on one tick (the Decider builds it)."""
    now: float
    current: str | None
    options: dict[str, Option]
    gate_safe: bool = True       # leaving what's on screen now cuts nothing (screen.leave_gate)
    gate_why: str = ""
    home: str | None = None      # the game we left for its break, if any
    horizon_s: float = 60.0


@dataclass
class Action:
    verb: str                    # one of VERBS
    target: str | None
    ev: float = 0.0
    ev_stay: float = 0.0
    reason: str = ""
    path: SwitchPath | None = None
    rewind_s: float = 0.0
    return_at: float | None = None
    kind: str = ""               # the Decider's finer label ("better", "break_visit", "chase"...)
    terms: dict[str, float] = field(default_factory=dict)   # the ev's parts, for the trace
    # ev - ev_stay, computed in the order the replaced rule compared (so float ties land the same way)
    margin: float | None = None

    @property
    def gain(self) -> float:
        return self.ev - self.ev_stay if self.margin is None else self.margin


# --------------------------------------------------------------------------- the one function

@dataclass(frozen=True)
class Knobs:
    """core.* config. Defaults reproduce the rules the core replaced (see tests/test_core.py)."""
    switch_margin: float = 10.0        # decider.switch_margin
    points_per_switch_s: float = 1.0   # each second of black screen costs this many points of lead
    dead_air_penalty: float = 0.0      # points per second a commercial on screen costs (vs 0 for black)
    visit_min: float = 25.0            # decider.break_destination_min: worth leaving a commercial for
    break_risk: float = 0.0            # 0..1: discount a destination by its chance of going to break
    lock_in_step: float = 10.0         # each reasoner lock_in level adds this much friction to leave
    locked_override_lead: float = 20.0
    reel_margin_s: float = 5.0         # reel must fit with this much to spare (both trips included)
    break_value_frac: float = 1.0      # how much of an on-break game's value still counts
    return_tolerance: float = 5.0      # decider.return_tolerance: go back if within this many points

    @classmethod
    def from_cfg(cls, cfg) -> "Knobs":
        """Knobs that existed before the core keep their decider.* key (one key per knob, so old
        overrides still apply); the new ones live under core.*."""
        g = cfg.get
        d = cls()
        return cls(switch_margin=float(g("decider.switch_margin", d.switch_margin)),
                   points_per_switch_s=float(g("core.points_per_switch_s", d.points_per_switch_s)),
                   dead_air_penalty=float(g("core.dead_air_penalty", d.dead_air_penalty)),
                   visit_min=float(g("decider.break_destination_min", d.visit_min)),
                   break_risk=float(g("core.break_risk", d.break_risk)),
                   lock_in_step=float(g("core.lock_in_step", d.lock_in_step)),
                   locked_override_lead=float(g("decider.locked_override_lead", d.locked_override_lead)),
                   reel_margin_s=float(g("core.reel_margin_s", d.reel_margin_s)),
                   break_value_frac=float(g("core.break_value_frac", d.break_value_frac)),
                   return_tolerance=float(g("decider.return_tolerance", d.return_tolerance)))


def rate(o: Option, k: Knobs) -> float:
    """Points per second this option is worth on screen over the window.

    A game on a break keeps break_value_frac of its value (1.0: the scorer has already priced the
    break in) less dead_air_penalty per second. A live game that may go to break inside the window
    is discounted by that chance (break_risk scales how much the core trusts the forecast)."""
    if o.value_override is not None:
        return o.value_override
    v = o.moment.value * o.priority.weight
    if o.brk.on_break:
        return v * k.break_value_frac - k.dead_air_penalty
    if k.break_risk and o.brk.break_soon_p > 0:
        v -= k.break_risk * o.brk.break_soon_p * (v + k.dead_air_penalty)
    return v


def window_value(o: Option, k: Knobs, seconds: float) -> float:
    """Points this option shows over `seconds` on screen (a visit's watch time after both trips)."""
    return rate(o, k) * seconds


def rank(o: Option, k: Knobs) -> tuple:
    """Order destinations by rate (ties by id, as the rules it replaced did)."""
    return (rate(o, k), o.id)


def friction(o: Option, cur: Option | None, k: Knobs, cost_s: float | None = None) -> float:
    """What it takes to be worth a switch: today's margin + cost, more to leave a locked moment."""
    c = o.best_path().cost_s if cost_s is None else cost_s
    f = k.switch_margin + c * k.points_per_switch_s
    if cur is not None:
        if cur.moment.lock and not o.moment.mine_late:   # a lock never holds against your team late
            f = max(f, k.locked_override_lead)
        f += k.lock_in_step * max(0, cur.moment.lock_in)
    return f


def ev(state: CoreState, verb: str, target: str | None, k: Knobs, cost_s: float | None = None) -> Action:
    """THE expected-value function. Every candidate action of every subsystem is scored here.

    stay / switch / return: the target's rate against what's on screen now, less the friction.
    visit / reel: the home game is on a break, so the alternative is the commercial itself
    (-dead_air_penalty); the bar is visit_min ("worth leaving a commercial for").
    replay: the replay's own value (value_override) against the live option it replaces."""
    cur = state.options.get(state.current) if state.current else None
    stay = rate(cur, k) if cur is not None else 0.0
    # A visit is scored against the commercial whoever is on screen (a chained visit can list the game
    # we're on); a switch or return to what's already on is just staying.
    if verb == "stay" or target is None or (target == state.current and verb in ("switch", "return")):
        return Action("stay", state.current, stay, stay, terms={"rate": stay})
    o = state.options[target]
    r = rate(o, k)
    if verb in ("visit", "reel"):
        base = -k.dead_air_penalty
        return Action(verb, target, r - k.visit_min, base, margin=r - (base + k.visit_min),
                      terms={"rate": r, "friction": k.visit_min, "stay": base})
    if verb == "return":
        fr = -k.return_tolerance   # going back where you came from is cheaper than a fresh switch
        return Action(verb, target, r - fr, stay, margin=r - (stay + fr),
                      terms={"rate": r, "friction": fr, "stay": stay})
    if verb == "replay":
        fr = k.switch_margin if cost_s is None else cost_s   # replay: cost_s carries the margin to use
    else:
        fr = friction(o, cur, k, cost_s)
    return Action(verb, target, r - fr, stay, margin=(r - stay) - fr,
                  terms={"rate": r, "friction": fr, "stay": stay})


def best(state: CoreState, verb: str, targets: Iterable[str], k: Knobs,
         cost: Callable[[str], float] | None = None) -> Action | None:
    """The best action of this verb among targets, or None if none beats staying."""
    acts = [ev(state, verb, t, k, cost(t) if cost else None) for t in targets if t in state.options]
    acts = [a for a in acts if a.gain >= 0]
    return max(acts, key=lambda a: (a.ev, a.target or ""), default=None)


def reel_fits(break_left_s: float, reel_s: float, trip_s: float, k: Knobs) -> bool:
    """A reel fits a break when it plays through with both trips and the margin to spare. break_left_s
    is the CAUTIOUS end of the break (p10), never the median."""
    return reel_s > 0 and break_left_s >= reel_s + trip_s + k.reel_margin_s


def fit_reel(break_left_s: float, reel: ReelView, trip_s: float, k: Knobs) -> float:
    """The reel length to play in this window: the full reel, or cut down to fit (never under its
    minimum). 0 = it doesn't fit."""
    room = break_left_s - trip_s - k.reel_margin_s
    if room >= reel.length_s:
        return reel.length_s
    return room if reel.min_length_s and room >= reel.min_length_s else 0.0


# --------------------------------------------------------------------------- guards (hard rules)

class Guard(Protocol):
    """A hard rule on top of the ev: returns a replacement Action (veto / force) or None."""
    name: str

    def check(self, state: CoreState, proposed: Action) -> Action | None: ...


def guarded(state: CoreState, proposed: Action, guards: Iterable[Guard]) -> Action:
    """Apply the hard rules in order; the first that objects wins."""
    for g in guards:
        out = g.check(state, proposed)
        if out is not None:
            out.terms = {**proposed.terms, "guard": 1.0}
            out.reason = f"{g.name}: {out.reason}" if out.reason else g.name
            return out
    return proposed


@dataclass
class NoMidPlayGuard:
    """Never leave a game while a play is on screen."""
    name: str = "no mid-play cut"

    def check(self, state: CoreState, a: Action) -> Action | None:
        if a.verb == "stay" or state.gate_safe:
            return None
        return replace(a, verb="stay", target=state.current, reason=f"waiting: {state.gate_why}")


class DecisionCore:
    """decide(state) -> Action. The Decider calls this; other threads supply the views."""

    def __init__(self, knobs: Knobs, guards: list[Guard] | None = None):
        self.k = knobs
        self.guards = guards if guards is not None else [NoMidPlayGuard()]

    def decide(self, state: CoreState, verbs: dict[str, Iterable[str]] | None = None) -> Action:
        """Score every candidate (verb -> targets; default: switch to every other option) and return
        the best, after the hard rules."""
        verbs = verbs or {"switch": [g for g in state.options if g != state.current]}
        stay = ev(state, "stay", None, self.k)
        cands = [stay] + [a for v, ts in verbs.items() if (a := best(state, v, ts, self.k))]
        top = max(cands, key=lambda a: a.gain)
        return guarded(state, top, self.guards)
