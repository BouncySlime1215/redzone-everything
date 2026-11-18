"""Numpy reference backend for the training loop: a tiny causal language model with hand-written gradients.

Why: WORKPLAN B-03 tests (initial loss near ln(vocab), overfit one batch, causality, gradient-accumulation
equivalence, save-resume equivalence, spike monitor) must pass in seconds on CPU, and neither the Mac's venv nor
CI has torch (requirements-ci.txt). This backend drives the same loop, data order, schedule, checkpoints and
fence checks as the torch backend, in float64, so every piece except the torch model itself is tested in CI;
the torch backend repeats the same tests on the GPU runtime (tools/trainer/tests).

Model (CausalMeanLM): h_t = mean(E[x_0..x_t]) + P[t]; logits_t = h_t W + b. It is causal by construction (h_t
reads only x_0..x_t), small, and has exact gradients (checked against finite differences in the tests).
Optimizer: AdamW (beta 0.9/0.95, eps 1e-8), decoupled weight decay on W only (no decay on embeddings or bias,
TRAINING-PLAN 3.2), global-norm clipping, z-loss. Also: a numpy causal softmax attention used by the causality
test of the mask the torch decoder relies on.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from tools.trainer.metrics import per_target

REF_PARAMS = ("E", "P", "W", "b")
REF_DECAYED = ("W",)


def save_arrays(path: Path, arrays: dict, extra: dict | None = None) -> None:
    """Deterministic container: one JSON header line (names, dtypes, shapes, extra) then the raw bytes in order.
    Same arrays, same bytes (np.savez stamps the clock into its zip entries)."""
    names = sorted(arrays)
    head = {"arrays": [[n, str(arrays[n].dtype), list(arrays[n].shape)] for n in names], "extra": extra or {}}
    with open(path, "wb") as f:
        f.write((json.dumps(head, sort_keys=True) + "\n").encode())
        for n in names:
            f.write(np.ascontiguousarray(arrays[n]).tobytes())


def load_arrays(path: Path) -> tuple[dict, dict]:
    with open(path, "rb") as f:
        head = json.loads(f.readline())
        out = {}
        for n, dt, shape in head["arrays"]:
            count = int(np.prod(shape)) if shape else 1
            out[n] = np.frombuffer(f.read(count * np.dtype(dt).itemsize), dtype=dt).reshape(shape).copy()
    return out, head["extra"]


def causal_mask(T: int) -> np.ndarray:
    """True where query t may attend to key s (s <= t)."""
    return np.tril(np.ones((T, T), dtype=bool))


def causal_attention(q: np.ndarray, k: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Softmax attention [.., T, d] with the causal mask (what scaled_dot_product_attention(is_causal=True) does)."""
    T = q.shape[-2]
    s = q @ np.swapaxes(k, -1, -2) / math.sqrt(q.shape[-1])
    s = np.where(causal_mask(T), s, -np.inf)
    s = s - s.max(axis=-1, keepdims=True)
    w = np.exp(s)
    w /= w.sum(axis=-1, keepdims=True)
    return w @ v


