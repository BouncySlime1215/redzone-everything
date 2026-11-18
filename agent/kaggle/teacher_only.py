#!/usr/bin/env python3
"""[EXP-086 / EXP-102] Teacher-only round: build moments for the starved leagues, ask the 32B Teacher about TRAIN
nights only, grade its blind calls against what really happened, and write teacher_out.jsonl for the students.

    python agent/kaggle/teacher_only.py --out /content/t --leagues cbb nhl mlb --budget-h 3.0

Serves: North Star "crunch live %" for the starved leagues (CBB / NHL / MLB had no Teacher labels: the pack's queue
is NFL / NBA / NHL only); owner: lane 1 (Noggin training).

Data gate (Nick's A/B call, #202 catalog): the moment builder (build_moments_kaggle.py) pulls ESPN's site API,
which the catalog rates red for training (Disney ToU). This script refuses to run that builder unless
RZ_DATA_PLAN=B, the value it takes once Nick picks plan B and decisions.md says so. Plan A (EXP-102,
RZ_DATA_PLAN=A, --source green) reads the archive scripts/green_summaries.py writes from nflverse NFL and NBA.com cdn
NBA (RZ_BUILD_ARCHIVE); it refuses any other league.

1. Moments: build_moments_kaggle.py --leagues ... (its time split by whole night; locked >= 25% per league; locked
   games are never downloaded, asserted there).
2. Queue: grounded requests (brief + what happened next) for TRAIN-night rows only, balanced by league, plus blind
   requests for a held-back sample of TRAIN-night rows (BLIND_EVERY) so the Teacher's own calls can be graded
   against real outcomes. No valid, test or locked row is ever asked.
3. Teacher: run_teacher.py (Qwen3-32B-AWQ on vLLM, falling back to 14B), resumable from teacher_out.jsonl.
4. Grades (printed + grades.json): answered share and valid-JSON share per league (bar: >= 90% answered, >= 99%
   valid), and per league the blind lock accuracy against the hindsight target beside the fast head's (out of fold,
   cuts fitted on the other sampled train rows) and "today"'s (None while briefs carry no brain line), with counts. Agreement with any student is not a line here (grading rule 3).
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
GREEN_LEAGUES = {"nfl", "nba"}   # scripts/green_summaries.py (nflverse, NBA.com cdn)
PER_LEAGUE = 6000         # grounded requests per league, at most (the 32B answers ~25-40/min on one A100)


class DataGate(SystemExit):
    pass


def gate(plan: str, source: str, archive: str = "") -> None:
    if source == "espn" and plan != "B":
        raise DataGate("the ESPN moment builder is red in the #202 catalog: set RZ_DATA_PLAN=B only after Nick picks "
                       "plan B (decisions.md); plan A uses --source green")
    if source == "green":
        if plan != "A":
            raise DataGate("--source green is the plan A path (EXP-102): set RZ_DATA_PLAN=A only after Nick picks plan A")
        if not archive:
            raise DataGate("--source green reads the archive scripts/green_summaries.py writes: set RZ_BUILD_ARCHIVE")


def read_rows(moments: Path, league: str, split: str) -> list[dict]:
    p = moments / "rows" / f"{league}_{split}.jsonl.gz"
    if not p.exists():
        return []
    with gzip.open(p, "rt") as f:
        return [json.loads(x) for x in f if x.strip()]


def queue(moments: Path, leagues: list[str], system: str, per_league: int = PER_LEAGUE) -> tuple[list, list, list]:
    """(requests, blind_rows, fit_rows): TRAIN rows only. The blind sample is graded against its hindsight target;
    the rest of the sampled rows (never asked blind) fit "today"'s per-league cuts, as valid rows do in compare."""
    from agent.distill.teacher import reasoner_requests
    rng = random.Random(SEED)
    blind_rows, fit_rows, per = [], [], []
    for lg in leagues:
        rows = [r for r in read_rows(moments, lg, "train") if r.get("split") == "train"]
        rng.shuffle(rows)
        rows = rows[:per_league]
        for i, r in enumerate(rows):
            (blind_rows if i % BLIND_EVERY == 0 else fit_rows).append(r)
        g = reasoner_requests(rows, system, grounded_splits=("train",), blind_splits=())
        b = reasoner_requests([{**r, "split": "test"} for r in rows[::BLIND_EVERY]], system,
                              grounded_splits=(), blind_splits=("test",))
        mine = []                                     # each blind call right after its 8 grounded ones
        for i in range(max(len(g), len(b) * BLIND_EVERY)):
            if i < len(g):
                mine.append(g[i])
            if i % BLIND_EVERY == BLIND_EVERY - 1 and i // BLIND_EVERY < len(b):
                mine.append(b[i // BLIND_EVERY])
        mine += b[len(mine) - len(g):] if len(mine) < len(g) + len(b) else []
        per.append(mine)
    # leagues round-robin: a budget stop leaves every league about equally answered, blind grades included
    reqs = [m[k] for k in range(max((len(m) for m in per), default=0)) for m in per if k < len(m)]
    assert all(r.get("split") == "train" for r in blind_rows), "only train nights are ever asked"
    return reqs, blind_rows, fit_rows


def grades(reqs: list[dict], answers: list[dict], blind_rows: list[dict], fit_rows: list[dict] = ()) -> dict:
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
    rows = [{**r, "split": "test"} for r in blind_rows] + [{**r, "split": "valid"} for r in fit_rows]
    if t and rows:
        # "today" reads the brain line, which round 2's briefs leave out (no brain leak), so it is None by design;
        # "head" is the fast head's out-of-fold odds on those nights: the baseline the Teacher must beat
        res = evaluate.compare(rows, {"today": None, "head": None, "teacher": t})
        for lg, v in out["per_league"].items():
            for src in ("today", "head", "teacher"):
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
    gate(os.environ.get("RZ_DATA_PLAN", ""), a.source, os.environ.get("RZ_BUILD_ARCHIVE", ""))
    if a.source == "green":
        bad = set(a.leagues) - GREEN_LEAGUES
        if bad:
            raise DataGate(f"no green moment source for {sorted(bad)} yet (green: {sorted(GREEN_LEAGUES)})")
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
    reqs, blind_rows, fit_rows = queue(moments, a.leagues, system, a.per_league)
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
    g = {"exp": a.exp, "seed": SEED,
         "license": "open (green sources only)" if a.source == "green" else "personal-use, not for commercial release", "leagues": a.leagues, "source": a.source,
         "data_plan": os.environ.get("RZ_DATA_PLAN"), "code_pack": os.environ.get("RZ_CODE_PACK"),
         "split": "by whole night (build_moments split_plan.json); only TRAIN nights asked; locked never downloaded",
         **grades(reqs, answers, blind_rows, fit_rows), "wall_h": round((time.time() - t0) / 3600, 2)}
    (a.out / "grades.json").write_text(json.dumps(g, indent=1))
    print("===== grades.json =====")
    print(json.dumps(g, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
