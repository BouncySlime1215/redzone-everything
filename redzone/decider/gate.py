"""One commit gate at the flip for every non-tap switch: an ordered list of named rules, re-read on every poll.

The controller polls `may_cut` until the switch goes or times out. With commit_gate.enabled on, `Engine._act.cut_veto`
asks `commit_gate` here instead of branching by kind, and the answer is the name of the first rule that says no.
Kinds differ only in which landing and leave BAR a rule uses (a jump's picture is judged by jump_lands_dead, an entry
by the plan its decision was made on); no kind has its own path around a rule. Phone taps never come here: a second
tap is Nick's call, and `Engine._act.cut_veto` keeps their path.

Host is the Engine (or a replay stand-in): this module reads it and never imports it. Pure apart from those reads.

Why: Rule #1 kept leaking through paths with their own checks (P1-REVIEW-01, noggin.md 11b); one gate with a name per
veto closes that class and makes every held switch explainable (build plan WP-05, brain-vs-code S4).
"""
from __future__ import annotations

import time
from dataclasses import replace as _replace
from functools import cached_property
from typing import Any, Callable

from redzone.channels import redzone_on
from redzone.decider.decider import ENTRY_EXEMPT, PSEUDO, REDZONE, REEL, Candidate
from redzone.decider.screen import break_on_arrival, catchable
from redzone.models import Phase
from redzone.reel import REEL_DIR


class Flip:
    """The facts of one poll. Each is read once, when a rule first needs it, so a poll judges one moment."""

    def __init__(self, host: Any, d: Any, before: str | None, target: str | None, cands: dict,
                 replay_exit: float | None = None, now: float | None = None):
        self.host, self.d, self.before, self.target, self.cands = host, d, before, target, cands
        self.replay_exit = replay_exit
        self.now = time.time() if now is None else now

    @property
    def jump(self) -> bool:
        return self.d.kind == "jump"

    @property
    def is_switch(self) -> bool:
        return self.d.action == "switch"

    @cached_property
    def game(self) -> Any:
        """The game this switch goes to; None for a replay, RedZone, a reel, or a game the merger lost."""
        if not self.is_switch or self.d.target in (REDZONE, REEL):
            return None
        return self.host.merger.games.get(self.d.target or "")

    @property
    def to_game(self) -> bool:
        """This switch needs a real game behind it (a replay and the pseudo channels have their own rules)."""
        return self.is_switch and self.d.target not in (REDZONE, REEL)

    @cached_property
    def timeline(self) -> Any:
        g = self.game
        return self.host.decider.timelines.get(g.game_id) if g is not None else None

    @cached_property
    def cost(self) -> float:
        return self.host.control.deadline_cost(self.target)

    @cached_property
    def delay(self) -> Any:
        return self.host.delays.get(self.target or "unknown")

    @cached_property
    def live(self) -> dict:
        """The decision's candidates on the scores of NOW: a game can turn into a red zone trip during the wait."""
        scored = self.host.scored
        return {gid: _replace(c, scored=scored[gid]) if gid in (self.before, self.d.target) and gid in scored else c
                for gid, c in self.cands.items()}

    @cached_property
    def replan(self) -> tuple[bool, Any]:
        """(this entry is timed, its plan now). Planned on the bar the decision used, and only where the decision
        was timed. [P1-REVIEW-01] Whistle-timed leagues (hold_only) count: their flip can come 25 s after the decision.
        The scrub path doesn't: its picture isn't the live edge a plan judges (landing_whistle gates it)."""
        d, host = self.d, self.host
        if self.jump or self.game is None or d.kind == "user":
            return False, None
        dec = host.decider
        one = {d.target: Candidate(host.scored[d.target], self.target)} if d.target in host.scored else {}
        x = one.get(d.target)
        timed = dec._cut_timed(d.target, one)
        entry_only = not timed and d.kind not in ENTRY_EXEMPT and dec._entry_only(x, d) \
            and not host.control._scrub_wanted(self.target)
        if not (timed or entry_only):
            return False, None
        pos = entry_only and dec._possession_start(x, d)
        urgent = d.kind == "crunch" or (entry_only and dec._entry_urgent(d, x, one))
        return True, dec.entry_plan(d.target, one, self.now, self.cost, urgent=urgent or pos, possession=pos)


# ----- the rules: True means veto --------------------------------------------------------------------------------

def _paused_or_dry_run(f: Flip) -> bool:
    return bool(f.host.decider.user.paused or f.host.cfg.get("mode.dry_run", True))


def _deferred(f: Flip) -> bool:
    return f.now < f.d.at + float(getattr(f.d, "defer_s", 0.0) or 0.0)   # [EXP-803 A1] the break's tail


def _replay_used(f: Flip) -> bool:
    return f.d.action == "replay" and not (f.d.replay and not f.d.replay.replayed)


def _redzone_off(f: Flip) -> bool:
    return f.is_switch and f.d.target == REDZONE and not redzone_on(f.host.merger.games.values())


def _reel_missing(f: Flip) -> bool:
    return f.is_switch and f.d.target == REEL and not (
        bool(f.target and f.target.startswith("reel:")) and (REEL_DIR / "reel.mp4").exists())


def _not_live(f: Flip) -> bool:
    return f.to_game and (f.game is None or f.game.phase != Phase.LIVE)


def _feed_stale(f: Flip) -> bool:
    return f.game is not None and bool(f.host.feeds.stale(f.game.game_id))


def _target_moved(f: Flip) -> bool:
    return f.game is not None and getattr(f.host.where.get(f.game.game_id), "target", None) != f.target


