# %% [markdown]
# # RedZone teacher (free Kaggle GPU)
#
# Runs a big open model over `teacher_requests.jsonl` (built in the repo by
# `python scripts/distill.py requests`) and writes `teacher_out.jsonl` to the notebook's output.
#
# **Setup, once (about 5 minutes):**
# 1. kaggle.com -> Create -> New Dataset -> upload `teacher_requests.jsonl` (name it
#    `redzone-teacher-input`). Keep it private.
# 2. Create -> New Notebook -> File -> Import Notebook -> this `.ipynb`.
# 3. Right panel: Add Input -> your `redzone-teacher-input` dataset. Session options:
#    Accelerator **GPU T4 x2**, Internet **On** (needs a phone-verified account).
# 4. Save Version -> **Save & Run All (Commit)**. It runs in the background (up to 12 h, uses
#    part of the free 30 GPU-hours a week) even with the browser closed.
# 5. When it finishes: open the version -> Output -> download `teacher_out.jsonl` and
#    `run_report.json`, and drop them in the project thread (or in the repo's data/distill/ on the Mac).
#
# It is resumable: if a run stops early, attach the previous version's output as an input and
# rerun; answered ids are skipped. Nothing here costs money. No API keys are used.

# %%
import glob
import gzip
import json
import os
import subprocess
import sys
import time

MODELS = [  # tried in order; the first that loads and passes the smoke test is used
    # (backend, model, attention backend override). T4s (sm75) have no FlashAttention 2; vLLM usually
    # picks a working backend itself, the overrides are retries if it doesn't.
    ("vllm", "Qwen/Qwen3-32B-AWQ", None),
    ("vllm", "Qwen/Qwen3-32B-AWQ", "TRITON_ATTN"),
    ("vllm", "Qwen/Qwen3-32B-AWQ", "FLEX_ATTENTION"),
    ("vllm", "Qwen/Qwen3-14B-AWQ", "TRITON_ATTN"),
    ("hf4bit", "Qwen/Qwen3-14B", None),
]
TIME_BUDGET_S = 10.5 * 3600            # stop cleanly before Kaggle's 12 h limit
OUT = "/kaggle/working/teacher_out.jsonl"
REPORT = "/kaggle/working/run_report.json"
BATCH = 256                            # requests per vLLM call (checkpointed after each)
T0 = time.time()


def find(name):
    hits = sorted(glob.glob(f"/kaggle/input/**/{name}", recursive=True))
    return hits[0] if hits else None


REQ_PATH = find("teacher_requests.jsonl") or find("teacher_requests.jsonl.gz")
assert REQ_PATH, "attach the redzone-teacher-input dataset (teacher_requests.jsonl)"
_open = gzip.open if REQ_PATH.endswith(".gz") else open
requests = [json.loads(line) for line in _open(REQ_PATH, "rt") if line.strip()]

done = {}
for prev in [find("teacher_out.jsonl"), OUT]:      # resume from an attached earlier output, then ours
    if prev and os.path.exists(prev):
        for line in open(prev):
            try:
                a = json.loads(line)
                done[a["id"]] = a
            except Exception:
                pass
if find("teacher_out.jsonl") and not os.path.exists(OUT):
    with open(OUT, "w") as f:
        for a in done.values():
            f.write(json.dumps(a) + "\n")
todo = [r for r in requests if r["id"] not in done]
print(f"{len(requests)} requests, {len(done)} already answered, {len(todo)} to go")

# %%
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "vllm", "bitsandbytes", "accelerate"], check=False)
import torch  # noqa: E402

print(torch.__version__, torch.cuda.device_count(), [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())])


# %%
class VLLMBackend:
    def __init__(self, model, attn=None):
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

    def prompt(self, messages):
        return self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                            enable_thinking=False)

    def generate(self, batch):
        prompts = [self.prompt(r["messages"]) for r in batch]
        sps = [self.SP(temperature=0.3, top_p=0.9, max_tokens=int(r.get("max_tokens", 200)), seed=7) for r in batch]
        outs = self.llm.generate(prompts, sps, use_tqdm=False)
        return [(o.outputs[0].text, len(o.prompt_token_ids), len(o.outputs[0].token_ids)) for o in outs]


