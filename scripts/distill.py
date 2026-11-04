"""Big teacher -> small student, end to end. Light: runs anywhere (the teacher itself runs on Kaggle).

    # 0. the reasoner thread's export: rows_{train,valid,test}.jsonl + chat_*.jsonl in one folder
    python scripts/export_reasoner_llm.py --out data/training/reasoner_llm
    # 1. requests for the teacher (+ watchdog wordings)
    python scripts/distill.py requests --rows-dir data/training/reasoner_llm --watchdog
    #    -> data/distill/teacher_requests.jsonl : upload to Kaggle, run agent/distill/kaggle/teacher_kaggle.ipynb
    # 2. teacher answers back -> student chat sets (teacher words) + the watchdog's wordings file
    python scripts/distill.py merge --rows-dir data/training/reasoner_llm --answers data/distill/teacher_out.jsonl
    python scripts/distill.py wordings --answers data/distill/teacher_out.jsonl
    python scripts/distill.py student-bundle --rows-dir data/training/reasoner_llm \
        --answers data/distill/teacher_out.jsonl --watchdog-data agent/train/data
    #    -> data/distill/student-input/ : upload to Kaggle, run agent/distill/kaggle/student_kaggle.ipynb
    # 3. today vs head vs teacher vs student on held-out (test) nights
    python scripts/distill.py eval --rows-dir data/training/reasoner_llm --teacher data/distill/teacher_out.jsonl \
        [--student data/distill/student_out.jsonl] [--md data/distill/report.md]
"""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.distill import evaluate, student, teacher  # noqa: E402

OUT = ROOT / "data" / "distill"


def _keep(rid: str, frac: float) -> bool:
    return (zlib.crc32(rid.encode()) % 10_000) / 10_000 < frac


def load_dir(d: str | Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """(rows, chats) per split from a reasoner export folder; missing files are empty."""
    d = Path(d)
    rows = {s: teacher.read_jsonl(d / f"rows_{s}.jsonl") if (d / f"rows_{s}.jsonl").exists() else []
            for s in ("train", "valid", "test")}
    chats = {s: teacher.read_jsonl(d / f"chat_{s}.jsonl") if (d / f"chat_{s}.jsonl").exists() else []
             for s in ("train", "valid", "test")}
    for s, rs in rows.items():
        bad = [r["id"] for r in rs if r.get("split", s) != s]
        if bad:
            raise SystemExit(f"rows_{s}.jsonl holds rows of another split: {bad[:3]}")
    return rows, chats


def system_of(chats: dict[str, list[dict]]) -> str:
    for rs in chats.values():
        if rs:
            return rs[0]["messages"][0]["content"]
    return teacher.STUDENT_SYSTEM


def cap(rows: list[dict], max_n: int) -> list[dict]:
    """A deterministic sample of at most max_n rows (0 = all), every must_see moment kept first."""
    if not max_n or len(rows) <= max_n:
        return rows
    hot = [r for r in rows if r.get("target", {}).get("lock_in") == "must_see"][:max_n]
    hot_ids = {r["id"] for r in hot}
    rest = [r for r in rows if r["id"] not in hot_ids]
    frac = (max_n - len(hot)) / max(1, len(rest))
    keep = hot_ids | {r["id"] for r in rest if _keep(r["id"], frac)}
    return [r for r in rows if r["id"] in keep]


def cmd_requests(a) -> None:
    reqs = []
    if a.rows_dir:
        rows, chats = load_dir(a.rows_dir)
        sample = cap(rows["train"], a.max_train) + rows["valid"] + cap(rows["test"], a.max_test)
        reqs += teacher.reasoner_requests(sample, system_of(chats), blind_train_every=a.blind_train_every)
        reqs += teacher.jump_requests(sample)
    if a.watchdog:
        reqs += teacher.watchdog_requests(novel(a.make_data), per_kind=a.wd_per_kind)
    n = teacher.write_jsonl(a.out, reqs)
    by: dict[str, int] = {}
    for r in reqs:
        by[r["task"]] = by.get(r["task"], 0) + 1
    chars = sum(len(m["content"]) for r in reqs for m in r["messages"])
    gen = sum(r["max_tokens"] for r in reqs)
    print(json.dumps({"out": str(a.out), "requests": n, "by_task": by, "prompt_tokens_est": chars // 4,
                      "max_gen_tokens": gen,
                      # rough: ~60% of the max answer length, ~300 gen tok/s and ~3000 prompt tok/s for a
                      # 32B AWQ on 2x T4; the notebook's run_report.json has the real numbers
                      "kaggle_hours_est": round(gen * 0.6 / 300 / 3600 + chars / 4 / 3000 / 3600, 2)},
                     indent=1))


def cmd_merge(a) -> None:
    rows, chats = load_dir(a.rows_dir)
    answers = teacher.read_jsonl(a.answers) if a.answers else []
    out = student.build(rows, chats, answers, teacher_only=a.teacher_only)
    d = Path(a.out_dir)
    for k in ("train", "valid"):
        teacher.write_jsonl(d / f"{k}.jsonl", out[k])      # mlx_lm lora reads {train,valid}.jsonl
    teacher.write_jsonl(d / "meta.jsonl", out["meta"])
    src: dict[str, int] = {}
    for m in out["meta"]:
        src[m["src"]] = src.get(m["src"], 0) + 1
    print(json.dumps({"out_dir": str(d), "train": len(out["train"]), "valid": len(out["valid"]),
                      "words_from": src}, indent=1))


def novel(path: str | None) -> dict:
    """The watchdog's NOVEL wordings, from this checkout or another branch's make_data.py.

    Read with ast (not imported), so a make_data.py from another branch works even when its imports
    (agent.policy constants) don't exist in this checkout.
    """
    import ast
    p = Path(path) if path else ROOT / "agent" / "train" / "make_data.py"
    for node in ast.parse(p.read_text()).body:
        tgt = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
        if isinstance(tgt, ast.Name) and tgt.id == "NOVEL" and node.value is not None:
            return ast.literal_eval(node.value)
    raise SystemExit(f"no NOVEL = {{...}} literal in {p}")


def cmd_wordings(a) -> None:
    NOVEL = novel(a.make_data)
    held = [w for v in NOVEL.values() if isinstance(v, dict) for s in ("test", "valid") for w in v.get(s, [])]
    w = teacher.watchdog_wordings(teacher.read_jsonl(a.answers), held)
    Path(a.out).write_text(json.dumps(w, indent=1) + "\n")
    print(json.dumps({"out": str(a.out), **{k: len(v) for k, v in w.items()}}, indent=1))


def cmd_student_bundle(a) -> None:
    """Folder to upload as the Kaggle 'redzone-student-input' dataset (student_kaggle.ipynb)."""
    out = Path(a.out_dir)
    rows, chats = load_dir(a.rows_dir)
    answers = teacher.read_jsonl(a.answers) if a.answers else []
    merged = student.build(rows, chats, answers, teacher_only=a.teacher_only)
    gr = out / "game-reasoner"
    teacher.write_jsonl(gr / "train.jsonl", merged["train"])
    teacher.write_jsonl(gr / "valid.jsonl", merged["valid"])
    system = system_of(chats)
    teacher.write_jsonl(gr / "test_prompts.jsonl", ({"id": r["id"], "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": r["brief"]}]} for r in rows["test"]))
    info = {"game-reasoner": {"train": len(merged["train"]), "valid": len(merged["valid"]), "test": len(rows["test"])}}
    if a.watchdog_data:
        wd, src = out / "watchdog", Path(a.watchdog_data)
        for k in ("train", "valid"):
            teacher.write_jsonl(wd / f"{k}.jsonl", teacher.read_jsonl(src / f"{k}.jsonl"))
        if (src / "test.jsonl").exists():
            teacher.write_jsonl(wd / "test_prompts.jsonl", ({"id": f"wd-test-{i}", "messages": r["messages"][:2]}
                                                            for i, r in enumerate(teacher.read_jsonl(src / "test.jsonl"))))
        info["watchdog"] = {k: sum(1 for _ in open(wd / f)) for k, f in
                            (("train", "train.jsonl"), ("valid", "valid.jsonl"), ("test", "test_prompts.jsonl"))
                            if (wd / f).exists()}
    print(json.dumps({"out_dir": str(out), **info}, indent=1))


