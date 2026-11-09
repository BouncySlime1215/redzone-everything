#!/usr/bin/env python3
"""Does v5 replace v4? Per-night paired capture deltas from two kaggle_gamevalue manifests, with a bootstrap interval over nights.

    python scripts/gamevalue_v5_report.py <v4 manifest.json> <v5 manifest.json> [--leagues nfl,nba,cbb] [--json]

Why: a v5 mean K=4 number can beat v4's by the noise of a few nights (independent review, 2026-11-09). The bar is per league and paired: the same test nights, the same decisions, the capture
delta of each night, and a bootstrap over whole nights whose 95% interval excludes 0. Report only: it changes no default and it is not the promotion, which stays the real Planner on the same nights and 7 of 7 shadow nights.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

BOOTSTRAPS, SEED, CONFIDENCE = 5000, 7, 95.0


def compare(v4: dict, v5: dict, boots: int = BOOTSTRAPS, seed: int = SEED) -> dict:
    """{night: {got, total, decisions}} for each version -> the paired report. Only nights where both saw the same decisions and the same available value are paired."""
    both = sorted(set(v4) & set(v5))
    paired = [n for n in both if v4[n]["decisions"] == v5[n]["decisions"] and abs(v4[n]["total"] - v5[n]["total"]) < 1e-6]
    unpaired = sorted((set(v4) | set(v5)) - set(paired))
    base = {"nights_paired": len(paired), "nights_unpaired": unpaired, "replaces_v4": False}
    usable = [n for n in paired if v4[n]["total"] > 0]
    if not usable:
        return {**base, "verdict": "no paired night with any value: nothing to compare"}
    got4, got5, tot = (np.array([d[n][key] for n in usable], float) for d, key in ((v4, "got"), (v5, "got"), (v4, "total")))
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(usable), (boots, len(usable)))
    boot = 100.0 * ((got5[draws] - got4[draws]).sum(axis=1) / tot[draws].sum(axis=1))
    lo, hi = (float(x) for x in np.percentile(boot, [(100.0 - CONFIDENCE) / 2.0, 100.0 - (100.0 - CONFIDENCE) / 2.0]))
    delta = float(100.0 * (got5.sum() - got4.sum()) / tot.sum())
    replaces = lo > 0.0
    verdict = f"v5 replaces v4: {'yes' if replaces else 'no'} (capture {delta:+.2f} points, {CONFIDENCE:.0f}% interval over {len(usable)} nights [{lo:+.2f}, {hi:+.2f}])"
    return {**base, "nights_used": len(usable), "delta_pct": delta, "ci_low": lo, "ci_high": hi, "replaces_v4": replaces, "verdict": verdict,
            "by_night": {n: {"v4": 100.0 * float(got4[i] / tot[i]), "v5": 100.0 * float(got5[i] / tot[i])} for i, n in enumerate(usable)}}


def by_night(manifest: dict, league: str) -> dict:
    return ((manifest.get("leagues") or {}).get(league, {}).get("models") or {}).get("v1", {}).get("test_by_night") or {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("v4", type=Path)
    ap.add_argument("v5", type=Path)
    ap.add_argument("--leagues", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    m4, m5 = json.loads(a.v4.read_text()), json.loads(a.v5.read_text())
    leagues = [x for x in a.leagues.split(",") if x] if a.leagues else sorted(set(m4.get("leagues") or {}) & set(m5.get("leagues") or {}))
    report = {lg: compare(by_night(m4, lg), by_night(m5, lg)) for lg in leagues}
    if a.json:
        print(json.dumps(report, indent=1, sort_keys=True))
    else:
        for lg, r in report.items():
            print(f"{lg}: {r['verdict']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
