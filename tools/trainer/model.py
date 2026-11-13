"""Dense decoder over play-by-play event tokens (torch): RMSNorm, GQA, RoPE, SwiGLU, QK-norm, init std 0.02.

Why: track B section 3 "Architecture" (dense Llama-style decoder: RMSNorm, GQA, RoPE, SwiGLU, QK-norm, z-loss,
init std 0.02) and TRAINING-PLAN 3.2 sizes 5M (4 x 256), 15M (6 x 384), 50M (10 x 640). z-loss and the optimizer
live in torch_backend.py. Needs torch, so it runs only on the GPU runtime (Kaggle or Colab); CI and the Mac test
the same loop with tools/trainer/reference.py. Untested until the first GPU-runtime test run (see README).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class DecoderConfig:
    vocab_size: int
    ctx: int
    d_model: int = 256
    n_layers: int = 4
    n_heads: int = 4
    n_kv_heads: int = 2
    ffn_mult: float = 8.0 / 3.0
    rope_base: float = 10000.0
    qk_norm: bool = True
    init_std: float = 0.02
    norm_eps: float = 1e-6

    def __post_init__(self):
        if self.d_model % self.n_heads or self.n_heads % self.n_kv_heads or (self.d_model // self.n_heads) % 2:
            raise ValueError("d_model % n_heads, n_heads % n_kv_heads and head_dim % 2 must all be 0")

    @property
    def head_dim(self) -> int:
        return self.d_model // self.n_heads

    @property
    def ffn_dim(self) -> int:
        return max(64, int(round(self.ffn_mult * self.d_model / 64.0)) * 64)


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6):
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        xf = x.float()
        y = xf * torch.rsqrt(xf.pow(2).mean(-1, keepdim=True) + self.eps)
        return (y * self.weight.float()).type_as(x)


def rope_tables(T: int, head_dim: int, base: float) -> tuple[torch.Tensor, torch.Tensor]:
    inv = 1.0 / (base ** (torch.arange(0, head_dim, 2, dtype=torch.float32) / head_dim))
    f = torch.outer(torch.arange(T, dtype=torch.float32), inv)            # [T, head_dim / 2]
    return f.cos(), f.sin()


def apply_rope(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    """Rotate interleaved pairs of the last dim; x is [B, H, T, head_dim]."""
    T = x.shape[-2]
    c, s = cos[:T], sin[:T]
    x1, x2 = x[..., 0::2].float(), x[..., 1::2].float()
    out = torch.stack((x1 * c - x2 * s, x1 * s + x2 * c), dim=-1).flatten(-2)
    return out.type_as(x)


class Attention(nn.Module):
    def __init__(self, c: DecoderConfig):
        super().__init__()
        self.h, self.kv, self.hd = c.n_heads, c.n_kv_heads, c.head_dim
        self.wq = nn.Linear(c.d_model, self.h * self.hd, bias=False)
        self.wk = nn.Linear(c.d_model, self.kv * self.hd, bias=False)
        self.wv = nn.Linear(c.d_model, self.kv * self.hd, bias=False)
        self.wo = nn.Linear(self.h * self.hd, c.d_model, bias=False)
        self.q_norm = RMSNorm(self.hd, c.norm_eps) if c.qk_norm else None
        self.k_norm = RMSNorm(self.hd, c.norm_eps) if c.qk_norm else None

    def _qkv(self, x, cos, sin):
        B, T, _ = x.shape
        q = self.wq(x).view(B, T, self.h, self.hd).transpose(1, 2)
        k = self.wk(x).view(B, T, self.kv, self.hd).transpose(1, 2)
        v = self.wv(x).view(B, T, self.kv, self.hd).transpose(1, 2)
        if self.q_norm is not None:
            q, k = self.q_norm(q), self.k_norm(k)
        q, k = apply_rope(q, cos, sin), apply_rope(k, cos, sin)
        if self.kv != self.h:                                   # grouped-query attention
            rep = self.h // self.kv
            k, v = k.repeat_interleave(rep, dim=1), v.repeat_interleave(rep, dim=1)
        return q, k, v

    def forward(self, x, cos, sin):
        B, T, _ = x.shape
        q, k, v = self._qkv(x, cos, sin)
        y = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        return self.wo(y.transpose(1, 2).reshape(B, T, self.h * self.hd))

    @torch.no_grad()
    def max_logit(self, x, cos, sin) -> float:
        """The largest pre-softmax attention logit over allowed (causal) pairs: a stability signal (OLMo 2)."""
        q, k, _ = self._qkv(x, cos, sin)
        s = (q.float() @ k.float().transpose(-1, -2)) / math.sqrt(self.hd)
        T = s.shape[-1]
        allowed = torch.ones(T, T, dtype=torch.bool, device=s.device).tril()
        return float(s.masked_fill(~allowed, float("-inf")).max())


class SwiGLU(nn.Module):
    def __init__(self, c: DecoderConfig):
        super().__init__()
        self.w1 = nn.Linear(c.d_model, c.ffn_dim, bias=False)
        self.w3 = nn.Linear(c.d_model, c.ffn_dim, bias=False)
        self.w2 = nn.Linear(c.ffn_dim, c.d_model, bias=False)

    def forward(self, x):
        return self.w2(F.silu(self.w1(x)) * self.w3(x))


class Block(nn.Module):
    def __init__(self, c: DecoderConfig):
        super().__init__()
        self.attn_norm = RMSNorm(c.d_model, c.norm_eps)
        self.attn = Attention(c)
        self.mlp_norm = RMSNorm(c.d_model, c.norm_eps)
        self.mlp = SwiGLU(c)

    def forward(self, x, cos, sin):
        x = x + self.attn(self.attn_norm(x), cos, sin)
        return x + self.mlp(self.mlp_norm(x))


class EventDecoder(nn.Module):
    def __init__(self, c: DecoderConfig):
        super().__init__()
        self.cfg = c
        self.embed = nn.Embedding(c.vocab_size, c.d_model)
        self.blocks = nn.ModuleList(Block(c) for _ in range(c.n_layers))
        self.norm = RMSNorm(c.d_model, c.norm_eps)
        self.head = nn.Linear(c.d_model, c.vocab_size, bias=False)
        cos, sin = rope_tables(c.ctx, c.head_dim, c.rope_base)
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)
        for m in self.modules():
            if isinstance(m, (nn.Linear, nn.Embedding)):
                nn.init.normal_(m.weight, mean=0.0, std=c.init_std)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Token ids [B, T] (T <= ctx) -> float32 logits [B, T, vocab]."""
        if x.shape[1] > self.cfg.ctx:
            raise ValueError(f"sequence of {x.shape[1]} tokens is longer than ctx {self.cfg.ctx}")
        h = self.embed(x)
        for b in self.blocks:
            h = b(h, self.rope_cos, self.rope_sin)
        return self.head(self.norm(h)).float()

    @torch.no_grad()
    def max_attention_logit(self, x: torch.Tensor) -> float:
        h = self.embed(x)
        out = float("-inf")
        for b in self.blocks:
            out = max(out, b.attn.max_logit(b.attn_norm(h), self.rope_cos, self.rope_sin))
            h = b(h, self.rope_cos, self.rope_sin)
        return out
