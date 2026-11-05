# EXP-1712 / 1713 valid read: STAY/LEAVE v1 (Nov 5 2026, lane 1)

Pin eval-data-v1, valid nights only (NBA 2, NFL 5, NHL 2; pooled 9). Buffer 60 s, feed lag 8 s, tune 5 s.
Prereg: [exp-1712.md](../prereg/exp-1712.md). % of oracle = North Star shape vs the full-hindsight oracle.

| Arm (pooled) | % of oracle [95 % CI] | Δ vs A0 [CI] | floor leaves | mid-play entries | switches/h | crunch-exit acc |
|---|---|---|---|---|---|---|
| A0 v1 past mean (120 s) | 86.3 [77.5, 92.0] | | 0 | 0 | 5.0 | 100 % (10) |
| A1 learned (EXP-1712) | 70.1 [41.0, 89.5] | -16.2 [-46.6, +6.2] | 0 | 0 | 6.8 | 100 % (13) |
| A2 learned + band (EXP-1713) | 69.0 [40.4, 82.5] | -1.1 vs A1 | 0 | 0 | 5.0 | 100 % (10) |
| windowed oracle, no floor | 87.0 [80.7, 93.0] | | **46** | 0 | 6.0 | 26 % (50) |
| full-hindsight oracle | 100 | | **45** | 0 | 6.3 | 36 % (58) |

Per league A1 vs A0: NBA 47.1 vs 88.2, NFL 82.6 vs 81.6, NHL 74.7 vs 74.7. Every league is PENDING (< 8 valid
nights); the pooled read is graded.

**Verdict: EXP-1712 FAIL, EXP-1713 FAIL. Nothing ships.** The floors held on every arm (0 high-leverage or
crunch leaves, 0 mid-play entries).

Why: the label includes the your-team crunch weight (x100), so the regressor's value is dominated by one rare
term and parks on a WSH game outside crunch (NBA). Ablation (refit without a group, pooled): without the state
group 87.3 %, without consequence 68.3 %, without history 61.4 %, without your-team 65.2 %.
Second finding: the full-hindsight ceiling itself makes 45 floor-violating leaves, so the 100 % should be the
floored oracle. Both fixes are pre-registered as [EXP-1714](../prereg/exp-1714.md) (code in
`scripts/stay_leave.py fit1714 / grade1714`), not yet run: paused by the overseer's budget throttle (10:45 AM).
