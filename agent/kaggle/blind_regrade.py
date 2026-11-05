#!/usr/bin/env python3
"""[EXP-1403, Data planner E] Blind Teacher vs fast head on the REAL-outcome lock label (EXP-302 y_out).

Nick, Nov 5 1:50 AM: the old lock label is our own clock rule, and n = 150 blind is too small. This asks the 32B
Teacher the blind reasoner question on >= 600 TRAIN moments per league (NBA first, then NFL) and grades it and the
fast head on y_out (redzone.lab.outcome_label: lead change / catch or a win-probability swing in the next few
minutes), with balanced accuracy and a 95% night-bootstrap CI (outcome_label.lock_grade).

    python agent/kaggle/blind_regrade.py --moments MOMENTS --out OUT --leagues nba nfl --per-league 700 --budget-h 1.5

Runs inside colab/teacher32b_blind_regrade_a100.ipynb with PYTHONPATH = the kaggle-inputs code pack, overlaid with
#278's redzone/lab/outcome_label.py + reasoner_data.py (so every moment row carries y_out).
Fairness: the head and the Teacher are graded on exactly the same answered rows. The head's must_see cut is fitted
on VALID rows (max balanced accuracy on y_out), never on the graded rows. The asked rows are train nights, which the
head may have been fit on, so the head's number on them can be optimistic; its valid and test AUC are reported too.
Pre-registered bar (EXP-302 / EXP-1403): the Teacher is worth distilling in a league only if its balanced-accuracy
CI lower bound beats the head's point estimate; n < 600 answered = PENDING.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

SEED = 1403
LICENSE = "personal-use, not for commercial release"


def read_rows(moments: Path, league: str, split: str) -> list[dict]:
    p = moments / "rows" / f"{league}_{split}.jsonl.gz"
    if not p.exists():
        return []
    with gzip.open(p, "rt") as f:
        return [json.loads(x) for x in f if x.strip()]


def queue(moments: Path, leagues: list[str], system: str, per_league: int) -> tuple[list[dict], dict]:
    from agent.distill.teacher import reasoner_requests
    rng = random.Random(SEED)
    per, asked = [], {}
    for lg in leagues:
        rows = [r for r in read_rows(moments, lg, "train") if r.get("split") == "train" and "y_out" in r]
        rng.shuffle(rows)
        rows = rows[:per_league]
        asked[lg] = rows
        per.append(reasoner_requests([{**r, "split": "test"} for r in rows], system, grounded_splits=(),
                                     blind_splits=("test",)))
    assert all(r["split"] == "train" for rs in asked.values() for r in rs), "only train nights are ever asked"
    return [m[k] for k in range(max((len(m) for m in per), default=0)) for m in per if k < len(m)], asked


def fit_cut(valid: list[dict]) -> float:
    """head_p cut for must_see that maximizes balanced accuracy on y_out over VALID rows."""
    from redzone.lab.outcome_label import _bal
    best, cut = -1.0, 0.5
    for c in sorted({round(float(r.get("head_p") or 0), 3) for r in valid}):
        calls = {r["id"]: "must_see" if float(r.get("head_p") or 0) >= c else "none" for r in valid}
        b = _bal(valid, calls, "y_out")[0]
        if b == b and b > best:
            best, cut = b, c
    return cut


def grade(moments: Path, asked: dict, answers: list[dict]) -> dict:
    from agent.distill.teacher import extract_json
    from redzone.lab.outcome_label import auc, lock_grade
    by_id = {a["id"].split("|")[0]: a for a in answers if a["id"].endswith("|blind")}
    out = {}
    for lg, rows in asked.items():
        t_calls = {}
        for r in rows:
            j = extract_json((by_id.get(r["id"]) or {}).get("text") or "")
            if j and str(j.get("lock_in", "")).lower() in ("none", "watch", "must_see"):
                t_calls[r["id"]] = str(j["lock_in"]).lower()
        graded = [r for r in rows if r["id"] in t_calls]
        valid = [r for r in read_rows(moments, lg, "valid") if "y_out" in r]
        test = [r for r in read_rows(moments, lg, "test") if "y_out" in r]
        cut = fit_cut(valid) if valid else 0.5
        h_calls = {r["id"]: "must_see" if float(r.get("head_p") or 0) >= cut else "none" for r in graded}
        t, h = lock_grade(graded, t_calls), lock_grade(graded, h_calls)
        ci = t.get("ci95")
        powered = t["n"] >= 600
        verdict = ("PENDING (n < 600)" if not powered else
                   "DISTILL (Teacher CI lower > head)" if ci and ci[0] > h["balanced_acc"] else "NO GAIN")

        def _auc(rs):
            return auc([int(r["y_out"]) for r in rs], [float(r.get("head_p") or 0) for r in rs]) if rs else None
        out[lg] = {"asked": len(rows), "answered_valid": len(graded), "teacher": t, "head": h,
                   "head_cut_fit_on_valid": cut, "head_auc_y_out": {"graded_train": _auc(graded), "valid": _auc(valid),
                                                                    "test": _auc(test)},
                   "verdict": verdict}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--moments", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", nargs="+", default=["nba", "nfl"])
    ap.add_argument("--per-league", type=int, default=700, help="asked; >= 600 must come back valid to be powered")
    ap.add_argument("--budget-h", type=float, default=1.5)
    ap.add_argument("--system", type=Path, default=None)
    ap.add_argument("--code", type=Path, required=True, help="the code pack root (run_teacher.py lives there)")
    a = ap.parse_args(argv)
    from agent.distill.teacher import STUDENT_SYSTEM
    system = a.system.read_text() if a.system and a.system.exists() else STUDENT_SYSTEM
    a.out.mkdir(parents=True, exist_ok=True)
    reqs, asked = queue(a.moments, a.leagues, system, a.per_league)
    rq = a.out / "blind_requests.jsonl"
    rq.write_text("".join(json.dumps(r) + "\n" for r in reqs))
    print("blind queue:", {lg: len(v) for lg, v in asked.items()}, flush=True)
    t0 = time.time()
    subprocess.run([sys.executable, "-u", "agent/kaggle/run_teacher.py", "--requests", str(rq), "--out", str(a.out),
                    "--budget-h", str(a.budget_h)], cwd=a.code, env=os.environ.copy(), check=False)
    p = a.out / "teacher_out.jsonl"
    answers = [json.loads(x) for x in open(p) if x.strip()] if p.exists() else []
    g = {"exp": "EXP-1403", "label": "y_out (EXP-302, #278)", "license": LICENSE, "seed": SEED,
         "data_plan": os.environ.get("RZ_DATA_PLAN"), "code_pack": os.environ.get("RZ_CODE_PACK"),
         "split": "asked = TRAIN nights only; head cut fit on VALID; locked never downloaded",
         "bar": "Teacher balanced-acc CI lower > head point estimate, n >= 600 answered per league",
         "per_league": grade(a.moments, asked, answers), "teacher_wall_h": round((time.time() - t0) / 3600, 2)}
    (a.out / "grades.json").write_text(json.dumps(g, indent=1))
    print("===== grades.json =====")
    print(json.dumps(g, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
