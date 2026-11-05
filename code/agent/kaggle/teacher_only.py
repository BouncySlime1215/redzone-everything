#!/usr/bin/env python3
"""[EXP-086 / EXP-102] Teacher-only round: build moments for the starved leagues, ask the 32B Teacher about TRAIN
nights only, grade its blind calls against what really happened, and write teacher_out.jsonl for the students.

    python agent/kaggle/teacher_only.py --out /content/t --leagues cbb nhl mlb --budget-h 3.0

Serves: North Star "crunch live %" for the starved leagues (CBB / NHL / MLB had no Teacher labels: the pack's queue
is NFL / NBA / NHL only); owner: lane 1 (Noggin training).

Data gate (Nick's A/B call, #202 catalog): the moment builder (build_moments_kaggle.py) pulls ESPN's site API,
which the catalog rates red for training (Disney ToU). This script refuses to run that builder unless
RZ_DATA_PLAN=B, the value it takes once Nick picks plan B and decisions.md says so. Plan A (EXP-102) needs the
green moment builder (nflverse / NBA.com cdn), which is not built yet; it refuses that too until it exists.

1. Moments: build_moments_kaggle.py --leagues ... (its time split by whole night; locked >= 25% per league; locked
   games are never downloaded, asserted there).
2. Queue: grounded requests (brief + what happened next) for TRAIN-night rows only, balanced by league, plus blind
   requests for a held-back sample of TRAIN-night rows (BLIND_EVERY) so the Teacher's own calls can be graded
   against real outcomes. No valid, test or locked row is ever asked.
3. Teacher: run_teacher.py (Qwen3-32B-AWQ on vLLM, falling back to 14B), resumable from teacher_out.jsonl.
4. Grades (printed + grades.json): answered share and valid-JSON share per league (bar: >= 90% answered, >= 99%
   valid), and per league the blind lock accuracy against the hindsight target beside "today" (the rules), with
   counts. Agreement with any student is not a line here (grading rule 3).
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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SEED = 7
BLIND_EVERY = 8           # every 8th sampled train row is also asked blind (the real-outcome grade)
PER_LEAGUE = 6000         # grounded requests per league, at most (the 32B answers ~25-40/min on one A100)


class DataGate(SystemExit):
    pass


def gate(plan: str, source: str) -> None:
    if source == "espn" and plan != "B":
        raise DataGate("the ESPN moment builder is red in the #202 catalog: set RZ_DATA_PLAN=B only after Nick picks "
                       "plan B (decisions.md); plan A uses --source green")
    if source == "green":
        raise DataGate("--source green needs the green moment builder (EXP-102), which is not built yet")


def read_rows(moments: Path, league: str, split: str) -> list[dict]:
    p = moments / "rows" / f"{league}_{split}.jsonl.gz"
    if not p.exists():
        return []
    with gzip.open(p, "rt") as f:
        return [json.loads(x) for x in f if x.strip()]


def queue(moments: Path, leagues: list[str], system: str, per_league: int = PER_LEAGUE) -> tuple[list, list]:
    """(requests, blind_rows): TRAIN rows only; the blind sample is graded against its hindsight target."""
    from agent.distill.teacher import reasoner_requests
    rng = random.Random(SEED)
    reqs, blind_rows = [], []
    for lg in leagues:
        rows = [r for r in read_rows(moments, lg, "train") if r.get("split") == "train"]
        rng.shuffle(rows)
        rows = rows[:per_league]
        for i, r in enumerate(rows):
            if i % BLIND_EVERY == 0:
                blind_rows.append(r)
        reqs += reasoner_requests(rows, system, grounded_splits=("train",), blind_splits=())
        reqs += reasoner_requests([{**r, "split": "test"} for r in rows[::BLIND_EVERY]], system,
                                  grounded_splits=(), blind_splits=("test",))
    assert all(r.get("split") == "train" for r in blind_rows), "only train nights are ever asked"
    return reqs, blind_rows


def grades(reqs: list[dict], answers: list[dict], blind_rows: list[dict]) -> dict:
    from agent.distill import evaluate
    from agent.distill.teacher import extract_json, index_answers
    by_id = {a["id"]: a for a in answers}
    out: dict = {"per_league": {}}
    for lg in sorted({r["id"].split(":")[0] for r in reqs}):
        mine = [r for r in reqs if r["id"].startswith(f"{lg}:")]
        got = [by_id[r["id"]] for r in mine if r["id"] in by_id]
        ok = [a for a in got if extract_json(a.get("text") or "") is not None]
        out["per_league"][lg] = {"asked": len(mine), "answered": len(got),
                                 "answered_pct": round(100 * len(got) / max(1, len(mine)), 1),
                                 "valid_json_pct": round(100 * len(ok) / max(1, len(got)), 1)}
    t = index_answers(a for a in answers if a.get("task") == "reasoner_blind")
    rows = [{**r, "split": "test"} for r in blind_rows]
    if t and rows:
        res = evaluate.compare(rows, {"today": None, "teacher": t})
        for lg, v in out["per_league"].items():
            for src in ("today", "teacher"):
                s = (res.get(src) or {}).get(lg) or {}
                v[f"blind_lock_acc_{src}"] = s.get("lock_acc")
                v[f"blind_n_{src}"] = s.get("n")
    tot = [v for v in out["per_league"].values()]
    out["bar"] = {"answered_ge_90": all(v["answered_pct"] >= 90 for v in tot),
                  "valid_json_ge_99": all(v["valid_json_pct"] >= 99 for v in tot)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", nargs="+", default=["cbb", "nhl", "mlb"])
    ap.add_argument("--source", choices=("espn", "green"), default="espn")
    ap.add_argument("--build-h", type=float, default=1.5, help="moment-download budget (CPU, runs first)")
    ap.add_argument("--budget-h", type=float, default=3.0, help="Teacher budget")
    ap.add_argument("--per-league", type=int, default=PER_LEAGUE)
    ap.add_argument("--exp", default="EXP-086")
    ap.add_argument("--system", type=Path, default=None, help="the pack's data/reasoner/system.txt")
    a = ap.parse_args(argv)
    gate(os.environ.get("RZ_DATA_PLAN", ""), a.source)
    t0 = time.time()
    a.out.mkdir(parents=True, exist_ok=True)
    moments = a.out / "moments"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    if not (moments / "DONE").exists():
        subprocess.run([sys.executable, "agent/kaggle/build_moments_kaggle.py", "--out", str(moments),
                        "--leagues", *a.leagues, "--budget-h", str(a.build_h),
                        *(["--archive", os.environ["RZ_BUILD_ARCHIVE"]] if os.environ.get("RZ_BUILD_ARCHIVE") else [])],
                       cwd=ROOT, env=env, check=True)
    from agent.distill.teacher import STUDENT_SYSTEM
    system = a.system.read_text() if a.system and a.system.exists() else STUDENT_SYSTEM
    reqs, blind_rows = queue(moments, a.leagues, system, a.per_league)
    rq = a.out / "teacher_requests.jsonl"
    with open(rq, "w") as f:
        for r in reqs:
            f.write(json.dumps(r) + "\n")
    print("queue:", len(reqs), {lg: sum(1 for r in reqs if r["id"].startswith(f"{lg}:")) for lg in a.leagues},
          flush=True)
    subprocess.run([sys.executable, "agent/kaggle/run_teacher.py", "--requests", str(rq), "--out", str(a.out),
                    "--budget-h", str(a.budget_h)], cwd=ROOT, env=env, check=False)
    answers = []
    if (a.out / "teacher_out.jsonl").exists():
        answers = [json.loads(x) for x in open(a.out / "teacher_out.jsonl") if x.strip()]
    g = {"exp": a.exp, "seed": SEED, "leagues": a.leagues, "source": a.source,
         "data_plan": os.environ.get("RZ_DATA_PLAN"), "code_pack": os.environ.get("RZ_CODE_PACK"),
         "split": "by whole night (build_moments split_plan.json); only TRAIN nights asked; locked never downloaded",
         **grades(reqs, answers, blind_rows), "wall_h": round((time.time() - t0) / 3600, 2)}
    (a.out / "grades.json").write_text(json.dumps(g, indent=1))
    print("===== grades.json =====")
    print(json.dumps(g, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
