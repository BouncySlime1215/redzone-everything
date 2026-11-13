"""Shared toy fixtures for the trainer tests: one synthetic pinned corpus and a Trainer around any backend.

Why: the B-03 tests run twice, against the numpy reference in CI (tests/test_trainer_*.py) and against the torch
backend on the GPU runtime (tools/trainer/tests/); one builder keeps the two suites testing the same thing.
"""
from __future__ import annotations

from pathlib import Path

from tools.trainer import data
from tools.trainer.loop import TrainConfig, Trainer

TOY_GIT_SHA = "0" * 39 + "1"      # fixed so tests do not depend on the checkout


def toy_corpus(root: Path, **kw) -> data.Corpus:
    manifest = data.write_toy_corpus(root, **kw)
    return data.load_pinned_corpus(manifest, root=root, vocab_size=data.TOY_VOCAB_SIZE,
                                   vocab_version=data.TOY_VOCAB_VERSION)


def toy_cfg(**over) -> TrainConfig:
    base = {"train_through": "20240220", "valid_from": "20240301", "seed": 5, "ctx": 16, "micro_batch": 4,
            "accum": 1, "total_steps": 12, "peak_lr": 1e-2, "warmup_frac": 0.1, "decay_frac": 0.25,
            "eval_every": 0, "eval_windows": 16, "ckpt_every": 1000, "log_every": 1000, "license": "synthetic",
            "model": {"backend": "numpy", "vocab": "toy"}}
    base.update(over)
    return TrainConfig.from_dict(base)


def toy_trainer(corpus: data.Corpus, out: Path, backend, cfg: TrainConfig) -> Trainer:
    train, held = data.split_walk_forward(corpus, cfg.train_through, cfg.valid_from, cfg.valid_through)
    return Trainer(cfg, backend, train, held, data_manifest_hash=corpus.manifest_sha256, out_dir=out,
                   code_hash="c" * 64, git_sha=TOY_GIT_SHA)
