"""The hard-rule (guard) layer, as seen by the rest of the system.

The guard TYPES are the decision core's: redzone/decider/core.py `Guard` (check(state, proposed) ->
Action | None), `guarded()` and the guards themselves (owner: DECISION). This file only adds what
the other owners need around them:
  - HARD_RULES: the rule list, in order (owned by the user, via INTEGRATION; changing it is an
    interface change),
  - traced(): run the guards and emit one Trace `guarded` event per decision that moves or is held.
"""
from __future__ import annotations

from typing import Iterable

from redzone.contracts.trace import Stage, TraceEvent, TraceSink
from redzone.decider.core import Action, CoreState, Guard, guarded

# Order matters: the first guard that objects wins (core.guarded). Names are the guards' `name`.
HARD_RULES: tuple[tuple[str, str], ...] = (
    ("user control", "paused, your lock, manual hands-off, safe mode, focus mode"),
    ("no overlap", "never start a switch while one is in flight"),
    ("no mid-play cut", "never cut away while a play is on screen (screen.leave_gate)"),
    ("no mid-play entry", "the whole entry window lands on a dead ball (cuts.plan_entry / EntryView.ok)"),
    ("locks", "never leave a locked moment, crunch time or your team late except by the lock rules"),
    ("trip fits", "visit/reel: there + back + exit fits the break's cautious (p10) remaining length"),
    ("no dead-air entry", "don't switch INTO a break while the current game is live, unless crunch/your team"),
    ("fresh data", "never move on stale feeds or an unknown TV input"),
)


def traced(state: CoreState, proposed: Action, guards: Iterable[Guard], sink: TraceSink | None,
           switch_id: str, component: str = "guard") -> Action:
    """core.guarded() plus a Trace `guarded` event: what was proposed, what came out, which rule."""
    out = guarded(state, proposed, guards)
    if sink is not None and (proposed.verb != "stay" or out.verb != "stay"):
        held = out is not proposed
        sink.emit(TraceEvent(switch_id, Stage.GUARDED, state.now, component, {
            "proposed": proposed.verb, "proposed_target": proposed.target,
            "verb": out.verb, "target": out.target, "held": held,
            "rule": out.reason.split(":", 1)[0] if held else "",
            "ev": round(proposed.ev, 3), "ev_stay": round(proposed.ev_stay, 3)}))
    return out
