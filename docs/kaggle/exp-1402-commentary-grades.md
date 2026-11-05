# EXP-1402 causal per-play commentary (Teacher 32B, crew E): grades

Thu Nov 5, 2026, finished 6:31 AM. Colab A100-40GB, Qwen3-32B-AWQ (vLLM), kpin eac55da / epin d4c0adb. Plan B,
**personal use only**. Train nights only. 3.23 h, 41.9 CU.

- **Answered: 66,048 plays** (11,008 per league x 6) out of a 10.2M-play queue (2 h budget; priority plays first).
  24.0M prompt tokens, 3.8M generated. Valid JSON 100% (NHL 99.9%).
- **Importance AUC vs a real big play next** (does the Teacher's 0-10 importance rank what actually happened?):

| League | AUC |
|---|---|
| NBA | 0.955 |
| CBB | 0.957 |
| CFB | 0.588 |
| NFL | 0.581 |
| MLB | 0.585 |
| NHL | 0.539 |

## Read
- **The basketball numbers are suspect, not a win yet.** Both basketball leagues score 0.95+ while every other league
  sits near 0.58: the same pattern as the Nov 4 CBB lock "leak" (the label tracks the clock / crunch rule, which the
  Teacher also sees). Audit "real big next" for basketball before using it as evidence (lane 3), using the
  outcome-based label Nick ordered (lead change, one-score finish, WP swing), never clock/crunch.
- NFL/CFB/MLB ~0.58 and NHL 0.54: weak but honest-looking signal; it may still help as a model input (it's causal).
- Output (personal use, NOT committed): teacher_out.jsonl (66k rows) handed to crew E privately.
