"""Teacher words + hindsight targets -> mlx_lm chat datasets for the game-reasoner adapter.

Starts from the reasoner thread's own chat export (chat_{train,valid}.jsonl, aligned line for line
with rows_*.jsonl) and only swaps the WORDS: storyline / why / next come from the teacher's grounded
answer. lock_in, safe_to_leave, confidence and stay_s stay exactly as the export wrote them (hindsight
+ the decider's rules; that is what gets graded). Key order is kept: lock_in first, because the
server's /score reads it with one prefill.

Test nights are never touched or written here: the reasoner thread's chat_test.jsonl is the eval set.
Why: Builds teacher-and-hindsight training sets for the game-reasoner student (Kaggle Mind pipeline).
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


# ------------------------------------------------------------------------------- jump / stay task
# The game-reasoner adapter also learns the highlight-jump call (same base, same adapter, its own
# system prompt). Targets: decision and reel_s from hindsight (agent/distill/jump.py, Nick's rules);
# the why from the teacher when its blind call agreed with hindsight, else the rule's own why.

JUMP_STUDENT_SYSTEM = (
    "RedZone highlight jump. A dead ball on the game on screen; big plays happened elsewhere. Jump to "
    "the catch-up reel only if the cautious break length covers reel + both switches + margin, never "
    "in crunch time, your team late, a must-see game or an NFL drive that resumes at the restart. "
    'Return at the restart. JSON only, keys in this order: {"decision":"jump|stay","reel_s","why"}'
)
HARD_WEIGHT = 2        # hand-reviewed boundary cases appear twice in train


def jump_prompt(r: dict) -> str:
    return f"{r['brief']}\n\nREEL / SWITCH:\n{r['jump']['text']}"


def jump_target(r: dict, answer: dict | None) -> tuple[str, str]:
    from agent.distill.teacher import clean_jump
    lab = r["jump"]["label"]
    t = clean_jump(extract_json(answer.get("text", ""))) if answer else None
    use = t is not None and t["decision"] == lab["decision"] and len(t["why"]) >= 8
    why = t["why"] if use else lab["why"]
    return json.dumps({"decision": lab["decision"], "reel_s": int(lab["reel_s"]), "why": why}), \
        ("teacher" if use else "rule")


def jump_sets(jrows: list[dict], answers: Iterable[dict]) -> dict[str, list[dict]]:
    """{'train', 'valid', 'test', 'meta'} for the jump task. test = prompts only (id '<row>|jump')."""
    ans = index_answers(answers)
    out: dict[str, list[dict]] = {"train": [], "valid": [], "test": [], "meta": []}
    for r in jrows:
        split = r.get("split", "train")
        msgs = [{"role": "system", "content": JUMP_STUDENT_SYSTEM}, {"role": "user", "content": jump_prompt(r)}]
        if split == "test":
            out["test"].append({"id": f"{r['id']}|jump", "task": "jump_blind", "messages": msgs})
            continue
        tgt, src = jump_target(r, ans.get(f"{r['id']}|jump"))
        row = {"messages": msgs + [{"role": "assistant", "content": tgt}]}
        n = HARD_WEIGHT if (split == "train" and r["jump"].get("hard")) else 1
        out[split if split in ("train", "valid") else "train"].extend([row] * n)
        out["meta"].append({"id": r["id"], "split": split, "src": src, "hard": r["jump"].get("hard", False)})
    return out
