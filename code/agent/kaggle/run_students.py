"""Phase 2 on Kaggle: LoRA-train one student adapter per job on the Mac's own base, answer the
held-out prompts, and convert each adapter to mlx_lm format.

    python agent/kaggle/run_students.py --pack <pack dir> --teacher-out <teacher_out.jsonl> --out <dir>

* Base: mlx-community/Qwen3-1.7B-4bit (what the Mac runs), dequantized with agent/kaggle/dequant.py,
  so the adapter is trained on exactly the weights it will run on.
* Adapter: rank 16, alpha 32, all 28 layers, q/k/v/o/gate/up/down (recipe D19 below; the shape
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
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_REPO = "mlx-community/Qwen3-1.7B-4bit"
# Student recipe D19 (docs/research/2026-11-03/04-architecture-distillation.md, "LoRA Without Regret"):
# LoRA on every layer incl. the MLP, rank 16, alpha 32, LR 2e-4. Round 1 was rank 8 / alpha 16 / last 8
# layers / LR 1e-4; RZ_LORA_RANK, RZ_LORA_LAYERS (0 = all), RZ_LR override it for ablations.
# [V7, Nick's D19 ask "r16-32"] rank per job: the game-reasoner (storylines, lock-in, jump) gets 32, the
# watchdog (11 actions on a status JSON) keeps 16. RZ_LORA_RANK overrides both.
RANKS = {"game-reasoner": 32, "watchdog": 16}
RANK = int(os.environ.get("RZ_LORA_RANK", "0")) or None


def rank_for(job: str) -> int:
    return RANK or RANKS.get(job, 16)

LAST_LAYERS = int(os.environ.get("RZ_LORA_LAYERS", "0")) or None       # None: every layer
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
EPOCHS = {"game-reasoner": 2, "watchdog": 1}
if os.environ.get("RZ_EPOCHS"):                 # [V8-quick] one epoch for every job
    EPOCHS = {k: int(os.environ["RZ_EPOCHS"]) for k in EPOCHS}
REUSE_BASE = os.environ.get("RZ_REUSE_BASE")    # [V8-quick] the previous round's base-model watchdog answers
# BATCH 2 x ACCUM 4 = an effective batch of 8 (V4 ran out of memory on a T4 at BATCH 8)
LR = float(os.environ.get("RZ_LR", "2e-4"))
BATCH, ACCUM, MAX_LEN = 2, 4, 768
# float16 frozen base (~3.4 GB less than float32) + float32 LoRA weights + GradScaler: the D19 recipe.
# RZ_BASE_DTYPE=float32 goes back to round 1's float32 base if fp16 ever overflows (loss nan/inf).
BASE_DTYPE = os.environ.get("RZ_BASE_DTYPE", "float16")
TIME_PER_JOB_S = 3.0 * 3600
# [V6] The whole student phase has a wall-clock budget (RZ_STUDENT_BUDGET_S, default 4.5 h from its start):
# training stops early enough to leave ANSWER_RESERVE_S for answers, answers stop at the deadline and keep
# what they have, and the parent stops a job that runs past it, so the run always ends with partial results.
BUDGET_S = float(os.environ.get("RZ_STUDENT_BUDGET_S", str(4.5 * 3600)))
ANSWER_RESERVE_S = float(os.environ.get("RZ_ANSWER_RESERVE_S", str(45 * 60)))
KILL_GRACE_S = 600.0
# [V6] Every round answers the FULL test set (Nick, Nov 4 3:00 AM: no samples). The speed comes from the
# fp16 base, batching and the progress tee; 5,542 rows x 160 tokens took 1.5-3 h on a T4 in V5.
# [V6, Nick's V5 fix] 32 answers at a time on fp32 weights OOM'd in lm_head (V5 game-reasoner, 5,542 rows):
# fp16 base + 8 at a time, a progress line every ANSWER_LOG_ROWS rows.
ANSWER_BATCH = int(os.environ.get("RZ_ANSWER_BATCH", "8"))
ANSWER_LOG_ROWS = 400
# [V7] JSON-constrained greedy decoding (V6 watchdog: 37% unusable JSON); RZ_CONSTRAINED=0 turns it off.
# 192 new tokens: the constrained answer always closes in time (the reasoner's shortest JSON is 112 chars).
CONSTRAINED = os.environ.get("RZ_CONSTRAINED", "1") == "1"
MAX_NEW = 192
SMOKE = os.environ.get("RZ_SMOKE") == "1"
# [Colab V8] resumable runs: a free Colab session can drop at any time, so the work dir lives on Drive and
# RZ_CKPT_S > 0 saves a training checkpoint (LoRA + optimizer + data position) that often; answers append and
# a rerun skips the rows already answered; a job is done only when its train_report.json exists.
CKPT_S = float(os.environ.get("RZ_CKPT_S", "0"))
# [Colab V8] one GPU runs the jobs one after another: each job gets its share of what is left of the budget
# (RZ_JOB_WEIGHTS "watchdog:1,game-reasoner:5", default from its rows), in RZ_JOB_ORDER (default: by name),
# and its answer reserve is its own test rows x RZ_ANSWER_S_PER_ROW (when set; else RZ_ANSWER_RESERVE_S).
ANSWER_S_PER_ROW = float(os.environ.get("RZ_ANSWER_S_PER_ROW", "0"))
# RZ_FINISH_TRAINING=1: when the budget ends training, checkpoint and stop (exit PAUSED) instead of keeping the
# partly trained adapter, so the next session trains to the end (more sessions, the full recipe).
FINISH_TRAINING = os.environ.get("RZ_FINISH_TRAINING") == "1"
PAUSED = 4                                    # job exit code: checkpointed, rerun to continue
FP16_OVERFLOW = 3                             # job exit code: rerun it once on a float32 base     # CPU smoke test: tiny model, few steps


def deadline() -> float:
    """Epoch seconds by which this job must be done (set by the parent; no budget when run alone)."""
    return float(os.environ.get("RZ_DEADLINE", "inf"))


def heartbeat(out: Path, **state) -> None:
    """<job>/heartbeat.json: the job's phase and progress, rewritten at every progress line, so a stuck
    job is visible (and the parent can tell alive from hung) before the run ends."""
    try:
        tmp = out / "heartbeat.json.tmp"
        tmp.write_text(json.dumps({"ts": round(time.time()), **state}))
        tmp.replace(out / "heartbeat.json")
    except OSError:
        pass


def _eta(done: int, total: int, t0: float) -> str:
    if done <= 0:
        return "?"
    left = (time.time() - t0) / done * max(0, total - done)
    return f"{left / 60:.0f} min"


def base_dir(work: Path) -> Path:
    """Download the Mac's 4-bit base and dequantize it once (float16 HF checkpoint)."""
    if os.environ.get("RZ_BASE_DIR"):                  # CPU smoke tests: a tiny local model
        return Path(os.environ["RZ_BASE_DIR"])
    out = Path(os.environ["RZ_BASE_CACHE"]) if os.environ.get("RZ_BASE_CACHE") else work / "base-deq"  # [Colab] local disk
    if (out / "model.safetensors").exists():
        return out
    from huggingface_hub import snapshot_download
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.kaggle.dequant import convert
    src = Path(snapshot_download(BASE_REPO))
    print("dequantize:", convert(src, out), flush=True)
    return out


