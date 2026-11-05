"""Score the watchdog model on the held-out test set: exact action accuracy, overall and per kind.

    .venv-agent/bin/python agent/train/evaluate.py [--adapter agent/model/adapter] [--limit N]

Also reports how often the answer wasn't usable JSON (the runtime falls back to "notify" then).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.llm import DEFAULT_MODEL, LocalModel  # noqa: E402
from agent.policy import parse_answer  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    rows = [json.loads(line) for line in (DATA / f"{a.split}.jsonl").open()]
    labels = [json.loads(line) for line in (DATA / f"{a.split}.labels.jsonl").open()]
    if a.limit:
        rows, labels = rows[: a.limit], labels[: a.limit]
    m = LocalModel(a.model, a.adapter)
    by = defaultdict(lambda: [0, 0])
    bad = 0
    t0 = time.time()
    for r, lab in zip(rows, labels):
        out = m.chat(r["messages"][:2])
        v = parse_answer(out)
        bad += v is None
        ok = v is not None and v.action == lab["label"]
        by[lab["kind"]][0] += ok
        by[lab["kind"]][1] += 1
    n = sum(t for _, t in by.values())
    right = sum(c for c, _ in by.values())
    res = {"model": a.model, "adapter": a.adapter, "n": n, "accuracy": round(100 * right / max(1, n), 1),
           "unusable_json": bad, "s_per_answer": round((time.time() - t0) / max(1, n), 2),
           "by_kind": {k: f"{c}/{t}" for k, (c, t) in sorted(by.items())}}
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
