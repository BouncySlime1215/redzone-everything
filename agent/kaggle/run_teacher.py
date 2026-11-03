"""Phase 1 on Kaggle: the big teacher answers every request in the pack. Resumable.

    python agent/kaggle/run_teacher.py --requests <pack>/data/teacher_requests.jsonl --out <dir>
        [--resume-from <earlier teacher_out.jsonl>] [--budget-h 10.5]

Runs in its own process so the GPU memory vLLM holds is freed for the student phase.
Tries the biggest teacher first and falls back: Qwen3-32B-AWQ (vLLM, both T4s), then 14B-AWQ,
then 14B in 4-bit through transformers (slow, but dependable). A smoke test picks the first that
works. Writes teacher_out.jsonl (appended and flushed after every batch) and teacher_report.json.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import time

MODELS = [  # (backend, model, attention backend override); T4s (sm75) have no FlashAttention 2
    ("vllm", "Qwen/Qwen3-32B-AWQ", None),
    ("vllm", "Qwen/Qwen3-32B-AWQ", "TRITON_ATTN"),
    ("vllm", "Qwen/Qwen3-32B-AWQ", "FLEX_ATTENTION"),
    ("vllm", "Qwen/Qwen3-14B-AWQ", "TRITON_ATTN"),
    ("hf4bit", "Qwen/Qwen3-14B", None),
]
BATCH = 256


class VLLMBackend:
    def __init__(self, model, attn=None):
        import torch
        if attn:
            os.environ["VLLM_ATTENTION_BACKEND"] = attn
        else:
            os.environ.pop("VLLM_ATTENTION_BACKEND", None)
        from vllm import LLM, SamplingParams
        self.SP = SamplingParams
        self.llm = LLM(model=model, tensor_parallel_size=max(1, torch.cuda.device_count()), dtype="half",
                       quantization="awq" if "AWQ" in model else None, max_model_len=4096,
                       gpu_memory_utilization=0.92, enable_prefix_caching=True, seed=7)
        self.tok = self.llm.get_tokenizer()

    def generate(self, batch):
        prompts = [self.tok.apply_chat_template(r["messages"], add_generation_prompt=True, tokenize=False,
                                                enable_thinking=False) for r in batch]
        sps = [self.SP(temperature=0.3, top_p=0.9, max_tokens=int(r.get("max_tokens", 200)), seed=7) for r in batch]
        outs = self.llm.generate(prompts, sps, use_tqdm=False)
        return [(o.outputs[0].text, len(o.prompt_token_ids), len(o.outputs[0].token_ids)) for o in outs]


class HF4bitBackend:
    def __init__(self, model, attn=None):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model, padding_side="left")
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type="nf4")
        self.m = AutoModelForCausalLM.from_pretrained(model, quantization_config=q, device_map="auto",
                                                      torch_dtype=torch.float16)

    def generate(self, batch, sub=16):
        res = []
        for i in range(0, len(batch), sub):
            part = batch[i:i + sub]
            prompts = [self.tok.apply_chat_template(r["messages"], add_generation_prompt=True, tokenize=False,
                                                    enable_thinking=False) for r in part]
            enc = self.tok(prompts, return_tensors="pt", padding=True).to(self.m.device)
            mx = max(int(r.get("max_tokens", 200)) for r in part)
            with self.torch.no_grad():
                out = self.m.generate(**enc, max_new_tokens=mx, do_sample=True, temperature=0.3, top_p=0.9,
                                      pad_token_id=self.tok.eos_token_id)
            for j in range(len(part)):
                gen = out[j, enc["input_ids"].shape[1]:]
                res.append((self.tok.decode(gen, skip_special_tokens=True), int(enc["attention_mask"][j].sum()),
                            int((gen != self.tok.eos_token_id).sum())))
        return res


SMOKE = [{"id": "smoke", "max_tokens": 40, "messages": [
    {"role": "system", "content": "Answer with JSON only."},
    {"role": "user", "content": 'Return {"ok": true, "sum": 2+3 as a number}.'}]}]


def pick_backend(models=MODELS):
    errors = []
    for kind, model, attn in models:
        b = None
        try:
            b = VLLMBackend(model, attn) if kind == "vllm" else HF4bitBackend(model)
            text = b.generate(SMOKE)[0][0]
            print(kind, model, attn, "smoke:", text[:120], flush=True)
            if "5" in text:
                return b, f"{kind}:{model}" + (f" ({attn})" if attn else ""), errors
            errors.append(f"{kind}:{model}:{attn}: bad smoke answer {text[:80]!r}")
        except Exception as e:  # noqa: BLE001 - try the next one
            errors.append(f"{kind}:{model}:{attn}: {type(e).__name__}: {str(e)[:300]}")
            print(errors[-1], flush=True)
        del b
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
    raise SystemExit(f"no teacher backend worked: {errors}")


def lines_of(text, field):
    """The list a follow-up fans out over (e.g. {"lines": [...]} from a paraphrase answer)."""
    s = text[text.find("{"):text.rfind("}") + 1]
    try:
        v = json.loads(s).get(field) or []
        return [str(x) for x in v if isinstance(x, (str, int, float))][:40]
    except Exception:  # noqa: BLE001
        return []


def load_done(paths):
    done = {}
    for p in paths:
        if p and os.path.exists(p):
            for line in open(p):
                try:
                    a = json.loads(line)
                    done[a["id"]] = a
                except Exception:  # noqa: BLE001 - a line cut off by a stopped session
                    pass
    return done


def run(requests, out_path, backend, model_used, budget_s, t0, batch=BATCH, done=None):
    done = done or {}
    todo = [r for r in requests if r["id"] not in done]
    stats = {"answered": 0, "prompt_tokens": 0, "gen_tokens": 0, "followups": 0}
    gen_s = 0.0
    with open(out_path, "a") as f:
        for i in range(0, len(todo), batch):
            if time.time() - t0 > budget_s:
                print("time budget reached; run again with this output attached to continue", flush=True)
                break
            part = todo[i:i + batch]
            t = time.time()
            res = backend.generate(part)
            fan = []
            for r, (text, _, _) in zip(part, res):
                fu = r.get("followup")
                if fu:
                    for item in lines_of(text, fu.get("field", "lines")):
                        msgs = [{"role": m["role"], "content": m["content"].replace("{item}", item)}
                                for m in fu["messages"]]
                        fan.append((r["id"], item, {"id": r["id"], "messages": msgs,
                                                    "max_tokens": fu.get("max_tokens", 32)}))
            fres = backend.generate([x[2] for x in fan]) if fan else []
            by = {}
            for (rid, item, _), (ftext, _, _) in zip(fan, fres):
                by.setdefault(rid, []).append({"item": item, "text": ftext})
            gen_s += time.time() - t
            for r, (text, pt, gt) in zip(part, res):
                row = {"id": r["id"], "task": r.get("task"), "model": model_used, "text": text}
                if "kind" in r:
                    row["kind"] = r["kind"]
                if r["id"] in by:
                    row["followups"] = by[r["id"]]
                f.write(json.dumps(row) + "\n")
                stats["answered"] += 1
                stats["prompt_tokens"] += pt
                stats["gen_tokens"] += gt
            stats["followups"] += len(fan)
            f.flush()
            print(f"teacher {min(i + batch, len(todo))}/{len(todo)}  "
                  f"{stats['gen_tokens'] / max(gen_s, 1e-6):.0f} gen tok/s", flush=True)
    stats["gen_s"] = round(gen_s)
    stats["remaining"] = len(todo) - stats["answered"]
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume-from", nargs="*", default=[])
    ap.add_argument("--budget-h", type=float, default=10.5)
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(a.out, exist_ok=True)
    out_path = os.path.join(a.out, "teacher_out.jsonl")
    requests = [json.loads(line) for line in open(a.requests) if line.strip()]
    done = load_done([*a.resume_from, out_path])
    if not os.path.exists(out_path) and done:           # carry an earlier session's answers forward
        with open(out_path, "w") as f:
            for v in done.values():
                f.write(json.dumps(v) + "\n")
    todo = sum(1 for r in requests if r["id"] not in done)
    print(f"teacher: {len(requests)} requests, {len(done)} already answered, {todo} to go", flush=True)
    report = {"requests": len(requests), "already_done": len(done)}
    if todo:
        backend, model_used, errors = pick_backend()
        report.update(model=model_used, errors_before=errors)
        report.update(run(requests, out_path, backend, model_used, a.budget_h * 3600, t0, done=done))
    report["wall_s"] = round(time.time() - t0)
    report["complete"] = report.get("remaining", 0) == 0
    json.dump(report, open(os.path.join(a.out, "teacher_report.json"), "w"), indent=1)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
