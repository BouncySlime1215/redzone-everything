"""[EXP-056] Planner v1's decider interface (lane 2). Shadow only: a planner never moves the TV.

Planner v1 (Sun Nov 8, docs/plans/2026-11-05-compressed-4day.md) searches over the world model with value
of information: peek with the hidden stick before committing, the flip cost counted. This module is the
contract between it and the hand-built decider, so lane 1 can build the search against a fixed shape:

    PlanInput  one tick's view: the game on screen, the hidden stick's game, every option with its costs
               and the floors the decider already enforces (entry_ok: a landing that is not mid-play now)
    Plan       the planner's answer: stay | switch | peek (hidden stick to a game, TV unchanged) |
               preload, a target, its value and a one-line why
    Planner    plan(PlanInput) -> Plan | None   (None: no opinion this tick)
    WorldModel sample(gid, horizon_s, n) -> futures, for the search; not called by the decider

The decider builds a PlanInput every tick when `noggin.planner.mode` is "shadow" and records
(its decision, the planner's plan) in `Decider.planner_log`, the record shadow grading (EXP-004) reads.
Floors stay the decider's: a planner's switch would still go through the entry plan, the crunch floor
and the safety gates before it could ever act ("act" mode does not exist yet: promotion only).
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Protocol

ACTIONS = ("stay", "switch", "peek", "preload")
LOG_MAX = 2000   # ticks of (decision, plan) kept in memory


@dataclass(frozen=True)
class OptionView:
    gid: str
    league: str               # full league ("cbb", not its base)
    target: str | None        # tuner target ("app:channel")
    score: float              # the decider's own 0-100 score
    crunch: bool = False
    mine: bool = False        # your team / bet (personal >= decider.crunch.hold_personal)
    on_break: bool = False
    flip_s: float = 0.0       # press-to-picture if we switched to it now (preloaded: a flip; else a tune)
    entry_ok: bool = True     # a landing there now would not be mid-play (the decider's entry plan)


@dataclass(frozen=True)
class PlanInput:
    now: float
    current: str | None                      # game on screen
    hidden: str | None                       # game the hidden stick holds
    options: tuple[OptionView, ...] = ()
    decided: str = ""                        # the decider's action this tick (for shadow comparison)
    decided_target: str | None = None


@dataclass(frozen=True)
class Plan:
    action: str
    target: str | None = None
    value: float = 0.0
    why: str = ""
    alternatives: tuple = field(default_factory=tuple)   # (action, target, value) the search also scored

    def __post_init__(self):
        if self.action not in ACTIONS:
            raise ValueError(f"unknown plan action {self.action!r}")


class Planner(Protocol):
    def plan(self, inp: PlanInput) -> Plan | None: ...


class WorldModel(Protocol):
    def sample(self, gid: str, horizon_s: float, n: int) -> list: ...


class Greedy:
    """Reference planner: the best option by score, switching only past `margin` (no world model). A
    stand-in for tests and for the shadow plumbing until planner v1 lands."""

    def __init__(self, margin: float = 10.0):
        self.margin = margin

    def plan(self, inp: PlanInput) -> Plan | None:
        ok = [o for o in inp.options if o.entry_ok and not o.on_break]
        if not ok:
            return Plan("stay", inp.current, why="nothing to land on")
        best = max(ok, key=lambda o: o.score)
        cur = next((o for o in inp.options if o.gid == inp.current), None)
        if best.gid == inp.current or (cur is not None and best.score < cur.score + self.margin):
            return Plan("stay", inp.current, cur.score if cur else 0.0, "nothing clears the margin")
        return Plan("switch", best.gid, best.score, f"{best.gid} leads by {best.score - (cur.score if cur else 0):.0f}")


class ShadowLog:
    """(decision, plan) per tick, newest last; agreement is the first shadow number."""

    def __init__(self, maxlen: int = LOG_MAX):
        self.rows: deque = deque(maxlen=maxlen)

    def add(self, inp: PlanInput, plan: Plan | None) -> None:
        self.rows.append({"t": inp.now, "decided": inp.decided, "decided_target": inp.decided_target,
                          "plan": asdict(plan) if plan is not None else None})

    def agreement(self) -> float | None:
        rows = [r for r in self.rows if r["plan"] is not None]
        if not rows:
            return None
        same = sum((r["plan"]["action"] == r["decided"] or (r["plan"]["action"] == "stay" and r["decided"] != "switch"))
                   and (r["plan"]["action"] != "switch" or r["plan"]["target"] == r["decided_target"]) for r in rows)
        return same / len(rows)
