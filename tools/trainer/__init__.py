"""Trainer for the play-by-play foundation model (Track B, WORKPLAN task B-03).

Why: WORKPLAN v2 B-03 and TRAINING-PLAN sections 2-3: one trainer (dense decoder, WSD schedule, evaluation,
GBM baseline, resumable checkpoints, manifest-pinned data, registry metadata, leak fence) that the F-03 job
runner calls on Kaggle or Colab. The Mac never trains; the show never imports this package.

Layout (torch-free unless named):
    schedule.py      warmup-stable-decay learning rate, branchable cooldowns
    data.py          manifest-pinned token corpus, fence checks, (seed, epoch) data order, resumable cursor
    metrics.py       NLL per token / event / field, accuracy, calibration error
    monitor.py       loss and gradient-norm spike score (OLMo 2 style), divergence halt
    checkpoint.py    atomic checkpoints with sha256, LATEST pointer, fallback to the previous one
    runmeta.py       git sha, code/config/data/prereg hashes, registry record, fence-stamped model metadata
    loop.py          the backend-agnostic training loop (accumulation, eval, checkpoints, self-stop)
    reference.py     numpy reference backend (runs in CI and on the Mac in seconds)
    gbm.py           LightGBM baseline for the same next-field task
    model.py         torch dense decoder (needs torch: GPU runtime only)
    torch_backend.py torch backend for the loop (needs torch)
    job.py           the job entry point F-03 calls: python -m tools.trainer.job ...
"""
