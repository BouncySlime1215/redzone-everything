"""[EXP-039] Grade the "Also close" line: does showing other late, close games as text cut the
curiosity switches (manual switches) without costing big plays?

Per night: manual share = user switches / all switches that went through. Whole nights alternate with
phone.also_close on and off. Pre-registered bar (docs/lanes/experience.md, EXP-039):
  PASS     >= ALSO_MIN_NIGHTS nights per side, the 95 % upper bound of share(on) - share(off) < 0
  FAIL     the lower bound > 0 (the line goes with MORE manual switches: it pulls, not informs)
  PENDING  anything else
Guardrail: "Big plays live or replayed within 3 min" must not drop on line nights (North Star table).
Reads the switches table only.
"""
from __future__ import annotations

from redzone.lab.night_ab import compare

ALSO_MIN_NIGHTS = 6


def manual_share(switches: list[dict]) -> float | None:
    rows = [s for s in switches if s.get("ok") in (1, True, None)]
    return round(sum(1 for s in rows if s.get("kind") == "user") / len(rows), 4) if rows else None


def line(on: list[float], off: list[float]) -> dict:
    return compare(on, off, ALSO_MIN_NIGHTS, lower_is_better=True)
