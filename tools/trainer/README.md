# Trainer for the play-by-play foundation model (B-03)

WORKPLAN v2 task B-03; design in `redzone-agents/v2/tracks/B-foundation-model.md` and `v2/TRAINING-PLAN.md`.
Nothing here runs in the show, and the show never imports it. The Mac never trains (CLAUDE.md): the only
things that run locally are the seconds-long toy tests.

## The job entry point (what F-03's runner calls)

Run from the repo root (or the root of the code bundle pushed to Kaggle/Colab):

```sh
python -m tools.trainer.job train --config CFG.json --data TAG_OR_PATH.manifest.json --data-root DIR --out OUT \
    [--stop-step N] [--max-hours H] [--prereg PREREG.yaml] [--run-id ID]
python -m tools.trainer.job smoke --out OUT [--backend numpy|torch] [--device cpu|cuda]   # kill-and-resume == uninterrupted
python -m tools.trainer.job gbm --config CFG.json --data ... --data-root DIR --out OUT [--fields 0,4,5]
```

- A work unit is (config, seed, step range): `--stop-step` ends the range; running the same command again
  resumes from `OUT/ckpt` by itself. A checkpoint of a different run (config, data manifest, seed, training
  cutoff, backend, precision or code changed) is refused, never overwritten (`--allow-code-change` for a
  code-only fix; `--allow-precision-change` when Colab hands a T4 (fp16) to a run started in bf16).
- `--max-hours`: self-stop before the step that would overrun it (set about 85% of the session cap); the
  checkpoint is saved and the exit code is 4, so the runner re-queues the same command.
- `RZ_GIT_SHA` (40 hex) must be set by the runner when the bundle has no `.git`; without a git sha the
  registry record is not produced (the error is written to `registry_record.json`).

| Exit | Meaning |
|---|---|
| 0 | finished, or reached `--stop-step` |
| 2 | refused: fenced night, manifest mismatch, malformed corpus, or a checkpoint of another run |
| 4 | stopped on the time budget: run the same command again |
| 5 | diverged (non-finite loss or gradient norm, or 5 loss spikes in a row); the last good checkpoint stays |
| 3 | not used: left for the runner's stall watchdog |

Outputs, all written atomically: `result.json`, `registry_record.json` (a validated F-02 `register` record with
verdict INCONCLUSIVE; the runner registers it, the trainer never writes the ledger), `model/model.bin` +
`model/model.meta.json` (training cutoff, train nights and their sha256, data manifest hash, config and code
hashes, git sha, seed, step range, licence; `redzone/lab/fence.py` reads it), `ckpt/step_XXXXXXXX/` +
`ckpt/LATEST.json`, `train_log.jsonl`, `heartbeat.json` (once a minute).

## Data

`--data` is a data-manifest tag or file (`redzone/lab/datamanifest.py`); `load_pinned(..., for_training=True)`
re-hashes every file first. The shards are the `scripts/noggin_tokens.py` format (`<league>/<year>.npy` plus the
`.json` index; both must be pinned). Season shards have no single night, so the trainer checks every game's own
night against the fence and refuses the whole corpus if any training or held-out game is in the locked
(2025-12-12..2026-01-05) or tuning (Oct 2026) window. The split is walk-forward by whole night:
train <= `train_through` < `valid_from` <= held-out <= `valid_through`. `train_through` is the training cutoff
stamped into every checkpoint and the exported model. No manifest kind for token shards exists yet: corpus v1
(B-02) adds it; until then a manifest of kind `history` pins them (as the tests do).

## Tests: what runs where

| Where | Command | What it proves |
|---|---|---|
| CI and the Mac (numpy, no torch) | `python -m pytest -q tests/test_trainer_reference.py tests/test_trainer_gbm.py` | WSD schedule; initial loss near ln(10,511) on vocab v1; exact gradients (finite differences); overfit one batch; causality (model and attention mask); gradient-accumulation equivalence; save-resume equivalence (bitwise) in-process and through real processes killed mid-run; refusal of another run's checkpoint; corrupt checkpoint falls back to the previous; spike monitor and divergence halt; time-budget self-stop; manifest pin (tamper, unpinned index, vocab mismatch); fence refusals on both sides; registry record validated by F-02's validator; fence accepts the exported model; GBM baseline causal, proper and learning |
| GPU runtime (Kaggle or Colab, torch) | `python -m pytest -q tools/trainer/tests` (it also runs the torch smoke on CPU and, on a GPU, on cuda) | The same model-level tests on the torch decoder: initial loss, overfit, causality, accumulation, save-resume (bitwise on CPU), GQA shapes, bf16 and fp16-with-loss-scaling runs on the GPU, and the kill-and-resume smoke (bitwise on CPU, within 1e-4 on cuda) |

CI does not collect `tools/trainer/tests` (pyproject `testpaths = ["tests"]`) because CI has no torch; the
torch tests use no skips. **The torch files (`model.py`, `torch_backend.py`, `tools/trainer/tests`) have never
been run**: neither the Mac nor CI has torch, and installing it needs Nick's OK. Run the GPU-runtime line above
as F-03's first smoke before any real run.

## Known limits (deliberate, for later tasks)

- One game per window, padded: no packing, so short NFL games waste compute. Packing needs a document mask.
- Single device; no DDP, `torch.compile`, multi-token prediction, masked-input objective or heads H1-H4
  (B-08, B-03 follow-ups); the factorised-event layout of B1 is a different model class.
- The GBM baseline is per field with a frequency-capped class set; tuning it is B-05's job (TRAINING-PLAN rule 5).
- `configs/example-5m.json` is a starting point (ESTIMATE): the real nights and step counts come from corpus v1
  and the B-04 pre-registration.
