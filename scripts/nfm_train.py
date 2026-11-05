#!/usr/bin/env python3
"""Portable world-model trainer (dense or MoE): one script on Kaggle TPU, Colab GPU, a SLURM GPU node and a GCP TPU VM.

Serves: North Star "crunch live %" and "dead air %" through the world model; Nick's compute order (12:05 AM Nov 5:
"how do we train this thing fast"). Owner: lane 1. Plan: docs/plans/2026-11-05-nfm-scale.md (EXP-106..108).
Personal use: grades.json carries "license": "personal-use, not for commercial release" when any shard is restrictive.

    python scripts/nfm_train.py --tokens SHARDS --out OUT --preset 60m [--minutes 120] [--train-tokens 1.2e9]
    PJRT_DEVICE=TPU python scripts/nfm_train.py ...          # Kaggle TPU v5e-8 / TPU VM: torch_xla, SPMD data parallel
    sbatch scripts/slurm/nfm_train.sbatch                    # UNC Longleaf (GPU partition)

Same data, split and grader as world_model_pilot.py (per-league time split by whole night; test graded once; selection
on valid loss only). What is new here: sequence packing (games back to back, EOS-separated, no padding), RoPE (any
context), RMSNorm + SwiGLU, bf16 autocast, a static-shape MoE layer (top-k with capacity, XLA-friendly), throughput
in tokens/s per chip, and a checkpoint every 30 min with exact resume (step, optimizer, data RNG).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PERSONAL = "personal-use, not for commercial release"
SEED = 1234

# name: d, layers, heads, experts, top_k, ffn mult. Dense params are counted by count_params (tests pin them).
PRESETS = {
    "tiny": dict(d=64, layers=2, heads=2),                          # CPU smoke
    "18m": dict(d=384, layers=8, heads=6),                          # EXP-103 point
    "60m": dict(d=640, layers=12, heads=10),
    "150m": dict(d=896, layers=16, heads=14),
    "300m": dict(d=1152, layers=20, heads=16),
    "1b": dict(d=2048, layers=20, heads=16),
    # MoE at equal ACTIVE compute to its dense twin (top-2 of 8 experts at half the ffn width each)
    "moe-60m": dict(d=640, layers=12, heads=10, experts=8, top_k=2, ffn_mult=4.0),
    # the target config for when big compute lands: ~5B total, ~1B active
    "moe-5b": dict(d=2048, layers=22, heads=16, experts=12, top_k=2, ffn_mult=4.0),
}


# ---------------------------------------------------------------------------------------------- device
def device(kind: str = "auto"):
    """('xla'|'cuda'|'cpu', torch device, n_chips)."""
    import torch
    if kind in ("auto", "xla") and (os.environ.get("PJRT_DEVICE") or os.environ.get("TPU_NAME")
                                    or os.path.exists("/dev/accel0") or kind == "xla"):
        try:
            import torch_xla
            import torch_xla.runtime as xr
            xr.use_spmd()
            return "xla", torch_xla.device(), xr.global_runtime_device_count()
        except Exception as e:  # noqa: BLE001 - no TPU runtime: fall through to GPU / CPU
            if kind == "xla":
                raise
            print("xla unavailable:", type(e).__name__, e, flush=True)
    if kind in ("auto", "cuda") and torch.cuda.is_available():
        return "cuda", torch.device("cuda"), torch.cuda.device_count()
    return "cpu", torch.device("cpu"), 1


# ---------------------------------------------------------------------------------------------- model
def ffn_hidden(d: int, mult: float = 4.0) -> int:
    """SwiGLU hidden width at the same params as a 4x GELU MLP, rounded to 128 (TPU/GPU friendly)."""
    return int(math.ceil(2 / 3 * mult * d / 128) * 128)


def count_params(vocab: int, d: int, layers: int, heads: int = 0, experts: int = 0, top_k: int = 0,
                 ffn_mult: float = 4.0) -> dict:
    """Total and active parameters (embeddings tied with the head)."""
    h = ffn_hidden(d, ffn_mult)
    attn = 4 * d * d
    ffn = 3 * d * h
    norms = 2 * d
    if experts:
        h_e = ffn_hidden(d, ffn_mult / top_k)        # each expert half-width at top-2: active ffn = one dense ffn
        ffn_total, ffn_active = experts * 3 * d * h_e + d * experts, top_k * 3 * d * h_e + d * experts
    else:
        ffn_total = ffn_active = ffn
    emb = vocab * d
    return {"total": emb + layers * (attn + ffn_total + norms) + d,
            "active": emb + layers * (attn + ffn_active + norms) + d,
            "non_embedding_active": layers * (attn + ffn_active + norms)}


RECIPE_KEYS = ("qk_norm", "relu2", "untie", "softcap", "value_emb", "doc_mask")


def build_model(vocab: int, d: int, layers: int, heads: int, experts: int = 0, top_k: int = 2,
                ffn_mult: float = 4.0, dropout: float = 0.0, capacity: float = 1.25, qk_norm: bool = False,
                relu2: bool = False, untie: bool = False, softcap: float = 0.0, value_emb: bool = False,
                doc_mask: bool = False, bos_ids=None):
    """Dense or MoE NFM. The speedrun flags (EXP-109, modded-nanoGPT bundle): qk_norm (RMSNorm on q and k), relu2
    (ReLU^2 MLP at 4x), untie (separate zero-init head), softcap (cap * tanh(logits / cap)), value_emb (a value
    embedding table mixed into every layer's v with a learned weight), doc_mask (no attention across games inside a
    packed window: FlexAttention on CUDA when available, else an explicit SDPA mask; bos_ids = the BOS token ids)."""
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class RMSNorm(nn.Module):
        def __init__(self, n):
            super().__init__()
            self.w = nn.Parameter(torch.ones(n))

        def forward(self, x):
            return x * torch.rsqrt(x.float().pow(2).mean(-1, keepdim=True) + 1e-6).to(x.dtype) * self.w

    def rope(x, base=10000.0):                      # x: b h t hd
        t, hd = x.shape[-2], x.shape[-1]
        inv = 1.0 / (base ** (torch.arange(0, hd, 2, device=x.device, dtype=torch.float32) / hd))
        ang = torch.arange(t, device=x.device, dtype=torch.float32)[:, None] * inv[None]
        cos, sin = ang.cos().to(x.dtype), ang.sin().to(x.dtype)
        x1, x2 = x[..., 0::2], x[..., 1::2]
        return torch.stack((x1 * cos - x2 * sin, x1 * sin + x2 * cos), -1).flatten(-2)

    class SwiGLU(nn.Module):
        def __init__(self, h):
            super().__init__()
            self.gate, self.up, self.down = nn.Linear(d, h, bias=False), nn.Linear(d, h, bias=False), \
                nn.Linear(h, d, bias=False)

        def forward(self, x):
            return self.down(F.silu(self.gate(x)) * self.up(x))

    class MoE(nn.Module):
        """Top-k routing with a fixed capacity per expert (static shapes for XLA); Switch-style balance loss."""

        def __init__(self):
            super().__init__()
            h = ffn_hidden(d, ffn_mult / top_k)
            self.router = nn.Linear(d, experts, bias=False)
            self.w_gate = nn.Parameter(torch.randn(experts, d, h) * d ** -0.5)
            self.w_up = nn.Parameter(torch.randn(experts, d, h) * d ** -0.5)
            self.w_down = nn.Parameter(torch.randn(experts, h, d) * h ** -0.5)
            self.aux = torch.zeros(())

        def forward(self, x):
            b, t, _ = x.shape
            xs = x.reshape(-1, d)
            n = xs.shape[0]
            probs = F.softmax(self.router(xs).float(), -1)                       # n E
            topv, topi = probs.topk(top_k, -1)                                   # n k
            topv = topv / topv.sum(-1, keepdim=True)
            cap = int(math.ceil(capacity * n * top_k / experts))
            mask = F.one_hot(topi, experts).float()                              # n k E
            pos = (mask.reshape(-1, experts).cumsum(0).reshape(n, top_k, experts) - 1) * mask
            keep = (pos < cap).float() * mask                                    # drop over capacity
            slot = F.one_hot(pos.clamp(max=cap - 1).long(), cap).float() * keep[..., None]   # n k E C
            disp = slot.sum(1)                                                   # n E C
            comb = (slot * topv[..., None, None]).sum(1)                         # n E C
            xe = torch.einsum("nec,nd->ecd", disp.to(xs.dtype), xs)              # E C d
            he = F.silu(torch.einsum("ecd,edh->ech", xe, self.w_gate.to(xs.dtype))) * \
                torch.einsum("ecd,edh->ech", xe, self.w_up.to(xs.dtype))
            ye = torch.einsum("ech,ehd->ecd", he, self.w_down.to(xs.dtype))
            y = torch.einsum("nec,ecd->nd", comb.to(xs.dtype), ye)
            frac = mask[:, 0].mean(0)                                            # share routed (first choice)
            self.aux = experts * (frac * probs.mean(0)).sum()
            return y.reshape(b, t, d)

    class ReLU2(nn.Module):
        def __init__(self):
            super().__init__()
            self.up, self.down = nn.Linear(d, 4 * d, bias=False), nn.Linear(4 * d, d, bias=False)

        def forward(self, x):
            return self.down(F.relu(self.up(x)).square())

    hd = d // heads

    class Block(nn.Module):
        def __init__(self):
            super().__init__()
            self.n1, self.n2 = RMSNorm(d), RMSNorm(d)
            self.qkv, self.proj = nn.Linear(d, 3 * d, bias=False), nn.Linear(d, d, bias=False)
            self.ffn = MoE() if experts else ReLU2() if relu2 else SwiGLU(ffn_hidden(d, ffn_mult))
            self.drop = nn.Dropout(dropout)
            if qk_norm:
                self.qn, self.kn = RMSNorm(hd), RMSNorm(hd)
            if value_emb:
                self.ve_w = nn.Parameter(torch.zeros(()))

        def forward(self, x, ve=None, mask=None):
            b, t, _ = x.shape
            q, k, v = self.qkv(self.n1(x)).view(b, t, 3, heads, hd).permute(2, 0, 3, 1, 4)
            if qk_norm:
                q, k = self.qn(q), self.kn(k)
            if ve is not None:
                v = v + self.ve_w * ve.view(b, t, heads, hd).transpose(1, 2)
            q, k = rope(q), rope(k)
            if mask is None:
                a = F.scaled_dot_product_attention(q, k, v, is_causal=True)   # flash kernel on GPU
            elif callable(getattr(mask, "as_tuple", None)) or type(mask).__name__ == "BlockMask":
                from torch.nn.attention.flex_attention import flex_attention
                a = flex_attention(q, k, v, block_mask=mask)
            else:
                a = F.scaled_dot_product_attention(q, k, v, attn_mask=mask)
            x = x + self.drop(self.proj(a.transpose(1, 2).reshape(b, t, d)))
            return x + self.drop(self.ffn(self.n2(x)))

    is_bos = torch.zeros(vocab, dtype=torch.bool)
    if bos_ids is not None:
        is_bos[torch.as_tensor(list(bos_ids), dtype=torch.long)] = True

    def doc_ids(idx):
        return torch.cumsum(is_bos.to(idx.device)[idx].int(), -1)          # b t: game number inside the window

    def make_mask(idx):
        b, t = idx.shape
        doc = doc_ids(idx)
        if idx.is_cuda:
            try:
                from torch.nn.attention.flex_attention import create_block_mask

                def mod(bb, h, qi, ki):
                    return (qi >= ki) & (doc[bb, qi] == doc[bb, ki])
                return create_block_mask(mod, b, None, t, t, device=idx.device)
            except Exception:  # noqa: BLE001 - older torch: explicit mask
                pass
        causal = torch.ones(t, t, dtype=torch.bool, device=idx.device).tril()
        return (causal[None] & (doc[:, :, None] == doc[:, None, :]))[:, None]

    class NFM(nn.Module):
        def __init__(self):
            super().__init__()
            self.tok = nn.Embedding(vocab, d)
            self.blocks = nn.ModuleList(Block() for _ in range(layers))
            self.norm = RMSNorm(d)
            nn.init.normal_(self.tok.weight, std=0.02)
            if untie:
                self.head = nn.Linear(d, vocab, bias=False)
                nn.init.zeros_(self.head.weight)
            if value_emb:
                self.ve = nn.Embedding(vocab, d)
                nn.init.normal_(self.ve.weight, std=0.02)

        def forward(self, idx):
            x = self.tok(idx)
            ve = self.ve(idx) if value_emb else None
            mask = make_mask(idx) if doc_mask and bos_ids is not None else None
            for blk in self.blocks:
                x = blk(x, ve, mask)
            x = self.norm(x)
            logits = self.head(x) if untie else x @ self.tok.weight.T     # tied head by default
            if softcap:
                logits = softcap * torch.tanh(logits / softcap)
            return logits

        def aux_loss(self):
            return sum(b.ffn.aux for b in self.blocks) / len(self.blocks) if experts else 0.0

    return NFM()


# ---------------------------------------------------------------------------------------------- data
def pack(games: list[dict]) -> np.ndarray:
    """Games back to back (each already BOS ... EOS): the packed train stream, no padding."""
    return np.concatenate([g["tokens"].astype(np.int32) for g in games]) if games else np.zeros(0, np.int32)


class Batches:
    """Random ctx+1 windows from the packed stream; the RNG is keyed by step, so a resume replays the same data."""

    def __init__(self, stream: np.ndarray, ctx: int, batch: int, seed: int = SEED):
        self.s, self.ctx, self.batch, self.seed = stream, ctx, batch, seed
        if len(stream) <= ctx + 1:
            raise SystemExit(f"train stream {len(stream)} tokens <= ctx {ctx}")

    def get(self, step: int) -> np.ndarray:
        rng = np.random.default_rng((self.seed, step))
        st = rng.integers(0, len(self.s) - self.ctx - 1, self.batch)
        return np.stack([self.s[i:i + self.ctx + 1] for i in st]).astype(np.int64)


def lr_at(step: int, total: int, peak: float, warmup: int, sched: str = "cosine", decay_frac: float = 0.2) -> float:
    """cosine: warmup then cosine to 10%. wsd (warmup-stable-decay): warmup, flat, then linear to 0 over the last
    decay_frac of the steps (EXP-109)."""
    if step < warmup:
        return peak * (step + 1) / warmup
    if sched == "wsd":
        start = int(total * (1 - decay_frac))
        return peak if step < start else peak * max(0.0, (total - step) / max(1, total - start))
    p = min(1.0, (step - warmup) / max(1, total - warmup))
    return peak * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * p)))


def newton_schulz(g, steps: int = 5):
    """Muon's orthogonalization (quintic Newton-Schulz, Keller Jordan's coefficients), batched over leading dims."""
    a, b, c = 3.4445, -4.7750, 2.0315
    x = g.bfloat16()
    tr = x.size(-2) > x.size(-1)
    if tr:
        x = x.mT
    x = x / (x.norm(dim=(-2, -1), keepdim=True) + 1e-7)
    for _ in range(steps):
        m = x @ x.mT
        x = a * x + (b * m + c * m @ m) @ x
    return (x.mT if tr else x).to(g.dtype)


def make_muon(params, lr: float = 0.02, momentum: float = 0.95, weight_decay: float = 0.0):
    import torch

    class Muon(torch.optim.Optimizer):
        """Momentum (Nesterov) then an orthogonalized update for hidden matrices; AdamW keeps the rest."""

        def __init__(self, ps):
            super().__init__(ps, dict(lr=lr, momentum=momentum, weight_decay=weight_decay))

        @torch.no_grad()
        def step(self):
            for gr in self.param_groups:
                for p in gr["params"]:
                    if p.grad is None:
                        continue
                    buf = self.state[p].setdefault("buf", torch.zeros_like(p.grad))
                    buf.mul_(gr["momentum"]).add_(p.grad)
                    u = newton_schulz(p.grad.add(buf, alpha=gr["momentum"]))
                    if gr["weight_decay"]:
                        p.mul_(1 - gr["lr"] * gr["weight_decay"])
                    p.add_(u, alpha=-gr["lr"] * max(1.0, p.size(-2) / p.size(-1)) ** 0.5)

    return Muon(params)


class Optims:
    """AdamW alone, or Muon (hidden matrices inside the blocks) + AdamW (embeddings, head, norms, scalars)."""

    def __init__(self, model, lr: float, muon: bool, muon_lr: float = 0.02):
        import torch
        hidden = [p for n, p in model.named_parameters() if n.startswith("blocks.") and p.ndim >= 2] if muon else []
        ids = {id(p) for p in hidden}
        rest = [p for p in model.parameters() if id(p) not in ids]
        self.opts = [torch.optim.AdamW(rest, lr=lr, betas=(0.9, 0.95), weight_decay=0.1)]
        if hidden:
            self.opts.append(make_muon(hidden, lr=muon_lr))
        for o in self.opts:
            for g in o.param_groups:
                g["base_lr"] = g["lr"]

    def set_factor(self, f: float):
        for o in self.opts:
            for g in o.param_groups:
                g["lr"] = g["base_lr"] * f

    def zero_grad(self, set_to_none=True):
        for o in self.opts:
            o.zero_grad(set_to_none=set_to_none)

    def step(self):
        for o in self.opts:
            o.step()

    def state_dict(self):
        return [o.state_dict() for o in self.opts]

    def load_state_dict(self, sd):
        for o, s in zip(self.opts, sd if isinstance(sd, list) else [sd]):
            o.load_state_dict(s)

    @property
    def param_groups(self):
        return self.opts[0].param_groups


# ---------------------------------------------------------------------------------------------- train
def main(argv=None) -> int:
    import world_model_pilot as W
    ap = argparse.ArgumentParser()
    ap.add_argument("--tokens", type=Path, default=None, help="shard dir (<dir>/<league>/<season>.npy + .json)")
    ap.add_argument("--build-vocab", type=int, default=0, choices=(0, 1, 2),
                    help="no --tokens: download public play by play and build vocab 1/2 shards under --data-dir")
    ap.add_argument("--data-dir", type=Path, default=None, help="raw + built shards (default <out>); share it across "
                                                                 "a ladder so the data is built once")
    ap.add_argument("--leagues", default="nfl,nba,mlb")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--ckpt-dir", type=Path, default=None, help="default <out> (Kaggle: /kaggle/working/...)")
    ap.add_argument("--preset", default="18m", choices=sorted(PRESETS))
    ap.add_argument("--ctx", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=32, help="global sequences per step")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--warmup", type=int, default=500)
    ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--train-tokens", type=float, default=0, help="token budget (0 = 20 x active params; EXP-107 uses 1.2e9)")
    ap.add_argument("--minutes", type=float, default=60.0, help="wall-clock cap for training")
    ap.add_argument("--ckpt-min", type=float, default=30.0)
    ap.add_argument("--eval-every", type=int, default=250)
    ap.add_argument("--grade-games", type=int, default=400, help="test games graded per league (CPU/GPU pass)")
    ap.add_argument("--device", default="auto", choices=("auto", "xla", "cuda", "cpu"))
    ap.add_argument("--exp", default="EXP-106")
    ap.add_argument("--throughput-only", action="store_true", help="EXP-106: time steps, no grading")
    # EXP-109 speedrun recipe (each piece ablated; adopt only what helps)
    ap.add_argument("--muon", action="store_true", help="Muon for hidden matrices, AdamW for the rest")
    ap.add_argument("--muon-lr", type=float, default=0.02)
    ap.add_argument("--sched", default="cosine", choices=("cosine", "wsd"))
    ap.add_argument("--qk-norm", action="store_true")
    ap.add_argument("--relu2", action="store_true")
    ap.add_argument("--untie", action="store_true")
    ap.add_argument("--softcap", type=float, default=0.0)
    ap.add_argument("--value-emb", action="store_true")
    ap.add_argument("--doc-mask", action="store_true", help="no attention across games inside a packed window")
    ap.add_argument("--compile", action="store_true", help="torch.compile (CUDA); XLA is always traced")
    ap.add_argument("--target-loss", type=float, default=0.0, help="record tokens + hours to reach this valid loss")
    a = ap.parse_args(argv)
    import torch
    torch.manual_seed(SEED)
    kind, dev, chips = device(a.device)
    a.out.mkdir(parents=True, exist_ok=True)
    ck_dir = a.ckpt_dir or a.out
    ck_dir.mkdir(parents=True, exist_ok=True)

    if a.tokens:
        games, licenses = W.load_shards(a.tokens, a.leagues.split(","))
    else:                                                          # self-contained notebooks (Kaggle / Colab)
        dd = a.data_dir or a.out
        dd.mkdir(parents=True, exist_ok=True)
        games = W.build_games(a.leagues.split(","), dd, False, a.build_vocab or 2)
        licenses = {json.loads(js.read_text()).get("license", "open") for js in (dd / "tokens").rglob("*.json")
                    if not js.name.endswith("_edges.json")}
        licenses |= {PERSONAL} if W.ESPN_DERIVED & set(a.leagues.split(",")) else set()
    sp = W.split(games)
    vocab = max(1 + max(int(g["tokens"].max()) for g in games if len(g["tokens"])), 64)
    vocab = int(math.ceil(vocab / 128) * 128)                      # pad for the MXU / tensor cores
    train, valid = pack(sp["train"]), pack(sp["valid"])
    cfg = PRESETS[a.preset]
    np_ = count_params(vocab, **cfg)
    budget = int(a.train_tokens or 20 * np_["active"])
    tok_per_step = a.batch * a.ctx
    total = max(1, budget // tok_per_step)
    print(f"{kind} x{chips} | preset {a.preset} {np_['total'] / 1e6:.1f}M total / {np_['active'] / 1e6:.1f}M active | "
          f"train {len(train):,} tokens, budget {budget:,} = {total} steps", flush=True)

    import noggin_tokens as V1
    import noggin_tokens_v2 as V2
    bos = list(range(V1.BOS0, V1.BOS0 + len(V1.LEAGUES))) + list(range(V2.BOS2_0, V2.BOS2_0 + V2.LEAGUE_SLOTS))
    recipe = dict(qk_norm=a.qk_norm, relu2=a.relu2, untie=a.untie, softcap=a.softcap, value_emb=a.value_emb,
                  doc_mask=a.doc_mask)
    mk = dict(**cfg, **recipe, bos_ids=[b for b in bos if b < vocab])
    model = build_model(vocab, dropout=a.dropout, **mk).to(dev)
    opt = Optims(model, a.lr, a.muon, a.muon_lr)
    fwd = torch.compile(model) if a.compile and kind == "cuda" else model
    shard = None
    if kind == "xla":
        import torch_xla.distributed.spmd as xs
        mesh = xs.Mesh(np.arange(chips), (chips, 1), ("data", "model"))
        shard = lambda t: xs.mark_sharding(t, mesh, ("data", None))   # noqa: E731 - batch split across chips
    step, best, curve, t_train = 0, float("inf"), [], 0.0
    ck = ck_dir / "ckpt.pt"
    if ck.exists():                                                # resume
        st = torch.load(ck, map_location="cpu", weights_only=False)
        model.load_state_dict(st["model"])
        opt.load_state_dict(st["opt"])
        step, best, curve, t_train = st["step"], st["best"], st["curve"], st.get("t_train", 0.0)
        print(f"resumed at step {step}", flush=True)
    data = Batches(train, a.ctx, a.batch)
    vdata = Batches(valid, a.ctx, a.batch, seed=SEED + 1) if len(valid) > a.ctx + 1 else None
    amp = dict(device_type="cuda" if kind == "cuda" else "xla" if kind == "xla" else "cpu", dtype=torch.bfloat16,
               enabled=kind != "cpu")

    def sync():
        if kind == "xla":
            import torch_xla
            torch_xla.sync()

    def loss_of(x):
        with torch.autocast(**amp):
            logits = fwd(x[:, :-1])
        lo = torch.nn.functional.cross_entropy(logits.float().reshape(-1, vocab), x[:, 1:].reshape(-1))
        return lo, lo + 0.01 * model.aux_loss()

    def evaluate(n=8, vd=None):
        vd = vd or vdata
        if vd is None:
            return None
        model.eval()
        tot = 0.0
        with torch.no_grad():
            for i in range(n):
                x = torch.from_numpy(vd.get(i)).to(dev)
                if shard:
                    shard(x)
                tot += float(loss_of(x)[0])
        model.train()
        return tot / n

    def save(path):
        sync()
        torch.save({"model": {k: v.cpu() for k, v in model.state_dict().items()},
                    "opt": opt.state_dict() if kind != "xla" else _cpu(opt.state_dict()),
                    "step": step, "best": best, "curve": curve, "t_train": t_train, "preset": a.preset,
                    "vocab": vocab, "cfg": cfg}, str(path) + ".tmp")
        os.replace(str(path) + ".tmp", path)

    t_prev, t0 = t_train, time.time()
    last_ck = t0
    tp_tok, tp_t, warm, hit = 0, 0.0, None, None
    model.train()
    while step < total and t_prev + time.time() - t0 < a.minutes * 60:
        opt.set_factor(lr_at(step, total, 1.0, a.warmup, a.sched))
        x = torch.from_numpy(data.get(step)).to(dev)
        if shard:
            shard(x)
        ts = time.time()
        _, loss = loss_of(x)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sync()
        step += 1
        if step == 20:
            warm = time.time()                                     # compile / autotune excluded
        if step > 20:
            if kind == "cuda":
                torch.cuda.synchronize()
            tp_tok += tok_per_step
            tp_t += time.time() - ts
        if step % a.eval_every == 0 or step == total:
            v = evaluate()
            curve.append([step, round(float(loss), 4), None if v is None else round(v, 4)])
            print(f"step {step}/{total} loss {float(loss):.4f} valid {v} lr {opt.param_groups[0]['lr']:.2e} "
                  f"{tp_tok / max(tp_t, 1e-9):,.0f} tok/s", flush=True)
            if v is not None and v < best:
                best = v
                save(ck_dir / "best.pt")
            if a.target_loss and hit is None and v is not None and v <= a.target_loss:
                hit = {"step": step, "tokens": step * tok_per_step, "train_hours": round(t_train / 3600, 4)}
                print("target reached:", hit, flush=True)
        t_train = t_prev + time.time() - t0
        if time.time() - last_ck > a.ckpt_min * 60:
            save(ck)
            last_ck = time.time()
    t_train = t_prev + time.time() - t0
    save(ck)
    valid_by_league = {}
    if (ck_dir / "best.pt").exists():                               # per-league valid loss of the selected model
        model.load_state_dict(torch.load(ck_dir / "best.pt", map_location="cpu", weights_only=False)["model"])
        for lg in sorted({x["league"] for x in sp["valid"]}):
            s_lg = pack([x for x in sp["valid"] if x["league"] == lg])
            if len(s_lg) > a.ctx + 1:
                valid_by_league[lg] = round(evaluate(16, Batches(s_lg, a.ctx, a.batch, seed=SEED + 2)), 4)
    tps = tp_tok / max(tp_t, 1e-9)
    flops_tok = 6 * np_["non_embedding_active"] + 12 * cfg["layers"] * a.ctx * cfg["d"]
    g = {"exp": a.exp, "seed": SEED, "preset": a.preset, "params": np_, "vocab_size": vocab, "ctx": a.ctx,
         "batch": a.batch, "steps": step, "planned_steps": total, "tokens_seen": step * tok_per_step,
         "train_tokens_unique": int(len(train)), "epochs": round(step * tok_per_step / max(1, len(train)), 2),
         "best_valid_loss": None if best == float("inf") else round(best, 4), "learning_curve": curve,
         "valid_loss_by_league": valid_by_league,
         "throughput": {"device": kind, "chips": chips, "tokens_per_s": round(tps),
                        "tokens_per_s_per_chip": round(tps / chips), "train_flops_per_token": flops_tok,
                        "achieved_tflops": round(tps * flops_tok / 1e12, 2), "warmup_steps_excluded": 20 if warm else 0},
         "train_minutes": round(t_train / 60, 1), "selection": "valid loss (best.pt)",
         "recipe": {**recipe, "muon": a.muon, "muon_lr": a.muon_lr if a.muon else None, "sched": a.sched,
                    "compile": a.compile, "lr": a.lr, "warmup": a.warmup},
         "target": {"loss": a.target_loss, "reached": hit} if a.target_loss else None,
         "license": PERSONAL if any(x != "open" and not str(x).startswith("open") for x in licenses) else "open",
         "data_sha256_16": W.data_hash(games), "split": {k: len(v) for k, v in sp.items()}}
    if not a.throughput_only and (ck_dir / "best.pt").exists():
        st = torch.load(ck_dir / "best.pt", map_location="cpu", weights_only=False)
        gdev = "cuda" if torch.cuda.is_available() else "cpu"      # grade off the TPU (dynamic shapes)
        gm = build_model(vocab, **mk).to(gdev)
        gm.load_state_dict(st["model"])
        base = W.baselines(sp["train"])
        g["test_by_league"] = {}
        for lg in sorted({x["league"] for x in sp["test"]}):
            mine = [x for x in sp["test"] if x["league"] == lg][: a.grade_games]
            r = W.grade(gm, mine, base, a.ctx, gdev)
            g["test_by_league"][lg] = {k: r[k] for k in ("next_kind_acc", "next_kind_acc_bigram", "break_dt_mae_s",
                                                         "break_dt_mae_s_baseline", "stop_ece", "stop_ece_baseline",
                                                         "games")}
    (a.out / "grades.json").write_text(json.dumps(g, indent=1))
    print(json.dumps({k: v for k, v in g.items() if k != "learning_curve"}, indent=1))
    return 0


def _cpu(obj):
    import torch
    if isinstance(obj, torch.Tensor):
        return obj.cpu()
    if isinstance(obj, dict):
        return {k: _cpu(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_cpu(v) for v in obj]
    return obj


if __name__ == "__main__":
    raise SystemExit(main())
