"""One dead-ball predictor: how much of a stoppage is still surely to come, from the measured break lengths.

Why: dead-ball length lived in five tables that disagree (NFL two-minute warning 50 / 70 / 150 / 145 s); anchor mode and the audit's
`--dead floor` need one guaranteed floor (docs/plans/2026-11-09-anchor-mode.md section 7). It reads the pooled `*` rows of
redzone/scoring/broadcast/break_lengths.json (stoppage -> real restart, train nights only) and interpolates the stored quantiles.
Nothing live calls it yet: only scripts/veto_audit.py --dead floor and its tests.

Pure: the table is read once, no clock.
"""
from __future__ import annotations

import json
import math
import re
from fractions import Fraction
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


# ----- [timing.dead_v2, off] v2: by break class and by the time already elapsed ----------------------------------------------------
# Why: the pooled quantile less the elapsed seconds is valid but wasteful. At the 99% level it allowed a clip in 0 of 891 NFL
# timeouts on 11 held-out nights; the official / team split and the survivors' remaining time allowed 598 (research ledger L1,
# Nov 10, simulated on recorded nights). scripts/measure_dead_left.py builds the table from train nights only.

TABLE_V2_FILE = LENGTHS_FILE.with_name("dead_left_table.json")
LANDMARKS = (0, 10, 20, 30, 45, 60, 90, 120)    # elapsed seconds the table conditions on (stoppages that outlasted each one)
ALPHAS = (0.10, 0.05, 0.02, 0.01)               # the risk levels the table stores: the share of stoppages allowed to restart sooner
_TEAM_TIMEOUT = re.compile(r"timeout #?(\d)")


def stoppage_class(league: League | str, kind: str, type_text: str = "", text: str = "") -> str:
    """The break class of a stoppage, from the feed's own words on the play that began it. Football timeouts split three ways:
    "official" (the type or the text says official, TV or media: a commercial break), "team" ("Timeout #2 by WSH": short) and
    "other". Every other kind and league is its own single class, named after the kind (so is a timeout whose play carries no
    timeout type: nothing to read). A class is only ever a finer cell of the same kind."""
    k = str(getattr(kind, "value", kind) or "")
    ty = (type_text or "").lower()
    if k != "timeout" or League(getattr(league, "value", league)).base != League.NFL or "timeout" not in ty:
        return k
    tx = (text or type_text or "").lower()      # models.Play.text falls back to the type the same way
    if "official" in ty or "official" in tx or "tv " in tx or "media" in tx:
        return "official"
    named = bool(_TEAM_TIMEOUT.search(tx)) or " by " in tx
    return "team" if named and "tv" not in tx else "other"


def conformal_lower(values, alpha: float) -> float | None:
    """One-sided split-conformal lower bound on a new draw: the floor(alpha * (n + 1))-th smallest of `values`. None when n is too
    small for that alpha (fewer than 1 / alpha - 1 values): nothing can be promised at that level."""
    v = sorted(float(x) for x in values)
    k = math.floor(Fraction(str(alpha)) * (len(v) + 1))
    return v[k - 1] if k >= 1 else None
