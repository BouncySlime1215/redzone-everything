"""Explicit state machines: one named state, declared transitions, a reason and a history.

Owner: PLATFORM. Called by: every component whose lifecycle used to live in loose flags (the standby
stick, the screen, the pending switch, the deciders' handoff, the supervisor's cool-downs, deploys, ...).
The specs themselves live in redzone/machines/ (one file per area) so the whole map is in one place;
docs/state-machines.md is generated from them (scripts/state_machines.py, checked in CI).

Why: the app's hardest bugs were states spread over 3-10 attributes that several tasks wrote, so
contradictory combinations were reachable and nobody could say "what is the standby stick doing?".
A Machine fixes that three ways:
  UX  one place decides, so the TV can't be pulled two ways at once;
  DX  `m.to("ready", why=...)` is the only way state moves; illegal moves fail loudly in tests;
  AX  GET /api/machines answers "what is everything doing, since when and why" in one read.

Rules:
- `Spec` is the static declaration (states with a one-line meaning, allowed edges, the initial state).
- `Machine` is one live instance. Only `to()` moves it; `reset()` is for restore/restart only.
- An undeclared edge raises IllegalTransition when STRICT (tests turn it on, tests/conftest.py).
  In production it logs a warning once per edge, counts it and still moves: a wrong table must never
  freeze the TV. The counter shows up in /api/machines so a bad edge gets noticed and declared.
- Same-state `to()` is a no-op (returns False) and keeps `since`: re-asserting a state doesn't reset
  its age. Pass `why` anyway; it updates the reason without a history row.
"""
from __future__ import annotations

import logging
import os
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Mapping

log = logging.getLogger(__name__)

# Tests set this True (tests/conftest.py). Env lets a dev run the live app strictly on purpose.
STRICT = os.environ.get("RZ_FSM_STRICT", "") == "1"

HISTORY_LEN = 24
SPECS: dict[str, "Spec"] = {}
_LIVE: "weakref.WeakSet[Machine]" = weakref.WeakSet()


class IllegalTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class Spec:
    """A declared machine. `states` maps state -> what it means; `edges` maps state -> states it may
    move to. `ANY` in a target list means any state (use for reset-like moves such as "lost")."""
    name: str
    owner: str                       # "control.Control.standby" - where the live instance lives
    doc: str                         # one line: what this machine is about
    states: Mapping[str, str]
    edges: Mapping[str, tuple[str, ...]]
    initial: str
    keyed: bool = False              # one machine per key (per target, per topic, ...)

    def __post_init__(self):
        bad = [s for s in [self.initial, *self.edges] if s not in self.states]
        bad += [t for ts in self.edges.values() for t in ts if t != ANY and t not in self.states]
        if bad:
            raise ValueError(f"{self.name}: unknown state(s) {sorted(set(bad))}")
        if self.name in SPECS and SPECS[self.name] is not self:
            # Re-import (tests reload modules) replaces the spec of the same name.
            pass
        SPECS[self.name] = self

    def allowed(self, a: str, b: str) -> bool:
        ts = self.edges.get(a, ())
        return b in ts or ANY in ts

    def new(self, *, key: str = "", clock: Callable[[], float] = time.time,
            on_change: Callable[["Machine", str, str, str], None] | None = None,
            state: str | None = None) -> "Machine":
        return Machine(self, key=key, clock=clock, on_change=on_change, state=state)


ANY = "*"


@dataclass
class Machine:
    spec: Spec
    key: str = ""
    clock: Callable[[], float] = time.time
    on_change: Callable[["Machine", str, str, str], None] | None = None
    state: str | None = None
    since: float = 0.0
    why: str = ""
    illegal: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LEN))
    _warned: set = field(default_factory=set)

    def __post_init__(self):
        if self.state is None:
            self.state = self.spec.initial
        if self.state not in self.spec.states:
            raise ValueError(f"{self.spec.name}: unknown state {self.state!r}")
        self.since = self.clock()
        _LIVE.add(self)

    __hash__ = object.__hash__        # WeakSet needs it; dataclass eq would remove it

    def __eq__(self, other):          # identity, not field equality
        return self is other

    # -- reading ---------------------------------------------------------------------------------
    def is_(self, *states: str) -> bool:
        return self.state in states

    def age(self, now: float | None = None) -> float:
        return max(0.0, (self.clock() if now is None else now) - self.since)

    def can(self, new: str) -> bool:
        return new == self.state or self.spec.allowed(self.state, new)

    # -- moving ----------------------------------------------------------------------------------
    def to(self, new: str, why: str = "", now: float | None = None) -> bool:
        """Move to `new`. Returns True when the state changed."""
        if new not in self.spec.states:
            raise ValueError(f"{self.spec.name}: unknown state {new!r}")
        if new == self.state:
            if why:
                self.why = why
            return False
        if not self.spec.allowed(self.state, new):
            self.illegal += 1
            edge = (self.state, new)
            msg = f"{self.spec.name}{'[' + self.key + ']' if self.key else ''}: {self.state} -> {new} not declared ({why})"
            if STRICT:
                raise IllegalTransition(msg)
            if edge not in self._warned:
                self._warned.add(edge)
                log.warning("state machine %s", msg)
        if not why:
            # Every move says why (docs/research/agent-coordination-and-rationale.md (c)1).
            if STRICT:
                raise ValueError(f"{self.spec.name}: {self.state} -> {new} needs a why")
            why = "unspecified"
        self._move(new, why, now)
        return True

    def reset(self, new: str | None = None, why: str = "reset", now: float | None = None) -> None:
        """Restore/restart only: jump to a state without an edge check."""
        new = self.spec.initial if new is None else new
        if new not in self.spec.states:
            raise ValueError(f"{self.spec.name}: unknown state {new!r}")
        if new == self.state:
            self.why = why
            return
        self._move(new, why, now)

    def _move(self, new: str, why: str, now: float | None) -> None:
        old = self.state
        t = self.clock() if now is None else now
        self.history.append((round(t, 3), old, new, why))
        self.state, self.since, self.why = new, t, why
        if _SINK is not None:
            _SINK.write(self, t, old, new, why)
        if self.on_change is not None:
            try:
                self.on_change(self, old, new, why)
            except Exception:  # an observer must never break a transition
                log.exception("state machine %s on_change failed", self.spec.name)

    # -- showing ---------------------------------------------------------------------------------
    def view(self, now: float | None = None, history: int = 5) -> dict:
        v = {"machine": self.spec.name, "state": self.state, "since": round(self.since, 3),
             "age_s": round(self.age(now), 1), "why": self.why}
        if self.key:
            v["key"] = self.key
        if self.illegal:
            v["illegal"] = self.illegal
        if history:
            v["recent"] = [list(h) for h in list(self.history)[-history:]]
        return v


