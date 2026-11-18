"""Whole-night A/B for display experiments (lane 5): one number per night on each side, a bootstrap
interval on mean(on) - mean(off), and a PASS / FAIL / PENDING status against a pre-registered direction.
The night is the unit (research 05 §2.2): never split a night, never pool plays across nights.

Why: first added in 1b312e87 (2026-11-05) as "Graders: EXP-039 'Also close' manual-switch share (redzone/lab/also_close.py, night_ab.py, script..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import random


def compare(on: list[float], off: list[float], min_nights: int, lower_is_better: bool = True,
            draws: int = 2000, seed: int = 7) -> dict:
    """PASS: >= min_nights per side and the whole 95 % interval on the good side of 0. FAIL: the whole
    interval on the bad side. PENDING otherwise (never counts as passing)."""
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    out = {"on_n": len(on), "off_n": len(off), "on_mean": round(mean(on), 4) if on else None,
           "off_mean": round(mean(off), 4) if off else None, "diff": None, "lo": None, "hi": None,
           "status": "PENDING", "min_nights": min_nights}
    if not on or not off:
        return out
    rng = random.Random(seed)
    diffs = sorted(mean([on[rng.randrange(len(on))] for _ in on]) - mean([off[rng.randrange(len(off))] for _ in off])
                   for _ in range(draws))
    lo, hi = diffs[int(0.025 * draws)], diffs[min(draws - 1, int(0.975 * draws))]
    out.update(diff=round(mean(on) - mean(off), 4), lo=round(lo, 4), hi=round(hi, 4))
    good, bad = (hi < 0, lo > 0) if lower_is_better else (lo > 0, hi < 0)
    if bad:
        out["status"] = "FAIL"
    elif good and len(on) >= min_nights and len(off) >= min_nights:
        out["status"] = "PASS"
    return out
