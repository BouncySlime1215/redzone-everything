"""LoRA for the watchdog model with PyTorch + PEFT, for machines where MLX can't train (Linux
CPU boxes: MLX's CPU backend takes 90 s+ per prompt there). The adapter is mlx_lm-shaped from
the start (last N layers, the same projections, rank/scale), and agent/train/peft_to_mlx.py turns
it into the adapters.safetensors + adapter_config.json the Mac loads.

The base is the Mac's own model, mlx-community/Qwen3-1.7B-4bit, dequantized to bf16
(`python -m mlx_lm convert --hf-path <4bit dir> --mlx-path <out> -d`, then drop "quantization"
from its config.json), so the adapter is trained on exactly the weights it will run on.

Fast path: LoRA lives only in the last N layers, so the first 28-N layers are frozen and their
output never changes. Each example's hidden state after them is computed once (--cache) and
training runs only the top N layers. The loss covers the assistant answer only (mask_prompt), and
the chat format is the runtime's: apply_chat_template(..., enable_thinking=False).

    python agent/train/train_peft.py --base <deq dir> --cache /tmp/rz_cache --out /tmp/rz_peft \\
        --layers 8 --rank 8 --alpha 16 --lr 2e-4 --epochs 2
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import time
from pathlib import Path

import torch
from transformers import DynamicCache

DATA = Path(__file__).resolve().parent / "data"
KEYS = ("self_attn.q_proj", "self_attn.k_proj", "self_attn.v_proj", "self_attn.o_proj",
        "mlp.gate_proj", "mlp.up_proj", "mlp.down_proj")


def encode(tok, messages: list[dict]) -> tuple[list[int], int]:
    """Token ids of the whole chat and where the answer starts (everything before is masked)."""
    prompt = tok.apply_chat_template(messages[:-1], add_generation_prompt=True, tokenize=False,
                                     enable_thinking=False)
    full = tok.apply_chat_template(messages, tokenize=False, enable_thinking=False)
    assert full.startswith(prompt), "chat template must extend the prompt"
    p_ids = tok(prompt, add_special_tokens=False)["input_ids"]
    f_ids = tok(full, add_special_tokens=False)["input_ids"]
    assert f_ids[:len(p_ids)] == p_ids
    f_ids = f_ids[:len(p_ids)] + tok(full[len(prompt):].rstrip("\n"), add_special_tokens=False)["input_ids"]
    return f_ids, len(p_ids)


def load_split(tok, name: str, limit: int = 0) -> list[tuple[list[int], int]]:
    rows = [json.loads(line)["messages"] for line in (DATA / f"{name}.jsonl").open()]
    if limit:
        rows = rows[:limit]
    return [encode(tok, m) for m in rows]


def batches(idx: list[int], lens: list[int], bs: int, rng: random.Random) -> list[list[int]]:
    """Length-bucketed batches (little padding), in random order."""
    idx = sorted(idx, key=lambda i: lens[i])
    out = [idx[i:i + bs] for i in range(0, len(idx), bs)]
    rng.shuffle(out)
    return out


def pad(seqs: list[list[int]], pad_id: int) -> tuple[torch.Tensor, torch.Tensor]:
    n = max(len(s) for s in seqs)
    ids = torch.full((len(seqs), n), pad_id, dtype=torch.long)
    mask = torch.zeros((len(seqs), n), dtype=torch.long)
    for i, s in enumerate(seqs):
        ids[i, :len(s)] = torch.tensor(s)
        mask[i, :len(s)] = 1
    return ids, mask


class Split:
    """Run the bottom (frozen) and top (LoRA) parts of the decoder separately."""

    def __init__(self, model, n_top: int):
        self.m = model
        self.inner = model.model
        self.all_layers = self.inner.layers
        self.cut = len(self.all_layers) - n_top
        self.norm = self.inner.norm

    @torch.no_grad()
    def bottom(self, ids: torch.Tensor, mask: torch.Tensor, past=None) -> tuple[torch.Tensor, object]:
        self.inner.layers = self.all_layers[:self.cut]
        self.inner.norm = torch.nn.Identity()
        try:
            r = self.inner(input_ids=ids, attention_mask=mask, past_key_values=past, use_cache=past is not None)
            return r.last_hidden_state, r.past_key_values
        finally:
            self.inner.layers, self.inner.norm = self.all_layers, self.norm

    def top(self, h: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        self.inner.layers = self.all_layers[self.cut:]
        try:
            # inputs_embeds skips the embedding; positions are 0..n-1 as in the full model
            return self.inner(inputs_embeds=h, attention_mask=mask, use_cache=False).last_hidden_state
        finally:
            self.inner.layers = self.all_layers


def shared_prefix(data) -> int:
    """Length of the token prefix every example shares (the system prompt and the user header).
    Its hidden states are the same for all of them (causal attention), so it's computed once."""
    first = data[0][0]
    n = min(start for _, start in data)
    for ids, _ in data:
        k = 0
        while k < n and ids[k] == first[k]:
            k += 1
        n = k
    return n


