"""agent/kaggle/dequant.py on an MLX-style 4-bit checkpoint whose scales/biases are bf16.

Kaggle V2 died here: safetensors.numpy.load_file can't read bfloat16, and mlx-community's
Qwen3-1.7B-4bit stores scales, biases and norms in bf16.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from agent.kaggle.dequant import convert, load_file, unpack

torch = pytest.importorskip("torch")
st_torch = pytest.importorskip("safetensors.torch")


def _pack(vals: np.ndarray) -> np.ndarray:
    """uint8 4-bit values (..., n) -> uint32 (..., n/8), low nibble first (MLX's order)."""
    v = vals.reshape(*vals.shape[:-1], -1, 8).astype(np.uint32)
    return (v << (np.arange(8, dtype=np.uint32) * 4)).sum(-1, dtype=np.uint64).astype(np.uint32)


def _bf16(x: np.ndarray):
    return torch.tensor(x, dtype=torch.float32).to(torch.bfloat16)


def test_bf16_scales_dequantize_exactly(tmp_path):
    if not hasattr(torch, "uint32"):
        pytest.skip("this torch can't write uint32 safetensors")
    rng = np.random.default_rng(0)
    rows, cols, gs = 4, 128, 64
    vals = rng.integers(0, 16, (rows, cols), dtype=np.uint8)
    q = _pack(vals)
    assert (unpack(q, 4) == vals).all()
    scales = _bf16(rng.normal(0, 0.05, (rows, cols // gs)))
    biases = _bf16(rng.normal(0, 0.05, (rows, cols // gs)))
    norm = _bf16(rng.normal(1, 0.1, (cols,)))
    src, out = tmp_path / "mlx", tmp_path / "deq"
    src.mkdir()
    st_torch.save_file({"layer.weight": torch.from_numpy(q.view(np.int32)).view(torch.uint32),
                        "layer.scales": scales, "layer.biases": biases, "norm.weight": norm},
                       str(src / "model.safetensors"))
    (src / "config.json").write_text(json.dumps({"quantization": {"group_size": gs, "bits": 4}}))
    (src / "tokenizer.json").write_text("{}")

    from safetensors.numpy import load_file as np_load
    with pytest.raises(TypeError):                 # the V2 crash, reproduced
        np_load(str(src / "model.safetensors"))
    t = load_file(str(src / "model.safetensors"))
    assert t["layer.scales"].dtype == np.float32 and t["layer.weight"].dtype == np.uint32
    assert np.array_equal(t["layer.scales"], scales.float().numpy())       # bf16 -> f32 is exact

    info = convert(src, out)
    assert info["dequantized"] == 1
    got = st_torch.load_file(str(out / "model.safetensors"))
    s, b = scales.float().numpy(), biases.float().numpy()
    want = (vals.reshape(rows, -1, gs) * s[..., None] + b[..., None]).reshape(rows, cols)
    assert got["layer.weight"].dtype == torch.float16
    np.testing.assert_allclose(got["layer.weight"].float().numpy(), want, rtol=1e-3, atol=1e-3)
    np.testing.assert_allclose(got["norm.weight"].float().numpy(), norm.float().numpy(), rtol=1e-3)
    assert "quantization" not in json.loads((out / "config.json").read_text())
    assert (out / "tokenizer.json").exists()
