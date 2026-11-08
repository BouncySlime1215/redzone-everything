"""Phase 3: grade today vs teacher vs student on the held-out nights. Stdlib only.

    python agent/kaggle/grade.py --pack <pack dir> --work <dir with teacher_out.jsonl and students/>

Writes report.json and report.md (the tables for the PR) into --work.
Why: Phase 3 of the Kaggle Mind pipeline: grades teacher vs student on held-out nights (docstring).
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
    dk = _abstain(rows, evaluate.answers_for("student", rows, s)) if s else None
    res["gate_split"] = {k: evaluate.gate_split(res, k, dk if k == "student" else None)
                         for k in sources if k != "today"}
    gold = [r for r in rows if r.get("gold") or r.get("split") == "valid"]
    if any(r.get("gold") and r["split"] == "test" for r in gold):
        g = evaluate.compare(gold, sources)
        res["gold_hard"] = {k: g[k]["all"] for k in sources}
    return res


def _abstain(rows: list[dict], preds: dict) -> dict:
    """Calibrated "I don't know": the confidence cut is picked on VALID rows only (the lowest cut whose
    answered valid rows meet the gates; else the best lock_acc with unsafe leaves <= 0.5%), then
    applied to TEST rows. Below the cut the student abstains: the rules decide (stay on the game,
    lock_in from the head's cuts) and the moment is escalated."""
    def conf(r):
        p = preds.get(r["id"])
        try:
            return float(p.get("confidence")) if p and p is not evaluate.MISSING else -1.0
        except (TypeError, ValueError):
            return -1.0

    valid = [r for r in rows if r["split"] == "valid"]
    test = [r for r in rows if r["split"] == "test"]
    if not valid or not test:
        return {}
    best = None
    for k in range(0, 100, 5):
        t = k / 100
        sub = [r for r in valid if conf(r) >= t]
        if len(sub) < max(20, len(valid) // 20):
            break
        sc = evaluate.score(sub, preds)
        ok_unsafe = (sc["unsafe_leave_pct"] or 0) <= evaluate.GATE["unsafe_leave_pct"][1]
        if ok_unsafe and (sc["lock_acc"] or 0) >= evaluate.GATE["lock_acc"][1]:
            best = (t, sc)
            break
        if ok_unsafe and (best is None or (sc["lock_acc"] or 0) > (best[1]["lock_acc"] or 0)):
            best = (t, sc)
    if best is None:
        return {"cut": None, "note": "no confidence cut keeps unsafe leaves <= 0.5% on valid"}
    t = best[0]
    answered = [r for r in test if conf(r) >= t]
    sc = evaluate.score(answered, preds) if answered else {}
    return {"cut": t, "valid_at_cut": {k: best[1][k] for k in ("n", "lock_acc", "unsafe_leave_pct")},
            "test_coverage_pct": round(100 * len(answered) / len(test), 1),
            "test_answered": {k: sc.get(k) for k in ("n", "lock_acc", "unsafe_leave_pct", "usable_json_pct")},
            "escalated": len(test) - len(answered),
            "gate_on_answered": evaluate.gate(sc) if sc else ["nothing answered"]}


def reasoner_big(rows: list[dict], teacher: list[dict], student: list[dict]) -> dict:
    """Round 2+: the multi-season build's dev-test sample, per league, with the gap, slices and the
    calibrated "I don't know" mode."""
    sources = {"today": None, "head": None}
    t = index_answers(a for a in teacher if a.get("task") == "reasoner_blind")
    s = index_answers(a for a in student if str(a.get("task", "")).startswith("reasoner_blind"))
    if t:
        sources["teacher"] = t
    if s:
        sources["student"] = s
    res = evaluate.compare(rows, sources)
    res["test_nights"] = [f"{len({r['night'] for r in rows if r['split'] == 'test' and r['league'] == lg})} "
                          f"{lg} nights" for lg in sorted({r["league"] for r in rows})]
    res["gate"] = {k: evaluate.gate(res[k]["all"]) for k in sources}
    if s:
        preds = evaluate.answers_for("student", rows, s)
        gap = {}
        for sp in ("train", "valid", "test"):
            sub = [r for r in rows if r["split"] == sp]
            if sub:
                sc = evaluate.score(sub, preds)
                gap[sp] = {k: sc[k] for k in ("n", "lock_acc", "unsafe_leave_pct")}
        res["gap"] = gap
        test = [r for r in rows if r["split"] == "test"]
        slices = {"crunch": lambda r: r.get("crunch") or r.get("crunch_next"), "early": lambda r: not r.get("late"),
                  "late": lambda r: r.get("late"), "decided (garbage)": lambda r: r.get("garbage"),
                  "your team": lambda r: r.get("mine")}
        res["slices"] = {}
        for name, f in slices.items():
            sub = [r for r in test if f(r)]
            if sub:
                sc = evaluate.score(sub, preds)
                res["slices"][name] = {k: sc[k] for k in ("n", "lock_acc", "unsafe_leave_pct")}
        res["dont_know"] = _abstain(rows, preds)
    res["gate_split"] = {k: evaluate.gate_split(res, k, res.get("dont_know") if k == "student" else None)
                         for k in sources if k != "today"}
    return res


def jumps(pack: Path, teacher: list[dict], student: list[dict], jrows: list[dict] | None = None) -> dict:
    rows = [r for r in (jrows if jrows is not None else _read(pack / "data" / "jump_rows.jsonl"))
            if r.get("split") == "test"]

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
    parts = ["## Game reasoner, dev-test nights (the locked test set is never graded here)", "",
             f"Nights: {', '.join(r['test_nights'])}", ""]
    parts += [f"lock_acc leaves out {lg.upper()}: {why}" for lg, why in evaluate.LOCK_EXCLUDED.items()] + [""]
    for lg in ["all", "nfl", "cfb", "nba", "cbb", "nhl", "mlb"]:
        if lg in r.get("today", {}):
            parts += [f"### {lg}", "", evaluate.table(r, lg), ""]
    if r.get("gold_hard"):
        parts += ["Gold hard cases only (held-out): " + "; ".join(
            f"{k}: lock_acc {v.get('lock_acc')} unsafe {v.get('unsafe_leave_pct')}" for k, v in r["gold_hard"].items()), ""]
    for k, title in (("gap", "Train vs valid vs test (student; a gap over a few points = overfit)"),
                     ("slices", "Slices, dev-test (student)")):
        if r.get(k):
            parts += [f"{title}:", "", "| | n | lock_acc | unsafe_leave_pct |", "|---|---|---|---|"]
            parts += [f"| {n} | {v['n']} | {v['lock_acc']} | {v['unsafe_leave_pct']} |" for n, v in r[k].items()]
            parts.append("")
    if r.get("dont_know"):
        parts += ["Calibrated \"I don't know\" (cut picked on valid, applied to dev-test): " +
                  json.dumps(r["dont_know"]), ""]
    if rep.get("build"):
        b = rep["build"]
        parts += [f"Build: {b.get('total_rows')} moments; rows per league/split {json.dumps(b.get('rows'))}; "
                  f"head AUC train/valid/test {json.dumps({lg: [v.get('auc_train'), v.get('auc_valid'), v.get('auc_test')] for lg, v in (b.get('head') or {}).items()})}", ""]
    if r.get("gate_split"):
        parts += ["## [V7] Split reasoner gate (B11)", "", "| line | " + " | ".join(r["gate_split"]) + " |",
                  "|---|" + "---|" * len(r["gate_split"])]
        keys = sorted({k for v in r["gate_split"].values() for k in v})
        for k in keys:
            cells = []
            for src in r["gate_split"]:
                v = r["gate_split"][src].get(k)
                cells.append("" if v is None else f"{v[0]} ({'ok' if v[2] else 'FAIL'}; {v[1]})")
            parts.append(f"| {k} | " + " | ".join(cells) + " |")
        parts += ["", "Split gate: " + "; ".join(
            f"{src}: {'PASS' if all(x[2] for x in v.values()) else 'FAIL'}" for src, v in r["gate_split"].items()), ""]
    parts += ["Legacy gate (100% valid answers, lock_in >= 95% vs gold, unsafe leaves <= 0.5%): " +
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
    big = work / "students" / "data" / "game-reasoner"
    if (big / "grade_rows.jsonl").exists():          # round 2+: the multi-season build
        rep = {"reasoner": reasoner_big(_read(big / "grade_rows.jsonl"), teacher, student),
               "jump": jumps(pack, teacher, student, _read(big / "jump_rows.jsonl")),
               "watchdog": watchdog(pack, work)}
        mb = work.parent / "moments" / "build_report.json"
        if mb.exists():
            rep["build"] = json.loads(mb.read_text())
    else:
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
