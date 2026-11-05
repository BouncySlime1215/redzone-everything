"""[EXP-127] Crew H pregame game context joined onto stay/leave features (offline only).

Tables are keyed (night YYYYMMDD, gid = ESPN event id), pregame only, from crew H's gamectx-out branches. The
columns are fixed by docs/prereg/exp-127.md; a missing table, row or column reads 0 with ctx_found = 0.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

CTX_COLUMNS = ["rivalry", "div_game", "national_tv", "prime_slot", "postseason", "win_pct_max", "win_pct_min",
               "win_pct_absdiff", "gap_playoff_min_abs", "revenge_any", "games_left_min", "ctx_found"]


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _row_ctx(r: dict) -> list[float]:
    def side(name):
        return [_f(r.get(f"{s}_{name}")) for s in ("home", "away")]

    wp = [x for x in side("win_pct") + side("win_pct_in") if x is not None]
    gap = [abs(x) for x in side("gap_playoff_line") if x is not None]
    gl = [x for x in side("games_left") if x is not None]
    rev = [x for x in side("revenge") if x is not None]
    st = _f(r.get("season_type"))
    post = 1.0 if (st == 3 or any((x or 0) > 0 for x in side("postseason"))) else 0.0
    v = {"rivalry": _f(r.get("rivalry")) or 0.0, "div_game": _f(r.get("div_game")) or 0.0,
         "national_tv": _f(r.get("national_tv")) or 0.0, "prime_slot": _f(r.get("prime_slot")) or 0.0,
         "postseason": post, "win_pct_max": max(wp) if wp else 0.0, "win_pct_min": min(wp) if wp else 0.0,
         "win_pct_absdiff": abs(wp[0] - wp[1]) if len(wp) >= 2 else 0.0,
         "gap_playoff_min_abs": min(gap) if gap else 0.0, "revenge_any": float(any(x > 0 for x in rev)),
         "games_left_min": min(gl) if gl else 0.0, "ctx_found": 1.0}
    return [float(v[c]) for c in CTX_COLUMNS]


def load_tables(paths: list[Path]) -> dict[tuple[str, str], list[float]]:
    """{(night, gid): context row}. Earlier paths win on a key collision (pass the richest table first)."""
    out: dict[tuple[str, str], list[float]] = {}
    for p in paths:
        if not Path(p).exists():
            continue
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                k = (str(r.get("night", "")), str(r.get("gid", "")))
                if k not in out:
                    out[k] = _row_ctx(r)
    return out


def extend(F: np.ndarray, night: str, gids: list[str], table: dict) -> np.ndarray:
    """F[g, k, f] with the game's constant context appended: F_ext[g, k, f + len(CTX_COLUMNS)]."""
    G, K, _ = F.shape
    C = np.zeros((G, len(CTX_COLUMNS)))
    for g, gid in enumerate(gids):
        row = table.get((night, str(gid)))
        if row is not None:
            C[g] = row
    return np.concatenate([F, np.broadcast_to(C[:, None, :], (G, K, C.shape[1]))], axis=-1)
