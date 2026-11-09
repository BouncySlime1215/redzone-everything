"""Brain contract: the typed input and output of the brain, and `valid()`. Numbers and words only.

Why: build plan WP-08 / P2 step 3 (docs/agents/planner/2026-11-09-brain-vs-code.md section 5). The brain
scores options code already allowed and values games; it never returns an action. Code (the arbiter, the
queue, the commit gate) schedules and cuts. A bad output must be dropped in the same tick and the baseline
must run, so `valid()` is the one place that decides "usable".

Pure: frozen dataclasses and standard library only. No clock read (`now` is passed in), no I/O, no model.
Nothing here runs a model or changes the app. Import by namespace (`from redzone.contracts import brain`):
`brain.Option` is a different type from `decider.core.Option` and from standby's.

Units: epoch s, seconds, scores and values 0..100 (the scorer's scale), probabilities 0..1.
`preload` is the field gamequeue reads (its BRAIN rung): game ids, best first.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

BRAIN_SCHEMA = 1
OPTION_VERBS = ("stay", "cut", "rewind", "reel", "fill")
SCORE_MIN, SCORE_MAX = -100.0, 100.0   # scores are relative; values are 0..100


@dataclass(frozen=True)
class Option:
    """One legal choice. Built by CODE; the brain reads it."""
    id: str
    verb: str                 # one of OPTION_VERBS
    game_id: str | None = None
    moment_id: str | None = None
    l1_class: int = 2         # 0 your team, 1 crunch, 2 open (code computes)
    earliest_s: float = 0.0   # flip may start this many seconds from now
    latest_s: float = 0.0     # ...and no later
    needs_prep_s: float = 0.0  # 0 if parked and ready
    ends_on: str = "play_end"  # play_end | setup_resolved | clip_end


@dataclass(frozen=True)
class GameFacts:
    game_id: str
    league: str = ""
    score: tuple[int, int] = (0, 0)
    period: int = 0
    clock_s: float = 0.0
    tier_events: int = 0      # tier events known so far


@dataclass(frozen=True)
class Moment:
    id: str
    game_id: str
    tier: int                 # 1, 2 or 3
    air_at: float             # epoch s it airs on the screen
    is_fact: bool = True      # False = estimate


@dataclass(frozen=True)
class ScreenFacts:
    game_id: str | None = None
    scene: str = ""
    seconds_on_game: float = 0.0
    plays_seen: int = 0


@dataclass(frozen=True)
class StickFacts:
    game_id: str | None = None
    ready: bool = False
    ready_in_s: float = 0.0


@dataclass(frozen=True)
class Preferences:
    """L2, read only. The brain never sees taps or overrides."""
    my_teams: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrainInput:
    tick: int
    now: float                # passed in, so replay is exact
    schema: int = BRAIN_SCHEMA
    lookahead_s: Mapping[str, float] = field(default_factory=dict)   # L per game id
    games: tuple[GameFacts, ...] = ()
    moments: tuple[Moment, ...] = ()
    options: tuple[Option, ...] = ()
    screen: ScreenFacts = field(default_factory=ScreenFacts)
    hidden: tuple[StickFacts, ...] = ()
    prefs: Preferences = field(default_factory=Preferences)
    unseen: tuple[Moment, ...] = ()


@dataclass(frozen=True)
class GameValue:
    game_id: str
    value_now: float          # 0-100
    value_next_3m: float      # 0-100: the terminal value past the look-ahead
    p_tier1_60s: float        # 0-1, calibrated
    why: str = ""             # short, for phone and trace


@dataclass(frozen=True)
class BrainOutput:
    tick: int                 # must echo BrainInput.tick
    model_id: str = ""
    made_at: float = 0.0
    ttl_s: float = 5.0        # Reflex 5 s, Mind 90 s
    schema: int = BRAIN_SCHEMA
    scores: Mapping[str, float] = field(default_factory=dict)   # option id -> score
    values: tuple[GameValue, ...] = ()
    preload: tuple[str, ...] = ()    # game ids, best first (gamequeue reads this)
    stay_one_more: float | None = None   # 0-1, read only at a dead ball on the current game


@runtime_checkable
class Brain(Protocol):
    def think(self, inp: BrainInput) -> BrainOutput:
        """Pure: same input, same output. No clock, no randomness the caller cannot seed, no I/O."""
        ...


def _num(x: object, lo: float, hi: float) -> bool:
    """A real finite number in [lo, hi]. bool and NaN/inf fail."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    return math.isfinite(x) and lo <= x <= hi


def problem(out: BrainOutput, inp: BrainInput) -> str | None:
    """Why `out` is unusable for `inp`, or None. Reasons: schema, tick, stale, nan, range, unknown_id."""
    if not isinstance(out, BrainOutput) or out.schema != BRAIN_SCHEMA or inp.schema != BRAIN_SCHEMA:
        return "schema"
    if out.tick != inp.tick:
        return "tick"
    if not (_num(out.made_at, -math.inf, math.inf) and _num(out.ttl_s, 0.0, math.inf) and out.ttl_s > 0):
        return "nan"
    if out.made_at > inp.now or inp.now - out.made_at > out.ttl_s:
        return "stale"      # from the future, or past its TTL
    option_ids = {o.id for o in inp.options}
    game_ids = {g.game_id for g in inp.games}
    for oid, s in out.scores.items():
        if not _num(s, SCORE_MIN, SCORE_MAX):
            return "nan"
        if oid not in option_ids:
            return "unknown_id"
    for v in out.values:
        if not (_num(v.value_now, 0, 100) and _num(v.value_next_3m, 0, 100) and _num(v.p_tier1_60s, 0, 1)):
            return "nan"
        if v.game_id not in game_ids:
            return "unknown_id"
    if any(g not in game_ids for g in out.preload) or len(set(out.preload)) != len(out.preload):
        return "unknown_id"
    if out.stay_one_more is not None and not _num(out.stay_one_more, 0, 1):
        return "nan"
    return None


def valid(out: BrainOutput, inp: BrainInput) -> bool:
    """True when the whole output may be used. False means: drop it and run the baseline this tick."""
    return problem(out, inp) is None