class HF4bitBackend:
    """Slow but dependable fallback: transformers + bitsandbytes 4-bit, batched, left-padded."""

    def __init__(self, model, attn=None):
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
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
            with torch.no_grad():
                out = self.m.generate(**enc, max_new_tokens=mx, do_sample=True, temperature=0.3, top_p=0.9,
                                      pad_token_id=self.tok.eos_token_id)
            for j, r in enumerate(part):
                gen = out[j, enc["input_ids"].shape[1]:]
                res.append((self.tok.decode(gen, skip_special_tokens=True), int(enc["attention_mask"][j].sum()),
                            int((gen != self.tok.eos_token_id).sum())))
        return res


SMOKE = [{"id": "smoke", "max_tokens": 40, "messages": [
    {"role": "system", "content": "Answer with JSON only."},
    {"role": "user", "content": 'Return {"ok": true, "sum": 2+3 as a number}.'}]}]

backend, model_used, errors = None, None, []
for kind, model, attn in MODELS:
    try:
        b = VLLMBackend(model, attn) if kind == "vllm" else HF4bitBackend(model)
        text = b.generate(SMOKE)[0][0]
        print(kind, model, "smoke:", text[:120])
        if "5" in text:
            backend, model_used = b, f"{kind}:{model}" + (f" ({attn})" if attn else "")
            break
        errors.append(f"{kind}:{model}: bad smoke answer {text[:80]!r}")
    except Exception as e:  # noqa: BLE001 - try the next model
        errors.append(f"{kind}:{model}:{attn}: {type(e).__name__}: {str(e)[:300]}")
        print(errors[-1])
        try:
            del b
        except NameError:
            pass
        import gc
        gc.collect()
        torch.cuda.empty_cache()
assert backend is not None, f"no backend worked: {errors}"


# %%
def lines_of(text, field):
    """The list a follow-up fans out over (e.g. {"lines": [...]} from a paraphrase answer)."""
    s = text[text.find("{"):text.rfind("}") + 1]
    try:
        v = json.loads(s).get(field) or []
        return [str(x) for x in v if isinstance(x, (str, int, float))][:40]
    except Exception:
        return []


stats = {"answered": 0, "prompt_tokens": 0, "gen_tokens": 0, "followups": 0}
gen_s = 0.0
with open(OUT, "a") as f:
    for i in range(0, len(todo), BATCH):
        if time.time() - T0 > TIME_BUDGET_S:
            print("time budget reached; rerun with this output attached to continue")
            break
        batch = todo[i:i + BATCH]
        t = time.time()
        res = backend.generate(batch)
        # follow-ups (e.g. blind kind check of each generated watchdog line), in one more batch
        fan = []
        for r, (text, _, _) in zip(batch, res):
            fu = r.get("followup")
            if fu:
                for item in lines_of(text, fu.get("field", "lines")):
                    msgs = [{"role": m["role"], "content": m["content"].replace("{item}", item)} for m in fu["messages"]]
                    fan.append((r["id"], item, {"id": r["id"], "messages": msgs, "max_tokens": fu.get("max_tokens", 32)}))
        fres = backend.generate([x[2] for x in fan]) if fan else []
        by = {}
        for (rid, item, _), (ftext, _, _) in zip(fan, fres):
            by.setdefault(rid, []).append({"item": item, "text": ftext})
        gen_s += time.time() - t
        for r, (text, pt, gt) in zip(batch, res):
            row = {"id": r["id"], "task": r.get("task"), "model": model_used, "text": text}
            for k in ("kind",):
                if k in r:
                    row[k] = r[k]
            if r["id"] in by:
                row["followups"] = by[r["id"]]
            f.write(json.dumps(row) + "\n")
            stats["answered"] += 1
            stats["prompt_tokens"] += pt
            stats["gen_tokens"] += gt
        stats["followups"] += len(fan)
        f.flush()
        print(f"{min(i + BATCH, len(todo))}/{len(todo)}  {stats['gen_tokens'] / max(gen_s, 1e-6):.0f} gen tok/s")

report = {"model": model_used, "errors_before": errors, "requests": len(requests), "already_done": len(done),
          **stats, "gen_s": round(gen_s), "wall_s": round(time.time() - T0),
          "gen_tok_per_s": round(stats["gen_tokens"] / max(gen_s, 1e-6), 1)}
json.dump(report, open(REPORT, "w"), indent=1)
print(report)
