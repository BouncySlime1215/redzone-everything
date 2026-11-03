"""The local model: Qwen3 1.7B (4-bit, MLX) on this Mac, with this project's LoRA adapter when
present. No network, no account, no per-call cost. Loaded once; ~1 GB of memory.

Greedy decoding and a short answer budget: the answer is one small JSON object, so a choice is
deterministic for a given status and takes about a second.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_MODEL = "mlx-community/Qwen3-1.7B-4bit"
ADAPTER = Path(__file__).resolve().parent / "model" / "adapter"


class LocalModel:
    def __init__(self, model: str = DEFAULT_MODEL, adapter: str | Path | None = None, max_tokens: int = 96):
        from mlx_lm import load

        from redzone.governor import apply_mlx_limits, load_options
        # Cap MLX before the weights load: the RAM budget for local models (governor.models_mb).
        apply_mlx_limits(float(load_options()["models_mb"]))
        self.max_tokens = max_tokens
        if adapter is not None and not (Path(adapter) / "adapters.safetensors").exists():
            adapter = None
        self.model, self.tok = load(model, adapter_path=str(adapter) if adapter else None)
        self.adapter = adapter

    def chat(self, messages: list[dict]) -> str:
        from mlx_lm import generate
        try:
            prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                                  enable_thinking=False)
        except TypeError:   # a template without the thinking switch
            prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        return generate(self.model, self.tok, prompt=prompt, max_tokens=self.max_tokens, verbose=False)
