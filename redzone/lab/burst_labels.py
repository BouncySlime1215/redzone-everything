"""[burst labels] EXP-021: YouTube TV MediaSession bursts as automatic dead-ball labels from our own nights.

Serves: EXP-021 (docs/experiments.md), North Star "Per-stick delay error p95" and "Landings in dead time".
Owner: lane 3.

A burst onset (sync_probe.burst_watch) is a moment on the stick's own clock. Less the stream's screen delay it
is a real-life moment; less the night's onset-to-dead-ball gap it should be a dead ball. The gap is FITTED on
the night's first half of onsets and only APPLIED to its second half, so a label never grades the fit that made
it. A label hits when a play-by-play dead ball (or break start) of that game lies within `tol_s` of it. The
same count of random moments over the same span is the null.

Pure functions: scripts/burst_labels.py reads the files and prints the grade.
"""
from __future__ import annotations

import bisect
import random
import statistics
from dataclasses import dataclass, field

# The registered bar (docs/experiments.md EXP-021): a grader's frozen thresholds, not app timings. Changing any
# of them means registering a new EXP id.
REGISTERED = {"tol_s": 5.0, "min_labels": 30, "precision": 0.95, "low": 0.90, "gap_sd_s": 3.0}


@dataclass
class NightGrade:
    gap_s: float | None                         # fitted on the first half
    hits: dict[int, list[bool]] = field(default_factory=dict)   # stick -> second-half label hits
    null_hit_rate: float | None = None


def _last_before(walls: list[float], t: float, max_gap_s: float) -> float | None:
    i = bisect.bisect_right(walls, t)
    if i == 0 or t - walls[i - 1] > max_gap_s:
        return None
    return walls[i - 1]


def _near(walls: list[float], t: float, tol_s: float) -> bool:
    i = bisect.bisect_left(walls, t - tol_s)
    return i < len(walls) and walls[i] <= t + tol_s


def grade_night(onsets: list[tuple[int, float]], dead_walls: list[float], delay_s: float, *,
                max_gap_s: float = 240.0, tol_s: float = REGISTERED["tol_s"], seed: int = 0) -> NightGrade:
    """onsets: (stick, onset on the stick's clock); dead_walls: the game's dead-ball feed walls."""
    walls = sorted(dead_walls)
    ons = sorted(onsets, key=lambda x: x[1])
    if len(ons) < 2 or not walls:
        return NightGrade(None)
    half = len(ons) // 2
    gaps = [r - w for r in (t - delay_s for _, t in ons[:half])
            if (w := _last_before(walls, r, max_gap_s)) is not None]
    if not gaps:
        return NightGrade(None)
    gap = statistics.median(gaps)
    g = NightGrade(round(gap, 2))
    for stick, t in ons[half:]:
        g.hits.setdefault(stick, []).append(_near(walls, t - delay_s - gap, tol_s))
    n = sum(len(v) for v in g.hits.values())
    rng = random.Random(seed)
    lo, hi = walls[0], walls[-1]
    g.null_hit_rate = round(sum(_near(walls, rng.uniform(lo, hi), tol_s) for _ in range(max(n, 1))) / max(n, 1), 3)
    return g


def grade(nights: list[NightGrade], *, boots: int = 2000, seed: int = 0) -> dict:
    """Per stick over nights: precision, its night-bootstrap 2.5% bound, and PASS / FAIL / PENDING."""
    rng = random.Random(seed)
    sticks = sorted({s for n in nights for s in n.hits})
    out: dict = {"sticks": {}, "nights": len(nights)}
    for s in sticks:
        per = [n.hits.get(s, []) for n in nights]
        flat = [h for p in per for h in p]
        prec = sum(flat) / len(flat) if flat else None
        lows = []
        if len(per) >= 2:
            for _ in range(boots):
                pick = [per[rng.randrange(len(per))] for _ in per]
                xs = [h for p in pick for h in p]
                if xs:
                    lows.append(sum(xs) / len(xs))
        low = sorted(lows)[int(0.025 * len(lows))] if lows else None
        if len(flat) < REGISTERED["min_labels"] or low is None:
            verdict = "PENDING"
        else:
            verdict = "PASS" if prec >= REGISTERED["precision"] and low >= REGISTERED["low"] else "FAIL"
        out["sticks"][f"stick {s + 1}"] = {"labels": len(flat), "precision": None if prec is None else round(prec, 3),
                                           "low": None if low is None else round(low, 3), "grade": verdict}
    fitted = [n.gap_s for n in nights if n.gap_s is not None]
    out["gap_s"] = fitted
    out["gap_sd_s"] = round(statistics.pstdev(fitted), 2) if len(fitted) >= 2 else None
    out["gap_ok"] = None if out["gap_sd_s"] is None else out["gap_sd_s"] <= REGISTERED["gap_sd_s"]
    out["null_hit_rate"] = [n.null_hit_rate for n in nights]
    verdicts = [v["grade"] for v in out["sticks"].values()]
    if not verdicts or "PENDING" in verdicts or out["gap_ok"] is None:
        out["grade"] = "PENDING"
    else:
        out["grade"] = "PASS" if all(v == "PASS" for v in verdicts) and out["gap_ok"] else "FAIL"
    return out
