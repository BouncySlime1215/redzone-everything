"""legal_options(): what the show may do right now, as typed options. The pre-shield (S2) of the brain plan.

Why: build plan WP-16 / brain-vs-code S2. These rules were spread over the decider's twelve stages, so a brain could
only ever be handed "everything" and vetoed afterwards. Code lists the legal choices first; a brain only scores them.

Pure and clock-free: each rule is a small function of facts, and `Decider` calls the same functions for its own
decisions, so the list a brain sees and the moves the app makes cannot disagree. No I/O, no decider import.

A brain's option is never of kind `resume` or `skip`: those land on a planned frame and the commit gate exempts them
from its landing check (tests/test_adversarial_replay.py, the resume hole). `rewind` is a replay, not a resume.
"""
from __future__ import annotations

from dataclasses import dataclass

from redzone.contracts import brain

FORBIDDEN_KINDS = frozenset({"resume", "skip"})   # the gate's ENTRY_EXEMPT holes: code-only kinds
_VERB_KIND = {"cut": "better", "rewind": "replay", "reel": "break_visit", "fill": "break_visit"}


def kind_of(verb: str) -> str | None:
    """The Decision kind a brain's option becomes. None: not an action (stay) or not a verb a brain may have."""
    k = _VERB_KIND.get(verb)
    return None if k in FORBIDDEN_KINDS else k


def user_block(safe_mode: bool, paused: bool, locked_game: str | None, manual_until: float, now: float) -> str | None:
    """Why only Nick (or safe mode) may move the TV now, or None. The names are the decider's owner codes."""
    if safe_mode:
        return "safe_mode"
    if paused:
        return "paused"
    if locked_game:
        return "locked_game"
    if now < manual_until:
        return "manual_pause"
    return None


def arriving(now: float, until: float) -> bool:
    """A switch is still landing: nothing else may start until the picture is up."""
    return now < until


def tunable(gid: str, x, muted) -> bool:
    """We know where this game is on, its data isn't stale, and Nick didn't mute it."""
    return bool(x.target) and not x.stale and gid not in muted


def no_dead_ball_data(has_timeline: bool, marks) -> bool:
    """Rule #1: nothing in the data can prove a dead ball on this game yet."""
    return not has_timeline or not marks


def feed_dark(has_timeline: bool, marks, fresh_at: float | None, now: float, stale_s: float) -> bool:
    """Rule #1: this game's feed can't prove a dead ball: no marks at all, or the data has gone quiet."""
    return no_dead_ball_data(has_timeline, marks) or fresh_at is None or now - fresh_at > stale_s


@dataclass(frozen=True)
class Facts:
    """What the rules read for one tick. Built by `Decider.legal_facts`; epoch s."""
    now: float
    current: str | None = None
    safe_mode: bool = False
    paused: bool = False
    locked_game: str | None = None
    manual_until: float = 0.0
    arrival_until: float = 0.0
    #F


STAY = brain.Option(id="stay", verb="stay")


def legal_options(f: Facts) -> tuple[brain.Option, ...]:
    """Every legal choice now, stay first and always present. Empty of cuts means: hold."""
    if user_block(f.safe_mode, f.paused, f.locked_game, f.manual_until, f.now):
        return (STAY,)
    if arriving(f.now, f.arrival_until):
        return (STAY,)
    #B
    return (STAY,)
