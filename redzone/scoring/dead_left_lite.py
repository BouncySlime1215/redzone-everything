"""[timing.dead_v2, off] The dead-time-left reader: seconds of an open stoppage surely still to come, from its break class
and the time it has already run. Standard library only and no `redzone` import, so a script outside the app can load this one
file by its path (tools/park_live does; see DEAD_V2_NOTE.md there).

    guaranteed_left("nba", "timeout", 30.0, 0.01)      # 54.0: a timeout 30 s in still has 54 s left at the 99% level
    guaranteed_left("nfl", "official", 0.0, 0.05)      # an NFL timeout the feed calls official, at its start, 95% level

The table (broadcast/dead_left_table.json, scripts/measure_dead_left.py, train nights only) holds, per league, kind, class and
elapsed landmark t, among the stoppages that outlasted t: the one-sided split-conformal lower bound on the remaining seconds at
each stored risk level (0.10, 0.05, 0.02, 0.01). Research ledger L1 (Nov 10): by class and elapsed time, 598 of 891 NFL
timeouts on held-out nights left room for a clip at the 99% level where the pooled quantile left 0.

Promises: 0 when the table has no cell that qualifies; never more than the cell read for the level asked (carried to the
elapsed time); never larger at a smaller alpha (a stricter level never promises more); never below 0. Pure: the file is read
once per path, no clock.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TABLE_FILE = Path(__file__).resolve().parent / "broadcast" / "dead_left_table.json"
LANDMARKS = (0, 10, 20, 30, 45, 60, 90, 120)    # elapsed seconds the table conditions on
ALPHAS = (0.10, 0.05, 0.02, 0.01)               # stored risk levels: the share of stoppages allowed to restart sooner
PRO = {"cfb": "nfl", "cbb": "nba", "wcbb": "nba"}   # a college league reads its own table first, then its pro league's
TIMEOUT_CLASSES = ("official", "team", "other")    # a football timeout's finer classes (dead_left.stoppage_class)


@lru_cache(maxsize=4)
def load(path: str | Path = TABLE_FILE) -> dict:
    """league -> kind -> class -> landmark -> {n, max_s, left_s: {"0.10": s or None, ...}}."""
    return json.loads(Path(path).read_text())["table"]


def kind_of(stop_class: str) -> str:
    """The stoppage kind a class is a cell of: a football timeout class is a "timeout"; every other class is its own kind."""
    return "timeout" if stop_class in TIMEOUT_CLASSES else stop_class


def level(alpha: float) -> str | None:
    """The stored level read for `alpha`: the largest not above it (never looser than asked). None: stricter than all."""
    if not 0.0 < float(alpha) < 1.0:
        raise ValueError(f"alpha must be a risk level between 0 and 1, not {alpha}")
    ok = [a for a in ALPHAS if a <= float(alpha) + 1e-12]
    return f"{max(ok):.2f}" if ok else None


def _name(x) -> str:
    return str(getattr(x, "value", x) or "")


def cell(league, stop_class: str, mark: int, lvl: str, kind: str | None = None, table: dict | None = None):
    """(class read, cell) at landmark `mark` holding a bound at stored level `lvl`: the class's own cell, else the kind's pooled
    class; the league's own table, then its pro league's. None: nothing qualifies."""
    lg, cls = _name(league).lower(), _name(stop_class)
    k = _name(kind) or kind_of(cls)
    t = load() if table is None else table
    for name in dict.fromkeys((lg, PRO.get(lg, lg))):
        classes = t.get(name, {}).get(k, {})
        for c in dict.fromkeys((cls or k, k)):
            got = classes.get(c, {}).get(str(mark))
            if got and got["left_s"].get(lvl) is not None:
                return c, got
    return None


def left_at(league, stop_class: str, mark: int, elapsed_s: float, alpha: float, kind: str | None = None,
            table: dict | None = None) -> float:
    """The bound read at landmark `mark` (at or before `elapsed_s`), less the seconds run since the landmark. Across levels it
    is the least of the levels at or looser than the one asked, so it is monotone in alpha even where a class cell falls
    back to the pooled one at a strict level only."""
    lvl = level(alpha)
    if lvl is None:
        return 0.0
    best = None
    for a in ALPHAS:
        s = f"{a:.2f}"
        if a < float(lvl) - 1e-12:
            continue
        got = cell(league, stop_class, mark, s, kind, table)
        v = 0.0 if got is None else float(got[1]["left_s"][s])
        best = v if best is None else min(best, v)
    return max(0.0, (best or 0.0) - (float(elapsed_s) - mark))


def guaranteed_left(league, stop_class: str, elapsed_s: float, alpha: float, kind: str | None = None,
                    table: dict | None = None) -> float:
    """Seconds of dead time still to come with probability 1 - alpha, in a stoppage of this class that has run `elapsed_s`
    seconds (negative reads as 0). `stop_class`: "official" / "team" / "other" for a football timeout, else the stoppage kind
    ("timeout", "two_minute_warning", "review", ...). `kind`: only to name the pooled fallback for a class the table does not
    know. 0 when the table has no cell for it."""
    e = max(0.0, float(elapsed_s))
    return left_at(league, stop_class, max(t for t in LANDMARKS if t <= e), e, alpha, kind, table)
