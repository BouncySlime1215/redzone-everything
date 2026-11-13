"""Torch backend for the training loop: the event decoder with AdamW, z-loss, clipping, mixed precision, RNG state.

Why: TRAINING-PLAN 3.2 (AdamW 0.9/0.95, eps 1e-8, clip 1.0, no weight decay on embeddings; bf16 on A100/L4,
fp16 with loss scaling on T4) and section 2 "Resume design" (a checkpoint holds model, optimiser, scaler and the
torch CPU and CUDA RNG states). It implements the backend interface of tools/trainer/loop.py, the same one the
numpy reference implements, so the loop's accumulation, resume and fence logic is shared and tested in CI.
Needs torch: GPU runtime only (tools/trainer/tests runs its tests there; nothing here ran on the Mac).
"""
from __future__ import annotations

import contextlib
import math
from pathlib import Path

import numpy as np
import torch

from tools.trainer.model import DecoderConfig, EventDecoder

TORCH_BACKEND_NAME = "torch-decoder"


def _grad_scaler(enabled: bool):
    try:
        return torch.amp.GradScaler("cuda", enabled=enabled)
    except (AttributeError, TypeError):                  # torch < 2.3
        return torch.cuda.amp.GradScaler(enabled=enabled)


class TorchBackend:
    name = TORCH_BACKEND_NAME

    def __init__(self, vocab_size: int, ctx: int, seed: int = 0, d_model: int = 256, n_layers: int = 4,
                 n_heads: int = 4, n_kv_heads: int = 2, ffn_mult: float = 8.0 / 3.0, qk_norm: bool = True,
                 init_std: float = 0.02, rope_base: float = 10000.0, weight_decay: float = 0.1, clip: float = 1.0,
                 z_loss: float = 1e-4, betas=(0.9, 0.95), eps: float = 1e-8, device: str | None = None,
                 precision: str = "auto"):
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        cuda = self.device.type == "cuda"
        if precision == "auto":
            precision = ("bf16" if torch.cuda.is_bf16_supported() else "fp16") if cuda else "fp32"
        if precision not in ("bf16", "fp16", "fp32") or (precision != "fp32" and not cuda):
            raise ValueError(f"precision {precision!r} on {self.device.type}")
        self.precision = precision
        self.amp_dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": None}[precision]
        torch.manual_seed(int(seed))                    # model init is a function of the seed
        cfg = DecoderConfig(vocab_size=vocab_size, ctx=ctx, d_model=d_model, n_layers=n_layers, n_heads=n_heads,
                            n_kv_heads=n_kv_heads, ffn_mult=ffn_mult, qk_norm=qk_norm, init_std=init_std,
                            rope_base=rope_base)
        self.model = EventDecoder(cfg).to(self.device)
        decay, no_decay = [], []
        for n, p in self.model.named_parameters():
            (decay if p.dim() >= 2 and not n.startswith("embed.") else no_decay).append(p)
        self.opt = torch.optim.AdamW([{"params": decay, "weight_decay": weight_decay},
                                      {"params": no_decay, "weight_decay": 0.0}], lr=0.0, betas=tuple(betas), eps=eps)
        self.scaler = _grad_scaler(precision == "fp16")
        self.clip, self.z = clip, z_loss
        self.opt.zero_grad(set_to_none=True)

    def _ids(self, a) -> torch.Tensor:
        return torch.from_numpy(np.ascontiguousarray(a, dtype=np.int64)).to(self.device)

    def _autocast(self):
        if self.amp_dtype is None:
            return contextlib.nullcontext()
        return torch.autocast(device_type=self.device.type, dtype=self.amp_dtype)

    def accumulate(self, batch: dict, scale: float) -> float:
        self.model.train()
        x, y = self._ids(batch["x"]), self._ids(batch["y"])
        m = torch.from_numpy(np.ascontiguousarray(batch["mask"], dtype=np.float32)).to(self.device)
        with self._autocast():
            logits = self.model(x)
        logits = logits.float()
        lse = torch.logsumexp(logits, dim=-1)
        nll = lse - logits.gather(-1, y.unsqueeze(-1)).squeeze(-1)
        loss = ((nll + self.z * lse.pow(2)) * m).sum() * scale
        self.scaler.scale(loss).backward()
        return float((nll.detach() * m).sum())

    def step(self, lr: float) -> dict:
        for g in self.opt.param_groups:
            g["lr"] = lr
        self.scaler.unscale_(self.opt)
        gn = float(torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.clip if self.clip else float("inf")))
        if self.scaler.is_enabled():
            before = self.scaler.get_scale()
            self.scaler.step(self.opt)                  # skips the update on inf/nan (fp16 overflow): normal
            self.scaler.update()
            skipped = self.scaler.get_scale() < before
            self.opt.zero_grad(set_to_none=True)
            return {"grad_norm": None if skipped else gn, "skipped": skipped}
        if not math.isfinite(gn):
            self.opt.zero_grad(set_to_none=True)
            return {"grad_norm": gn, "skipped": True}     # the monitor halts on it
        self.opt.step()
        self.opt.zero_grad(set_to_none=True)
        return {"grad_norm": gn, "skipped": False}

    @torch.no_grad()
    def eval_batch(self, batch: dict) -> dict:
        self.model.eval()
        x, y = self._ids(batch["x"]), self._ids(batch["y"])
        with self._autocast():
            logits = self.model(x)
        lp = torch.log_softmax(logits.float(), dim=-1)
        nll = -lp.gather(-1, y.unsqueeze(-1)).squeeze(-1)
        top_lp, top = lp.max(dim=-1)
        to_np = lambda t: t.double().cpu().numpy()      # noqa: E731
        return {"nll": to_np(nll), "p_top1": to_np(top_lp.exp()), "correct": to_np((top == y).float())}

    def max_attention_logit(self, batch: dict) -> float:
        self.model.eval()
        with self._autocast():
            return self.model.max_attention_logit(self._ids(batch["x"]))

    # --- persistence ------------------------------------------------------------------------------------------
    def save(self, folder: Path) -> None:
        st = {"model": self.model.state_dict(), "opt": self.opt.state_dict(), "scaler": self.scaler.state_dict(),
              "rng_cpu": torch.get_rng_state(),
              "rng_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}
        torch.save(st, Path(folder) / "backend.pt")

    def load(self, folder: Path) -> None:
        st = torch.load(Path(folder) / "backend.pt", map_location=self.device, weights_only=True)
        self.model.load_state_dict(st["model"])
        self.opt.load_state_dict(st["opt"])
        if st.get("scaler"):
            self.scaler.load_state_dict(st["scaler"])
        torch.set_rng_state(st["rng_cpu"].cpu())
        if st.get("rng_cuda") and torch.cuda.is_available():
            torch.cuda.set_rng_state_all([s.cpu() for s in st["rng_cuda"]])
        self.opt.zero_grad(set_to_none=True)

    def export(self, path: Path) -> None:
        torch.save({k: v.detach().cpu() for k, v in self.model.state_dict().items()}, Path(path))

    def describe(self) -> dict:
        gpu = torch.cuda.get_device_name(self.device) if self.device.type == "cuda" else "cpu"
        return {"gpu_type": gpu, "n_params": int(sum(p.numel() for p in self.model.parameters())),
                "backend": self.name, "precision": self.precision}


def max_abs_diff(a: Path, b: Path) -> float:
    """Largest absolute difference between two exported state dicts (inf when the keys or shapes differ)."""
    sa = torch.load(a, map_location="cpu", weights_only=True)
    sb = torch.load(b, map_location="cpu", weights_only=True)
    if sa.keys() != sb.keys() or any(sa[k].shape != sb[k].shape for k in sa):
        return float("inf")
    return max(float((sa[k].float() - sb[k].float()).abs().max()) for k in sa) if sa else 0.0
