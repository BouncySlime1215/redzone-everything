"""Score the watchdog model on the held-out test set: exact action accuracy, overall and per kind.

    .venv-agent/bin/python agent/train/evaluate.py [--adapter agent/model/watchdog] [--limit N]

Also reports how often the answer wasn't usable JSON (the runtime falls back to "notify" then),
and, for actions aimed at something (a stick, a part of the app), how often the target was right.

--backend torch grades the same prompts with PyTorch instead of MLX (for Linux training boxes,
where MLX's CPU backend is far too slow): --model is then the dequantized base and --adapter a
PEFT adapter directory. Greedy decoding either way, the runtime's chat template and max_tokens.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.policy import parse_answer  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
TARGETED = ("reconnect_stick", "resume_player", "clear_prompt", "retune_stick", "restart_component")


class TorchModel:
    def __init__(self, base: str, adapter: str | None, max_tokens: int = 96, threads: int = 0):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if threads:
            torch.set_num_threads(threads)
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(base)
        self.tok.padding_side = "left"
        m = AutoModelForCausalLM.from_pretrained(base, dtype=torch.bfloat16)
        if adapter:
            from peft import PeftModel
            m = PeftModel.from_pretrained(m, adapter).merge_and_unload()
        self.m = m.eval()
        self.max_tokens = max_tokens

    def chat_batch(self, batch: list[list[dict]]) -> list[str]:
        prompts = [self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                                enable_thinking=False) for msgs in batch]
        enc = self.tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False)
        with self.torch.no_grad():
            out = self.m.generate(**enc, max_new_tokens=self.max_tokens, do_sample=False,
                                  pad_token_id=self.tok.pad_token_id)
        return [self.tok.decode(o[enc.input_ids.shape[1]:], skip_special_tokens=True) for o in out]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--split", default="test")
    ap.add_argument("--backend", choices=("mlx", "torch"), default="mlx")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--save", default=None, help="write every answer here (jsonl)")
    a = ap.parse_args()
    rows = [json.loads(line) for line in (DATA / f"{a.split}.jsonl").open()]
    labels = [json.loads(line) for line in (DATA / f"{a.split}.labels.jsonl").open()]
    if a.limit:
        rows, labels = rows[: a.limit], labels[: a.limit]
    if a.backend == "torch":
        m = TorchModel(a.model, a.adapter, threads=a.threads)
        chat_batch = m.chat_batch
    else:
        from agent.llm import DEFAULT_MODEL, LocalModel
        m = LocalModel(a.model or DEFAULT_MODEL, a.adapter)
        chat_batch = lambda batch: [m.chat(msgs) for msgs in batch]  # noqa: E731
    by = defaultdict(lambda: [0, 0])
    tgt = [0, 0]
    bad = 0
    confusion = defaultdict(int)
    saved = []
    t0 = time.time()
    for b in range(0, len(rows), a.batch):
        outs = chat_batch([r["messages"][:2] for r in rows[b:b + a.batch]])
        for out, lab in zip(outs, labels[b:b + a.batch]):
            v = parse_answer(out)
            bad += v is None
            ok = v is not None and v.action == lab["label"]
            by[lab["kind"]][0] += ok
            by[lab["kind"]][1] += 1
            if not ok:
                confusion[(lab["label"], v.action if v else "UNUSABLE")] += 1
            if ok and lab["label"] in TARGETED and "target" in lab:
                tgt[0] += v.target == lab["target"]
                tgt[1] += 1
            saved.append({"label": lab, "answer": out})
        print(f"{min(len(rows), b + a.batch)}/{len(rows)}", file=sys.stderr, flush=True)
    n = sum(t for _, t in by.values())
    right = sum(c for c, _ in by.values())
    res = {"model": a.model, "adapter": a.adapter, "backend": a.backend, "split": a.split, "n": n,
           "accuracy": round(100 * right / max(1, n), 1), "unusable_json": bad,
           "target_right": f"{tgt[0]}/{tgt[1]}",
           "s_per_answer": round((time.time() - t0) / max(1, n), 2),
           "by_kind": {k: f"{c}/{t}" for k, (c, t) in sorted(by.items())},
           "mistakes": {f"{want}->{got}": c for (want, got), c in sorted(confusion.items(), key=lambda x: -x[1])}}
    if a.save:
        with open(a.save, "w") as f:
            for s in saved:
                f.write(json.dumps(s) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
