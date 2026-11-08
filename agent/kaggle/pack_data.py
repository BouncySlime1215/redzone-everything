"""Build the ONE upload-ready folder for the private Kaggle dataset `redzone-distill`. Light, no GPU.

    python agent/kaggle/pack_data.py --reasoner-dir <export of every moment> --archive data/archive \\
        --watchdog-src <checkout with agent/policy.py, agent/train/{make_data,peft_to_mlx}.py> \\
        --out kaggle-inputs

Runs anywhere (built in the cloud from eval-data-v1, so the Mac does nothing). No secrets, no git
token: everything the notebook needs is inside, the code included. Three label tiers:

  (a) outcome labels for EVERY archived moment: the reasoner export's hindsight targets, with
      Nick's rules applied exactly (agent/distill/gold.py) -> data/reasoner/rows_*.jsonl.gz
  (b) the teacher queue, ranked hardest + most important first -> teacher_requests.jsonl.gz; the
      notebook works down it until its GPU budget runs out and resumes there next session
  (c) gold: the ~1,000 hardest lock-in and jump-or-stay moments, labeled by Claude with Nick's rules
      and hand-reviewed -> data/gold/*.jsonl (training up-weight + their own gate lines)
Why: Builds the single upload folder for the private Kaggle dataset redzone-distill (docstring).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.distill import gold, jump, split, teacher  # noqa: E402

GRADE_N = 4000           # held-out moments graded for teacher AND student (stratified, all gold-hard kept)
GOLD_LOCK_N, GOLD_JUMP_N = 500, 500
CODE = ["agent/__init__.py", "agent/distill/__init__.py", "agent/distill/teacher.py", "agent/distill/student.py",
        "agent/distill/evaluate.py", "agent/distill/jump.py", "agent/distill/gold.py", "agent/distill/split.py",
        "agent/kaggle/__init__.py", "agent/kaggle/dequant.py", "agent/kaggle/run_teacher.py",
        "agent/kaggle/run_students.py", "agent/kaggle/student_data.py", "agent/kaggle/grade.py"]
WD_CODE = ["agent/policy.py", "agent/train/__init__.py", "agent/train/peft_to_mlx.py"]


def wgz(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(path, "wt", compresslevel=6) as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += 1
    return n


def wjl(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return teacher.write_jsonl(path, rows)


def grade_sample(test: list[dict], keep: set[str], n: int, seed: int = 7) -> list[str]:
    """Held-out ids graded for teacher and student: every gold-hard test id, then a stratified
    random sample per (league, gold lock_in) up to n."""
    rng = random.Random(seed)
    ids = [r["id"] for r in test if r["id"] in keep]
    by: dict[tuple, list[str]] = {}
    for r in test:
        if r["id"] not in keep:
            by.setdefault((r["league"], r["target"]["lock_in"]), []).append(r["id"])
    rest = max(0, n - len(ids))
    total = sum(len(v) for v in by.values()) or 1
    for k, v in sorted(by.items()):
        rng.shuffle(v)
        ids += v[:max(1, round(rest * len(v) / total))]
    return sorted(set(ids))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-pack", help="only write rz_code.tar.gz here (round 2+) and stop")
    ap.add_argument("--serving-src", default=str(ROOT), help="checkout whose redzone/ + scripts go in the code pack")
    ap.add_argument("--reasoner-dir")
    ap.add_argument("--archive", default=str(ROOT / "data" / "archive"))
    ap.add_argument("--watchdog-src")
    ap.add_argument("--out")
    ap.add_argument("--wd-per-kind", type=int, default=8)
    ap.add_argument("--parts", help="also write the pack as rz_pack.part{aa,ab,..}.bin here (tar.gz split "
                                    "into < PART_MB pieces: the Kaggle web upload caps files at 10 MB)")
    a = ap.parse_args()
    if a.code_pack:
        print(code_pack(Path(a.serving_src), Path(a.code_pack), overlay=ROOT))
        return
    if not (a.reasoner_dir and a.watchdog_src and a.out):
        ap.error("--reasoner-dir, --watchdog-src and --out are required to build the data pack")
    t0 = time.time()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    src = Path(a.reasoner_dir)

    # ---- tier (a): every moment, gold rules applied
    allrows = [r for s in ("train", "valid", "test") for r in teacher.read_jsonl(src / f"rows_{s}.jsonl")]
    system = _first_system(src / "chat_test.jsonl")
    # split BY TIME, whole nights, per league; the locked held-out nights leave the pack entirely
    split_plan = split.assign(allrows)
    n_locked = sum(r["split"] == "locked" for r in allrows)
    allrows = [r for r in allrows if r["split"] != "locked"]
    split.check(allrows)
    rows = {s: [r for r in allrows if r["split"] == s] for s in ("train", "valid", "test")}
    rule_counts = gold.apply(allrows)

    # ---- jump-or-stay moments (dead balls) with hindsight labels
    jrows = jump.build(allrows, Path(a.archive))
    for r in jrows:
        r["hard_score"] = r.get("hard", 0.0) + (3.0 if r["jump"]["hard"] else 0.0)

    # ---- tier (c): gold sets
    lock_gold = gold.pick_hard(allrows, GOLD_LOCK_N)
    jump_gold = gold.pick_hard(jrows, GOLD_JUMP_N, key=lambda r: r["hard_score"],
                               cls=lambda r: r["jump"]["label"]["decision"], share={"jump": 0.5, "stay": 0.5})
    lock_ids = {r["id"] for r in lock_gold}
    jump_ids = {r["id"] for r in jump_gold}
    for r in allrows:
        r["gold"] = r["id"] in lock_ids
    for r in jrows:
        r["gold"] = r["id"] in jump_ids

    d = out / "data"
    (d / "reasoner").mkdir(parents=True)
    (d / "reasoner" / "system.txt").write_text(system)
    counts = {}
    for s in ("train", "valid", "test"):
        counts[f"reasoner_{s}"] = wgz(d / "reasoner" / f"rows_{s}.jsonl.gz", rows[s])
    counts["jump_rows"] = wgz(d / "jump_rows.jsonl.gz", jrows)
    wjl(d / "gold" / "lock_gold.jsonl", ({"id": r["id"], "split": r["split"], "league": r["league"],
                                          "brief": r["brief"], "gold": r["target"], "export": r["export_target"],
                                          "hard": r["hard"]} for r in lock_gold))
    wjl(d / "gold" / "jump_gold.jsonl", ({"id": r["id"], "split": r["split"], "league": r["league"],
                                          "brief": r["brief"], "context": r["jump"]["text"],
                                          "gold": r["jump"]["label"], "window_s": r["jump"]["window_s"]}
                                         for r in jump_gold))
    test = rows["test"]
    grade_ids = grade_sample(test, lock_ids, GRADE_N)
    (d / "grade_ids.json").write_text(json.dumps(grade_ids))

    # ---- watchdog data (its own builder; labels from agent/policy rules)
    wd = Path(a.watchdog_src)
    subprocess.run([sys.executable, str(wd / "agent/train/make_data.py")], check=True, cwd=wd)
    (d / "watchdog").mkdir(parents=True, exist_ok=True)
    for f in ("train.jsonl", "valid.jsonl", "test.jsonl", "test.labels.jsonl", "valid.labels.jsonl"):
        shutil.copy(wd / "agent/train/data" / f, d / "watchdog" / f)
    import ast
    tree = ast.parse((wd / "agent/train/make_data.py").read_text())
    novel = next(ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, (ast.Assign, ast.AnnAssign)) and
                 getattr(n.targets[0] if isinstance(n, ast.Assign) else n.target, "id", "") == "NOVEL")
    (d / "watchdog" / "novel.json").write_text(json.dumps(novel))
    counts["watchdog"] = {f: sum(1 for _ in open(d / "watchdog" / f)) for f in ("train.jsonl", "valid.jsonl", "test.jsonl")}

    # ---- tier (b): the teacher queue, most valuable first
    by_id = {r["id"]: r for r in allrows}
    q = []
    q += teacher.reasoner_requests([by_id[i] for i in grade_ids], system, grounded_splits=(), blind_splits=("test",))
    q += teacher.watchdog_requests(novel, per_kind=a.wd_per_kind)
    jq = sorted(jrows, key=lambda r: (not r["gold"], -r["hard_score"], r["id"]))
    q += teacher.jump_requests(jq)
    # hindsight (grounded) only on train nights; nothing for valid/test beyond the blind grade sample
    tr = sorted((r for r in allrows if r["split"] == "train"), key=lambda r: (not r["gold"], -r["hard"], r["id"]))
    q += teacher.reasoner_requests(tr, system, grounded_splits=("train",), blind_splits=())
    counts["teacher_requests"] = wgz(out / "teacher_requests.jsonl.gz", q)
    by_task: dict[str, int] = {}
    for r in q:
        by_task[r["task"]] = by_task.get(r["task"], 0) + 1

    # ---- code + notebook
    for f in CODE:
        (out / "code" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / f, out / "code" / f)
    for f in WD_CODE:
        (out / "code" / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(wd / f, out / "code" / f)
    shutil.copy(ROOT / "agent/kaggle/teacher_distill.ipynb", out / "teacher_distill.ipynb")

    assert not any(r["task"] == "reasoner_grounded" and by_id[r["id"].split("|")[0]]["split"] != "train"
                   for r in q if r["task"] == "reasoner_grounded"), "hindsight outside train nights"
    info = {"built_s": round(time.time() - t0), "split": split_plan, "locked_rows_left_out": n_locked,
            "counts": counts, "teacher_by_task": by_task,
            "gold_rule_changes": rule_counts,
            "gold": {"lock": len(lock_gold), "jump": len(jump_gold),
                     "lock_by_split": _count(lock_gold, "split"), "jump_by_split": _count(jump_gold, "split")},
            "grade_ids": len(grade_ids),
            "jump_labels": _count(jrows, lambda r: r["jump"]["label"]["rule"])}
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_file():
            files[str(p.relative_to(out))] = {"bytes": p.stat().st_size,
                                              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
    info["files"] = files
    info["total_mb"] = round(sum(v["bytes"] for v in files.values()) / 1e6, 1)
    (out / "manifest.json").write_text(json.dumps(info, indent=1))
    print(json.dumps({k: v for k, v in info.items() if k != "files"}, indent=1))
    if a.parts:
        print("parts:", split_parts(out, Path(a.parts)))


PART_MB = 9.5
CODE_PACK_FILES = ["agent", "redzone", "scripts/export_reasoner_llm.py", "scripts/download_recent.py",
                   "scripts/train_reasoner.py", "scripts/build_reasoner_data.py", "data/eval_config.yaml",
                   "pyproject.toml"]


def code_pack(src: Path, dest: Path, overlay: Path | None = None) -> dict:
    """Round 2+: the small code pack (rz_code.tar.gz, top folder ``code/``) the notebook unpacks
    instead of the pack's code: the serving code (redzone/, the exporter, the downloader) from
    ``src`` (an integration/next checkout) with agent/{distill,kaggle,train} from ``overlay``.
    No data, no secrets: the notebook downloads everything else from ESPN inside Kaggle."""
    import io
    import re
    import tarfile
    skip = re.compile(r"(__pycache__|\.pyc$|/models?/.*\.(gguf|safetensors)$|\.env|secret|token)", re.I)
    buf = io.BytesIO()
    names = []
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as t:
        def add(path: Path, arc: str):
            for p in ([path] if path.is_file() else sorted(path.rglob("*"))):
                rel = arc if path.is_file() else f"{arc}/{p.relative_to(path)}"
                if p.is_file() and not skip.search(rel):
                    t.add(p, arcname=f"code/{rel}")
                    names.append(rel)
        for f in CODE_PACK_FILES:
            top = f.split("/")[0]
            if overlay is not None and top == "agent":
                continue
            if (src / f).exists():
                add(src / f, f)
        if overlay is not None:
            for sub in ("agent",):
                add(overlay / sub, sub)
            for f in CODE_PACK_FILES:
                if f.startswith("data/") and (overlay / f).exists() and not (src / f).exists():
                    add(overlay / f, f)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(buf.getvalue())
    return {"files": len(names), "bytes": dest.stat().st_size}


def split_parts(pack: Path, dest: Path, part_bytes: int = int(PART_MB * 1_000_000)) -> list[str]:
    """tar.gz the pack (top folder ``pack/``) and split it into rz_pack.partaa.bin, partab.bin, ...;
    the notebook's first cell cats them back together in name order."""
    import io
    import string
    import tarfile
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("rz_pack.part*.bin"):
        old.unlink()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as t:
        t.add(pack, arcname="pack")
    data = buf.getvalue()
    names = []
    for k in range(0, len(data), part_bytes):
        i = k // part_bytes
        name = f"rz_pack.part{string.ascii_lowercase[i // 26]}{string.ascii_lowercase[i % 26]}.bin"
        (dest / name).write_bytes(data[k:k + part_bytes])
        names.append(name)
    return names


def _first_system(path: Path) -> str:
    if not path.exists():
        return teacher.STUDENT_SYSTEM
    with open(path) as f:
        return json.loads(f.readline())["messages"][0]["content"]


def _count(rows, key):
    out: dict = {}
    for r in rows:
        k = key(r) if callable(key) else r.get(key)
        out[k] = out.get(k, 0) + 1
    return out


if __name__ == "__main__":
    main()
