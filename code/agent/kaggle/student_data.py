"""Training sets per adapter job from the pack + the teacher's answers. Stdlib only.

    jobs(pack_dir, teacher_answers) -> {"game-reasoner": {...}, "watchdog": {...}}

Each job: train / valid (mlx_lm chat rows) and test (held-out prompts {id, task, messages}).

game-reasoner: the reasoner moments (tier a: every archived moment, gold rules applied) as chat rows,
  with the teacher's words where it wrote them (tier b) and the gold moments up-weighted (tier c),
  plus the jump-or-stay task. Train is capped (GPU time): every teacher-worded and gold row first,
  then a sample of the rest that keeps must_see / watch / none balanced. Test = the graded sample.
watchdog: the watchdog's own make_data output (labels from agent/policy rules). The teacher's new
  wordings go to out.zip as wordings_teacher.json; the watchdog's make_data labels and leak-checks
  them, and the next student run trains on them.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from agent.distill import student
from agent.distill.teacher import STUDENT_SYSTEM, index_answers, read_jsonl

MAX_TRAIN = {"game-reasoner": 24000, "watchdog": 100000}
MAX_VALID = 1500
GOLD_WEIGHT = 2


def _rows(d: Path, name: str) -> list[dict]:
    p = d / name
    return read_jsonl(p) if (p.exists() or Path(str(p) + ".gz").exists()) else []


def chat_of(system: str, r: dict) -> dict:
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": r["brief"]},
                         {"role": "assistant", "content": json.dumps(r["target"])}]}


def pick_train(rows: list[dict], answered: set[str], cap: int, seed: int = 7) -> list[dict]:
    """Teacher-worded and gold rows first, then a lock-balanced sample of the rest, up to cap."""
    rng = random.Random(seed)
    first = [r for r in rows if r["id"] in answered or r.get("gold")]
    rest = [r for r in rows if not (r["id"] in answered or r.get("gold"))]
    room = max(0, cap - len(first))
    by: dict[str, list[dict]] = {}
    for r in rest:
        by.setdefault(r["target"]["lock_in"], []).append(r)
    take = []
    share = {"must_see": 0.4, "watch": 0.3, "none": 0.3}
    for k, v in by.items():
        rng.shuffle(v)
        take += v[:int(room * share.get(k, 0.3))]
    out = (first + take)[:cap] if len(first) > cap else first + take
    out.sort(key=lambda r: r["id"])
    return out


def reasoner_job(pack: Path, answers: list[dict]) -> dict:
    d = pack / "data" / "reasoner"
    system = (d / "system.txt").read_text() if (d / "system.txt").exists() else STUDENT_SYSTEM
    rows = {s: _rows(d, f"rows_{s}.jsonl") for s in ("train", "valid", "test")}
    ans = index_answers(answers)
    worded = {k.split("|")[0] for k in ans if k.endswith("|grounded")}
    rows["train"] = pick_train(rows["train"], worded, MAX_TRAIN["game-reasoner"])
    rows["valid"] = pick_train(rows["valid"], worded, MAX_VALID)
    chats = {s: [chat_of(system, r) for r in rows[s]] for s in ("train", "valid")}
    merged = student.build({s: rows[s] for s in ("train", "valid")}, chats, answers)
    gold_ids = {r["id"] for r in rows["train"] if r.get("gold")}
    train = []
    for m, row in zip(merged["meta"], merged["train"] + merged["valid"]):
        if m["split"] == "train":
            train += [row] * (GOLD_WEIGHT if m["id"] in gold_ids else 1)
    grade = set(json.loads((pack / "data" / "grade_ids.json").read_text())) \
        if (pack / "data" / "grade_ids.json").exists() else {r["id"] for r in rows["test"]}
    test = [{"id": r["id"], "task": "reasoner_blind",
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": r["brief"]}]}
            for r in rows["test"] if r["id"] in grade]
    jrows = _rows(pack / "data", "jump_rows.jsonl")
    for r in jrows:
        r["jump"]["hard"] = bool(r["jump"].get("hard") or r.get("gold"))
    js = student.jump_sets(jrows, answers)
    meta = merged["meta"] + [{**m, "task": "jump"} for m in js["meta"]]
    return {"train": train + js["train"], "valid": merged["valid"] + js["valid"][:MAX_VALID],
            "test": test + js["test"], "meta": meta}


def watchdog_job(pack: Path) -> dict | None:
    d = pack / "data" / "watchdog"
    if not (d / "train.jsonl").exists():
        return None
    test = [{"id": f"wd-test-{i}", "task": "watchdog", "messages": r["messages"][:2]}
            for i, r in enumerate(_rows(d, "test.jsonl"))]
    return {"train": _rows(d, "train.jsonl"), "valid": _rows(d, "valid.jsonl"), "test": test, "meta": []}


def jobs(pack: Path, answers: list[dict]) -> dict[str, dict]:
    out = {"game-reasoner": reasoner_job(pack, answers)}
    wd = watchdog_job(pack)
    if wd:
        out["watchdog"] = wd
    return out


def write(job_sets: dict[str, dict], out: Path) -> dict:
    info = {}
    for name, s in job_sets.items():
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        for k in ("train", "valid", "test", "meta"):
            with open(d / f"{k}.jsonl", "w") as f:
                for r in s[k]:
                    f.write(json.dumps(r) + "\n")
        src: dict[str, int] = {}
        for m in s["meta"]:
            key = f"{m.get('task', 'reasoner')}:{m.get('src', '?')}"
            src[key] = src.get(key, 0) + 1
        info[name] = {"train": len(s["train"]), "valid": len(s["valid"]), "test": len(s["test"]), "src": src}
    return info
