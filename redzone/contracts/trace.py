"""Trace contract: one trace per switch, decision -> command -> picture confirmed, with timings.

Owner: PLATFORM (observability). Every component that touches a switch appends to its trace by
switch_id; nothing reads traces on the decision path. Sink: data/traces.jsonl (one line per event).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Protocol, runtime_checkable


class Stage(str, Enum):
    DECIDED = "decided"            # DecisionCore produced the action
    GUARDED = "guarded"            # Guard allowed / replaced it
    COMMAND = "command"            # first command sent to stick/TV
    PICTURE = "picture"            # picture confirmed (playback transition / input readback)
    ABORTED = "aborted"            # cancelled before picture
    FAILED = "failed"              # command failed / timed out


@dataclass(frozen=True)
class TraceEvent:
    switch_id: str
    stage: Stage
    at: float                      # epoch s
    component: str                 # "decider", "guard", "control", "supervisor", ...
    detail: dict = field(default_factory=dict)

    def jsonl(self) -> str:
        d = asdict(self)
        d["stage"] = self.stage.value
        return json.dumps(d, separators=(",", ":"), default=str)


@runtime_checkable
class TraceSink(Protocol):
    def emit(self, ev: TraceEvent) -> None: ...


class MemoryTrace:
    """In-memory sink for tests and the end-to-end night test."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, ev: TraceEvent) -> None:
        self.events.append(ev)

    def of(self, switch_id: str) -> list[TraceEvent]:
        return [e for e in self.events if e.switch_id == switch_id]

    def timings(self, switch_id: str) -> dict[str, float]:
        """Seconds from DECIDED to each later stage."""
        evs = self.of(switch_id)
        t0 = next((e.at for e in evs if e.stage == Stage.DECIDED), None)
        return {} if t0 is None else {e.stage.value: e.at - t0 for e in evs}
