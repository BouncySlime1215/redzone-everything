#!/usr/bin/env bash
# Data Division D (markets): the Kaggle/Colab CPU job. Never run on the Mac.
# Pulls public Polymarket price history for the six product leagues (2024-07 onward), then tokenizes.
# Usage: bash scripts/markets/run_kaggle.sh [START] [END] [MAX_MARKETS]
# Smoke test first: bash scripts/markets/run_kaggle.sh 2025-10-21 2025-10-28 20
set -euo pipefail
START=${1:-2024-07-01}; END=${2:-$(date -u +%F)}; MAX=${3:-}
OUT=${OUT:-out/markets}
for lg in nfl nba nhl mlb cfb cbb; do
  python scripts/markets/polymarket.py --league "$lg" --start "$START" --end "$END" --out "$OUT" ${MAX:+--max-markets "$MAX"} \
    || echo "polymarket $lg failed (continuing)"
done
python scripts/markets/tokens.py --in "$OUT" --out "${OUT}_tokens"
echo "counts: ${OUT}_tokens/counts.json  (copy the totals into docs/datasources/markets/ledger.md)"
