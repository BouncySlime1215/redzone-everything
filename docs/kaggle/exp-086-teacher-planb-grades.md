# EXP-086 Teacher 32B, plan B (CBB, NHL, MLB): grades

Thu Nov 5, 2026, ~1:15 AM. Colab A100-SXM4-40GB, Qwen3-32B-AWQ via vLLM, code pack
`kaggle-inputs@d55d661`, seed 7. Data plan B (ESPN site API, **personal use only**). Split by whole
night; only TRAIN nights asked; locked nights never downloaded.

## Bar (pre-registered): PASS
- Answered: 4,050 / 4,050 (100%), bar ≥ 90%.
- Valid JSON: 100% CBB, 99.9% MLB, 100% NHL, bar ≥ 99%.

## Blind lock calls (150 blind moments per league)

| League | Fast head | Teacher 32B | Read |
|---|---|---|---|
| CBB | 96.7% | 93.3% | Head is **leak-suspect** (AUC 0.967, lift over context only +0.007). Audit CBB inputs before any training use. |
| MLB | 56.7% | 57.0% | Tie; both weak. Head AUC 0.872, honest lift +0.104. |
| NHL | 57.3% | **70.0%** | Teacher +12.7 pts over the head. Head AUC 0.719, honest lift +0.029. |

## Fast-head build (moments)
- 28,074 games built: CBB 16,232 / NHL 3,794 / MLB 8,048 games; 11.47M rows; 62 min CPU.
- CBB head flagged `leak_suspect: true`. NHL, MLB not flagged.

## Notes
- A Colab drop at 12:27 AM cost a reconnect; the run's answers were already saved, and the
  resumed Run all finished in 0.06 h (0.8 CU). Total spend ≈ the original ~45 CU budget or less.
- Output `teacher_out.jsonl` (4,050 rows) is held locally (not in this public repo) for crew E
  (EXP-1400 teacher tokens). It is ESPN-derived: personal use only.

## Next
1. Audit the CBB fast-head features for the leak before E1 turns CBB answers into tokens.
2. NHL: the Teacher's lock calls beat the head by ~13 pts, so they're worth distilling.
3. MLB: neither is good at lock calls; don't spend more Teacher time there until the head improves.
