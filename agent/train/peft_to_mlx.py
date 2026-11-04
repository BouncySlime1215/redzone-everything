"""Turn a PEFT LoRA adapter (agent/train/train_peft.py) into mlx_lm's adapter format, the one the
Mac loads: adapters.safetensors + adapter_config.json.

PEFT:    y = W x + (alpha / r) * B(A(x)),   lora_A.weight: (r, in),  lora_B.weight: (out, r)
mlx_lm:  y = W x + scale * ((x @ lora_a) @ lora_b),   lora_a: (in, r),  lora_b: (r, out)
so lora_a = A^T, lora_b = B^T, scale = alpha / r, and the key
  base_model.model.model.layers.20.self_attn.q_proj.lora_A.weight
becomes
  model.layers.20.self_attn.q_proj.lora_a
mlx_lm puts LoRA on the LAST num_layers layers, so the PEFT adapter must cover a contiguous run
ending at the last layer (train_peft.py does).

    python agent/train/peft_to_mlx.py <peft dir> agent/model/watchdog
    python agent/train/peft_to_mlx.py <peft dir> agent/model/watchdog --verify 20 \\
        --torch-base <dequantized dir> --mlx-base <mlx-community/Qwen3-1.7B-4bit dir>

--verify N proves the conversion: for N held-out prompts, the merged PEFT model (PyTorch, on the
dequantized base) writes its greedy answer, then mlx_lm with the 4-bit base + converted adapter
scores that same answer in one forward pass. If MLX's argmax matches every answer token, MLX's
greedy decoding writes the identical answer. Also reports the largest logit gap.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

KEY = re.compile(r"^base_model\.model\.(model\.layers\.(\d+)\.(.+))\.lora_([AB])\.weight$")


def convert(peft_dir: Path, out: Path, n_layers_total: int | None = None) -> dict:
    from safetensors.numpy import save_file
    from safetensors.torch import load_file
    cfg = json.loads((peft_dir / "adapter_config.json").read_text())
    sd = load_file(str(peft_dir / "adapter_model.safetensors"))
    out_w: dict[str, np.ndarray] = {}
    layers, keys = set(), set()
    for k, v in sd.items():
        m = KEY.match(k)
        if not m:
            raise ValueError(f"unexpected PEFT key {k}")
        path, layer, mod, ab = m.groups()
        layers.add(int(layer))
        keys.add(mod)
        out_w[f"{path}.lora_{ab.lower()}"] = v.float().numpy().T.copy()   # A^T: (in, r); B^T: (r, out)
    layers = sorted(layers)
    if layers != list(range(layers[0], layers[-1] + 1)):
        raise ValueError(f"LoRA layers {layers} aren't contiguous")
    if n_layers_total is not None and layers[-1] != n_layers_total - 1:
        raise ValueError(f"LoRA must end at the last layer ({n_layers_total - 1}), got {layers[-1]}")
    r = int(cfg["r"])
    mlx_cfg = {"fine_tune_type": "lora", "num_layers": len(layers),
               "lora_parameters": {"rank": r, "scale": float(cfg["lora_alpha"]) / r,
                                   "dropout": 0.0, "keys": sorted(keys)},
               "model": "mlx-community/Qwen3-1.7B-4bit", "source": "agent/train/train_peft.py via peft_to_mlx.py"}
    out.mkdir(parents=True, exist_ok=True)
    save_file(out_w, str(out / "adapters.safetensors"))
    (out / "adapter_config.json").write_text(json.dumps(mlx_cfg, indent=1) + "\n")
    return mlx_cfg


def verify(peft_dir: Path, out: Path, torch_base: str, mlx_base: str, n: int, split: str) -> dict:
    import mlx.core as mx
    import torch
    from mlx_lm import load
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    data = Path(__file__).resolve().parent / "data" / f"{split}.jsonl"
    rows = [json.loads(line)["messages"][:2] for line in data.open()][:n]
    tok = AutoTokenizer.from_pretrained(torch_base)
    tm = PeftModel.from_pretrained(AutoModelForCausalLM.from_pretrained(torch_base, dtype=torch.bfloat16),
                                   str(peft_dir)).merge_and_unload().eval()
    mm, mtok = load(mlx_base, adapter_path=str(out))
    res = []
    for i, msgs in enumerate(rows):
        prompt = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        p_ids = tok(prompt, add_special_tokens=False, return_tensors="pt").input_ids
        with torch.no_grad():
            g = tm.generate(p_ids, max_new_tokens=96, do_sample=False, pad_token_id=tok.pad_token_id)
            full = g[0].tolist()
            t_logits = tm(g).logits[0].float().numpy()
        ans = full[p_ids.shape[1]:]
        assert mtok.encode(prompt, add_special_tokens=False) == p_ids[0].tolist(), "tokenizers differ"
        m_logits = np.array(mm(mx.array([full]))[0].astype(mx.float32))
        lo = p_ids.shape[1] - 1
        m_arg = m_logits[lo:lo + len(ans)].argmax(-1).tolist()
        gap = float(np.abs(m_logits[lo:lo + len(ans)] - t_logits[lo:lo + len(ans)]).max())
        same = m_arg == ans
        res.append({"i": i, "answer": tok.decode(ans, skip_special_tokens=True), "mlx_same_greedy": same,
                    "tokens": len(ans), "max_logit_gap": round(gap, 3)})
        print(json.dumps(res[-1]), flush=True)
    ok = sum(r["mlx_same_greedy"] for r in res)
    return {"prompts": len(res), "identical_greedy_answers": ok,
            "max_logit_gap": max(r["max_logit_gap"] for r in res)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("peft_dir")
    ap.add_argument("out")
    ap.add_argument("--layers-total", type=int, default=28)
    ap.add_argument("--verify", type=int, default=0)
    ap.add_argument("--torch-base")
    ap.add_argument("--mlx-base")
    ap.add_argument("--split", default="test")
    a = ap.parse_args()
    cfg = convert(Path(a.peft_dir), Path(a.out), a.layers_total)
    print(json.dumps(cfg))
    if a.verify:
        if not (a.torch_base and a.mlx_base):
            sys.exit("--verify needs --torch-base and --mlx-base")
        print(json.dumps(verify(Path(a.peft_dir), Path(a.out), a.torch_base, a.mlx_base, a.verify, a.split)))


if __name__ == "__main__":
    main()
