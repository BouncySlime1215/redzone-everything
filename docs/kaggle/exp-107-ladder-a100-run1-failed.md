# EXP-107 scaling ladder on Colab A100 (recipe E), run 1: FAILED, no rung graded

Thu Nov 5, 2026, 6:10 → 7:25 AM, pin ef13442, A100-40GB High-RAM, 1.14 h, ~14.8 CU. Vocab v2 data: 216.3M train
tokens (nflverse NFL, NBA.com cdn NBA, Retrosheet MLB); budget 1.2B tokens = 18,310 steps (batch 32 x ctx 2048).

| Rung | What happened |
|---|---|
| 18m (21.8M) | Trained 10,750 / 18,310 steps at 417k tok/s, then **crashed at the 30-min checkpoint**: `nfm_train.py:585 sync.push(...)` → `AttributeError: 'function' object has no attribute 'push'` (compute-exchange checkpoint sync). No grades.json. |
| 60m (73.7M) | **CUDA OOM** on the first backward (tried 4.84 GiB; 38.9 of 39.5 GiB in use). |
| 150m (173.8M) | **CUDA OOM** in the compiled forward (an fp32 32 x 2048 x 896 buffer). |

## Read
- **Memorization warning (18m):** train loss 0.28 vs valid ~0.50, flat from step ~6,000. 216M unique tokens against a
  1.2B budget is ~5.5 epochs. The research report's rule (params ~ unique tokens / 20, at most ~4 passes) says the
  ladder needs either more unique data (the factory's 1.6B rows → tokens) or a budget of ~0.8B tokens (~4 epochs) plus
  stronger regularization, else bigger rungs only memorize faster.
- Vocab v2 losses (~0.50) aren't comparable to vocab v1 (EXP-103/109 ~0.33).

## Fixes (lane 1)
1. `sync.push`: the checkpoint sync object is a function (an import/name clash); make it a no-op when no HF_TOKEN.
2. Memory: micro-batch 8-16 with gradient accumulation to keep the effective batch at 32; fp32 logits buffer → bf16 or a
   chunked cross-entropy; set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
3. Data: feed the ladder from the factory's plan-B event tokens (more unique data) and cap epochs at ~4.
