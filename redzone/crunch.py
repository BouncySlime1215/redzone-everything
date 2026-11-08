"""[one brain, step 2] One crunch table (docs/onebrain-migration.md). Serves: crunch_live_* (North Star
"crunch time live"), your_team_crunch_live, and the scorecard's graded crunch window.

Why: one brain step 2 (docs/onebrain-migration.md, 2026-11-04): every 'is this crunch time?' reads one table, so app, planner, demo and grading agree.

Every "is this crunch time?" question in the app, the standby planner, the demo and the grading reads
its numbers from the `crunch:` section of redzone/default_config.yaml through `row(kind, league)` and
answers with `inside(row, period, clock_s, margin)`. A league's rules (which period, how close, which
clock) stay with the caller: callers keep their own period / clock reading (college halves as pro
quarters, NHL elapsed-to-remaining) and league mapping; only the numbers live here.

Kinds (each a different question, not a copy of one):
  run_up   flagged a little BEFORE the graded window, so the decider gets there at a stoppage
           (scoring.engine.crunch_time)
  window   the graded window the app aims at (scoring.engine.crunch_window: NHL from 2:00)
  graded   the lab scorecard's crunch plays (lab.sportsgrade.is_crunch, demo crunch counts)
  soon     crunch coming within a few minutes: the standby planner pre-positions (standby.crunch_soon)

A row: period (the last regulation period), clock_s (at or under this many seconds left in it; None =
the whole period and every later one), margin (score difference at most), ot (from this period on it
is crunch whatever the clock; None = never by period alone), ot_close (overtime still needs the margin).
Values are the ones shipped before this step, exactly; changing one is its own decision (decisions.md).
"""
from __future__ import annotations

from dataclasses import dataclass

from redzone.config import default

CRUNCH_KINDS = ("run_up", "window", "graded", "soon")
ONE_SCORE_NFL = 8          # one score in football: a touchdown and a two-point try


@dataclass(frozen=True)
class Row:
    period: int
    clock_s: float | None
    margin: int
    ot: int | None = None
    ot_close: bool = False


def _load() -> dict[str, dict[str, Row]]:
    raw = default("crunch") or {}
    out: dict[str, dict[str, Row]] = {}
    for kind in CRUNCH_KINDS:
        out[kind] = {lg: Row(int(v["period"]), None if v.get("clock_s") is None else float(v["clock_s"]),
                             int(v["margin"]), None if v.get("ot") is None else int(v["ot"]), bool(v.get("ot_close", False)))
                     for lg, v in (raw.get(kind) or {}).items()}
    return out


TABLE = _load()


def row(kind: str, league) -> Row | None:
    """The row for this league (its value, e.g. "nba"); None when the table has no row for it."""
    return TABLE[kind].get(getattr(league, "value", league))


def inside(r: Row | None, period: int, clock_s: float | None, margin: int | float | None) -> bool:
    """Is (period, clock, |score difference|) inside the row?"""
    if r is None or margin is None:
        return False
    close = abs(margin) <= r.margin
    if r.ot is not None and period >= r.ot:
        return close or not r.ot_close
    if r.clock_s is None:
        return period >= r.period and close
    return period == r.period and clock_s is not None and clock_s <= r.clock_s and close


def trailing_drill(period: int, clock_s: float | None, off_diff: int | float | None) -> bool:
    """NFL two-minute drill: the offence trails by one score or is tied, final 2:00 of the 4th or OT."""
    return period >= 4 and clock_s is not None and clock_s <= 120 and off_diff is not None \
        and -ONE_SCORE_NFL <= off_diff <= 0
