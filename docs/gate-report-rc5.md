# Gate report, RC 5 candidate (integration/next f1034b5)

Locked test split, `ci_scorecard.py --split test --robust --no-resync`, judged by #51's night-level gate
(PASS needs >= 8 nights, >= 60 decisions and a 95 % lower bound at the bar; a point estimate under the
bar FAILs). Run 2026-11-04 ~05:45 UTC on a clean checkout of f1034b5. **49 pass, 16 fail, 52 pending.**
Not a release: Nick cuts releases. RC 2's verdicts on this gate were 41 / 10 / 45; the 6 new FAILs are the
`*_reallag` cards #47 added (scored at the measured 35-50 s feed lag), not regressions.

## Triage of the 16 FAILs (planner item 5: fix, or accept and flag off)

| # | rows | cause | decision | owner / next |
|---|---|---|---|---|
| 1 | NBA entries at a dead ball / mid-play ≤ 5 % (nba, nba_sync±3: 6 rows) | 3 of 21 NBA entries land mid-play: the gate times an NBA entry from a whistle it hears ~35-50 s late and has no restart countdown | **fix**: no flag can hide entries (switching itself). A3's NBA countdown (planner item 2: NBA entries mid-play 9 → 0) | TIMING (A3) |
| 2 | NBA stale-feed entries + big seen (nba_stale: 3 rows) | a 10 s stale feed on 4 NBA nights: 4/13 entries mid-play, 11/16 big seen | **fix** with item 1; recheck after A3. Only 13-16 decisions: FAIL on the point estimate, the CI reaches 100 | TIMING (A3 + stale hold) |
| 3 | NBA real-lag entries + big seen (nba_reallag: 3 rows) | at the measured feed lag, 8/17 entries land mid-play | **fix**: the time-shift buffer (item 1; sim at B=90: NBA seen 67.8 → 87.4 %) plus A3 | TIMING (buffer, A3) |
| 4 | NFL big plays seen, real lag + your team (nfl_reallag: 2 rows) | 85.2 % seen and your team 95.2 % at the real 35-50 s lag; at the 8 s sim lag it is 95.3 % | **fix**: the buffer (sim at B=60-90: NFL seen 77.9 → 97 %); catch-up T1-T5 (#59, off) adds +2.1 pts within 10 min on valid | TIMING (buffer), INTEGRATION (#59) |
| 5 | NFL big plays seen, stale feed (nfl_stale: 1 row) | 94.3 % (567/601) with a 10 s stale feed, lower bound 92.1 | **accept for now**: 0.7 pts under; the stale-feed hold (#49 feeds fallback, off) and #59 target it. Re-score with both on | ERRORS (#49), INTEGRATION (#59) |
| 6 | demo entries mid-play = 0 (demo_20261101: 1 row) | 2 of 193 entries on the recorded Nov 1 night land mid-play (99.5 % of landings in dead time); the same 2 as today's baseline | **accept** (never above today holds; the strict 0 row is #54's new Nov 4 target) and fix with A3 + buffer | TIMING |

Nothing here is a regression against RC 4 (deployed): every FAIL is a row RC 4 also misses or a new,
stricter card. Every part these rows judge that has a flag is already OFF (`replay.redzone_way`,
`hardening.*` except #45's cap, `timing.behind_live_s` not built yet).

## Full table

| gate | scope | status | value |
|---|---|---|---|
| mid-play cuts = 0 | nfl | ✅ PASS | 0; true rate <= 0.70% (rule of three, 430 trials) |
| entries at a dead ball >= 95% | nfl | ✅ PASS | 100.0% (430/430), 95% CI 100.0-100.0 over 10 nights |
| entries mid-play <= 5% | nfl | ✅ PASS | 0.0% (0/430), judged with the row above |
| entries mid-play never above today | nfl | ✅ PASS | 0 vs today 0 |
| big plays seen >= 95% | nfl | ⏳ PENDING | 95.3% (573/601), 95% CI 92.6-98.1 over 10 nights; needs lower bound 92.6 < 95 |
| your-team big plays seen = 100% | nfl | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl | ⏳ PENDING | 73.4% (ceiling: pending) |
| mid-play cuts = 0 | nba | ✅ PASS | 0; true rate <= 14.29% (rule of three, 21 trials) |
| entries at a dead ball >= 95% | nba | ❌ FAIL | 85.7% (18/21), 95% CI 76.0-100.0 over 4 nights; point estimate under 95 |
| entries mid-play <= 5% | nba | ❌ FAIL | 14.3% (3/21), judged with the row above |
| entries mid-play never above today | nba | ✅ PASS | 3 vs today 10 |
| big plays seen >= 95% | nba | ⏳ PENDING | 100.0% (16/16), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 16/60 decisions |
| your-team crunch live = 100% | nba | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl | ✅ PASS | 0; true rate <= 2.13% (rule of three, 141 trials) |
| entries at a dead ball >= 95% | nhl | ⏳ PENDING | 100.0% (141/141), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights |
| entries mid-play <= 5% | nhl | ⏳ PENDING | 0.0% (0/141), judged with the row above |
| entries mid-play never above today | nhl | ✅ PASS | 0 vs today 2 |
| big plays seen >= 95% | nhl | ⏳ PENDING | 99.0% (206/208), 95% CI 97.9-100.0 over 4 nights; needs 4/8 nights |
| your-team crunch live = 100% | nhl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_sync+3 | ✅ PASS | 0; true rate <= 0.70% (rule of three, 430 trials) |
| entries at a dead ball >= 95% | nfl_sync+3 | ✅ PASS | 100.0% (430/430), 95% CI 100.0-100.0 over 10 nights |
| entries mid-play <= 5% | nfl_sync+3 | ✅ PASS | 0.0% (0/430), judged with the row above |
| big plays seen >= 95% | nfl_sync+3 | ⏳ PENDING | 95.3% (573/601), 95% CI 92.6-98.1 over 10 nights; needs lower bound 92.6 < 95 |
| your-team big plays seen = 100% | nfl_sync+3 | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl_sync+3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_sync+3 | ⏳ PENDING | 73.4% (ceiling: pending) |
| mid-play cuts = 0 | nba_sync+3 | ✅ PASS | 0; true rate <= 14.29% (rule of three, 21 trials) |
| entries at a dead ball >= 95% | nba_sync+3 | ❌ FAIL | 85.7% (18/21), 95% CI 76.0-100.0 over 4 nights; point estimate under 95 |
| entries mid-play <= 5% | nba_sync+3 | ❌ FAIL | 14.3% (3/21), judged with the row above |
| big plays seen >= 95% | nba_sync+3 | ⏳ PENDING | 100.0% (16/16), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 16/60 decisions |
| your-team crunch live = 100% | nba_sync+3 | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_sync+3 | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl_sync+3 | ✅ PASS | 0; true rate <= 2.13% (rule of three, 141 trials) |
| entries at a dead ball >= 95% | nhl_sync+3 | ⏳ PENDING | 100.0% (141/141), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights |
| entries mid-play <= 5% | nhl_sync+3 | ⏳ PENDING | 0.0% (0/141), judged with the row above |
| big plays seen >= 95% | nhl_sync+3 | ⏳ PENDING | 99.0% (206/208), 95% CI 97.9-100.0 over 4 nights; needs 4/8 nights |
| your-team crunch live = 100% | nhl_sync+3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_sync+3 | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_sync-3 | ✅ PASS | 0; true rate <= 0.70% (rule of three, 430 trials) |
| entries at a dead ball >= 95% | nfl_sync-3 | ✅ PASS | 100.0% (430/430), 95% CI 100.0-100.0 over 10 nights |
| entries mid-play <= 5% | nfl_sync-3 | ✅ PASS | 0.0% (0/430), judged with the row above |
| big plays seen >= 95% | nfl_sync-3 | ⏳ PENDING | 95.3% (573/601), 95% CI 92.6-98.1 over 10 nights; needs lower bound 92.6 < 95 |
| your-team big plays seen = 100% | nfl_sync-3 | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl_sync-3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_sync-3 | ⏳ PENDING | 73.4% (ceiling: pending) |
| mid-play cuts = 0 | nba_sync-3 | ✅ PASS | 0; true rate <= 14.29% (rule of three, 21 trials) |
| entries at a dead ball >= 95% | nba_sync-3 | ❌ FAIL | 85.7% (18/21), 95% CI 76.0-100.0 over 4 nights; point estimate under 95 |
| entries mid-play <= 5% | nba_sync-3 | ❌ FAIL | 14.3% (3/21), judged with the row above |
| big plays seen >= 95% | nba_sync-3 | ⏳ PENDING | 100.0% (16/16), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 16/60 decisions |
| your-team crunch live = 100% | nba_sync-3 | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_sync-3 | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl_sync-3 | ✅ PASS | 0; true rate <= 2.13% (rule of three, 141 trials) |
| entries at a dead ball >= 95% | nhl_sync-3 | ⏳ PENDING | 100.0% (141/141), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights |
| entries mid-play <= 5% | nhl_sync-3 | ⏳ PENDING | 0.0% (0/141), judged with the row above |
| big plays seen >= 95% | nhl_sync-3 | ⏳ PENDING | 99.0% (206/208), 95% CI 97.9-100.0 over 4 nights; needs 4/8 nights |
| your-team crunch live = 100% | nhl_sync-3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_sync-3 | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_stale | ✅ PASS | 0; true rate <= 0.78% (rule of three, 387 trials) |
| entries at a dead ball >= 95% | nfl_stale | ✅ PASS | 99.7% (386/387), 95% CI 99.2-100.0 over 10 nights |
| entries mid-play <= 5% | nfl_stale | ✅ PASS | 0.3% (1/387), judged with the row above |
| big plays seen >= 95% | nfl_stale | ❌ FAIL | 94.3% (567/601), 95% CI 92.1-96.8 over 10 nights; point estimate under 95 |
| your-team big plays seen = 100% | nfl_stale | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl_stale | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_stale | ⏳ PENDING | 68.4% (ceiling: pending) |
| mid-play cuts = 0 | nba_stale | ✅ PASS | 0; true rate <= 23.08% (rule of three, 13 trials) |
| entries at a dead ball >= 95% | nba_stale | ❌ FAIL | 69.2% (9/13), 95% CI 33.3-100.0 over 4 nights; point estimate under 95 |
| entries mid-play <= 5% | nba_stale | ❌ FAIL | 30.8% (4/13), judged with the row above |
| big plays seen >= 95% | nba_stale | ❌ FAIL | 68.8% (11/16), 95% CI 55.9-100.0 over 4 nights; point estimate under 95 |
| your-team crunch live = 100% | nba_stale | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_stale | ⏳ PENDING | 81.0% (ceiling: pending) |
| mid-play cuts = 0 | nhl_stale | ✅ PASS | 0; true rate <= 2.36% (rule of three, 127 trials) |
| entries at a dead ball >= 95% | nhl_stale | ⏳ PENDING | 98.4% (125/127), 95% CI 96.1-100.0 over 4 nights; needs 4/8 nights |
| entries mid-play <= 5% | nhl_stale | ⏳ PENDING | 1.6% (2/127), judged with the row above |
| big plays seen >= 95% | nhl_stale | ⏳ PENDING | 99.0% (206/208), 95% CI 97.9-100.0 over 4 nights; needs 4/8 nights |
| your-team crunch live = 100% | nhl_stale | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_stale | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_reallag | ✅ PASS | 0; true rate <= 1.18% (rule of three, 255 trials) |
| entries at a dead ball >= 95% | nfl_reallag | ✅ PASS | 99.6% (254/255), 95% CI 98.7-100.0 over 10 nights |
| entries mid-play <= 5% | nfl_reallag | ✅ PASS | 0.4% (1/255), judged with the row above |
| big plays seen >= 95% | nfl_reallag | ❌ FAIL | 85.2% (512/601), 95% CI 76.8-91.3 over 10 nights |
| your-team big plays seen = 100% | nfl_reallag | ❌ FAIL | 95.2% |
| your-team crunch live = 100% | nfl_reallag | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_reallag | ⏳ PENDING | 59.6% (ceiling: pending) |
| mid-play cuts = 0 | nba_reallag | ✅ PASS | 0; true rate <= 17.65% (rule of three, 17 trials) |
| entries at a dead ball >= 95% | nba_reallag | ❌ FAIL | 52.9% (9/17), 95% CI 20.0-75.0 over 4 nights |
| entries mid-play <= 5% | nba_reallag | ❌ FAIL | 47.1% (8/17), judged with the row above |
| big plays seen >= 95% | nba_reallag | ❌ FAIL | 68.8% (11/16), 95% CI 55.9-100.0 over 4 nights; point estimate under 95 |
| your-team crunch live = 100% | nba_reallag | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_reallag | ⏳ PENDING | 73.7% (ceiling: pending) |
| mid-play cuts = 0 | nhl_reallag | ✅ PASS | 0; true rate <= 2.78% (rule of three, 108 trials) |
| entries at a dead ball >= 95% | nhl_reallag | ⏳ PENDING | 96.3% (104/108), 95% CI 93.9-97.3 over 4 nights; needs 4/8 nights, lower bound 93.9 < 95 |
| entries mid-play <= 5% | nhl_reallag | ⏳ PENDING | 3.7% (4/108), judged with the row above |
| big plays seen >= 95% | nhl_reallag | ⏳ PENDING | 96.6% (201/208), 95% CI 93.6-100.0 over 4 nights; needs 4/8 nights, lower bound 93.6 < 95 |
| your-team crunch live = 100% | nhl_reallag | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_reallag | ⏳ PENDING | 89.4% (ceiling: pending) |
| mid-play cuts = 0 | demo_20261101 | ✅ PASS | 0; true rate <= 1.55% (rule of three, 193 trials) |
| entries at a dead ball >= 95% | demo_20261101 | ✅ PASS | 99.0% (191/193); no per-night counts: point estimate |
| entries mid-play <= 5% | demo_20261101 | ✅ PASS | 1.0% (2/193) |
| entries mid-play = 0 | demo_20261101 | ❌ FAIL | 2 |
| landings in dead time >= 95% | demo_20261101 | ✅ PASS | 99.5% |
| entries mid-play never above today | demo_20261101 | ✅ PASS | 2 vs today 2 |
| big plays seen >= 95% (NFL) | demo_20261101 | ✅ PASS | 137/137 (100%) |
| big plays seen >= 95% (NHL) | demo_20261101 | ✅ PASS | 36/36 (100%) |
| crunch live >= 95% or feasible ceiling | demo_20261101 | ⏳ PENDING | 76.6% (ceiling: pending) |
| reels >= 95% in dead time, 0 live big plays missed on a reel | system | ⏳ PENDING | reel scorecard line not built yet |
| reasoner >= 95% agreement, unsafe leaves <= 0.5%, 100% valid JSON | system | ⏳ PENDING | no gold labels yet |
| watchdog >= 95% correct, every kind >= 90%, 0 unsafe | system | ⏳ PENDING | no held-out error set yet |
| sync error < 2 s on >= 95% of samples | system | ⏳ PENDING | needs real screen samples (Mac) |
| preloaded switches < 3 s on >= 95% | system | ⏳ PENDING | TV path not working yet |
| fault-injection night: >= 95% minutes correct picture, all faults recovered | system | ⏳ PENDING | e2e night test reports no uptime figure yet |
| slices: per network/app, close/blowout, crunch/early, your teams | system | ⏳ PENDING | scorecards don't report slices yet |
| locked test size: >= 25% of nights AND a few thousand games | system | ⏳ PENDING | eval-data-v1 locked test is 130 games (110 graded); needs eval-data-v2 |
| fresh nights after the pin | system | ⏳ PENDING | none in eval-data-v1; the 5 AM archive feeds eval-data-v2 |
| models trained only before the locked cutoff (no leakage) | system | ⏳ PENDING | brain models were trained on seasons that include the locked nights (NBA dev-test AUC 0.999): every number above that uses brain features is OPTIMISTIC until the time-cutoff retrain lands |

49 pass, 16 fail, 52 pending (pending never counts as passing).
