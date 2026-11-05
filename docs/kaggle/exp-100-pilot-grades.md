# EXP-100 world model pilot: grades (Colab A100, Nov 4, 8:40 PM)

Notebook `colab/world_model_pilot.ipynb` at 08b0f37 (pack pin 4b779d5). NFL only (nflverse 2001-2024), seed 7, data sha 5cbd1a149035580d, vocab v1 (10,511 tokens).

| | Train | Valid | Test |
|---|---|---|---|
| Games | 5,325 | 575 | 569 |
| Tokens | 5.29M | 0.58M | 0.58M |
| Nights | 1,012 | 127 | 127 |
| Dates | 2001-09 to 2021-01 | 2021-01 to 2023-09 | 2023-09 to 2025-02 |

**Recipe:** 31.1M params (d 512, 8 layers, 8 heads, ctx 1020, batch 64, lr 6e-4). Early stop on valid loss at step 1,700, 14.9 epochs, best valid loss 0.5712.

| Test (569 games) | Model | Baseline |
|---|---|---|
| Next-event kind accuracy | **0.4319** [0.4283, 0.4356], n 94,982 | bigram 0.372 [0.3686, 0.3757] |
| Time to next break, MAE | **46.19 s** [45.08, 47.47], n 13,081 | 61.87 s [60.72, 63.48] |
| Stop ECE | **0.0022** [0.0011, 0.0034] | 0.0121 [0.0108, 0.0136] |
| Stop Brier | 0.0299 | 0.0298 (tie) |

**Train slice:** next-kind accuracy 0.4233, stop ECE 0.0048, break MAE **8.69 s** [7.39, 10.44].

The large train-vs-test gap on break timing, with ~15 epochs over only 5.3M tokens, points to memorization. The cure is more data (more leagues and seasons) plus regularization.

**Throughput:** 386k train tokens/s on an A100-40GB, 287 s of training. Scaled to 100M params: ~120k tokens/s, so **2.31 A100-hours per 1B tokens**.

**Locked nights:** never downloaded.