class MachineMap:
    """One machine per key (per target, per topic). Keys at the initial state are dropped by `prune()`."""

    def __init__(self, spec: Spec, *, clock: Callable[[], float] = time.time,
                 on_change: Callable[[Machine, str, str, str], None] | None = None):
        self.spec, self.clock, self.on_change = spec, clock, on_change
        self._m: dict[str, Machine] = {}

    def __getitem__(self, key: str) -> Machine:
        m = self._m.get(key)
        if m is None:
            m = self._m[key] = self.spec.new(key=key, clock=self.clock, on_change=self.on_change)
        return m

    def get(self, key: str) -> Machine | None:
        return self._m.get(key)

    def state(self, key: str) -> str:
        m = self._m.get(key)
        return self.spec.initial if m is None else m.state

    def __contains__(self, key: str) -> bool:
        return key in self._m

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._m))

    def items(self) -> Iterable[tuple[str, Machine]]:
        return list(self._m.items())

    def in_state(self, *states: str) -> list[str]:
        return [k for k, m in self._m.items() if m.state in states]

    def prune(self) -> None:
        for k in [k for k, m in self._m.items() if m.state == self.spec.initial]:
            del self._m[k]

    def view(self, now: float | None = None) -> list[dict]:
        return [m.view(now, history=0) for m in self._m.values()]


class TransitionLog:
    """Appends every move to a JSONL file (data/transitions.jsonl) so "why is it in this state?" survives a
    restart. Local only, never uploaded. Rotates to <name>.1 past `max_bytes`. A write error never breaks a
    move: it disables the log and says so once."""

    def __init__(self, path, commit: str = "", max_bytes: int = 5_000_000):
        from pathlib import Path
        self.path, self.commit, self.max_bytes = Path(path), commit, max_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", buffering=1)
        self.broken = False

    def write(self, m: "Machine", t: float, old: str, new: str, why: str) -> None:
        if self.broken:
            return
        import json
        try:
            row = {"ts": round(t, 3), "machine": m.spec.name, "old": old, "new": new, "why": why}
            if m.key:
                row["key"] = m.key
            if self.commit:
                row["commit"] = self.commit
            self._f.write(json.dumps(row, separators=(",", ":")) + "\n")
            if self._f.tell() > self.max_bytes:
                self._f.close()
                self.path.replace(self.path.with_name(self.path.name + ".1"))
                self._f = open(self.path, "a", buffering=1)
        except Exception:
            self.broken = True
            log.exception("transition log %s disabled", self.path)

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


_SINK: TransitionLog | None = None


def log_transitions(path, commit: str = "") -> TransitionLog:
    """Start writing every move to `path` (the app calls this once at start; tests don't)."""
    global _SINK
    if _SINK is not None:
        _SINK.close()
    _SINK = TransitionLog(path, commit)
    return _SINK


def run_commit() -> str:
    """The commit the running app was started from ("" in tests), for stamping decision rows."""
    return _SINK.commit if _SINK is not None else ""


def stop_logging() -> None:
    global _SINK
    if _SINK is not None:
        _SINK.close()
    _SINK = None


def read_transitions(path, name: str = "", limit: int = 50) -> list[dict]:
    """Last `limit` rows for machine `name` (all when empty), from the log and its rotated file."""
    import json
    from pathlib import Path
    rows: list[dict] = []
    for p in (Path(str(path) + ".1"), Path(path)):
        try:
            for line in p.read_text().splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not name or r.get("machine") == name or str(r.get("machine", "")).startswith(name + "."):
                    rows.append(r)
        except OSError:
            continue
    return rows[-limit:]


def snapshot_all(now: float | None = None) -> list[dict]:
    """Every live machine, for GET /api/machines. Initial-state keyed machines are left out."""
    out = []
    for m in list(_LIVE):
        if m.spec.keyed and m.state == m.spec.initial:
            continue
        out.append(m.view(now))
    out.sort(key=lambda v: (v["machine"], v.get("key", "")))
    return out


def mermaid(spec: Spec) -> str:
    lines = ["stateDiagram-v2", f"    [*] --> {spec.initial}"]
    for a, ts in spec.edges.items():
        for b in ts:
            if b == ANY:
                lines.append(f"    {a} --> any")
            else:
                lines.append(f"    {a} --> {b}")
    return "\n".join(lines)
