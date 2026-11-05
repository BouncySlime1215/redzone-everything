"""[EXP-038] Grade the orientation beat: do overrides right after an automatic switch drop when the phone
shows a 2-3 s "who, score, clock, one fact" beat?

An OVERRIDE is a user switch (`kind == "user"`) to a different game within `phone.orient_override_s`
(30 s) of an automatic switch. The night is the unit: whole nights alternate with phone.orient on and off
(never within a night), and the rate is overridden automatic switches / automatic switches.

Pre-registered bar (docs/lanes/experience.md, EXP-038), on override rates per night:
  PASS     >= ORIENT_MIN_NIGHTS nights per side and the 95 % bootstrap UPPER bound of rate(on) - rate(off) < 0
  FAIL     the LOWER bound > 0 (the beat goes with more overrides)
  PENDING  anything else, including too few nights. Pending never counts as passing.
Guardrail: Right game agreement must not drop on beat nights (read from the North Star table).
Reads the switches table only, never the feedback table (Nick's taps grade, they never train).
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from redzone.config import default

ORIENT_MIN_NIGHTS = 6
ORIENT_DRAWS = 2000


def overrides(switches: list[dict], window_s: float | None = None) -> dict:
    """switches: oldest first, each {ts, to_game, kind, ok, dry_run}. Counts automatic switches that
    went through and how many a user switch to another game overrode within the window."""
    win = float(default("phone.orient_override_s")) if window_s is None else window_s
    rows = [s for s in switches if s.get("ok") in (1, True, None)]
    auto = over = 0
    for i, s in enumerate(rows):
        if s.get("kind") == "user":
            continue
        auto += 1
        for t in rows[i + 1:]:
            if t["ts"] - s["ts"] > win:
                break
            if t.get("kind") == "user" and t.get("to_game") != s.get("to_game"):
                over += 1
                break
    return {"auto": auto, "overridden": over, "rate": round(over / auto, 4) if auto else None}


def night_switches(db: Path, t0: float, t1: float) -> list[dict]:
    """Read-only: one night's switches from the app's database."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = con.execute("SELECT ts, to_game, kind, ok, dry_run FROM switches WHERE ts >= ? AND ts < ? ORDER BY ts",
                          (t0, t1))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def compare(on: list[float], off: list[float], draws: int = ORIENT_DRAWS, seed: int = 7) -> dict:
    """Per-night override rates with the beat on vs off -> the EXP-038 line."""
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    out = {"on_n": len(on), "off_n": len(off), "on_rate": round(mean(on), 4) if on else None,
           "off_rate": round(mean(off), 4) if off else None, "diff": None, "lo": None, "hi": None,
           "status": "PENDING", "min_nights": ORIENT_MIN_NIGHTS}
    if not on or not off:
        return out
    rng = random.Random(seed)
    diffs = sorted(mean([on[rng.randrange(len(on))] for _ in on]) - mean([off[rng.randrange(len(off))] for _ in off])
                   for _ in range(draws))
    lo, hi = diffs[int(0.025 * draws)], diffs[min(draws - 1, int(0.975 * draws))]
    out.update(diff=round(mean(on) - mean(off), 4), lo=round(lo, 4), hi=round(hi, 4))
    if lo > 0:
        out["status"] = "FAIL"
    elif len(on) >= ORIENT_MIN_NIGHTS and len(off) >= ORIENT_MIN_NIGHTS and hi < 0:
        out["status"] = "PASS"
    return out
