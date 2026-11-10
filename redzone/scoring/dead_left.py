"""One dead-ball predictor: how much of a stoppage is still surely to come, from the measured break lengths.

Why: dead-ball length lived in five tables that disagree (NFL two-minute warning 50 / 70 / 150 / 145 s); anchor mode and the audit's
`--dead floor` need one guaranteed floor (docs/plans/2026-11-09-anchor-mode.md section 7). It reads the pooled `*` rows of
redzone/scoring/broadcast/break_lengths.json (stoppage -> real restart, train nights only) and interpolates the stored quantiles.
Nothing live calls it yet: only scripts/veto_audit.py --dead floor and its tests.

Pure: the table is read once, no clock.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from redzone.models import League

LENGTHS_FILE = Path(__file__).resolve().parent / "broadcast" / "break_lengths.json"
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)      # the table's q10 ... q90 (a missing one is skipped)


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(LENGTHS_FILE.read_text())["table"]


def _row(league: League | str, kind: str) -> dict | None:
    """The pooled row for this league and stoppage kind; a college league reads its pro league's row."""
    lg = League(getattr(league, "value", league))
    for name in dict.fromkeys((lg.value, lg.base.value)):
        cell = _table().get(name, {}).get(str(getattr(kind, "value", kind)), {}).get("*")
        if cell:
            return cell
    return None


def quantile(league: League | str, kind: str, q: float) -> float | None:
    """The q quantile (0.10-0.90) of this stoppage's length, linear between the stored quantiles. None: the table has no such row."""
    if not QUANTILES[0] <= q <= QUANTILES[-1]:
        raise ValueError(f"q must be within the measured {QUANTILES[0]}-{QUANTILES[-1]}, not {q}")
    cell = _row(league, kind)
    if cell is None:
        return None
    pts = [(p, float(cell[k])) for p in QUANTILES if (k := f"q{round(p * 100)}") in cell]
    for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
        if p0 <= q <= p1:
            return v0 + (v1 - v0) * (q - p0) / (p1 - p0)
    return pts[0][1] if pts and q == pts[0][0] else None


def guaranteed_left(league: League | str, stoppage_kind: str, elapsed_s: float, q: float = 0.10) -> float:
    """Seconds of this stoppage still to come with probability 1 - q, `elapsed_s` after it began: its q-quantile length less what
    already ran, never below 0. A kind the table never measured (a plain dead ball, `none`) guarantees nothing: 0."""
    length = quantile(league, stoppage_kind, q)
    return 0.0 if length is None else max(0.0, length - max(0.0, elapsed_s))
