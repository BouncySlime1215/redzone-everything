"""Job entry point for one training work unit (config, seed, step range): the command the F-03 runner calls.

Why: WORKPLAN B-03 "a documented job entry point exists that F-03's runner will call on Kaggle/Colab", and
TRAINING-PLAN section 2 (resumable units, atomic results, self-stop before the session cap, the kill-and-resume
smoke). Run from the repo root (or the code bundle's root):

    python -m tools.trainer.job train --config CFG.json --data TAG_OR_MANIFEST.json --data-root DIR --out OUT \
        [--stop-step N] [--max-hours H] [--prereg PREREG.yaml] [--run-id ID]
    python -m tools.trainer.job smoke --out OUT [--backend numpy|torch]       # kill-and-resume == uninterrupted
    python -m tools.trainer.job gbm --config CFG.json --data ... --data-root DIR --out OUT [--fields 0,4,5]

`train` resumes by itself when OUT/ckpt holds a checkpoint of the same run (same config, data, seed, cutoff,
code); a checkpoint of another run is refused, never overwritten. Outputs (all atomic): OUT/result.json,
OUT/registry_record.json (for the runner to register; the trainer never writes the ledger), OUT/model/ (weights
+ fence-stamped meta), OUT/ckpt/, OUT/train_log.jsonl, OUT/heartbeat.json.

Exit codes: 0 finished or reached --stop-step; 4 stopped on the time budget (run the same command again);
5 diverged (loss non-finite or spiking; the last good checkpoint stays); 2 refused (fence, manifest, corpus or
a checkpoint of another run). 3 is left to the runner's stall watchdog.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                       # `python tools/trainer/job.py` from the repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.trainer import checkpoint, data  # noqa: E402
from tools.trainer.loop import (STATUS_BUDGET, STATUS_DIVERGED, ResumeRefused, TrainConfig,  # noqa: E402
                                Trainer)

EXIT_OK, EXIT_REFUSED, EXIT_BUDGET, EXIT_DIVERGED = 0, 2, 4, 5
DIE_AFTER_ENV = "RZ_TRAINER_DIE_AFTER_STEP"          # test hook: the process dies after this step, unsaved

TOY_CONFIG = {
    "train_through": "20240220", "valid_from": "20240301", "seed": 3, "ctx": 24, "micro_batch": 4, "accum": 2,
    "total_steps": 24, "peak_lr": 1e-2, "warmup_frac": 0.1, "decay_frac": 0.25, "eval_every": 8,
    "eval_windows": 32, "ckpt_every": 5, "log_every": 4, "license": "synthetic",
    "model": {"backend": "numpy", "vocab": "toy", "d_model": 16, "weight_decay": 0.1, "clip": 1.0,
              "z_loss": 1e-4},
}


def vocab_of(model_cfg: dict) -> dict:
    if model_cfg.get("vocab", "v1") == "toy":
        return {"vocab_size": data.TOY_VOCAB_SIZE, "vocab_version": data.TOY_VOCAB_VERSION, "pad": 0,
                "tokens_per_event": 6, "header": 3}
    return data.vocab_v1()


def make_backend(cfg: TrainConfig, vocab_size: int):
    m = dict(cfg.model)
    kind = m.pop("backend", "torch")
    m.pop("vocab", None)
    if kind == "numpy":
        from tools.trainer.reference import NumpyBackend
        return NumpyBackend(vocab_size, cfg.ctx, seed=cfg.seed, **m)
    if kind == "torch":
        from tools.trainer.torch_backend import TorchBackend
        return TorchBackend(vocab_size, cfg.ctx, seed=cfg.seed, **m)
    raise ValueError(f"unknown backend {kind!r}")


def build(cfg_dict: dict, data_ref, data_root, out, prereg=None):
    """(Trainer, corpus) for one unit, every refusal raised before any compute."""
    cfg = TrainConfig.from_dict(cfg_dict)
    v = vocab_of(cfg.model)
    corpus = data.load_pinned_corpus(data_ref, root=data_root, vocab_size=v["vocab_size"],
                                     vocab_version=v["vocab_version"])
    train, held = data.split_walk_forward(corpus, cfg.train_through, cfg.valid_from, cfg.valid_through)
    backend = make_backend(cfg, v["vocab_size"])
    tr = Trainer(cfg, backend, train, held, data_manifest_hash=corpus.manifest_sha256, out_dir=out, pad=v["pad"],
                 tokens_per_event=v["tokens_per_event"], header=v["header"], prereg=prereg)
    return tr, corpus


def cmd_train(a) -> int:
    ManifestError = data.repo_module("redzone.lab.datamanifest").ManifestError
    FenceError = data.repo_module("redzone.lab.fence").FenceError
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg_dict = json.loads(Path(a.config).read_text())
    try:
        tr, corpus = build(cfg_dict, a.data, a.data_root, out, a.prereg)
        resumed = tr.resume(allow_code_change=a.allow_code_change)
    except (FenceError, ManifestError, data.CorpusError, ResumeRefused) as e:
        print(f"[trainer] REFUSED: {type(e).__name__}: {e}", flush=True)
        checkpoint.atomic_write_json(out / "result.json", {"status": "refused", "reason": f"{type(e).__name__}: {e}"})
        return EXIT_REFUSED
    desc = tr.backend.describe()
    print(f"[trainer] {'resumed at step ' + str(tr.step) if resumed else 'fresh start'}; {desc['backend']} on "
          f"{desc['gpu_type']}; {desc['n_params']:,} parameters; {len(tr.ws)} train windows; corpus {corpus.tag} "
          f"{corpus.manifest_sha256[:12]}; cutoff {tr.meta['training_cutoff']}", flush=True)
    die = os.environ.get(DIE_AFTER_ENV)
    res = tr.run(stop_step=a.stop_step, max_seconds=a.max_hours * 3600.0 if a.max_hours else None,
                 die_after_step=int(die) if die else None)
    if res["status"] in ("done",):
        res["export"] = tr.export(a.run_id or f"b03-{tr.cfg_hash[:10]}-s{tr.cfg.seed}", a.model_name)
    checkpoint.atomic_write_json(out / "result.json", res)
    print(f"[trainer] {res['status'].upper()} at step {res['step']}/{res['total_steps']} "
          f"eval {json.dumps({k: round(v, 4) for k, v in res['last_eval'].items() if isinstance(v, float)})}",
          flush=True)
    return {STATUS_BUDGET: EXIT_BUDGET, STATUS_DIVERGED: EXIT_DIVERGED}.get(res["status"], EXIT_OK)


def _run_train(cfg_path: Path, manifest: Path, root: Path, out: Path, env_extra: dict | None = None) -> int:
    env = {**os.environ, **(env_extra or {})}
    cmd = [sys.executable, "-m", "tools.trainer.job", "train", "--config", str(cfg_path), "--data", str(manifest),
           "--data-root", str(root), "--out", str(out)]
    return subprocess.run(cmd, cwd=str(data.TRAINER_REPO), env=env, check=False).returncode


def exports_match(a: Path, b: Path, backend: str) -> tuple[bool, str]:
    """Bitwise for numpy and torch on CPU; on a GPU, within 1e-5 absolute (TRAINING-PLAN section 2 pass test)."""
    if checkpoint.sha256_path(a) == checkpoint.sha256_path(b):
        return True, "bitwise identical"
    if backend != "torch":
        return False, "exports differ"
    import torch
    from tools.trainer.torch_backend import max_abs_diff
    d = max_abs_diff(a, b)
    tol = 1e-5 if torch.cuda.is_available() else 0.0
    return d <= tol, f"max abs difference {d:.3g} (tolerance {tol:g})"


def cmd_smoke(a) -> int:
    """The pass test before any long run, end to end through real processes: a unit killed mid-run (no save) and
    re-run equals the same unit run uninterrupted. Synthetic data; seconds on CPU."""
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = data.write_toy_corpus(out / "data")
    cfg = json.loads(json.dumps(TOY_CONFIG))
    if a.backend == "torch":
        cfg["model"] = {"backend": "torch", "vocab": "toy", "d_model": 32, "n_layers": 2, "n_heads": 4,
                        "n_kv_heads": 2, "weight_decay": 0.1, "clip": 1.0, "z_loss": 1e-4}
    cfg_path = out / "config.json"
    cfg_path.write_text(json.dumps(cfg, indent=1, sort_keys=True))
    t0 = time.monotonic()
    rc_a = _run_train(cfg_path, manifest, out / "data", out / "uninterrupted")
    rc_kill = _run_train(cfg_path, manifest, out / "data", out / "killed", {DIE_AFTER_ENV: "13"})
    rc_b = _run_train(cfg_path, manifest, out / "data", out / "killed")
    res_b = json.loads((out / "killed" / "result.json").read_text()) if (out / "killed" / "result.json").exists() else {}
    ok = rc_a == 0 and rc_kill == 9 and rc_b == 0 and res_b.get("resumed_from") == 10
    same, how = (False, "a run failed")
    if ok:
        same, how = exports_match(out / "uninterrupted" / "model" / "model.bin", out / "killed" / "model" / "model.bin",
                                  a.backend)
    verdict = "SMOKE PASS" if ok and same else "SMOKE FAIL"
    print(f"[smoke] {verdict}: exit codes {rc_a}/{rc_kill}/{rc_b}, resumed from {res_b.get('resumed_from')}, "
          f"{how}, {time.monotonic() - t0:.1f} s", flush=True)
    return EXIT_OK if verdict == "SMOKE PASS" else 1


def cmd_gbm(a) -> int:
    """Fit the GBM baseline per field on the run's training games and score it on the held-out games."""
    from tools.trainer import gbm
    cfg = TrainConfig.from_dict(json.loads(Path(a.config).read_text()))
    v = vocab_of(cfg.model)
    corpus = data.load_pinned_corpus(a.data, root=a.data_root, vocab_size=v["vocab_size"],
                                     vocab_version=v["vocab_version"])
    train, held = data.split_walk_forward(corpus, cfg.train_through, cfg.valid_from, cfg.valid_through)
    sizes = gbm.FIELD_VOCAB_V1 if cfg.model.get("vocab", "v1") != "toy" else (data.TOY_FIELD_IDS,) * 6
    out = {"corpus": corpus.tag, "data_manifest_hash": corpus.manifest_sha256, "fields": {}}
    for f in [int(x) for x in a.fields.split(",")]:
        Xtr, ytr = gbm.field_examples(train, f, k_prev=a.k_prev, max_rows=a.max_rows, seed=cfg.seed)
        Xte, yte = gbm.field_examples(held, f, k_prev=a.k_prev)
        m = gbm.GBMFieldBaseline(f, sizes[f], seed=cfg.seed).fit(Xtr, ytr)
        out["fields"][str(f)] = {"nll": m.nll(Xte, yte), "train_rows": int(len(ytr)), "test_rows": int(len(yte)),
                                 "head_classes": int(len(m.head))}
        print(f"[gbm] field {f}: held-out NLL {out['fields'][str(f)]['nll']:.4f} nats", flush=True)
    Path(a.out).mkdir(parents=True, exist_ok=True)
    checkpoint.atomic_write_json(Path(a.out) / "gbm_result.json", out)
    return EXIT_OK


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.trainer.job", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--config", required=True)
    t.add_argument("--data", required=True, help="manifest tag or path to a .manifest.json")
    t.add_argument("--data-root", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--stop-step", type=int, default=None, help="end of this unit's step range")
    t.add_argument("--max-hours", type=float, default=None, help="self-stop budget (about 85%% of the session cap)")
    t.add_argument("--prereg", default=None)
    t.add_argument("--run-id", default=None)
    t.add_argument("--model-name", default="b03-event-decoder")
    t.add_argument("--allow-code-change", action="store_true", help="resume although the trainer code changed")
    s = sub.add_parser("smoke")
    s.add_argument("--out", required=True)
    s.add_argument("--backend", choices=("numpy", "torch"), default="numpy")
    g = sub.add_parser("gbm")
    g.add_argument("--config", required=True)
    g.add_argument("--data", required=True)
    g.add_argument("--data-root", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--fields", default="0,1,2,3,4,5")
    g.add_argument("--k-prev", type=int, default=2)
    g.add_argument("--max-rows", type=int, default=200_000)
    a = p.parse_args(argv)
    return {"train": cmd_train, "smoke": cmd_smoke, "gbm": cmd_gbm}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
