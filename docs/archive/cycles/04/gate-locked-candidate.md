| gate | scope | status | value |
|---|---|---|---|
| mid-play cuts = 0 | nfl | ✅ PASS | 0; true rate <= 0.63% (rule of three, 477 trials) |
| entries at a dead ball >= 95% | nfl | ✅ PASS | 100.0% (477/477), 95% CI 100.0-100.0 over 10 nights |
| entries mid-play <= 5% | nfl | ✅ PASS | 0.0% (0/477), judged with the row above |
| entries mid-play never above today | nfl | ✅ PASS | 0 vs today 0 |
| big plays seen >= 95% | nfl | ❌ FAIL | 94.2% (566/601), 95% CI 92.7-96.2 over 10 nights; point estimate under 95 |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nfl | ❌ FAIL | 87.5% (526/601), 95% CI 83.0-92.3 over 10 nights |
| catch-up: your-team big plays within 10 min = 100% | nfl | ❌ FAIL | 18/21 |
| catch-up: crunch plays aired during a replay = 0 | nfl | ❌ FAIL | 7 |
| your-team big plays seen = 100% | nfl | ❌ FAIL | 90.5% |
| your-team crunch live = 100% | nfl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl | ⏳ PENDING | 70.6% (ceiling: pending) |
| mid-play cuts = 0 | nba | ✅ PASS | 0; true rate <= 12.50% (rule of three, 24 trials) |
| entries at a dead ball >= 95% | nba | ⏳ PENDING | 100.0% (24/24), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 24/60 decisions |
| entries mid-play <= 5% | nba | ⏳ PENDING | 0.0% (0/24), judged with the row above |
| entries mid-play never above today | nba | ✅ PASS | 0 vs today 10 |
| big plays seen >= 95% | nba | ❌ FAIL | 68.8% (11/16), 95% CI 55.9-100.0 over 4 nights; point estimate under 95 |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nba | ❌ FAIL | 68.8% (11/16), 95% CI 55.9-100.0 over 4 nights; point estimate under 99 |
| catch-up: your-team big plays within 10 min = 100% | nba | ⏳ PENDING | no your-team big plays in this slice (0/0) |
| catch-up: crunch plays aired during a replay = 0 | nba | ✅ PASS | 0 |
| your-team crunch live = 100% | nba | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba | ⏳ PENDING | 84.9% (ceiling: pending) |
| mid-play cuts = 0 | nhl | ✅ PASS | 0; true rate <= 2.70% (rule of three, 111 trials) |
| entries at a dead ball >= 95% | nhl | ⏳ PENDING | 100.0% (111/111), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights |
| entries mid-play <= 5% | nhl | ⏳ PENDING | 0.0% (0/111), judged with the row above |
| entries mid-play never above today | nhl | ✅ PASS | 0 vs today 2 |
| big plays seen >= 95% | nhl | ⏳ PENDING | 97.6% (203/208), 95% CI 95.7-100.0 over 4 nights; needs 4/8 nights |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nhl | ❌ FAIL | 79.3% (165/208), 95% CI 72.7-96.6 over 4 nights |
| catch-up: your-team big plays within 10 min = 100% | nhl | ✅ PASS | 13/13 |
| catch-up: crunch plays aired during a replay = 0 | nhl | ❌ FAIL | 10 |
| your-team crunch live = 100% | nhl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl | ⏳ PENDING | 76.2% (ceiling: pending) |
| reels >= 95% in dead time, 0 live big plays missed on a reel | system | ⏳ PENDING | ESPN clip reels are not simulated; rewind replays are graded by the catch-up rows and the cut grade |
| reasoner >= 95% agreement, unsafe leaves <= 0.5%, 100% valid JSON | system | ⏳ PENDING | no gold labels yet |
| watchdog >= 95% correct, every kind >= 90%, 0 unsafe | system | ⏳ PENDING | no held-out error set yet |
| sync error < 2 s on >= 95% of samples | system | ⏳ PENDING | needs real screen samples (Mac) |
| preloaded switches < 3 s on >= 95% | system | ⏳ PENDING | TV path not working yet |
| fault-injection night: >= 95% minutes correct picture, all faults recovered | system | ⏳ PENDING | e2e night test reports no uptime figure yet |
| slices: per network/app, close/blowout, crunch/early, your teams | system | ⏳ PENDING | scorecards don't report slices yet |
| locked test size: >= 25% of nights AND a few thousand games | system | ⏳ PENDING | eval-data-v1 locked test is 130 games (110 graded); needs eval-data-v2 |
| fresh nights after the pin | system | ⏳ PENDING | none in eval-data-v1; the 5 AM archive feeds eval-data-v2 |
| models trained only before the locked cutoff (no leakage) | system | ⏳ PENDING | brain models were trained on seasons that include the locked nights (NBA dev-test AUC 0.999): every number above that uses brain features is OPTIMISTIC until the time-cutoff retrain lands |

12 pass, 9 fail, 20 pending (pending never counts as passing).
