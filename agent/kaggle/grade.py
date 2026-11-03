"""Phase 3: grade today vs teacher vs student on the held-out nights. Stdlib only.

    python agent/kaggle/grade.py --pack <pack dir> --work <dir with teacher_out.jsonl and students/>

Writes report.json and report.md (the tables for the PR) into --work.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.distill import evaluate, jump  # noqa: E402
from agent.distill.teacher import clean_jump, extract_json, index_answers, read_jsonl  # noqa: E402


def _read(p: Path) -> list[dict]:
    gz = p.with_name(p.name + ".gz")
    return read_jsonl(p) if p.exists() or gz.exists() else []


def reasoner(pack: Path, teacher: list[dict], student: list[dict]) -> dict:
    d = pack / "data" / "reasoner"
    gi = pack / "data" / "grade_ids.json"
    grade = set(json.loads(gi.read_text())) if gi.exists() else None
    rows = _read(d / "rows_valid.jsonl") + [r for r in _read(d / "rows_test.jsonl") if grade is None or r["id"] in grade]
    sources = {"today": None, "head": None}
    t = index_answers(a for a in teacher if a.get("task") == "reasoner_blind")
    s = index_answers(a for a in student if a.get("task") == "reasoner_blind")
    if t:
        sources["teacher"] = t
    if s:
        sources["student"] = s
    res = evaluate.compare(rows, sources)
    res["test_nights"] = sorted({f"{r['league']} {r['night']}" for r in rows if r["split"] == "test"})
    res["gate"] = {k: evaluate.gate(res[k]["all"]) for k in sources}
    gold = [r for r in rows if r.get("gold") or r.get("split") == "valid"]
    if any(r.get("gold") and r["split"] == "test" for r in gold):
        g = evaluate.compare(gold, sources)
        res["gold_hard"] = {k: g[k]["all"] for k in sources}
    return res


def jumps(pack: Path, teacher: list[dict], student: list[dict]) -> dict:
    rows = [r for r in _read(pack / "data" / "jump_rows.jsonl") if r.get("split") == "test"]

    def parsed(answers):
        ans = index_answers(a for a in answers if a.get("task") == "jump_blind")
        return {r["id"]: (clean_jump(extract_json(ans[f"{r['id']}|jump"]["text"])) if f"{r['id']}|jump" in ans
                          else None) for r in rows}

    def fixed(decide):
        return {r["id"]: decide(r) for r in rows}

    def naive(r):    # "today" without judgment: jump whenever the cautious length fits a 30 s reel
        fits = r["jump"]["p10_s"] >= jump.NEED_S and len(r["jump"]["clips"]) >= jump.MIN_CLIPS
        return {"decision": "jump" if fits else "stay", "reel_s": 30 if fits else 0}

    out = {"always_stay": jump.grade(rows, fixed(lambda r: {"decision": "stay", "reel_s": 0})),
           "jump_when_it_fits": jump.grade(rows, fixed(naive))}
    if any(a.get("task") == "jump_blind" for a in teacher):
        out["teacher"] = jump.grade(rows, parsed(teacher))
    if any(a.get("task") == "jump_blind" for a in student):
        out["student"] = jump.grade(rows, parsed(student))
    hard = [r for r in rows if r["jump"].get("hard")]
    if hard:
        sub = {}
        for k, ans in (("teacher", teacher), ("student", student)):
            if any(a.get("task") == "jump_blind" for a in ans):
                p = parsed(ans)
                sub[k] = jump.grade(hard, {r["id"]: p[r["id"]] for r in hard})
        out["hard_cases"] = sub
    return out


def watchdog(pack: Path, work: Path) -> dict:
    labels = _read(pack / "data" / "watchdog" / "test.labels.jsonl")
    if not labels:
        return {}
    from agent.policy import parse_answer
    out = {}
    for name, f in (("base (no adapter)", "test_out_base.jsonl"), ("student", "test_out.jsonl")):
        ans = _read(work / "students" / "watchdog" / f)
        if not ans:
            continue
        by: dict[str, list[int]] = {}
        bad = right = 0
        for a, lab in zip(ans, labels):
            v = parse_answer(a["text"])
            bad += v is None
            ok = v is not None and v.action == lab["label"] and \
                (not lab.get("target") or v.target == lab["target"] or v.action in ("none", "notify"))
            right += ok
            k = by.setdefault(lab.get("kind", "?"), [0, 0])
            k[0] += ok
            k[1] += 1
        n = len(labels)
        out[name] = {"n": n, "accuracy": round(100 * right / max(1, n), 1), "unusable_json": bad,
                     "gate": "PASS" if 100 * right / max(1, n) >= evaluate.WATCHDOG_GATE else "FAIL",
                     "by_kind": {k: f"{c}/{t}" for k, (c, t) in sorted(by.items())}}
    return out


def md(rep: dict) -> str:
    r = rep["reasoner"]
    parts = ["## Game reasoner, held-out nights", "", f"Nights: {', '.join(r['test_nights'])}", ""]
    for lg in ["all", "nfl", "nba", "nhl"]:
        if lg in r.get("today", {}):
            parts += [f"### {lg}", "", evaluate.table(r, lg), ""]
    if r.get("gold_hard"):
        parts += ["Gold hard cases only (held-out): " + "; ".join(
            f"{k}: lock_acc {v.get('lock_acc')} unsafe {v.get('unsafe_leave_pct')}" for k, v in r["gold_hard"].items()), ""]
    parts += ["Ship gate (100% valid answers, lock_in >= 95% vs gold, unsafe leaves <= 0.5%): " +
              "; ".join(f"{k}: {'PASS' if not v else 'FAIL ' + ', '.join(v)}" for k, v in r["gate"].items()), ""]
    j = rep["jump"]
    keys = ["n", "jumps", "reels_in_dead_time_pct", "big_missed_on_reel", "agree_pct", "jump_recall_pct"]
    srcs = [k for k in j if k != "hard_cases"]
    parts += ["## Highlight jump vs stay, held-out nights", "",
              "| line | " + " | ".join(srcs) + " |", "|---|" + "---|" * len(srcs)]
    for k in keys:
        parts.append(f"| {k} | " + " | ".join(str(j[s].get(k, "")) for s in srcs) + " |")
    if j.get("hard_cases"):
        parts += ["", "Hard cases only: " + json.dumps(j["hard_cases"])]
    w = rep.get("watchdog") or {}
    if w:
        parts += ["", "## Watchdog, held-out wordings", "",
                  "| | " + " | ".join(w) + " |", "|---|" + "---|" * len(w)]
        for k in ("accuracy", "unusable_json", "gate"):
            parts.append(f"| {k} | " + " | ".join(str(w[s][k]) for s in w) + " |")
        kinds = sorted({kk for s in w.values() for kk in s["by_kind"]})
        for kk in kinds:
            parts.append(f"| {kk} | " + " | ".join(w[s]["by_kind"].get(kk, "") for s in w) + " |")
    return "\n".join(parts) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--work", required=True)
    a = ap.parse_args()
    pack, work = Path(a.pack), Path(a.work)
    sys.path.insert(0, str(pack / "code"))
    teacher = _read(work / "teacher_out.jsonl")
    student = _read(work / "students" / "game-reasoner" / "test_out.jsonl")
    rep = {"reasoner": reasoner(pack, teacher, student), "jump": jumps(pack, teacher, student),
           "watchdog": watchdog(pack, work)}
    for f in ("teacher_report.json", "students/student_data.json"):
        if (work / f).exists():
            rep[os.path.basename(f)] = json.loads((work / f).read_text())
    (work / "report.json").write_text(json.dumps(rep, indent=1))
    (work / "report.md").write_text(md(rep))
    print(md(rep))


if __name__ == "__main__":
    main()
