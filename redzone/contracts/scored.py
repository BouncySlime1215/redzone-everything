"""[one brain, step 7] Typed, frozen views of the scorer's and decider's outputs (docs/onebrain-migration.md).
Serves: decision provenance (Nick's "every TV action and decision is traced") and the later steps 8-10,
which move ranking onto these types. Owner: INTEGRATION.

Shadow only: the live path still passes redzone.scoring.engine.Scored, redzone.decider.decider.Candidate
and Decision. `from_*` copy them into these frozen types (the per-tick WorldState holds them); nothing
decides on them yet. A field here is a promise: the producer fills it every tick, or it is None.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Scored:
    game_id: str
    league: str                 # League.value
    score: float                # what the decider ranks on (smoothed)
    raw: float                  # this poll before smoothing
    moment: float
    stakes: float
    personal: float
    reason: str
    lock: str | None = None
    pre_moment: str | None = None
    is_break: bool = False
    garbage: bool = False
    late: bool = False
    crunch: bool = False
    crunch_now: bool = False
    factors: Mapping[str, float] = MappingProxyType({})
    watch_value: float | None = None     # Heat 2.0 (lane 1); None until computed
    watch_why: str = ""
    crunch_state: str | None = None      # "entering" | "in" | "leaving"; None until computed


@dataclass(frozen=True)
class Candidate:
    scored: Scored
    target: str | None          # tuner target; None = not tunable now
    stale: bool = False         # its feed went dark: may not be entered

    @property
    def game_id(self) -> str:
        return self.scored.game_id


@dataclass(frozen=True)
class Decision:
    at: float
    action: str                 # "stay" | "switch" | "replay"
    target: str | None          # game id
    reason: str
    kind: str = ""
    gate: str = ""
    want: str | None = None


def from_scored(s) -> Scored:
    """scoring.engine.Scored -> the frozen contract copy."""
    return Scored(game_id=s.game_id, league=getattr(s.league, "value", str(s.league)), score=float(s.score),
                  raw=float(s.raw), moment=float(s.moment), stakes=float(s.stakes), personal=float(s.personal),
                  reason=s.reason, lock=s.lock, pre_moment=s.pre_moment, is_break=bool(s.is_break),
                  garbage=bool(s.garbage), late=bool(s.late), crunch=bool(s.crunch), crunch_now=bool(s.crunch_now),
                  factors=MappingProxyType(dict(s.factors or {})), watch_value=s.watch_value,
                  watch_why=s.watch_why or "", crunch_state=s.crunch_state)


def from_candidate(c) -> Candidate:
    return Candidate(from_scored(c.scored), c.target, bool(c.stale))


def from_decision(d) -> Decision:
    return Decision(at=float(d.at), action=d.action, target=d.target, reason=d.reason, kind=d.kind or "",
                    gate=d.gate or "", want=d.want)
