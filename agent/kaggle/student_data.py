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
Why: Builds each student adapter's training set from the pack and teacher answers (docstring).
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


# [V7] The watchdog's training rows travel in the code pack (agent/train/make_data.py on
# claude/watchdog-v7-data: +10 train wordings and 3x rows for paused, prompt, frozen and upstream), so the
# data pack needn't be re-uploaded. Valid and test (and their labels) are byte-identical to the pack's.
WATCHDOG_TRAIN_V7 = Path(__file__).resolve().parent / "assets" / "watchdog_v7"
# [V8.1, EXP-101] V7's rows + counterfactual rows (agent/kaggle/watchdog_cf.py); picked with RZ_WATCHDOG_TRAIN=v81.
WATCHDOG_TRAIN_V81 = Path(__file__).resolve().parent / "assets" / "watchdog_v81"


def watchdog_job(pack: Path) -> dict | None:
    d = pack / "data" / "watchdog"
    if not (d / "train.jsonl").exists():
        return None
    test = [{"id": f"wd-test-{i}", "task": "watchdog", "messages": r["messages"][:2]}
            for i, r in enumerate(_rows(d, "test.jsonl"))]
    import os
    src = WATCHDOG_TRAIN_V81 if os.environ.get("RZ_WATCHDOG_TRAIN") == "v81" else WATCHDOG_TRAIN_V7
    tr = _rows(src, "train.jsonl") or _rows(d, "train.jsonl")
    return {"train": tr, "valid": _rows(d, "valid.jsonl"), "test": test, "meta": []}


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
        for extra in ("grade_rows", "jump_rows"):        # what grade.py scores (big build only)
            if extra in s:
                with open(d / f"{extra}.jsonl", "w") as f:
                    for r in s[extra]:
                        f.write(json.dumps(r) + "\n")
        info[name] = {"train": len(s["train"]), "valid": len(s["valid"]), "test": len(s["test"]), "src": src, **s.get("info", {})}
    return info


# ---------------------------------------------------------------- round 2+: the multi-season build
BIG_TRAIN = 40000          # what ~3 h of one T4 trains on (1.7B LoRA, ~500-token prompts)
LEAGUE_SHARE = {"nfl": 0.30, "cfb": 0.17, "nba": 0.15, "nhl": 0.15, "cbb": 0.13, "mlb": 0.10}
GRADE_PER_LEAGUE = {"test": 400, "valid": 150, "train": 100}   # dev-test, threshold picks, train side of the gap


def weight(r: dict) -> float:
    """Pick weight: crunch and your-team moments up, real big plays up, the hardest up."""
    from agent.distill.evaluate import sticky
    # V7: the crunch slice left 100% unsafely (n=125), so crunch and down-the-stretch rows weigh most
    return (1.0 + 6.0 * bool(r.get("crunch") or r.get("crunch_next")) + 3.0 * sticky(r) + 2.0 * bool(r.get("mine"))
            + 1.5 * bool(r.get("y")) + 0.5 * float(r.get("hard") or 0.0))


def _reservoir(paths: list[Path], k: int, key, seed: str, keep_first=lambda r: False) -> list[dict]:
    """Weighted sample without replacement (Efraimidis-Spirakis), streamed: millions never sit in memory.
    Rows for which keep_first() is true are always kept (they count toward k)."""
    import gzip
    import heapq
    rng = random.Random(seed)
    first, heap = [], []
    for p in paths:
        if not p.exists():
            continue
        with gzip.open(p, "rt") as f:
            for line in f:
                r = json.loads(line)
                if keep_first(r):
                    first.append(r)
                    continue
                u = rng.random() ** (1.0 / max(1e-6, key(r)))
                if len(heap) < k:
                    heapq.heappush(heap, (u, r["id"], r))
                elif u > heap[0][0]:
                    heapq.heapreplace(heap, (u, r["id"], r))
    rest = [x[2] for x in sorted(heap, key=lambda x: -x[0])]
    return (first + rest)[:max(k, len(first))]