def cmd_eval(a) -> None:
    rows, _ = load_dir(a.rows_dir)
    allrows = rows["valid"] + rows["test"]
    sources: dict = {"today": None, "head": None}
    for name in ("teacher", "student"):
        p = getattr(a, name)
        if p:
            sources[name] = teacher.index_answers(x for x in teacher.read_jsonl(p)
                                                  if x.get("task") in (None, "reasoner_blind"))
    res = evaluate.compare(allrows, sources)
    print(json.dumps(res, indent=1))
    if a.md:
        leagues = sorted({r.get("league") for r in rows["test"]})
        parts = [f"Held-out test nights: {', '.join(res['test_nights'])}", ""]
        for lg in ["all", *leagues]:
            parts += [f"### {lg}", "", evaluate.table(res, lg), ""]
        Path(a.md).write_text("\n".join(parts))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("requests")
    r.add_argument("--rows-dir")
    r.add_argument("--watchdog", action="store_true")
    r.add_argument("--wd-per-kind", type=int, default=6)
    r.add_argument("--make-data", help="watchdog make_data.py to read NOVEL from (default: this checkout)")
    r.add_argument("--max-train", type=int, default=0, help="cap train moments (0 = all)")
    r.add_argument("--max-test", type=int, default=0, help="cap held-out moments asked blind (0 = all)")
    r.add_argument("--blind-train-every", type=int, default=10)
    r.add_argument("--out", default=str(OUT / "teacher_requests.jsonl"))
    m = sub.add_parser("merge")
    m.add_argument("--rows-dir", required=True)
    m.add_argument("--answers")
    m.add_argument("--teacher-only", action="store_true")
    m.add_argument("--out-dir", default=str(OUT / "reasoner"))
    w = sub.add_parser("wordings")
    w.add_argument("--answers", required=True)
    w.add_argument("--make-data", help="watchdog make_data.py to read held-out wordings from")
    w.add_argument("--out", default=str(ROOT / "agent" / "train" / "wordings_teacher.json"))
    b = sub.add_parser("student-bundle")
    b.add_argument("--rows-dir", required=True)
    b.add_argument("--answers", help="teacher_out.jsonl (without it the export's rule words are used)")
    b.add_argument("--teacher-only", action="store_true")
    b.add_argument("--watchdog-data", help="the watchdog's agent/train/data folder (train/valid/test.jsonl)")
    b.add_argument("--out-dir", default=str(OUT / "student-input"))
    e = sub.add_parser("eval")
    e.add_argument("--rows-dir", required=True)
    e.add_argument("--teacher")
    e.add_argument("--student")
    e.add_argument("--md")
    a = ap.parse_args()
    {"requests": cmd_requests, "merge": cmd_merge, "wordings": cmd_wordings, "student-bundle": cmd_student_bundle,
     "eval": cmd_eval}[a.cmd](a)


if __name__ == "__main__":
    main()
