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


def dwell_left(since: float, min_s: float, kind: str, kinds, now: float) -> float:
    """Seconds this optional switch kind must still wait on the current game (<= 0: free to go)."""
    if min_s <= 0 or kind not in set(kinds or ()):
        return 0.0
    return since + min_s - now


def too_soon(last_switch_at: float | None, gap_s: float, now: float) -> bool:
    """The switch-rate cap: no flip again within gap_s of the last switch (anti ping-pong)."""
    return last_switch_at is not None and now - last_switch_at < gap_s


def may_leave(gate_ok: bool, lock) -> bool:
    """Rule #1 and the moment lock: the leave gate proves a gap on screen and no LOCKED moment holds this game."""
    return bool(gate_ok) and not lock


def must_see(best_score: float, cur_score: float, min_score: float, lead: float) -> bool:
    """Something huge elsewhere: the only thing that may pull the TV off a must-see focus game."""
    return best_score >= min_score and best_score >= cur_score + lead


@dataclass(frozen=True)
class GameRow:
    """One game code could go to, with what code already worked out about it."""
    game_id: str
    app: str = ""
    tunable: bool = True
    dark: bool = False            # feed_dark: no proof of a dead ball
    landable: bool = True         # the entry plan found a dead-ball landing
    reel: bool = False
    l1_class: int = 2             # 0 your team, 1 crunch, 2 open (code computes)
    must_see: bool = False
    latest_s: float = 0.0
    needs_prep_s: float = 0.0


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
    since: float = 0.0
    dwell_min_s: float = 0.0
    dwell_kinds: tuple[str, ...] = ()
    last_switch_at: float | None = None
    min_gap_s: float = 0.0
    gate_ok: bool = True
    moment_lock: str = ""
    focus_game: str | None = None     # a focus mode is on: only this game (or a must-see, below)
    focus_must_see: bool = False
    banned_apps: frozenset[str] = frozenset({"peacock"})
    games: tuple[GameRow, ...] = ()


STAY = brain.Option(id="stay", verb="stay")


def legal_options(f: Facts) -> tuple[brain.Option, ...]:
    """Every legal choice now, stay first and always present. Empty of cuts means: hold."""
    if user_block(f.safe_mode, f.paused, f.locked_game, f.manual_until, f.now):
        return (STAY,)
    if arriving(f.now, f.arrival_until):
        return (STAY,)
    if f.current and dwell_left(f.since, f.dwell_min_s, kind_of("cut") or "", f.dwell_kinds, f.now) > 0:
        return (STAY,)
    if too_soon(f.last_switch_at, f.min_gap_s, f.now):
        return (STAY,)
    if f.current and not may_leave(f.gate_ok, f.moment_lock):
        return (STAY,)
    opts = [STAY]
    for g in f.games:
        if g.game_id == f.current or not g.tunable or g.dark or not g.landable or g.app.lower() in f.banned_apps:
            continue
        if f.focus_game and g.game_id != f.focus_game and not (f.focus_must_see and g.must_see):
            continue
        verb = "reel" if g.reel else "cut"
        opts.append(brain.Option(id=f"{verb}:{g.game_id}", verb=verb, game_id=g.game_id, l1_class=g.l1_class,
                                 latest_s=g.latest_s, needs_prep_s=g.needs_prep_s))
    return tuple(o for o in opts if o.verb == "stay" or kind_of(o.verb))   # fails closed on a verb with no safe kind


def refusals(f: Facts, game_id: str) -> tuple[str, ...]:
    """Every rule that keeps a cut to `game_id` off the list right now, in legal_options' order; empty when it is on the list.
    Read only by scripts/veto_audit.py: the decider never calls it, so no decision can depend on it."""
    why = user_block(f.safe_mode, f.paused, f.locked_game, f.manual_until, f.now)
    out = [f"user:{why}"] if why else []
    if arriving(f.now, f.arrival_until):
        out.append("arriving")
    if f.current and dwell_left(f.since, f.dwell_min_s, kind_of("cut") or "", f.dwell_kinds, f.now) > 0:
        out.append("dwell")
    if too_soon(f.last_switch_at, f.min_gap_s, f.now):
        out.append("too_soon")
    if f.current and not f.gate_ok:
        out.append("leave_gate")
    if f.current and f.moment_lock:
        out.append("moment_lock")
    g = next((r for r in f.games if r.game_id == game_id), None)
    if g is None:
        return (*out, "unknown_game")
    out += [n for n, hit in (("on_screen", game_id == f.current), ("not_tunable", not g.tunable), ("dark", g.dark),
                             ("no_landing", not g.landable), ("banned_app", g.app.lower() in f.banned_apps),
                             ("focus", bool(f.focus_game) and game_id != f.focus_game and not (f.focus_must_see and g.must_see)))
            if hit]
    return tuple(out)
