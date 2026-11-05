"""[EXP-032] Red team: hunt the stress knobs for dev nights that break the brain (lab only, OFF by default).

    .venv/bin/python scripts/redteam.py --enable --league nba nhl --split valid --budget 48 \\
        --data /path/to/redzone/data --out redteam.json

For each dev night (train / valid; a locked night is refused): run the fixed stress grid, spend exactly
--budget backtest evaluations searching the knobs (redzone/lab/redteam.py), rerun each distinct find 3
times, and write every reproducible one up as a tests/golden/ candidate. Report-only; nothing live
reads the output. Pre-registration: docs/prereg/exp-032.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import redteam as R  # noqa: E402

EVAL_END = "20261101"   # the pinned window's end when eval-data.lock.json has none


def summarize(nights: list[dict]) -> dict:
    """Rates over nights with a bootstrap CI; PENDING under R.MIN_NIGHTS (grading rule 4)."""
    out = {"nights": len(nights), "evals_spent": sum(n["evals_spent"] for n in nights)}
    lines = {
        "grid_breaks": [float(bool(n["grid_breaks"])) for n in nights],
        "search_breaks": [float(n["search_hits"] > 0) for n in nights],
        "novel_reproducible": [float(any(f["reproducible"] and f["novel_vs_grid"] for f in n["findings"]))
                               for n in nights],
    }
    for k, v in lines.items():
        mean = sum(v) / max(1, len(v))
        lo, hi = R.bootstrap_ci(v, seed=32)
        out[k] = {"rate": round(mean, 3), "ci95": [round(lo, 3), round(hi, 3)],
                  "status": "PENDING" if len(v) < R.MIN_NIGHTS else "ok"}
    found = sum(1 for n in nights for f in n["findings"] if f["reproducible"] and f["novel_vs_grid"])
    out["verdict_b"] = ("found" if found else "null") + f" ({found} novel reproducible mixes, budget spent)"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--enable", action="store_true", help="the flag: off by default, the script does nothing")
    ap.add_argument("--league", nargs="+", default=["nba", "nhl"])
    ap.add_argument("--split", default="valid", choices=["train", "valid", "fit"], help="dev nights only")
    ap.add_argument("--nights", type=int, default=0, help="the newest N nights of the split (0 = all)")
    ap.add_argument("--budget", type=int, default=48, help="search evaluations per night (grid and reruns extra)")
    ap.add_argument("--seed", type=int, default=0, help="search seed")
    ap.add_argument("--reruns", type=int, default=3)
    ap.add_argument("--max-repro", type=int, default=3, help="distinct finds reproduced per night")
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="read-only: archive/ and eval_config.yaml")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    if not (a.enable or R.ENABLED):
        print("redteam is OFF (pass --enable to run it; lab only, report-only)")
        return 0

    from redzone.config import Config
    from redzone.lab import evaldata
    from redzone.models import League

    pinned = a.data / "eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else a.data / "config.yaml")
    evaldata.use_shipped_switch_costs(cfg)
    report = {"prereg": "docs/prereg/exp-032.md", "budget": a.budget, "seed": a.seed, "split": a.split,
              "eval_data": evaldata.stamp(a.data.parent), "leagues": {}, "golden_candidates": []}
    every: list[dict] = []
    for lg_name in a.league:
        lg = League(lg_name)
        arch = a.data / "archive" / lg.value
        end = evaldata.window_end(lg.value, EVAL_END)
        nights = R.dev_nights(arch, a.split, end)
        nights = nights[-a.nights:] if a.nights else nights
        locked = R.locked_nights(arch, end)
        mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
        per = []
        for night in nights:
            if night in locked:
                raise SystemExit(f"refusing locked night {night}")
            reps, targets = R.load_night(arch, lg, night, a.data / "_eval_channels.json")
            if len(reps) < 2:
                continue
            t0 = time.time()
            res = R.hunt_night(R.backtest_evaluator(reps, targets, cfg, mine), a.budget, a.seed,
                               a.reruns, a.max_repro)
            res.update(night=night, games=len(reps), seconds=round(time.time() - t0, 1))
            per.append(res)
            for f in res["findings"]:
                if f["reproducible"]:
                    report["golden_candidates"].append(R.golden_candidate(lg.value, night, f))
            print(f"{lg.value} {night}: grid breaks {res['grid_breaks'] or '-'}; search hits {res['search_hits']}"
                  f" (first at {res['search_first_hit']}); finds "
                  f"{[(f['reproducible'], f['novel_vs_grid']) for f in res['findings']]}; {res['seconds']} s",
                  flush=True)
        report["leagues"][lg.value] = {"per_night": per, "summary": summarize(per)}
        every.extend(per)
    report["summary"] = summarize(every)
    print(json.dumps(report["summary"], indent=1))
    if a.out:
        a.out.write_text(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