class NumpyBackend:
    name = "numpy-reference"

    def __init__(self, vocab_size: int, ctx: int, d_model: int = 32, seed: int = 0, init_std: float = 0.02,
                 weight_decay: float = 0.1, clip: float = 1.0, z_loss: float = 1e-4, betas=(0.9, 0.95),
                 eps: float = 1e-8):
        rng = np.random.default_rng([int(seed), 7919])
        V, T, d = vocab_size, ctx, d_model
        self.V, self.T, self.d = V, T, d
        self.p = {"E": rng.normal(0, init_std, (V, d)), "P": rng.normal(0, init_std, (T, d)),
                  "W": rng.normal(0, init_std, (d, V)), "b": np.zeros(V)}
        self.wd, self.clip, self.z, self.betas, self.eps = weight_decay, clip, z_loss, tuple(betas), eps
        self.m = {k: np.zeros_like(v) for k, v in self.p.items()}
        self.v = {k: np.zeros_like(v) for k, v in self.p.items()}
        self.t = 0
        self.zero_grad()

    # --- model ------------------------------------------------------------------------------------------------
    def _hidden(self, x: np.ndarray) -> np.ndarray:
        T = x.shape[1]
        e = self.p["E"][x]
        c = np.cumsum(e, axis=1) / np.arange(1, T + 1)[None, :, None]
        return c + self.p["P"][:T][None]

    def logits(self, x: np.ndarray) -> np.ndarray:
        return self._hidden(x) @ self.p["W"] + self.p["b"]

    def zero_grad(self) -> None:
        self.g = {k: np.zeros_like(v) for k, v in self.p.items()}

    def accumulate(self, batch: dict, scale: float) -> float:
        """Add the gradient of scale * sum over real targets of (nll + z * logsumexp^2). Returns the nll sum."""
        x, y, mask = batch["x"], batch["y"], batch["mask"]
        B, T = x.shape
        h = self._hidden(x)
        lg = h @ self.p["W"] + self.p["b"]
        mx = lg.max(axis=-1, keepdims=True)
        ex = np.exp(lg - mx)
        se = ex.sum(axis=-1, keepdims=True)
        lse = (np.log(se) + mx)[..., 0]
        prob = ex / se
        nll = lse - np.take_along_axis(lg, y[..., None], axis=-1)[..., 0]
        w = (mask * scale)[..., None]
        dl = prob * (1.0 + 2.0 * self.z * lse[..., None])
        np.put_along_axis(dl, y[..., None], np.take_along_axis(dl, y[..., None], axis=-1) - 1.0, axis=-1)
        dl *= w
        self.g["W"] += h.reshape(-1, self.d).T @ dl.reshape(-1, self.V)
        self.g["b"] += dl.sum(axis=(0, 1))
        dh = dl @ self.p["W"].T
        self.g["P"][:T] += dh.sum(axis=0)
        gc = dh / np.arange(1, T + 1)[None, :, None]
        de = np.flip(np.cumsum(np.flip(gc, axis=1), axis=1), axis=1)
        np.add.at(self.g["E"], x, de)
        return float((nll * mask).sum())

    def step(self, lr: float) -> dict:
        """Clip, AdamW update, zero the gradients. Returns the pre-clip global gradient norm."""
        gn = math.sqrt(sum(float((g * g).sum()) for g in self.g.values()))
        if not math.isfinite(gn):
            self.zero_grad()
            return {"grad_norm": gn, "skipped": True}
        f = min(1.0, self.clip / (gn + 1e-12)) if self.clip else 1.0
        b1, b2 = self.betas
        self.t += 1
        for k in REF_PARAMS:
            g = self.g[k] * f
            self.m[k] = b1 * self.m[k] + (1 - b1) * g
            self.v[k] = b2 * self.v[k] + (1 - b2) * g * g
            mh = self.m[k] / (1 - b1 ** self.t)
            vh = self.v[k] / (1 - b2 ** self.t)
            if k in REF_DECAYED and self.wd:
                self.p[k] = self.p[k] * (1 - lr * self.wd)
            self.p[k] = self.p[k] - lr * mh / (np.sqrt(vh) + self.eps)
        self.zero_grad()
        return {"grad_norm": gn, "skipped": False}

    def eval_batch(self, batch: dict) -> dict:
        return per_target(self.logits(batch["x"]), batch["y"])

    # --- persistence ----------------------------------------------------------------------------------------
    def save(self, folder: Path) -> None:
        arrays = {f"p.{k}": v for k, v in self.p.items()}
        arrays.update({f"m.{k}": v for k, v in self.m.items()})
        arrays.update({f"v.{k}": v for k, v in self.v.items()})
        save_arrays(Path(folder) / "backend.bin", arrays, {"t": self.t, "name": self.name})

    def load(self, folder: Path) -> None:
        arrays, extra = load_arrays(Path(folder) / "backend.bin")
        for k in REF_PARAMS:
            self.p[k], self.m[k], self.v[k] = arrays[f"p.{k}"], arrays[f"m.{k}"], arrays[f"v.{k}"]
        self.t = int(extra["t"])
        self.zero_grad()

    def export(self, path: Path) -> None:
        save_arrays(Path(path), dict(self.p), {"name": self.name})

    def describe(self) -> dict:
        return {"gpu_type": "cpu", "n_params": int(sum(v.size for v in self.p.values())), "backend": self.name}
