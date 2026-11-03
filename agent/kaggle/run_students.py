"""Phase 2 on Kaggle: LoRA-train one student adapter per job on the Mac's own base, answer the
held-out prompts, and convert each adapter to mlx_lm format.

    python agent/kaggle/run_students.py --pack <pack dir> --teacher-out <teacher_out.jsonl> --out <dir>

* Base: mlx-community/Qwen3-1.7B-4bit (what the Mac runs), dequantized with agent/kaggle/dequant.py,
  so the adapter is trained on exactly the weights it will run on.
* Adapter: rank 8, alpha 16, the last 8 of 28 layers, q/k/v/o/gate/up/down (the shape
  agent/train/peft_to_mlx.py converts and agent/llm_server.py loads). Loss on the answer only.
* Jobs run in parallel, one per T4 (game-reasoner on GPU 0, watchdog on GPU 1).
* Answers: greedy, like the Mac. The watchdog job also answers with the adapter switched off, the
  "before" column.
Writes <out>/<job>/peft/, <out>/<job>/mlx/ (adapters.safetensors + adapter_config.json),
<out>/<job>/test_out.jsonl, <out>/<job>/train_report.json. A job whose mlx/ exists is skipped (resume).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

BASE_REPO = "mlx-community/Qwen3-1.7B-4bit"
RANK, ALPHA, LAST_LAYERS = 8, 16, 8
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
EPOCHS = {"game-reasoner": 2, "watchdog": 1}
LR, BATCH, MAX_LEN = 1e-4, 8, 768
TIME_PER_JOB_S = 3.0 * 3600
SMOKE = os.environ.get("RZ_SMOKE") == "1"     # CPU smoke test: tiny model, few steps


def base_dir(work: Path) -> Path:
    """Download the Mac's 4-bit base and dequantize it once (float16 HF checkpoint)."""
    if os.environ.get("RZ_BASE_DIR"):                  # CPU smoke tests: a tiny local model
        return Path(os.environ["RZ_BASE_DIR"])
    out = work / "base-deq"
    if (out / "model.safetensors").exists():
        return out
    from huggingface_hub import snapshot_download
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.kaggle.dequant import convert
    src = Path(snapshot_download(BASE_REPO))
    print("dequantize:", convert(src, out), flush=True)
    return out


