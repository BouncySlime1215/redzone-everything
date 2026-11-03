# %% [markdown]
# # RedZone student adapters (free Kaggle GPU)
#
# LoRA-trains the small model the Mac runs (Qwen3-1.7B) on each job folder in the attached
# `redzone-student-input` dataset (built in the repo by `python scripts/distill.py student-bundle`):
#
#     <job>/train.jsonl, <job>/valid.jsonl     mlx_lm chat rows (system, user, assistant)
#     <job>/test_prompts.jsonl                 {"id", "messages": [system, user]}  (held-out, optional)
#
# Jobs today: `game-reasoner` (teacher words + hindsight targets) and `watchdog` (if its folder is there).
# For each job it writes `adapters/<job>/` (PEFT LoRA: rank 8, last 8 layers, q/k/v/o/gate/up/down,
# the shape the Mac's mlx_lm adapters use) and `<job>_test_out.jsonl` (greedy answers to the held-out
# prompts). Convert with agent/train/peft_to_mlx.py, then grade in the repo.
#
# **Setup:** upload the bundle folder as a private Dataset, import this notebook, Add Input -> the
# dataset, Accelerator **GPU T4 x2** (one is used), Internet **On**, then Save & Run All (Commit).
# About 1-2 h for both jobs. Download `adapters.zip` and the `*_test_out.jsonl` files from Output.

# %%
import glob
import json
import os
import random
import shutil
import subprocess
import sys
import time

BASE = "Qwen/Qwen3-1.7B"     # the full-precision original of mlx-community/Qwen3-1.7B-4bit
RANK, ALPHA, LAST_LAYERS = 8, 16, 8
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
EPOCHS, LR, BATCH, MAX_LEN = 2, 1e-4, 8, 640
TIME_PER_JOB_S = 2.5 * 3600
OUT = "/kaggle/working"
random.seed(7)

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "peft", "accelerate"], check=False)
import torch  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

jobs = sorted({os.path.dirname(p) for p in glob.glob("/kaggle/input/**/train.jsonl", recursive=True)})
print("jobs:", jobs)
assert jobs, "attach the redzone-student-input dataset"
tok = AutoTokenizer.from_pretrained(BASE)
tok.padding_side = "left"


def render(messages, add_generation_prompt=False):
    return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=add_generation_prompt,
                                   enable_thinking=False)


def encode(row):
    """Token ids + labels with the loss on the assistant answer only (mask_prompt, like mlx_lm)."""
    prompt = render(row["messages"][:-1], add_generation_prompt=True)
    full = render(row["messages"])
    if not full.startswith(prompt):            # template differences: fall back to prompt + answer
        full = prompt + row["messages"][-1]["content"] + tok.eos_token
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    f_ids = tok(full, add_special_tokens=False)["input_ids"][:MAX_LEN]
    labels = [-100] * min(len(p_ids), len(f_ids)) + f_ids[len(p_ids):]
    return f_ids, labels[:len(f_ids)]


def batches(rows, bs, shuffle):
    idx = list(range(len(rows)))
    if shuffle:
        random.shuffle(idx)
    for i in range(0, len(idx), bs):
        part = [rows[j] for j in idx[i:i + bs]]
        n = max(len(x[0]) for x in part)
        ids = torch.full((len(part), n), tok.pad_token_id)
        lab = torch.full((len(part), n), -100)
        att = torch.zeros((len(part), n), dtype=torch.long)
        for k, (a, b) in enumerate(part):          # right-pad for training
            ids[k, :len(a)] = torch.tensor(a)
            lab[k, :len(b)] = torch.tensor(b)
            att[k, :len(a)] = 1
        yield ids.cuda(), lab.cuda(), att.cuda()


# %%
def train_job(job_dir):
    name = os.path.basename(job_dir)
    t0 = time.time()
    rd = lambda f: [json.loads(x) for x in open(os.path.join(job_dir, f))] if os.path.exists(os.path.join(job_dir, f)) else []  # noqa: E731
    train = [encode(r) for r in rd("train.jsonl")]
    valid = [encode(r) for r in rd("valid.jsonl")][:400]
    print(name, "train", len(train), "valid", len(valid))
    # fp32 master weights + fp16 autocast: T4 has no bf16, and pure fp16 can overflow in Qwen3
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float32).cuda()
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    n_layers = model.config.num_hidden_layers
    cfg = LoraConfig(r=RANK, lora_alpha=ALPHA, lora_dropout=0.05, target_modules=TARGETS,
                     layers_to_transform=list(range(n_layers - LAST_LAYERS, n_layers)), task_type="CAUSAL_LM")
    model = get_peft_model(model, cfg)
    model.print_trainable_parameters()
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=LR, weight_decay=0.0)
    scaler = torch.cuda.amp.GradScaler()
    steps = EPOCHS * ((len(train) + BATCH - 1) // BATCH)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 20) * max(0.05, 1 - s / steps))
    log, step = [], 0

    def val_loss():
        model.eval()
        tot, n = 0.0, 0
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
            for ids, lab, att in batches(valid, BATCH, False):
                tot += model(input_ids=ids, attention_mask=att, labels=lab).loss.item()
                n += 1
        model.train()
        return tot / max(1, n)

    model.train()
    for ep in range(EPOCHS):
        for ids, lab, att in batches(train, BATCH, True):
            with torch.autocast("cuda", dtype=torch.float16):
                loss = model(input_ids=ids, attention_mask=att, labels=lab).loss
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt)
            scaler.update()
            opt.zero_grad(set_to_none=True)
            sched.step()
            step += 1
            if step % 50 == 0:
                log.append({"step": step, "loss": round(loss.item(), 4)})
                print(name, log[-1])
            if time.time() - t0 > TIME_PER_JOB_S:
                break
        log.append({"epoch": ep + 1, "val_loss": round(val_loss(), 4)})
        print(name, log[-1])
    out_dir = os.path.join(OUT, "adapters", name)
    model.save_pretrained(out_dir)
    json.dump({"base": BASE, "rank": RANK, "alpha": ALPHA, "last_layers": LAST_LAYERS, "targets": TARGETS,
               "num_hidden_layers": n_layers, "train": len(train), "log": log, "train_s": round(time.time() - t0)},
              open(os.path.join(out_dir, "train_report.json"), "w"), indent=1)

    # held-out answers (greedy, like the Mac), batched with left padding
    tests = rd("test_prompts.jsonl")
    if tests:
        model.eval()
        with open(os.path.join(OUT, f"{name}_test_out.jsonl"), "w") as f:
            for i in range(0, len(tests), 32):
                part = tests[i:i + 32]
                enc = tok([render(r["messages"][:2], add_generation_prompt=True) for r in part], return_tensors="pt",
                          padding=True, add_special_tokens=False).to("cuda")
                with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16):
                    gen = model.generate(**enc, max_new_tokens=160, do_sample=False, pad_token_id=tok.pad_token_id)
                for r, g in zip(part, gen):
                    text = tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True)
                    f.write(json.dumps({"id": r["id"], "task": "reasoner_blind" if name == "game-reasoner" else name,
                                        "text": text}) + "\n")
    model = opt = None  # free the GPU before the next job
    torch.cuda.empty_cache()


for j in jobs:
    train_job(j)
shutil.make_archive(os.path.join(OUT, "adapters"), "zip", os.path.join(OUT, "adapters"))
print("done:", os.listdir(OUT))
