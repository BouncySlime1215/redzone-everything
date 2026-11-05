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

## EXP-1714 (two heads, floored ceiling), valid, oracle #322 @ 52c03c6, feed lag 8 s

| Pooled, % of floored oracle | % [CI] | Δ vs A0 [CI] | switches/h |
|---|---|---|---|
| A0 v1 past mean | 87.0 [80.6, 92.8] | | 5.0 |
| B1 two heads | 91.5 [84.2, 94.9] | +4.5 [-4.0, +10.9] | 6.4 |

NBA 100 vs 88.2 (+11.8, CI [5.5, 14.7]); NFL 93.7 vs 82.6 (+11.0, CI [4.6, 16.0]); NHL 73.2 vs 78.9. **FAIL** on its
bar (pooled CI includes 0, NHL worse, switches +27 %). Floors 0.

## EXP-125 (two heads + hysteresis band), valid, at the MEASURED feed lag (p90: NBA 70, NFL 62, NHL 75 s) and at 8 s

| Pooled, % of floored oracle | feed lag p90 | feed lag 8 s |
|---|---|---|
| A0 v1 past mean | 81.2 | 87.0 |
| B1 two heads (m = 0) | 75.7 (Δ -5.5, CI [-19.2, +10.9]) | 91.5 |
| band m = 0.25 / 0.5 / 1.0 | 72.2 / 60.1 / 50.0 | 75.8 / 77.4 / 61.3 |

**FAIL.** The band lowers % of oracle at every m (it cuts switches by holding on games that have gone quiet). At the
measured feed lag the window is ~10 s, the decision is almost all beyond-window value, and the learned heads no
longer beat the placeholder (NFL +9.8 with a CI spanning 0; NBA -6.4; NHL -9.9). Floors 0 everywhere.

## Where this leaves stay/leave (rows tried on the same 9 valid nights: EXP-1712, 1713, 1714, 125)

1. The safety floors work: 0 high-leverage or crunch leaves and 0 mid-play joins on every arm. The unfloored
   hindsight oracle makes ~45 such leaves, so the floored ceiling is the honest 100 %.
2. A learned game-context value helps only when the buffer window is wide (8 s feed lag). At today's measured feed
   lag it does not, so faster feeds (T6) are worth more to stay/leave than a better regressor.
3. Stop tuning on these 9 nights (forking paths). Next, pre-registered and NOT run: EXP-126, a pairwise ranker across
   games live at the same minute, trained on the eval-data-v3 pin (more NBA/NHL dev nights, Fri Nov 6) and graded at
   the measured feed lag. The locked read stays the integrator's, once.

## EXP-126 (relative-value ranker) and EXP-128 (last season added to train), valid, % of floored oracle

| Pooled | feed lag p90 | feed lag 8 s |
|---|---|---|
| A0 v1 past mean | 81.2 | 87.0 |
| B1 (EXP-1714) | 75.7 | 91.5 |
| R1 ranker (EXP-126) | 75.2 (Δ -6.0, CI [-20.3, +9.7]) | n/a |
| N1 + last season (EXP-128) | 74.0 (Δ -7.2, CI [-45.9, +12.4]) | 81.2 |

Both **FAIL**. EXP-128 trained on this season (pin v1 train) + last season (36 NBA/NHL nights Mar-Oct 2026 from pin v2,
weight 0.5). Per league at p90: **NFL 90.7 vs 74.4 (Δ +16.2, CI [4.8, 22.6])**, NBA 35.3 vs 88.2 (2 nights, CI
[0, 98]), NHL 29.6 vs 43.7. Every league is PENDING (< 8 valid nights); the NFL signal is the only CI clear of 0 so
far and needs the v3 pin / locked read to mean anything. Floors 0 everywhere. Rows tried on these 9 nights: 7.
