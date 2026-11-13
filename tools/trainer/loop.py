"""The training loop, independent of the model library: accumulation, WSD, evaluation, spike monitor, resumable
checkpoints stamped with the training cutoff, self-stop on a time budget.

Why: WORKPLAN B-03 and TRAINING-PLAN section 2 "Resume design": a work unit is (config, seed, step range); data
order is a function of (seed, epoch); a checkpoint holds model, optimiser, step, epoch, samples seen, RNG states
and the hashes; the pass test before any long run is "train 2N steps == train N, save, resume, train N". The same
loop drives the numpy reference backend (CI, the Mac, seconds) and the torch backend (Kaggle or Colab), so the
resume, accumulation and fence logic is tested without a GPU.

A backend implements: accumulate(batch, scale) -> nll sum; step(lr) -> {grad_norm, skipped}; eval_batch(batch) ->
{nll, p_top1, correct}; save(folder); load(folder); export(path); describe() -> {gpu_type, n_params, backend}.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

from tools.trainer import checkpoint, runmeta
from tools.trainer.data import Cursor, WindowSet, epoch_order, take
from tools.trainer.metrics import EvalAggregator
from tools.trainer.monitor import SpikeMonitor
from tools.trainer.schedule import wsd_lr

STATUS_DONE, STATUS_STOPPED, STATUS_BUDGET, STATUS_DIVERGED = "done", "stopped", "budget", "diverged"
EVAL_ORDER_SEED = 0          # every run evaluates the same held-out windows, whatever its own seed


class ResumeRefused(RuntimeError):
    """The checkpoint on disk belongs to a different run (config, data, seed, cutoff or code changed)."""


@dataclass
class TrainConfig:
    train_through: str                 # training cutoff night (inclusive); stamped into every checkpoint
    valid_from: str                    # first held-out night
    valid_through: str | None = None
    seed: int = 0
    ctx: int = 64                      # tokens per window (one game per window)
    micro_batch: int = 4
    accum: int = 1                     # optimizer step = micro_batch * accum windows
    total_steps: int = 100
    peak_lr: float = 3e-3
    warmup_frac: float = 0.02
    decay_frac: float = 0.15
    final_lr_frac: float = 0.0
    decay_shape: str = "sqrt"
    decay_start: int | None = None     # a branch: cool down from this step instead
    eval_every: int = 50
    eval_windows: int = 64
    ckpt_every: int = 50
    ckpt_minutes: float = 15.0
    keep_ckpts: int = 2
    log_every: int = 10
    license: str = ""
    model: dict = field(default_factory=dict)     # backend-specific: kind, sizes, weight_decay, clip, z_loss ...
    spike: dict = field(default_factory=dict)     # SpikeMonitor overrides

    @classmethod
    def from_dict(cls, d: dict) -> "TrainConfig":
        known = {f.name for f in fields(cls)}
        extra = sorted(set(d) - known)
        if extra:
            raise ValueError(f"unknown config field(s): {', '.join(extra)}")
        return cls(**d)

    def lr(self, step: int) -> float:
        return wsd_lr(step, self.total_steps, self.peak_lr, self.warmup_frac, self.decay_frac, self.final_lr_frac,
                      self.decay_shape, self.decay_start)


class Trainer:
    def __init__(self, cfg: TrainConfig, backend, train_docs: list, eval_docs: list, *, data_manifest_hash: str,
                 out_dir, pad: int = 0, tokens_per_event: int = 6, header: int = 3, prereg: str | None = None,
                 code_hash: str | None = None, git_sha: str | None = None):
        self.cfg, self.backend, self.out = cfg, backend, Path(out_dir)
        self.ws = WindowSet(train_docs, cfg.ctx, pad, tokens_per_event, header)
        self.eval_ws = WindowSet(eval_docs, cfg.ctx, pad, tokens_per_event, header) if eval_docs else None
        self.eval_ids = (np.sort(epoch_order(EVAL_ORDER_SEED, 0, len(self.eval_ws))[: cfg.eval_windows])
                         if self.eval_ws is not None and len(self.eval_ws) else np.zeros(0, np.int64))
        self.cfg_hash = runmeta.config_hash(asdict(cfg))
        self.prereg, self.preregistered = runmeta.prereg_hash(prereg)
        self.meta = runmeta.model_meta(
            training_cutoff=cfg.train_through, train_nights=[d.night for d in train_docs],
            data_manifest_hash=data_manifest_hash, cfg_hash=self.cfg_hash,
            code=code_hash or runmeta.code_hash(), git=git_sha if git_sha is not None else runmeta.git_sha(),
            seed=cfg.seed, step_range=[0, cfg.total_steps], license=cfg.license)
        if max(d.night for d in train_docs) > self.meta["training_cutoff"].replace("-", ""):
            raise ValueError("a training game is after train_through")
        runmeta.check_model_meta(self.meta, [d.night for d in eval_docs])    # FenceError before any compute
        self.step, self.cursor, self.samples, self.tokens = 0, Cursor(), 0, 0
        self.monitor = SpikeMonitor(**cfg.spike)
        self.hours = 0.0
        self.last_eval: dict = {}
        self.last_train: dict = {}
        self.resumed_from: int | None = None

    # --- identity --------------------------------------------------------------------------------------------
    def identity(self) -> dict:
        m = self.meta
        return {"config_hash": self.cfg_hash, "data_manifest_hash": m["data_manifest_hash"], "seed": self.cfg.seed,
                "training_cutoff": m["training_cutoff"], "train_nights_sha256": m["train_nights_sha256"],
                "code_hash": m["code_hash"], "backend": self.backend.describe()["backend"],
                "precision": self.backend.describe().get("precision", "float64")}

    def _state(self) -> dict:
        return {"identity": self.identity(), "config": asdict(self.cfg), "step": self.step,
                "cursor": [self.cursor.epoch, self.cursor.pos], "samples_seen": self.samples,
                "tokens_seen": self.tokens, "monitor": self.monitor.state(), "hours": self.hours,
                "last_eval": self.last_eval, "last_train": self.last_train,
                "meta": {k: v for k, v in self.meta.items() if k != "train_nights"}}

    def save(self) -> Path:
        return checkpoint.save(self.out / "ckpt", self.step, self._state(), self.backend.save, self.cfg.keep_ckpts)

    def resume(self, allow_code_change: bool = False, allow_precision_change: bool = False) -> bool:
        """Load the newest good checkpoint of this same run. False when there is none. ResumeRefused when the
        checkpoint belongs to another run (a different config, data, seed, cutoff, backend, precision or code).
        A precision change (bf16 on an A100 resumed as fp16 on a T4) is refused unless allowed explicitly."""
        folder, notes = checkpoint.latest(self.out / "ckpt")
        for n in notes:
            print(f"[trainer] checkpoint note: {n}", flush=True)
        if folder is None:
            return False
        st = checkpoint.read_state(folder)
        mine, theirs = self.identity(), st.get("identity", {})
        waived = {k for k, ok in (("code_hash", allow_code_change), ("precision", allow_precision_change)) if ok}
        diff = [k for k in mine if mine[k] != theirs.get(k) and k not in waived]
        if diff:
            raise ResumeRefused(f"{folder.name} belongs to another run: {', '.join(diff)} differ")
        self.backend.load(folder)
        self.step = int(st["step"])
        self.cursor = Cursor(*st["cursor"])
        self.samples, self.tokens = int(st["samples_seen"]), int(st["tokens_seen"])
        self.monitor = SpikeMonitor.from_state(st["monitor"])
        self.hours = float(st["hours"])
        self.last_eval, self.last_train = st.get("last_eval") or {}, st.get("last_train") or {}
        self.resumed_from = self.step
        return True

    # --- work --------------------------------------------------------------------------------------------------
    def train_step(self) -> dict:
        c = self.cfg
        ids, self.cursor = take(self.cursor, c.micro_batch * c.accum, len(self.ws), c.seed)
        chunks = [self.ws.batch(ids[i * c.micro_batch:(i + 1) * c.micro_batch]) for i in range(c.accum)]
        n_tok = float(sum(b["mask"].sum() for b in chunks))
        lr = c.lr(self.step)
        nll = 0.0
        if n_tok > 0:
            for b in chunks:                                  # every micro-batch scaled by the step's token count
                nll += self.backend.accumulate(b, 1.0 / n_tok)
        info = self.backend.step(lr) if n_tok > 0 else {"grad_norm": None, "skipped": True}   # nan loss: halt
        loss = nll / n_tok if n_tok else float("nan")
        mon = self.monitor.update(loss, info.get("grad_norm"))
        self.step += 1
        self.samples += len(ids)
        self.tokens += int(n_tok)
        self.last_train = {"step": self.step, "loss": loss, "lr": lr, "grad_norm": info.get("grad_norm"),
                           "skipped": bool(info.get("skipped")), "loss_spike": mon["loss_spike"],
                           "gnorm_spike": mon["gnorm_spike"], "spike_score": self.monitor.loss_spike_score,
                           "gnorm_spike_score": self.monitor.gnorm_spike_score}
        return {**self.last_train, "halt": mon["halt"], "reason": mon["reason"]}

    def evaluate(self) -> dict:
        if not len(self.eval_ids):
            return {}
        agg = EvalAggregator()
        mb = self.cfg.micro_batch
        extra = {}
        for i in range(0, len(self.eval_ids), mb):
            b = self.eval_ws.batch(self.eval_ids[i: i + mb])
            r = self.backend.eval_batch(b)
            agg.add(r["nll"], r["p_top1"], r["correct"], b["mask"], b["field"])
            if i == 0 and hasattr(self.backend, "max_attention_logit"):      # OLMo 2 stability signal
                extra["max_attn_logit"] = self.backend.max_attention_logit(b)
        self.last_eval = {"step": self.step, **agg.result(), **extra}
        return self.last_eval

    def _log(self, row: dict) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        with open(self.out / "train_log.jsonl", "a") as f:
            f.write(json.dumps(row, sort_keys=True, default=float) + "\n")

    def run(self, stop_step: int | None = None, max_seconds: float | None = None,
            die_after_step: int | None = None) -> dict:
        """Train until stop_step (default total_steps), the time budget, or divergence. Saves a checkpoint at the
        end of every exit except divergence (the last good checkpoint stays). die_after_step is a test hook that
        kills the process without saving, the way a pre-empted VM dies."""
        c = self.cfg
        stop = min(stop_step if stop_step is not None else c.total_steps, c.total_steps)
        start = mark = last_ckpt = last_beat = time.monotonic()     # start: the budget; mark: hours accounting
        step_time = 0.0
        status = STATUS_DONE if stop >= c.total_steps else STATUS_STOPPED
        while self.step < stop:
            # self-stop before a step that would overrun the budget (with a 50% margin and room to save)
            if max_seconds is not None and (time.monotonic() - start) + 1.5 * step_time > max_seconds:
                status = STATUS_BUDGET
                break
            s0 = time.monotonic()
            r = self.train_step()
            dt = time.monotonic() - s0
            step_time = dt if not step_time else 0.9 * step_time + 0.1 * dt
            if r["halt"]:
                self._log({"event": "halt", **r})
                print(f"[trainer] HALT at step {self.step}: {r['reason']}", flush=True)
                self.hours += (time.monotonic() - mark) / 3600.0
                return self._result(STATUS_DIVERGED, r["reason"])
            if self.step % c.log_every == 0 or r["loss_spike"] or r["gnorm_spike"]:
                self._log({"event": "train", **self.last_train})
            if c.eval_every and self.step % c.eval_every == 0:
                self._log({"event": "eval", **self.evaluate()})
            if die_after_step is not None and self.step == die_after_step:
                os._exit(9)
            if time.monotonic() - last_beat > 60:
                last_beat = time.monotonic()
                checkpoint.atomic_write_json(self.out / "heartbeat.json", {"step": self.step, "loss": r["loss"]})
                print(f"[trainer] step {self.step}/{c.total_steps} loss {r['loss']:.4f} lr {r['lr']:.2e}", flush=True)
            if self.step < stop and (self.step % c.ckpt_every == 0
                                     or time.monotonic() - last_ckpt > 60.0 * c.ckpt_minutes):
                self.hours += (time.monotonic() - mark) / 3600.0
                mark = time.monotonic()
                self.save()
                last_ckpt = time.monotonic()
        if status == STATUS_DONE and (not self.last_eval or self.last_eval.get("step") != self.step):
            self._log({"event": "eval", **self.evaluate()})
        self.hours += (time.monotonic() - mark) / 3600.0
        self.save()
        return self._result(status, "")

    def _result(self, status: str, reason: str) -> dict:
        return {"status": status, "reason": reason, "step": self.step, "total_steps": self.cfg.total_steps,
                "samples_seen": self.samples, "tokens_seen": self.tokens, "hours": self.hours,
                "resumed_from": self.resumed_from, "last_train": self.last_train, "last_eval": self.last_eval,
                "spike_score": self.monitor.loss_spike_score, "loss_spikes": self.monitor.total_loss_spikes,
                "gnorm_spikes": self.monitor.total_gnorm_spikes, "identity": self.identity()}

    # --- the finished model --------------------------------------------------------------------------------------
    def export(self, run_id: str, model_name: str) -> dict:
        """Write model/model.bin and model/model.meta.json (fence-stamped), and the registry record for F-03 to
        register. The verdict is INCONCLUSIVE: PASS or FAIL comes from the sealed evaluator against the prereg."""
        mdir = self.out / "model"
        mdir.mkdir(parents=True, exist_ok=True)
        art = mdir / "model.bin"
        self.backend.export(art)
        metrics = {k: v for k, v in {**{f"eval_{k}": v for k, v in self.last_eval.items()},
                                     "train_loss": self.last_train.get("loss"), "steps": self.step,
                                     "spike_score": self.monitor.loss_spike_score,
                                     "tokens_seen": self.tokens}.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
        desc = self.backend.describe()
        meta = {**self.meta, "metrics": metrics, "n_params": desc["n_params"], "backend": desc["backend"],
                "preregistered": self.preregistered, "promoted": False}
        checkpoint.atomic_write_json(mdir / "model.meta.json", meta)
        rel = os.path.relpath(art, self.out).replace(os.sep, "/")
        try:
            rec = runmeta.registry_record(run_id=run_id, meta=self.meta, prereg=self.prereg, metrics=metrics,
                                          verdict="INCONCLUSIVE", artifact_path=f"runs/{run_id}/{rel}",
                                          artifact_sha256=checkpoint.sha256_path(art), gpu_type=desc["gpu_type"],
                                          hours=self.hours, model=model_name,
                                          note="" if self.preregistered else "exploratory: no pre-registration")
            out = {"registry_record": rec}
        except Exception as e:      # noqa: BLE001 - RegistryError or ValueError: reported, never hidden
            out = {"registry_record": None, "registry_error": f"{type(e).__name__}: {e}"}
            print(f"[trainer] REGISTRY RECORD NOT VALID: {out['registry_error']}", flush=True)
        checkpoint.atomic_write_json(self.out / "registry_record.json", out)
        return out