def answer_loss(model, ids, lab, att):
    """The causal-LM loss over the answer tokens only, the same value as model(..., labels=lab).loss,
    but the 152k-vocab logits are computed only where a label is (a few dozen answer tokens), not
    for every prompt position: [B, 768, 152k] float32 logits were most of V4's out-of-memory."""
    import torch
    lm = model.get_base_model() if hasattr(model, "get_base_model") else model
    h = lm.model(input_ids=ids, attention_mask=att).last_hidden_state
    tgt = lab[:, 1:]
    keep = tgt != -100
    logits = lm.lm_head(h[:, :-1][keep]).float()
    return torch.nn.functional.cross_entropy(logits, tgt[keep])


RECIPE = "v7"       # [V7] bump when the recipe or the data change: an older adapter is never reused


def recipe_of(name: str) -> str:
    return f"{RECIPE} r{rank_for(name)} lr{LR} layers{LAST_LAYERS or 'all'}"


def train_sha(job_dir: Path) -> str | None:
    """sha256 (16 hex) of the job's training rows: an adapter is reused only for the very same data."""
    import hashlib
    f = job_dir / "train.jsonl"
    if not f.exists():
        return None
    d = hashlib.sha256()
    with open(f, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            d.update(b)
    return d.hexdigest()[:16]


def _reusable(d: Path, name: str, sha: str | None) -> bool:
    """A finished adapter: weights, this recipe, and (when the data hash is known) this exact training data.
    [V8] V7's output is attached for its teacher answers; same recipe stamp, different data: not reused."""
    if not ((d / "adapter_config.json").exists() and
            ((d / "adapter_model.safetensors").exists() or (d / "adapter_model.bin").exists())):
        return False
    if _stamp(d) != recipe_of(name):
        return False
    return sha is None or _read(d / "train_sha.txt") == sha


def _read(f: Path) -> str | None:
    try:
        return f.read_text().strip()
    except OSError:
        return None


def previous_adapter(name: str, roots: list[Path] | None = None, sha: str | None = None) -> Path | None:
    """[V6, Nick's V5 fix] A trained adapter for this job in an attached notebook output
    (/kaggle/input/**/students/<job>/peft), so a crash after training never costs the training again.
    [V8] With ``sha`` (train_job always passes it) only an adapter trained on the same rows counts.
    RZ_RETRAIN=1 always retrains."""
    if os.environ.get("RZ_RETRAIN") == "1":
        return None
    if roots is None:
        roots = [Path(p) for p in os.environ.get("RZ_PREV_ROOTS", "/kaggle/input").split(os.pathsep) if p]
    found = []
    for root in roots:
        if root.is_dir():
            found += [c.parent for c in root.glob(f"**/students/{name}/peft/adapter_config.json")
                      if _reusable(c.parent, name, sha)]      # [V7] V6's adapters (r16, old data) don't count
    return max(found, key=lambda d: (d / "adapter_config.json").stat().st_mtime) if found else None


def _stamp(d: Path) -> str | None:
    return _read(d / "recipe.txt")


def answered_ids(path: Path) -> set[str]:
    """[Colab V8] ids already answered in a test_out file; a torn last line (a disconnect mid-write) is dropped
    and the file rewritten without it, so appending stays valid JSON lines."""
    if not path.exists():
        return set()
    good, ids = [], set()
    for line in open(path):
        try:
            ids.add(json.loads(line)["id"])
            good.append(line if line.endswith("\n") else line + "\n")
        except (ValueError, KeyError, TypeError):
            continue
    with open(path, "w") as f:
        f.writelines(good)
    return ids


def train_job(name: str, job_dir: Path, base: Path, out: Path) -> None:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, LogitsProcessorList

    from agent.kaggle.constrained import JsonSchemaProcessor, schema_for

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

    def order(rows, shuffle):
        idx = sorted(range(len(rows)), key=lambda i: len(rows[i][0]))      # length buckets: little padding
        groups = [idx[i:i + BATCH] for i in range(0, len(idx), BATCH)]
        if shuffle:
            random.shuffle(groups)          # seeded: a resumed run replays the same order
        return groups

    def batches(rows, shuffle, groups=None):
        for g in groups if groups is not None else order(rows, shuffle):
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
    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=getattr(torch, BASE_DTYPE),
                                                 attn_implementation="sdpa").to(dev)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    n_layers = model.config.num_hidden_layers
    sha = train_sha(job_dir)
    prev = (out / "peft") if _reusable(out / "peft", name, sha) else previous_adapter(name, sha=sha)
    log, step, stopped, steps, resumes = [], 0, False, 0, 0
    n_test = sum(1 for _ in open(job_dir / "test.jsonl")) if (job_dir / "test.jsonl").exists() else 0
    reserve = ANSWER_S_PER_ROW * n_test * (2 if name == "watchdog" and not REUSE_BASE else 1) if ANSWER_S_PER_ROW \
        else ANSWER_RESERVE_S
    ck = out / "ckpt" / "state.pt"
    if prev is not None:
        from peft import PeftModel
        print(f"{name}: reusing the trained adapter {prev} (RZ_RETRAIN=1 retrains)", flush=True)
        model = PeftModel.from_pretrained(model, prev)
        log.append({"reused_adapter": str(prev)})
        heartbeat(out, phase="reused", adapter=str(prev))
    else:
        cfg = LoraConfig(r=rank_for(name), lora_alpha=2 * rank_for(name), lora_dropout=0.05, target_modules=TARGETS,
                         layers_to_transform=list(range(n_layers - min(LAST_LAYERS or n_layers, n_layers), n_layers)),
                         task_type="CAUSAL_LM")
        model = get_peft_model(model, cfg)
        params = [p for p in model.parameters() if p.requires_grad]
        for p in params:
            p.data = p.data.float()           # LoRA weights in float32 even on a float16 base
        opt = torch.optim.AdamW(params, lr=LR, weight_decay=0.0)
        scaler = torch.amp.GradScaler(dev, enabled=dev == "cuda")
        epochs = EPOCHS.get(name, 1)
        steps = max(1, epochs * ((len(train) + BATCH * ACCUM - 1) // (BATCH * ACCUM)))
        sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / 30) * max(0.05, 1 - s / steps))
        log, step = [], 0

        def val_loss():
            model.eval()
            tot, n = 0.0, 0
            with torch.no_grad(), torch.autocast(**amp):
                for ids, lab, att in batches(valid, False):
                    tot += answer_loss(model, ids, lab, att).item()
                    n += 1
            model.train()
            return round(tot / max(1, n), 4)

        start_ep, skip, bad_loss = 0, 0, 0
        st = torch.load(ck, map_location=dev, weights_only=False) if ck.exists() else None
        if st is not None and st.get("recipe") == recipe_of(name) and st.get("sha") == sha:
            from peft import set_peft_model_state_dict
            set_peft_model_state_dict(model, st["lora"])
            opt.load_state_dict(st["opt"])
            sched.load_state_dict(st["sched"])
            scaler.load_state_dict(st["scaler"])
            log, step, start_ep, skip, bad_loss = st["log"], st["step"], st["ep"], st["micro"], st["bad_loss"]
            torch.set_rng_state(st["rng"].cpu())
            resumes = st.get("resumes", 0) + 1
            log.append({"resumed_at_step": step, "epoch": start_ep + 1})
            print(f"{name}: resumed from the checkpoint at step {step}/{steps} (epoch {start_ep + 1})", flush=True)
            heartbeat(out, phase="resumed", step=step, steps=steps)
        else:
            heartbeat(out, phase="val0")
            log.append({"step": 0, "val_loss": val_loss()})
        model.train()
        stopped = False
        last_ck = time.time()

        def save_ckpt(ep, micro):
            """LoRA + optimizer + scheduler + scaler + the data position, atomically (tmp, then rename)."""
            from peft import get_peft_model_state_dict
            ck.parent.mkdir(parents=True, exist_ok=True)
            tmp = ck.with_suffix(".tmp")
            torch.save({"lora": get_peft_model_state_dict(model), "opt": opt.state_dict(), "sched": sched.state_dict(),
                        "scaler": scaler.state_dict(), "log": log, "step": step, "ep": ep, "micro": micro,
                        "bad_loss": bad_loss, "rng": torch.get_rng_state(), "recipe": recipe_of(name), "sha": sha,
                        "resumes": resumes, "ts": round(time.time())}, tmp)
            tmp.replace(ck)
            print(f"{name}: checkpoint at step {step}/{steps}", flush=True)

        def opt_step():
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            opt.zero_grad(set_to_none=True)
            sched.step()

        for ep in range(epochs):
            groups = order(train, True)              # every epoch draws its order, even a skipped one
            if ep < start_ep:
                continue
            micro = skip if ep == start_ep else 0
            for ids, lab, att in batches(train, True, groups[micro:]):
                with torch.autocast(**amp):
                    loss = answer_loss(model, ids, lab, att)
                if not torch.isfinite(loss):
                    bad_loss += 1
                    if BASE_DTYPE == "float16" and bad_loss > 20:
                        print(f"{name}: {bad_loss} non-finite losses on the float16 base", flush=True)
                        raise SystemExit(FP16_OVERFLOW)
                scaler.scale(loss / ACCUM).backward()
                micro += 1
                if micro % ACCUM:
                    continue
                opt_step()
                step += 1
                if step % 50 == 0:
                    print(f"{name} step {step}/{steps} loss {loss.item():.4f} eta {_eta(step, steps, t0)}", flush=True)
                    heartbeat(out, phase="train", step=step, steps=steps, loss=round(loss.item(), 4))
                if CKPT_S and time.time() - last_ck > CKPT_S:
                    save_ckpt(ep, micro)
                    last_ck = time.time()
                if time.time() - t0 > TIME_PER_JOB_S or time.time() > deadline() - reserve:
                    if FINISH_TRAINING and CKPT_S:
                        save_ckpt(ep, micro)
                        print(f"{name}: budget reached at step {step}/{steps}; checkpointed, rerun to continue",
                              flush=True)
                        heartbeat(out, phase="paused", step=step, steps=steps)
                        raise SystemExit(PAUSED)
                    print(f"{name}: training time cap at step {step}/{steps}", flush=True)
                    stopped = True
                    break
            if micro % ACCUM:                    # the epoch's last, partial accumulation
                opt_step()
                step += 1
            log.append({"epoch": ep + 1, "step": step, "val_loss": val_loss()})
            print(name, log[-1], flush=True)
            if stopped:
                break
    peft_dir = out / "peft"
    train_s = round(time.time() - t0)
    if prev == peft_dir:            # [Colab V8] this job's own adapter from an earlier session: keep its training log
        try:
            tl = json.load(open(peft_dir / "train_log.json"))
            step, stopped, log, resumes = tl["steps"], tl["stopped_early"], tl["log"] + log, tl["ckpt_resumes"]
            train_s = tl["train_s"]
        except (OSError, ValueError, KeyError):
            pass
    else:
        model.save_pretrained(peft_dir)
        (peft_dir / "recipe.txt").write_text(recipe_of(name))
        json.dump({"steps": step, "stopped_early": stopped, "log": log, "ckpt_resumes": resumes, "train_s": train_s},
                  open(peft_dir / "train_log.json", "w"))
        if sha:
            (peft_dir / "train_sha.txt").write_text(sha)     # written last: the adapter is complete
    shutil.rmtree(out / "ckpt", ignore_errors=True)
    # [V6] convert first: a run stopped during the answers still leaves a usable Mac adapter
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent.train.peft_to_mlx import convert
    mlx_cfg = convert(peft_dir, out / "mlx", n_layers)

    # held-out answers, greedy like the Mac
    model.eval()
    model.config.use_cache = True
    if dev == "cuda" and BASE_DTYPE != "float16":
        model.half()        # [V6] answering never needs a float32 base (training fell back to float32)
    tests = rd("test.jsonl")[:8 if SMOKE else None]
    print(f"{name}: answering all {len(tests)} test rows", flush=True)

    def answer(rows, path: Path, adapter_on=True) -> dict:
        """Answers in batches, appended as they come (a stop keeps every finished batch)."""
        have = answered_ids(path)                  # [Colab V8] a rerun answers only what is left
        if have:
            print(f"{name}: {len(have)} answers already in {path.name}; answering the rest", flush=True)
        prior = sum(1 for r in rows if r["id"] in have)
        rows = [r for r in rows if r["id"] not in have]
        nb = (len(rows) + ANSWER_BATCH - 1) // ANSWER_BATCH
        ta, done, cut, forced = time.time(), 0, False, 0
        with open(path, "a") as f:
            for b, i in enumerate(range(0, len(rows), ANSWER_BATCH)):
                if time.time() > deadline():
                    print(f"{name}: wall-clock budget reached at answer batch {b}/{nb}; keeping {done} answers", flush=True)
                    cut = True
                    break
                part = rows[i:i + ANSWER_BATCH]
                enc = tok([render(r["messages"][:2], True) for r in part], return_tensors="pt", padding=True,
                          add_special_tokens=False).to(dev)
                kw = {"max_new_tokens": 8 if SMOKE else MAX_NEW, "do_sample": False, "pad_token_id": tok.pad_token_id}
                if CONSTRAINED:     # [V7] every answer is its schema's JSON (agent/kaggle/constrained.py)
                    proc = JsonSchemaProcessor(tok, [schema_for(r["task"], r["messages"][1]["content"]) for r in part],
                                               max_new_tokens=kw["max_new_tokens"])
                    kw["logits_processor"] = LogitsProcessorList([proc])
                with torch.no_grad(), torch.autocast(**amp):
                    if adapter_on:
                        gen = model.generate(**enc, **kw)
                    else:
                        with model.disable_adapter():
                            gen = model.generate(**enc, **kw)
                if CONSTRAINED:
                    forced += proc.forced
                for r, g in zip(part, gen):
                    f.write(json.dumps({"id": r["id"], "task": r["task"],
                                        "text": tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True)}) + "\n")
                f.flush()
                done += len(part)
                if done // ANSWER_LOG_ROWS > (done - len(part)) // ANSWER_LOG_ROWS or b + 1 == nb:
                    print(f"{name} answers {'' if adapter_on else '(no adapter) '}batch {b + 1}/{nb} "
                          f"({done}/{len(rows)} rows) eta {_eta(b + 1, nb, ta)}", flush=True)
                    heartbeat(out, phase="answer" if adapter_on else "answer_base", batch=b + 1, batches=nb)
        return {"answered": prior + done, "of": prior + len(rows), "this_session": done, "cut_by_budget": cut,
                "constrained": CONSTRAINED, "forced_tokens": forced}

    t1 = time.time()
    answered = {"student": answer(tests, out / "test_out.jsonl")}
    if name == "watchdog" and REUSE_BASE and Path(REUSE_BASE).exists() and \
            sum(1 for _ in open(REUSE_BASE)) == len(tests):            # same test set: the base can't have changed
        shutil.copy(REUSE_BASE, out / "test_out_base.jsonl")
        answered["base"] = {"reused": REUSE_BASE, "answered": len(tests), "of": len(tests)}
    elif name == "watchdog":
        answered["base"] = answer(tests, out / "test_out_base.jsonl", adapter_on=False)
    answer_s = round(time.time() - t1)
    complete = not any(v.get("cut_by_budget") for v in answered.values())
    heartbeat(out, phase="done" if complete else "answers_cut")
    if not complete:
        print(f"{name}: answers cut by the budget; a rerun in the same work dir answers the rest", flush=True)

    json.dump({"job": name, "base": BASE_REPO + " (dequantized)", "rank": rank_for(name), "alpha": 2 * rank_for(name),
               "last_layers": LAST_LAYERS or n_layers, "lr": LR, "base_dtype": BASE_DTYPE, "batch": BATCH * ACCUM, "targets": TARGETS, "train_rows": len(train), "steps": step,
               "complete": complete, "stopped_early": stopped, "ckpt_resumes": resumes, "train_sha": sha, "log": log, "train_s": train_s, "answer_s": answer_s, "answered": answered,
               "mlx": mlx_cfg},
              open(out / "train_report.json", "w"), indent=1)
    print(name, "done", flush=True)


