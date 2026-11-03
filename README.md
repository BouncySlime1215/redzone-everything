The one pinned eval dataset: every scorecard, thread and CI grades on these exact files.

- Window: NFL 2025-09-05..2026-11-01 (284 games), NBA 2026-10-20..2026-11-01 (92), NHL 2026-10-19..2026-11-01 (95)
- Plus data/eval_config.yaml (pinned switch costs), data/demo/20261101/ (Nov 1 mixed demo), data/eval_pin.json (per-file sha256)
- 600 files, tree sha256 b0d5c00a7877c27bf3301da3419fd65b1e496d21f370961d026601a7235947b9
- Untars at the repo root. Use `python scripts/fetch_eval_data.py` (verifies everything); see docs/eval-data.md.

Raw play-by-play from ESPN's public site API, for backtesting only. Do not replace this asset: make eval-data-v2 instead.
