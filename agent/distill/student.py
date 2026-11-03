"""Teacher words + hindsight targets -> mlx_lm chat datasets for the game-reasoner adapter.

Starts from the reasoner thread's own chat export (chat_{train,valid}.jsonl, aligned line for line
with rows_*.jsonl) and only swaps the WORDS: storyline / why / next come from the teacher's grounded
answer. lock_in, safe_to_leave, confidence and stay_s stay exactly as the export wrote them (hindsight
+ the decider's rules; that is what gets graded). Key order is kept: lock_in first, because the
server's /score reads it with one prefill.

Test nights are never touched or written here: the reasoner thread's chat_test.jsonl is the eval set.
"""
from __future__ import annotations

import json
from typing import Iterable

from agent.distill.teacher import ORDER, clean_reasoner, extract_json, index_answers

WORDS = ("storyline", "why", "next")


def teacher_words(answer: dict | None) -> dict | None:
    """The grounded answer's words, or None when unusable (empty, too short, leaked hindsight)."""
    d = extract_json(answer.get("text", "")) if answer else None
    if not isinstance(d, dict):
        return None
    w = {k: str(d.get(k) or "").strip() for k in WORDS}
    if len(w["storyline"]) < 8 or len(w["why"]) < 8:
        return None
    blob = " ".join(w.values()).lower()
    # The grounded prompt shows the future; a line that quotes it ("actually", "later scored") is a leak.
    if any(t in blob for t in ("actually happened", "hidden from", "in hindsight", "what happened next")):
        return None
    return w


def merge_row(chat: dict, row: dict, answer: dict | None) -> tuple[dict, str]:
    """(chat row with the teacher's words, 'teacher'|'rule')."""
    msgs = chat["messages"]
    assert msgs[1]["content"] == row["brief"], f"chat/rows misaligned at {row['id']}"
    tgt = json.loads(msgs[2]["content"])
    w = teacher_words(answer)
    if w is None:
        return chat, "rule"
    merged = clean_reasoner({**tgt, **w}, {k: tgt[k] for k in ("lock_in", "safe_to_leave", "stay_s", "confidence")})
    if merged is None:
        return chat, "rule"
    keys = [k for k in tgt if k in merged] + [k for k in ORDER if k not in tgt]
    out = {k: merged[k] for k in keys}
    return {"messages": [msgs[0], msgs[1], {"role": "assistant", "content": json.dumps(out)}]}, "teacher"


def build(rows: dict[str, list[dict]], chats: dict[str, list[dict]], answers: Iterable[dict],
          teacher_only: bool = False) -> dict[str, list[dict]]:
    """{'train', 'valid', 'meta'}: the export's chat rows with teacher words where the teacher gave them."""
    ans = index_answers(answers)
    out: dict[str, list[dict]] = {"train": [], "valid": [], "meta": []}
    for split in ("train", "valid"):
        rs, cs = rows.get(split, []), chats.get(split, [])
        if len(rs) != len(cs):
            raise ValueError(f"{split}: {len(rs)} rows but {len(cs)} chat rows (re-export both together)")
        for r, c in zip(rs, cs):
            if r.get("split", split) == "test":
                raise ValueError(f"test-night row {r['id']} in the {split} file")
            m, src = merge_row(c, r, ans.get(f"{r['id']}|grounded"))
            if teacher_only and src != "teacher":
                continue
            out[split].append(m)
            out["meta"].append({"id": r["id"], "split": split, "src": src, "night": r.get("night")})
    return out
