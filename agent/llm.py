"""The local model: Qwen3 1.7B (4-bit, MLX) on this Mac, with this project's LoRA adapter, served
by agent/llm_server.py so every job (watchdog, game reasoner) shares ONE copy of the base. No
network, no account, no per-call cost.

LocalModel(adapter="watchdog") asks the local server (starting it inside this process when none
answers on 127.0.0.1:8779); LocalModel(model, adapter_path, inproc=True) loads its own copy the
old way (evaluate.py on MLX). Greedy decoding and a short answer budget: the answer is one small
JSON object, so a choice is deterministic for a given status and takes about a second.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_MODEL = "mlx-community/Qwen3-1.7B-4bit"
MODELS = Path(__file__).resolve().parent / "model"
ADAPTER = MODELS / "watchdog"


class LocalModel:
    def __init__(self, model: str = DEFAULT_MODEL, adapter: str | Path | None = None, max_tokens: int = 96,
                 inproc: bool | None = None):
        self.max_tokens = max_tokens
        a = Path(adapter) if adapter else None
        if a is not None and not (a / "adapters.safetensors").exists():
            a = None
        self.adapter = a
        # Served when the adapter lives in agent/model/<name>/ and the base is the default one.
        self.inproc = inproc if inproc is not None else not (model == DEFAULT_MODEL and (a is None or a.parent == MODELS))
        if self.inproc:
            from mlx_lm import load
            self.model, self.tok = load(model, adapter_path=str(a) if a else None)
        else:
            from agent import llm_server
            llm_server.ensure_server()
            self._srv = llm_server

    def chat(self, messages: list[dict]) -> str:
        if not self.inproc:
            return self._srv.call("/generate", {"adapter": self.adapter.name if self.adapter else "",
                                                "messages": messages, "max_tokens": self.max_tokens})["text"]
        from mlx_lm import generate
        try:
            prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False,
                                                  enable_thinking=False)
        except TypeError:   # a template without the thinking switch
            prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        return generate(self.model, self.tok, prompt=prompt, max_tokens=self.max_tokens, verbose=False)