def cache_split(split: Split, data, cache: Path, name: str, bs: int = 8) -> None:
    """Hidden state after the frozen layers: the shared prefix once (prefix.pt) and each example's
    own suffix (i.pt)."""
    d = cache / name
    d.mkdir(parents=True, exist_ok=True)
    n0 = shared_prefix(data)
    pre = torch.tensor([data[0][0][:n0]])
    hp, kv = split.bottom(pre, torch.ones_like(pre), past=DynamicCache())
    torch.save({"n": n0, "h": hp[0].clone()}, d / "prefix.pt")
    done = {int(p.stem) for p in d.glob("[0-9]*.pt")}
    t0, todo = time.time(), [i for i in range(len(data)) if i not in done]
    lens = [len(s) for s, _ in data]
    order = sorted(todo, key=lambda i: lens[i])
    for b in range(0, len(order), bs):
        group = order[b:b + bs]
        ids, mask = pad([data[i][0][n0:] for i in group], 0)
        past = copy.deepcopy(kv)
        past.batch_repeat_interleave(len(group))
        h, _ = split.bottom(ids, torch.cat([torch.ones((len(group), n0), dtype=torch.long), mask], 1), past=past)
        for j, i in enumerate(group):
            torch.save(h[j, :lens[i] - n0].clone(), d / f"{i}.pt")
        if (b // bs) % 25 == 0:
            el = time.time() - t0
            print(f"cache {name}: {b + len(group)}/{len(order)}  {el / 60:.1f} min", flush=True)


_PREFIX: dict = {}


def _prefix(d: Path) -> torch.Tensor:
    if d not in _PREFIX:
        _PREFIX[d] = torch.load(d / "prefix.pt")["h"]
    return _PREFIX[d]


def loss_on(split: Split, data, group: list[int], cache: Path, name: str) -> tuple[torch.Tensor, int]:
    seqs = [data[i][0] for i in group]
    pre = _prefix(cache / name)
    hs = [torch.cat([pre, torch.load(cache / name / f"{i}.pt")]) for i in group]
    n = max(len(s) for s in seqs)
    h = torch.zeros((len(group), n, hs[0].shape[-1]), dtype=hs[0].dtype)
    mask = torch.zeros((len(group), n), dtype=torch.long)
    for j, x in enumerate(hs):
        h[j, :len(x)] = x
        mask[j, :len(x)] = 1
    out = split.top(h, mask)
    # predict token t+1 from position t, answer tokens only (lm_head only where needed)
    rows, targets = [], []
    for j, i in enumerate(group):
        ids, start = data[i]
        for t in range(start - 1, len(ids) - 1):
            rows.append((j, t))
            targets.append(ids[t + 1])
    sel = out[[r[0] for r in rows], [r[1] for r in rows]]
    logits = split.m.lm_head(sel).float()
    return torch.nn.functional.cross_entropy(logits, torch.tensor(targets)), len(targets)


def save(pm, named, masters, d: Path) -> None:
    """A standard PEFT adapter directory, with the fp32 master weights."""
    from safetensors.torch import save_file
    d.mkdir(parents=True, exist_ok=True)
    pm.peft_config["default"].save_pretrained(str(d))
    save_file({n.replace(".default.", "."): m.detach().contiguous() for (n, _), m in zip(named, masters)},
              str(d / "adapter_model.safetensors"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--layers", type=int, default=8)
    ap.add_argument("--rank", type=int, default=8)
    ap.add_argument("--alpha", type=float, default=16.0)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--eval-every", type=int, default=200)
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--cache-only", action="store_true")
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    torch.set_num_threads(a.threads)
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(a.base)
    model = AutoModelForCausalLM.from_pretrained(a.base, dtype=torch.bfloat16)
    model.eval()
    for p in model.parameters():
        p.requires_grad = False
    n_layers = model.config.num_hidden_layers
    split = Split(model, a.layers)
    cache = Path(a.cache) / f"cut{split.cut}"
    train, valid = load_split(tok, "train"), load_split(tok, "valid")
    print(f"train {len(train)}  valid {len(valid)}  tokens/ex {sum(len(s) for s, _ in train) / len(train):.0f}",
          flush=True)
    cache_split(split, train, cache, "train")
    cache_split(split, valid, cache, "valid")
    if a.cache_only:
        return

    cfg = LoraConfig(r=a.rank, lora_alpha=a.alpha, lora_dropout=a.dropout, bias="none",
                     target_modules=[k.split(".")[-1] for k in KEYS],
                     layers_to_transform=list(range(split.cut, n_layers)), task_type="CAUSAL_LM")
    # bf16 adapters run ~30% faster on CPU than fp32 ones; the optimizer keeps fp32 master copies
    # so small updates aren't rounded away.
    pm = get_peft_model(model, cfg, autocast_adapter_dtype=False)
    pm.print_trainable_parameters()
    split = Split(pm.base_model.model, a.layers)
    named = [(n, p) for n, p in pm.named_parameters() if p.requires_grad]
    params = [p for _, p in named]
    masters = [p.detach().float().clone().requires_grad_(True) for p in params]
    opt = torch.optim.AdamW(masters, lr=a.lr, weight_decay=0.0)
    rng = random.Random(a.seed)
    lens = [len(s) for s, _ in train]
    steps_per_epoch = math.ceil(len(train) / a.batch)
    total = int(steps_per_epoch * a.epochs)
    warm = max(1, total // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * 0.5 * (1 + math.cos(math.pi * min(1.0, s / total))))

    def val_loss() -> float:
        pm.eval()
        tot, n = 0.0, 0
        with torch.no_grad():
            for b in range(0, len(valid), a.batch):
                g = list(range(b, min(len(valid), b + a.batch)))
                l, k = loss_on(split, valid, g, cache, "valid")
                tot, n = tot + l.item() * k, n + k
        pm.train()
        return tot / max(1, n)

    log = []
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    best = float("inf")
    print(f"steps {total}  ({steps_per_epoch}/epoch)  val loss before {val_loss():.4f}", flush=True)
    pm.train()
    step, t0, run = 0, time.time(), 0.0
    while step < total:
        for g in batches(list(range(len(train))), lens, a.batch, rng):
            if step >= total:
                break
            l, _ = loss_on(split, train, g, cache, "train")
            for p in params:
                p.grad = None
            l.backward()
            for p, m in zip(params, masters):
                m.grad = p.grad.float()
            torch.nn.utils.clip_grad_norm_(masters, 1.0)
            opt.step()
            with torch.no_grad():
                for p, m in zip(params, masters):
                    p.copy_(m)
            sched.step()
            step += 1
            run = 0.9 * run + 0.1 * l.item() if step > 1 else l.item()
            if step % 25 == 0:
                print(f"step {step}/{total}  loss {run:.4f}  lr {sched.get_last_lr()[0]:.2e}  "
                      f"{(time.time() - t0) / step:.1f} s/step", flush=True)
            if step % a.eval_every == 0 or step == total:
                v = val_loss()
                log.append({"step": step, "train_loss": round(run, 4), "val_loss": round(v, 4),
                            "min": round((time.time() - t0) / 60, 1)})
                print(f"== step {step}  val loss {v:.4f}", flush=True)
                save(pm, named, masters, out / f"step{step}")
                if v < best:
                    best = v
                    save(pm, named, masters, out / "best")
    (out / "train_log.json").write_text(json.dumps({"args": vars(a), "log": log, "best_val_loss": best,
                                                    "minutes": round((time.time() - t0) / 60, 1)}, indent=1))


if __name__ == "__main__":
    main()
