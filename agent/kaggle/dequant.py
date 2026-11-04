"""Dequantize the Mac's own base (mlx-community/Qwen3-1.7B-4bit) with numpy, no MLX needed.

The student adapters are trained on exactly the weights the Mac runs: MLX's affine 4-bit
quantization, w = q * scale + bias per group of `group_size` input columns, eight 4-bit values per
uint32, low nibble first. Output: a plain Hugging Face checkpoint (float16 safetensors + the
config without "quantization") that transformers loads as Qwen3ForCausalLM.

    python agent/kaggle/dequant.py <mlx 4-bit dir> <out dir>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np


def unpack(q: np.ndarray, bits: int) -> np.ndarray:
    """uint32 (..., n * bits / 32) -> uint8 values (..., n), low bits first (MLX's order)."""
    per = 32 // bits
    mask = (1 << bits) - 1
    shifts = (np.arange(per, dtype=np.uint32) * bits)
    vals = (q[..., None] >> shifts) & mask
    return vals.reshape(*q.shape[:-1], q.shape[-1] * per).astype(np.uint8)


def dequantize(q: np.ndarray, scales: np.ndarray, biases: np.ndarray, group_size: int = 64,
               bits: int = 4) -> np.ndarray:
    v = unpack(q, bits).astype(np.float32)
    g = v.reshape(*v.shape[:-1], -1, group_size)
    w = g * scales.astype(np.float32)[..., None] + biases.astype(np.float32)[..., None]
    return w.reshape(v.shape)


_DT = {"F64": np.float64, "F32": np.float32, "F16": np.float16, "BF16": np.uint16, "I64": np.int64,
       "I32": np.int32, "I16": np.int16, "I8": np.int8, "U64": np.uint64, "U32": np.uint32, "U16": np.uint16,
       "U8": np.uint8, "BOOL": np.bool_}


def load_file(path: str) -> dict[str, np.ndarray]:
    """Read a .safetensors file into numpy, bf16 included (numpy has no bfloat16, and
    safetensors.numpy.load_file raises "data type 'bfloat16' not understood"; mlx-community stores
    the 4-bit scales/biases in bf16). bf16 -> float32 is exact: the bf16 bits are the top half of
    a float32. Pure numpy, so no torch dtype support (uint32) is needed either."""
    import struct
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(n))
        blob = f.read()
    header.pop("__metadata__", None)
    out = {}
    for k, h in header.items():
        a, b = h["data_offsets"]
        if h["dtype"] not in _DT:
            raise ValueError(f"{path}: {k} has unsupported dtype {h['dtype']}")
        v = np.frombuffer(blob[a:b], dtype=np.dtype(_DT[h["dtype"]]).newbyteorder("<")).reshape(h["shape"])
        if h["dtype"] == "BF16":
            v = (v.astype(np.uint32) << 16).view(np.float32)
        out[k] = v
    return out


def convert(src: Path, out: Path, dtype=np.float16) -> dict:
    from safetensors.numpy import save_file
    cfg = json.loads((src / "config.json").read_text())
    qc = cfg.pop("quantization", None) or {}
    cfg.pop("quantization_config", None)
    gs, bits = int(qc.get("group_size", 64)), int(qc.get("bits", 4))
    tensors: dict[str, np.ndarray] = {}
    for f in sorted(src.glob("*.safetensors")):
        tensors.update(load_file(str(f)))
    out_t, n_q = {}, 0
    for k, v in tensors.items():
        if k.endswith(".scales") or k.endswith(".biases"):
            continue
        base = k[: -len(".weight")] if k.endswith(".weight") else None
        if base and f"{base}.scales" in tensors:
            over = qc.get(base) if isinstance(qc.get(base), dict) else {}
            w = dequantize(v, tensors[f"{base}.scales"], tensors[f"{base}.biases"],
                           int(over.get("group_size", gs)), int(over.get("bits", bits)))
            out_t[k] = w.astype(dtype)
            n_q += 1
        else:
            out_t[k] = v.astype(dtype) if v.dtype.kind == "f" else v
    out.mkdir(parents=True, exist_ok=True)
    save_file(out_t, str(out / "model.safetensors"), metadata={"format": "pt"})
    cfg["torch_dtype"] = "float16"
    (out / "config.json").write_text(json.dumps(cfg, indent=1))
    for f in src.iterdir():
        if f.name.startswith("tokenizer") or f.name in ("vocab.json", "merges.txt", "special_tokens_map.json",
                                                         "added_tokens.json", "generation_config.json"):
            shutil.copy(f, out / f.name)
    return {"tensors": len(out_t), "dequantized": n_q, "group_size": gs, "bits": bits}


if __name__ == "__main__":
    print(json.dumps(convert(Path(sys.argv[1]), Path(sys.argv[2]))))
