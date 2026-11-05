#!/usr/bin/env python3
"""Compute exchange router (item e): pick a site for a job from docs/compute_ledger.json, and record what ran.

    python scripts/exchange/router.py route --kind pretrain --a100-h 5
    python scripts/exchange/router.py record --source kaggle_tpu --exp EXP-107 --hours 2.4 --grades OUT/grades.json

Rules: only 'active' sources; a job goes where its A100-hours fit in one session and in what is left; pretrains go to
the biggest pool (most A100-equivalents) that fits, sweeps and evals to the cheapest free pool that fits (Kaggle
before Colab CU). Colab never dips into its CU reserve. A source marked costs_money is never picked ($0 budget).
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / "docs" / "compute_ledger.json"
COST_RANK = {"kaggle_tpu": 0, "kaggle_gpu": 1, "colab_pro_plus": 3}      # free quota before paid-for CU


def hours_on(src: dict, a100_h: float) -> float:
    return a100_h / max(src.get("a100_equiv", 1.0), 1e-6)


def capacity_h(name: str, src: dict) -> float:
    if "cu_left" in src:
        return max(0.0, (src["cu_left"] - src.get("cu_reserve", 0)) / src.get("cu_per_a100_h", 13)) \
            / max(src.get("a100_equiv", 1.0), 1e-6)
    left = src.get("hours_left_week", src.get("hours_left"))
    return float("inf") if left is None else float(left)


def route(ledger: dict, kind: str, a100_h: float) -> tuple[str | None, list[str]]:
    why, ok = [], []
    for name, s in ledger["sources"].items():
        if s.get("status") != "active":
            why.append(f"{name}: {s.get('status')}")
            continue
        if s.get("costs_money"):
            why.append(f"{name}: costs money")
            continue
        if kind not in s.get("good_for", []):
            why.append(f"{name}: not for {kind}")
            continue
        h = hours_on(s, a100_h)
        if s.get("max_session_h") and h > s["max_session_h"] and kind != "pretrain":
            why.append(f"{name}: {h:.1f} h > {s['max_session_h']} h session")
            continue
        if h > capacity_h(name, s):
            why.append(f"{name}: {h:.1f} h > {capacity_h(name, s):.1f} h left")
            continue
        ok.append(name)
    if not ok:
        return None, why
    if kind == "pretrain":
        ok.sort(key=lambda n: -ledger["sources"][n].get("a100_equiv", 0))
    else:
        ok.sort(key=lambda n: (COST_RANK.get(n, 2), hours_on(ledger["sources"][n], a100_h)))
    return ok[0], why


def record(ledger: dict, source: str, exp: str, hours: float, grades: Path | None) -> dict:
    s = ledger["sources"][source]
    g = json.loads(grades.read_text()) if grades and grades.exists() else {}
    run = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": source, "exp": exp, "hours": hours,
           "best_valid_loss": g.get("best_valid_loss"), "license": g.get("license"),
           "tokens_per_s": (g.get("throughput") or {}).get("tokens_per_s")}
    if "cu_left" in s:
        s["cu_left"] = round(s["cu_left"] - hours * s.get("cu_per_a100_h", 13) * s.get("a100_equiv", 1), 1)
    elif s.get("hours_left_week") is not None:
        s["hours_left_week"] = round(s["hours_left_week"] - hours, 2)
    ledger["runs"].append(run)
    ledger["updated"] = run["time"]
    return run


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("route", "record", "show"))
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--kind", default="pretrain", choices=("pretrain", "sweep", "eval", "data"))
    ap.add_argument("--a100-h", type=float, default=1.0)
    ap.add_argument("--source", default="")
    ap.add_argument("--exp", default="")
    ap.add_argument("--hours", type=float, default=0.0)
    ap.add_argument("--grades", type=Path, default=None)
    a = ap.parse_args(argv)
    led = json.loads(a.ledger.read_text())
    if a.cmd == "route":
        pick, why = route(led, a.kind, a.a100_h)
        print(json.dumps({"pick": pick, "skipped": why}, indent=1))
        return 0 if pick else 1
    if a.cmd == "record":
        print(json.dumps(record(led, a.source, a.exp, a.hours, a.grades)))
        a.ledger.write_text(json.dumps(led, indent=1) + "\n")
        return 0
    for n, s in led["sources"].items():
        print(f"{n:16s} {s['status']:8s} {s['accel']:18s} left {capacity_h(n, s) if s['status'] == 'active' else '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
