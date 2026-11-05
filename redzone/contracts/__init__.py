"""Interfaces every thread codes against. See docs/architecture.md.

Owner: INTEGRATION. Changing a shape here is an interface change: open it as its own small PR into
integration/next, list the callers, and update tests/test_contracts.py in the same PR.
"""
from redzone.contracts.breaks import BreakForecast, BreakKind, BreakModel
from redzone.contracts.budget import BUDGET_MS, TickTimer
from redzone.contracts.clock import Clock
from redzone.contracts.events import Event, Primitive, from_play, primitive_of, tokens_v1
from redzone.contracts.feeds import FeedSnapshot, GameFeed, Feeds
from redzone.contracts.flags import DEGRADED, Flag, enabled
from redzone.contracts.guard import HARD_RULES, traced
from redzone.contracts.health import Fault, Fix, Health, HealthReport, Severity
from redzone.contracts.moment import MomentModel, MomentPrediction
from redzone.contracts.reasoner import Expectation, Reasoner, ReasonerView
from redzone.contracts.reel import ReelJump, ReelJumper, ReelOffer, ReelProvider
from redzone.contracts.switching import KINDS, SwitchPaths, deadline
from redzone.contracts.sync import SyncEstimate, SyncModel
from redzone.contracts.trace import MemoryTrace, Stage, TraceEvent, TraceSink
from redzone.contracts.world import GameView, Screen, WorldState, freeze
from redzone.contracts.views import break_view, moment_view, reel_option, sync_view
# The decision core's types are canonical: redzone/decider/core.py (re-exported, never redefined).
from redzone.decider.core import (Action, BreakView, CoreState, DecisionCore, EntryView, Knobs, MomentView, Option,
                                  Priority, ReelView, SwitchPath, SyncView, guarded)

CONTRACT_VERSION = 1


__all__ = [
    "BreakForecast",
    "BreakKind",
    "BreakModel",
    "BUDGET_MS",
    "TickTimer",
    "Clock",
    "Event",
    "Primitive",
    "from_play",
    "primitive_of",
    "tokens_v1",
    "FeedSnapshot",
    "GameFeed",
    "Feeds",
    "DEGRADED",
    "Flag",
    "enabled",
    "HARD_RULES",
    "traced",
    "Fault",
    "Fix",
    "Health",
    "HealthReport",
    "Severity",
    "MomentModel",
    "MomentPrediction",
    "Expectation",
    "Reasoner",
    "ReasonerView",
    "ReelJump",
    "ReelJumper",
    "ReelOffer",
    "ReelProvider",
    "KINDS",
    "SwitchPaths",
    "deadline",
    "SyncEstimate",
    "SyncModel",
    "MemoryTrace",
    "Stage",
    "TraceEvent",
    "TraceSink",
    "GameView",
    "Screen",
    "WorldState",
    "freeze",
    "break_view",
    "moment_view",
    "reel_option",
    "sync_view",
    "Action",
    "BreakView",
    "CoreState",
    "DecisionCore",
    "EntryView",
    "Knobs",
    "MomentView",
    "Option",
    "Priority",
    "ReelView",
    "SwitchPath",
    "SyncView",
    "guarded",
    "CONTRACT_VERSION",
]