def job_done(d: Path) -> bool:
    """[Colab V8] done = train_report.json says complete (older reports without the field: mlx adapter present)."""
    try:
        rep = json.load(open(d / "train_report.json"))
    except (OSError, ValueError):
        return False
    return bool(rep.get("complete", (d / "mlx" / "adapters.safetensors").exists()))


def job_order(info: dict) -> list[str]:
    """RZ_JOB_ORDER ("watchdog,game-reasoner"), then any other job by name."""
    want = [x for x in os.environ.get("RZ_JOB_ORDER", "").split(",") if x in info]
    return want + sorted(x for x in info if x not in want)


def job_weights(info: dict) -> dict[str, float]:
    """RZ_JOB_WEIGHTS ("watchdog:1,game-reasoner:5"), else train rows x epochs + 4 x test rows."""
    env = dict(kv.split(":") for kv in os.environ.get("RZ_JOB_WEIGHTS", "").split(",") if ":" in kv)
    return {nm: float(env[nm]) if nm in env else
            max(1.0, v.get("train", 0) * EPOCHS.get(nm, 1) + 4 * v.get("test", 0)) for nm, v in info.items()}


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
    if os.environ.get("RZ_JOBS"):                 # [Colab] e.g. "watchdog": train only these jobs this run
        keep = set(os.environ["RZ_JOBS"].split(","))
        info = {k: v for k, v in info.items() if k in keep}
        print("jobs this run:", sorted(info), flush=True)
    base_dir(out)
    import threading

    import torch
    n_gpu = max(1, torch.cuda.device_count())
    t_start = time.time()
    dl = t_start + BUDGET_S
    print(f"student phase budget {BUDGET_S / 3600:.1f} h (deadline in {BUDGET_S / 60:.0f} min)", flush=True)

    def launch(name: str, env: dict, mode: str = "w", job_dl: float | None = None):
        """Start one job; [V6] tee its output to <job>/train.log AND the notebook's stdout, prefixed."""
        log = open(out / name / "train.log", mode)
        p = subprocess.Popen([sys.executable, __file__, "--pack", a.pack, "--out", a.out, "--job", name],
                             env={**env, "RZ_DEADLINE": str(job_dl or dl), "PYTHONUNBUFFERED": "1"},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        def pump():
            for line in p.stdout:
                log.write(line)
                log.flush()
                print(f"[{name}] {line}", end="", flush=True)
            log.close()
        threading.Thread(target=pump, daemon=True).start()
        return p

    def wait(name: str, p) -> int:
        """Wait for a job, but never past the budget (+ grace): a hung job is stopped, results kept."""
        while p.poll() is None:
            if time.time() > dl + KILL_GRACE_S:
                print(f"{name}: past the wall-clock budget; stopping it (its finished answers are kept)", flush=True)
                p.terminate()
                try:
                    p.wait(60)
                except subprocess.TimeoutExpired:
                    p.kill()
                return -9
            time.sleep(5)
        return p.returncode

    def status():
        """[V7] one line per job from its heartbeat.json, every 10 min, in the notebook's stdout."""
        while time.time() < dl + KILL_GRACE_S:
            time.sleep(600)
            parts = []
            for nm in sorted(info):
                try:
                    hb = json.load(open(out / nm / "heartbeat.json"))
                    parts.append(f"{nm}: " + " ".join(f"{k}={v}" for k, v in hb.items()))
                except (OSError, ValueError):
                    parts.append(f"{nm}: (no heartbeat yet)")
            print(f"[status {(time.time() - t_start) / 60:.0f} min, {(dl - time.time()) / 60:.0f} min to the budget] "
                  + " | ".join(parts), flush=True)
    threading.Thread(target=status, daemon=True).start()

    names = job_order(info)
    todo = [nm for nm in names if not job_done(out / nm)]
    for nm in names:
        if nm not in todo:
            print(nm, "already done (train_report.json complete); skipping", flush=True)
    w = job_weights(info)
    procs = []
    for k, name in enumerate(todo):
        (out / name).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "CUDA_VISIBLE_DEVICES": str(k % n_gpu)}
        job_dl = None
        if n_gpu == 1 and len(todo) > 1:              # [Colab V8] one GPU: a share of what is left, by weight
            left = todo[k:]
            job_dl = time.time() + max(0.0, dl - time.time()) * w[name] / sum(w[x] for x in left)
            print(f"{name}: {(job_dl - time.time()) / 60:.0f} min of the {(dl - time.time()) / 60:.0f} min left",
                  flush=True)
        p = launch(name, env, mode="a", job_dl=job_dl)
        procs.append((name, p, env))
        if k % n_gpu == n_gpu - 1:          # more jobs than GPUs: wait for this round
            for nm, q, _ in procs:
                wait(nm, q)
    bad = []
    for name, p, env in procs:
        rc = wait(name, p)
        if rc == FP16_OVERFLOW and time.time() < dl - ANSWER_RESERVE_S:   # same job again on a float32 base
            print(name, "overflowed on the float16 base; retrying on float32", flush=True)
            p = launch(name, {**env, "RZ_BASE_DTYPE": "float32"}, mode="a")
            rc = wait(name, p)
        if rc == PAUSED:
            print(name, "checkpointed by the budget; rerun to continue its training", flush=True)
        if rc != 0:
            bad.append(name)
    for name, _, _ in procs:
        print(f"--- {name} log tail ---\n" + "".join(open(out / name / "train.log").readlines()[-15:]), flush=True)
    if bad:
        # [V6] never fail the notebook here: grading runs on whatever answers exist (partial results)
        print(f"student jobs not clean: {bad} (see {out}/<job>/train.log, heartbeat.json); grading what exists",
              flush=True)


if __name__ == "__main__":
    main()
