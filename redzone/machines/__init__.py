"""Every explicit state machine in RedZone, declared in one place (one module per area).

Why: lifecycle state was spread over loose flags; declaring every machine here gives 'what states can X be in?' one answer (decisions 2026-11-08).

Read this package to answer "what states can X be in and how does it move?". The live instances live
on their owners (see each Spec.owner); GET /api/machines shows them now; docs/state-machines.md is
generated from these specs by scripts/state_machines.py (CI fails if it is stale).
"""
from redzone.fsm import SPECS  # noqa: F401

from redzone.machines import control, decider, platform  # noqa: F401,E402

ALL = SPECS