def big_sets(pack: Path, moments: Path, answers: list[dict], n_train: int = BIG_TRAIN, seed: int = 7) -> dict:
    """The game-reasoner job from the multi-season build (rows/<league>_<split>.jsonl.gz).

    train: per-league quotas (NFL first), weighted by ``weight``; every teacher-worded row and
    every gold row of a TRAIN night is kept first. Pack rows are not re-used (the same games are in
    the build); the pack's gold labels and jump rows are, re-split by the build's time plan.
    """
    plan = json.loads((moments / "split_plan.json").read_text())
    rows_dir = moments / "rows"
    d = pack / "data" / "reasoner"
    system = (d / "system.txt").read_text() if (d / "system.txt").exists() else STUDENT_SYSTEM
    ans = index_answers(answers)
    worded = {k.split("|")[0] for k in ans if k.endswith("|grounded")}
    gold = {}
    for g in _rows(pack / "data" / "gold", "lock_gold.jsonl"):
        gold[g["id"]] = g
    leagues = [lg for lg in LEAGUE_SHARE if (rows_dir / f"{lg}_train.jsonl.gz").exists()]
    share = {lg: LEAGUE_SHARE[lg] / sum(LEAGUE_SHARE[x] for x in leagues) for lg in leagues}
    train, picked = [], {}
    for lg in leagues:
        k = int(n_train * share[lg])
        rs = _reservoir([rows_dir / f"{lg}_train.jsonl.gz"], k, weight, f"{seed}{lg}train",
                        keep_first=lambda r: r["id"] in worded or r["id"] in gold)
        for r in rs:
            assert plan[lg][r["night"]] == "train"
            if r["id"] in gold:
                r["target"] = gold[r["id"]]["gold"]
                r["gold"] = True
        picked[lg] = len(rs)
        train += rs
    valid = []
    grade_rows = []
    for lg in leagues:
        valid += _reservoir([rows_dir / f"{lg}_valid.jsonl.gz"], MAX_VALID // len(leagues), weight, f"{seed}{lg}v")
        for s, n in GRADE_PER_LEAGUE.items():
            src = rows_dir / f"{lg}_{s}.jsonl.gz"
            # uniform (not weighted): what live play looks like. V7 drew the train side from the WEIGHTED
            # pick (crunch x3, hard rows up), so train looked worse than test (75.2/21.7 vs 82.2/7.6); every
            # side is uniform now, train from train nights
            rs = _reservoir([src], n, lambda r: 1.0, f"{seed}{lg}{s}g")
            grade_rows += [{**r, "split": s} for r in rs]
    chats = {"train": [chat_of(system, r) for r in train], "valid": [chat_of(system, r) for r in valid]}
    merged = student.build({"train": train, "valid": valid}, chats, answers)
    tr = []
    for m, row in zip(merged["meta"], merged["train"] + merged["valid"]):
        if m["split"] == "train":
            tr += [row] * (GOLD_WEIGHT if m["id"] in gold else 1)
    test = [{"id": r["id"], "task": "reasoner_blind" if r["split"] == "test" else f"reasoner_blind_{r['split']}",
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": r["brief"]}]}
            for r in grade_rows]
    # jump-or-stay: the pack's dead balls, re-split by the build's time plan (locked ones dropped)
    jrows = []
    for r in _rows(pack / "data", "jump_rows.jsonl"):
        s = plan.get(r["league"], {}).get(str(r["night"]))
        if s is None or s == "locked":
            continue
        r["split"] = s
        r["jump"]["hard"] = bool(r["jump"].get("hard") or r.get("gold"))
        jrows.append(r)
    js = student.jump_sets(jrows, answers)
    meta = merged["meta"] + [{**m, "task": "jump"} for m in js["meta"]]
    info = {"picked_train": picked, "valid": len(valid), "grade_rows": len(grade_rows), "jump_rows": len(jrows)}
    return {"train": tr + js["train"], "valid": merged["valid"] + js["valid"][:MAX_VALID],
            "test": test + js["test"], "meta": meta, "grade_rows": grade_rows, "jump_rows": jrows, "info": info,
            "system": system}


def jobs_big(pack: Path, moments: Path, answers: list[dict]) -> dict[str, dict]:
    out = {"game-reasoner": big_sets(pack, moments, answers)}
    wd = watchdog_job(pack)
    if wd:
        out["watchdog"] = wd
    return out
