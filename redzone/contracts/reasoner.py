"""Reasoner contract: the local LLM's read of the night. Advisory only.

Owner: LOCAL AI ("Train watchdog model for unseen errors"). Implementation: agent/ + the shared
LLM server (agent/llm_server.py, one base model + one LoRA per job, <= 1.5 GB RAM with watchdog).
Called by: DecisionCore, which may only RAISE a game's value or ask for a lock-in inside the
guard's rules; it never bypasses the guard. Cadence: async, at most every 30 s per game and only
for hot or user-team games; results carry a TTL and are ignored once stale. The decision tick
never waits on it (a missing view = no opinion).

Units: epoch s, seconds, probabilities 0..1, value bonus in score points (0..20, clamped by the core).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Expectation:
    what: str            # "SEA 4th-and-short go", "power play goal chance"
    within_s: float      # feed seconds from `at`
    p: float             # 0..1


@dataclass(frozen=True)
class ReasonerView:
    game_id: str
    at: float                      # epoch s it was produced
    ttl_s: float = 60.0            # ignore after at + ttl_s
    lock_in: bool = False          # "stay here through the next stoppage"
    lock_reason: str = ""
    value_bonus: float = 0.0       # suggested score points; the core clamps to [0, 20]
    storyline: str = ""            # one line for the phone
    expectations: tuple[Expectation, ...] = field(default_factory=tuple)

    def fresh(self, now: float) -> bool:
        return now - self.at <= self.ttl_s


@runtime_checkable
class Reasoner(Protocol):
    def view(self, game_id: str, now: float) -> ReasonerView | None:
        """Latest fresh view, or None. Must return immediately (no model call inline)."""
        ...