def train_job(name: str, job_dir: Path, base: Path, out: Path) -> None:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(7)
    torch.manual_seed(7)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    amp = dict(device_type=dev, dtype=torch.float16, enabled=dev == "cuda")
    t0 = time.time()
    tok = AutoTokenizer.from_pretrained(base)
    tok.padding_side = "left"
    rd = lambda f: [json.loads(x) for x in open(job_dir / f)] if (job_dir / f).exists() else []  # noqa: E731

    def render(messages, gen=False):
        return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=gen, enable_thinking=False)

    def encode(row):
        prompt = render(row["messages"][:-1], True)
        full = render(row["messages"])
        if not full.startswith(prompt):
            full = prompt + row["messages"][-1]["content"] + "<|im_end|>"
        p = tok(prompt, add_special_tokens=False)["input_ids"]
        f = tok(full.rstrip("\n"), add_special_tokens=False)["input_ids"][:MAX_LEN]
        return f, [-100] * min(len(p), len(f)) + f[len(p):]

    train = [encode(r) for r in rd("train.jsonl")[:64 if SMOKE else None]]
    valid = [encode(r) for r in rd("valid.jsonl")][:400]
    train = [x for x in train if any(v != -100 for v in x[1])]      # an answer cut off by MAX_LEN teaches nothing

    def batches(rows, shuffle):
        idx = sorted(range(len(rows)), key=lambda i: len(rows[i][0]))      # length buckets: little padding
        groups = [idx[i:i + BATCH] for i in range(0, len(idx), BATCH)]
        if shuffle:
            random.shuffle(groups)
        for g in groups:
            n = max(len(rows[i][0]) for i in g)
            ids = torch.full((len(g), n), tok.pad_token_id)
            lab = torch.full((len(g), n), -100)
            att = torch.zeros((len(g), n), dtype=torch.long)
            for k, i in enumerate(g):
                a, b = rows[i]
                ids[k, :len(a)] = torch.tensor(a)
                lab[k, :len(b)] = torch.tensor(b)
                att[k, :len(a)] = 1
            yield ids.to(dev), lab.to(dev), att.to(dev)

    # fp32 master weights + fp16 autocast: T4s have no bf16, and pure fp16 can overflow in Qwen3
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.float32).to(dev)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    n_layers = model.config.num_hidden_layers
    cfg = LoraConfig(r=RANK, lora_alpha=ALPHA, lora_dropout=0.05, target_modules=TARGETS,
                     layers_to_transform=list(range(n_layers - LAST_LAYERS, n_layers)), task_type="CAUSAL_LM")
    model = get_peft_model(model, cfg)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=LR, weight_decay=0.0)
    scaler = torch.amp.GradScaler(dev, enabled=dev == "cuda")
    epochs = EPOCHS.get(name, 1)
    steps = max(1, epochs * ((len(train) + BATCH - 1) // BATCH))
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 30) * max(0.05, 1 - s / steps))
    log, step = [], 0

    def val_loss():
        model.eval()
        tot, n = 0.0, 0
        with torch.no_grad(), torch.autocast(**amp):
            for ids, lab, att in batches(valid, False):
                tot += model(input_ids=ids, attention_mask=att, labels=lab).loss.item()
                n += 1
        model.train()
        return round(tot / max(1, n), 4)

    log.append({"step": 0, "val_loss": val_loss()})
    model.train()
    stopped = False
    for ep in range(epochs):
        for ids, lab, att in batches(train, True):
            with torch.autocast(**amp):
                loss = model(input_ids=ids, attention_mask=att, labels=lab).loss
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            opt.zero_grad(set_to_none=True)
            sched.step()
            step += 1
            if step % 50 == 0:
                print(f"{name} step {step}/{steps} loss {loss.item():.4f}", flush=True)
            if time.time() - t0 > TIME_PER_JOB_S:
                stopped = True
                break
        log.append({"epoch": ep + 1, "step": step, "val_loss": val_loss()})
        print(name, log[-1], flush=True)
        if stopped:
            break
    peft_dir = out / "peft"
    model.save_pretrained(peft_dir)
    train_s = round(time.time() - t0)

    # held-out answers, greedy like the Mac
    model.eval()
    model.config.use_cache = True
    tests = rd("test.jsonl")[:8 if SMOKE else None]

    def answer(rows, adapter_on=True):
        res = []
        for i in range(0, len(rows), 32):
            part = rows[i:i + 32]
            enc = tok([render(r["messages"][:2], True) for r in part], return_tensors="pt", padding=True,
                      add_special_tokens=False).to(dev)
            with torch.no_grad(), torch.autocast(**amp):
                if adapter_on:
                    gen = model.generate(**enc, max_new_tokens=8 if SMOKE else 160, do_sample=False, pad_token_id=tok.pad_token_id)
                else:
                    with model.disable_adapter():
                        gen = model.generate(**enc, max_new_tokens=8 if SMOKE else 160, do_sample=False,
                                             pad_token_id=tok.pad_token_id)
            for r, g in zip(part, gen):
                res.append({"id": r["id"], "task": r["task"],
                            "text": tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True)})
        return res

    t1 = time.time()
    with open(out / "test_out.jsonl", "w") as f:
        for a in answer(tests):
            f.write(json.dumps(a) + "\n")
    if name == "watchdog":
        with open(out / "test_out_base.jsonl", "w") as f:
            for a in answer(tests, adapter_on=False):
                f.write(json.dumps(a) + "\n")
    answer_s = round(time.time() - t1)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.train.peft_to_mlx import convert
    mlx_cfg = convert(peft_dir, out / "mlx", n_layers)
    json.dump({"job": name, "base": BASE_REPO + " (dequantized)", "rank": RANK, "alpha": ALPHA,
               "last_layers": LAST_LAYERS, "targets": TARGETS, "train_rows": len(train), "steps": step,
               "stopped_early": stopped, "log": log, "train_s": train_s, "answer_s": answer_s, "mlx": mlx_cfg},
              open(out / "train_report.json", "w"), indent=1)
    print(name, "done", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True)
    ap.add_argument("--teacher-out", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--job", help="(internal) train this one job on the visible GPU")
    ap.add_argument("--moments", default=None, help="round 2+: the multi-season build (build_moments_kaggle.py)")
    ap.add_argument("--prepare-only", action="store_true", help="write the training/test data and stop")
    ap.add_argument("--prepared", action="store_true", help="the data is already written: train only")
    a = ap.parse_args()
    out = Path(a.out)
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    if a.job:
        train_job(a.job, out / "data" / a.job, base_dir(out), out / a.job)
        return
    from agent.distill.teacher import read_jsonl
    from agent.kaggle.student_data import jobs, jobs_big, write
    if a.prepared and (out / "student_data.json").exists():
        info = json.load(open(out / "student_data.json"))
    else:
        answers = read_jsonl(a.teacher_out) if a.teacher_out and os.path.exists(a.teacher_out) else []
        moments = Path(a.moments) if a.moments else None
        big = moments is not None and (moments / "DONE").exists()
        info = write(jobs_big(Path(a.pack), moments, answers) if big else jobs(Path(a.pack), answers), out / "data")
        info["_mode"] = "multi-season build" if big else "pack only"
        print("student data:", json.dumps(info), flush=True)
        json.dump(info, open(out / "student_data.json", "w"), indent=1)
    if a.prepare_only:
        return
    info = {k: v for k, v in info.items() if not k.startswith("_")}
    base_dir(out)
    import torch
    n_gpu = max(1, torch.cuda.device_count())
    procs = []
    for k, name in enumerate(sorted(info)):
        if (out / name / "mlx" / "adapters.safetensors").exists():
            print(name, "already trained; skipping", flush=True)
            continue
        (out / name).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(k % n_gpu)}
        cmd = [sys.executable, __file__, "--pack", a.pack, "--out", a.out, "--job", name]
        log = open(out / name / "train.log", "w")
        p = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append((name, p))
        if k % n_gpu == n_gpu - 1:          # more jobs than GPUs: wait for this round
            for _, q in procs:
                q.wait()
    bad = []
    for name, p in procs:
        if p.wait() != 0:
            bad.append(name)
    for name, _ in procs:
        print(f"--- {name} log tail ---\n" + "".join(open(out / name / "train.log").readlines()[-15:]), flush=True)
    if bad:
        raise SystemExit(f"student jobs failed: {bad} (see {out}/<job>/train.log)")


if __name__ == "__main__":
    main()
