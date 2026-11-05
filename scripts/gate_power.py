"""How many nights does a gate need? (PROPOSAL, test writers, EXP-726.) Not frozen.

Nick, 2:00 AM Nov 5: NBA big plays seen live is 78.9% on only 71 graded plays over 9 nights, too few to gate on.
Grow each graded set to >= 60 decisions per gate. This grader reads per-night counts for a line and reports:
- decisions so far, and whether the >= 60 floor (release_gate's decision minimum) is met;
- the night-cluster bootstrap 95% CI and its half-width;
- the nights needed for a target half-width, assuming the half-width shrinks as 1 / sqrt(nights)
  (night-cluster variance, the same resampling the release gate uses);
- the nights needed for the 60-decision floor at the current decisions per night.

    python scripts/gate_power.py counts.json [--target-hw 3.0] [--min-decisions 60] [--json]

counts.json: {"lines": [{"line": "big_live_nba", "league": "nba",
                         "nights": [{"night": "20251101", "hits": 6, "n": 8}, ...]}, ...]}
Self-contained: no imports from training or decider code. Deterministic (seeded bootstrap).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

MIN_DECISIONS = 60
DRAWS = 2000


def boot_ci(nights: list[tuple[int, int]], draws: int = DRAWS, seed: int = 0) -> tuple[float, float, float]:
    """Pooled ratio (percent) and its night-cluster bootstrap 95% CI."""
    hits, n = sum(h for h, _ in nights), sum(t for _, t in nights)
    point = 100.0 * hits / n if n else float("nan")
    rng = random.Random(seed)
    k = len(nights)
    stats = []
    for _ in range(draws):
        sample = [nights[rng.randrange(k)] for _ in range(k)]
        sn = sum(t for _, t in sample)
        if sn:
            stats.append(100.0 * sum(h for h, _ in sample) / sn)
    stats.sort()
    lo = stats[int(0.025 * (len(stats) - 1))]
    hi = stats[int(0.975 * (len(stats) - 1))]
    return point, lo, hi


def line_power(line: dict, target_hw: float, min_decisions: int = MIN_DECISIONS) -> dict:
    nights = [(int(x["hits"]), int(x["n"])) for x in line.get("nights", []) if int(x.get("n") or 0) > 0]
    out = {"line": line.get("line"), "league": line.get("league"), "nights": len(nights),
           "decisions": sum(n for _, n in nights)}
    if len(nights) < 2:
        out.update(status="PENDING", note="needs >= 2 nights with decisions to estimate night variance")
        return out
    point, lo, hi = boot_ci(nights)
    hw = (hi - lo) / 2
    per_night = out["decisions"] / len(nights)
    need_hw = math.ceil(len(nights) * (hw / target_hw) ** 2) if hw > target_hw else len(nights)
    need_dec = math.ceil(min_decisions / per_night) if per_night else None
    need = max(need_hw, need_dec or 0)
    out.update(point=round(point, 1), ci=[round(lo, 1), round(hi, 1)], half_width=round(hw, 2),
               decisions_per_night=round(per_night, 1), nights_for_target_hw=need_hw,
               nights_for_min_decisions=need_dec, nights_needed=need, more_nights=max(0, need - len(nights)),
               status="READY" if need <= len(nights) else "UNDERPOWERED")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("counts", type=Path)
    ap.add_argument("--target-hw", type=float, default=3.0, help="target CI half-width in points")
    ap.add_argument("--min-decisions", type=int, default=MIN_DECISIONS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    doc = json.loads(a.counts.read_text())
    res = [line_power(x, a.target_hw, a.min_decisions) for x in doc.get("lines", [])]
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for r in res:
            if r["status"] == "PENDING":
                print(f"PENDING      {r['league']} {r['line']}: {r['note']}")
                continue
            print(f"{r['status']:12} {r['league']} {r['line']}: {r['point']}% [{r['ci'][0]}, {r['ci'][1]}] "
                  f"hw {r['half_width']} on {r['decisions']} decisions / {r['nights']} nights -> "
                  f"needs {r['nights_needed']} nights (+{r['more_nights']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
