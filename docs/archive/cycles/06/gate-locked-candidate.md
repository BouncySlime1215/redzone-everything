| gate | scope | status | value |
|---|---|---|---|
| mid-play cuts = 0 | nfl | ✅ PASS | 0; true rate <= 0.61% (rule of three, 494 trials) |
| entries at a dead ball >= 95% | nfl | ✅ PASS | 100.0% (494/494), 95% CI 100.0-100.0 over 10 nights |
| entries mid-play <= 5% | nfl | ✅ PASS | 0.0% (0/494), judged with the row above |
| entries mid-play never above today | nfl | ✅ PASS | 0 vs today 0 |
| big plays seen >= 95% | nfl | ❌ FAIL | 92.7% (557/601), 95% CI 90.8-94.9 over 10 nights |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nfl | ❌ FAIL | 86.5% (520/601), 95% CI 82.2-91.2 over 10 nights |
| catch-up: your-team big plays within 10 min = 100% | nfl | ❌ FAIL | 18/21 |
| catch-up: crunch plays aired during a replay = 0 | nfl | ❌ FAIL | 8 |
| your-team big plays seen = 100% | nfl | ❌ FAIL | 90.5% |
| your-team crunch live = 100% | nfl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl | ⏳ PENDING | 69.1% (ceiling: pending) |
| mid-play cuts = 0 | nba | ✅ PASS | 0; true rate <= 13.64% (rule of three, 22 trials) |
| entries at a dead ball >= 95% | nba | ⏳ PENDING | 100.0% (22/22), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 22/60 decisions |
| entries mid-play <= 5% | nba | ⏳ PENDING | 0.0% (0/22), judged with the row above |
| entries mid-play never above today | nba | ✅ PASS | 0 vs today 10 |
| big plays seen >= 95% | nba | ⏳ PENDING | 100.0% (16/16), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 16/60 decisions |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nba | ⏳ PENDING | 100.0% (16/16), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights, 16/60 decisions |
| catch-up: your-team big plays within 10 min = 100% | nba | ⏳ PENDING | no your-team big plays in this slice (0/0) |
| catch-up: crunch plays aired during a replay = 0 | nba | ✅ PASS | 0 |
| your-team crunch live = 100% | nba | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl | ✅ PASS | 0; true rate <= 2.83% (rule of three, 106 trials) |
| entries at a dead ball >= 95% | nhl | ⏳ PENDING | 100.0% (106/106), 95% CI 100.0-100.0 over 4 nights; needs 4/8 nights |
| entries mid-play <= 5% | nhl | ⏳ PENDING | 0.0% (0/106), judged with the row above |
| entries mid-play never above today | nhl | ✅ PASS | 0 vs today 2 |
| big plays seen >= 95% | nhl | ⏳ PENDING | 95.2% (198/208), 95% CI 90.4-100.0 over 4 nights; needs 4/8 nights, lower bound 90.4 < 95 |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nhl | ❌ FAIL | 76.4% (159/208), 95% CI 67.5-96.6 over 4 nights |
| catch-up: your-team big plays within 10 min = 100% | nhl | ❌ FAIL | 11/13 |
| catch-up: crunch plays aired during a replay = 0 | nhl | ❌ FAIL | 4 |
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

11 pass, 8 fail, 22 pending (pending never counts as passing).