def _no_timeline(f: Flip) -> bool:
    return f.to_game and f.timeline is None


def _chase_gone(f: Flip) -> bool:
    return f.d.kind == "chase" and f.timeline is not None and catchable(f.timeline, f.delay, f.now, f.cost) is None


def _landing(f: Flip) -> bool:
    """Rule #1: the picture must still come up on a dead ball."""
    if f.game is None:
        return False
    if f.jump:
        return not f.host.decider.jump_lands_dead(f.d.target, f.live.get(f.d.target), f.now, f.cost)
    timed, plan = f.replan
    return timed and plan is not None and not plan.ok


def _landing_unproven(f: Flip) -> bool:
    """A timed entry with no landing evidence left waits, like one that has it and fails. [D-01] No live fallback."""
    timed, plan = f.replan
    return timed and plan is None and bool(f.host.cfg.get("decider.entry.enabled", True))


def _rewind_changed(f: Flip) -> bool:
    """The hidden stick was cued for the decision's rewind: a different one now means a different frame on screen."""
    timed, plan = f.replan
    return timed and plan is not None and plan.ok and plan.rewind_s != getattr(f.d, "rewind_s", 0.0)


def _arrival_break(f: Flip) -> bool:
    """Not onto a commercial. A planned rewind is exempt: landing before the snap on purpose, the tail is the line-up."""
    if f.game is None or f.jump or f.timeline is None:
        return False
    timed, plan = f.replan
    if timed and plan is not None and plan.rewind_s:
        return False
    return bool(break_on_arrival(f.timeline, f.delay, f.now, f.now + f.cost))


def _hold(f: Flip) -> bool:
    """[P1-REVIEW-06] The screen's game holds the screen on the scores of NOW. Non-jump kinds may leave a held game on
    purpose (crunch to better crunch), so for them it vetoes only a hold the decision didn't know about."""
    if not f.is_switch or f.before is None or f.before not in f.live:
        return False
    dec = f.host.decider
    if not dec._jump_hold(f.live[f.before]):
        return False
    return f.jump or f.before not in f.cands or not dec._jump_hold(f.cands[f.before])


def _ending_airing(f: Flip) -> bool:
    """The game on screen ended in the data but its last play is still airing (noggin.md 11b: a switch off an ended
    game skipped the leave gate)."""
    if not f.is_switch or not f.before or f.before in PSEUDO or f.replay_exit is not None:
        return False
    g = f.host.merger.games.get(f.before)
    if g is not None and g.phase != Phase.POST:
        return False
    return f.host.decider._ending_still_airing(f.before, f.now) > 0


def _replay_exit(f: Flip) -> bool:
    return f.replay_exit is not None and f.now < f.replay_exit


def _leave(f: Flip) -> bool:
    """Leaving the game on screen. A jump uses the rotation's gate (dead windows, low-leverage leaves), anything else the
    'data covers the screen' window. Coming off a replay is _replay_exit's."""
    if f.replay_exit is not None:
        return False
    if f.jump:
        return not f.host.decider.jump_may_leave(f.before, f.live, f.now, f.host._leave_margin())
    return not f.host.safe_to_leave(f.before, required_s=8.0)   # the value cut_veto has always used


def _landing_whistle(f: Flip) -> bool:
    """[EXP-610] Scrub path: the delayed picture at a stopped clock. A jump's own bar already judges its delayed picture."""
    return not f.jump and not f.host._flip_lands_whistle(f.d, f.target)


Rule = tuple[str, Callable[[Flip], bool]]

# The order is the order of the name you read in the log: first what makes the switch meaningless, then the landing,
# then the leave. Is the entry still valid = ENTRY_RULES; may it go now = GATE_RULES.
ENTRY_RULES: tuple[Rule, ...] = (
    ("replay_used", _replay_used),
    ("redzone_off", _redzone_off),
    ("reel_missing", _reel_missing),
    ("not_live", _not_live),
    ("feed_stale", _feed_stale),
    ("target_moved", _target_moved),
    ("no_timeline", _no_timeline),
    ("chase_gone", _chase_gone),
    ("landing", _landing),
    ("landing_unproven", _landing_unproven),
    ("rewind_changed", _rewind_changed),
    ("arrival_break", _arrival_break),
)

GATE_RULES: tuple[Rule, ...] = (
    ("paused_or_dry_run", _paused_or_dry_run),
    ("deferred", _deferred),
    *ENTRY_RULES,
    ("hold", _hold),
    ("ending_airing", _ending_airing),
    ("replay_exit", _replay_exit),
    ("leave", _leave),
    ("landing_whistle", _landing_whistle),
)


def commit_gate(host: Any, d: Any, before: str | None, target: str | None, cands: dict,
                replay_exit: float | None = None, now: float | None = None) -> str:
    """Why this non-tap switch may not cut right now ("" = it may): the name of the first rule that says no."""
    f = Flip(host, d, before, target, cands, replay_exit, now)
    veto = next((name for name, rule in GATE_RULES if rule(f)), "")
    if f.jump and d.jump is not None:
        d.jump["veto"], d.jump["veto_cost_s"] = veto, round(host.control.deadline_cost(target), 1)
    return veto


def entry_veto(host: Any, d: Any, target: str | None, now: float | None = None) -> str:
    """Why the destination is no longer valid ("" = it is): the entry half of the gate."""
    one = {d.target: Candidate(host.scored[d.target], target)} if d.target in host.scored else {}
    f = Flip(host, d, None, target, one, None, now)
    return next((name for name, rule in ENTRY_RULES if rule(f)), "")
