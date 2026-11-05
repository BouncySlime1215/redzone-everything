| gate | scope | status | value |
|---|---|---|---|
| mid-play cuts = 0 | nfl | ✅ PASS | 0; true rate <= 1.13% (rule of three, 266 trials) |
| entries at a dead ball >= 95% | nfl | ✅ PASS | 100.0% (266/266), 95% CI 100.0-100.0 over 10 nights |
| entries mid-play <= 5% | nfl | ✅ PASS | 0.0% (0/266), judged with the row above |
| entries mid-play never above today | nfl | ✅ PASS | 0 vs today 0 |
| big plays seen >= 95% | nfl | ❌ FAIL | 84.0% (505/601), 95% CI 77.6-91.1 over 10 nights |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nfl | ❌ FAIL | 60.6% (364/601), 95% CI 51.0-73.5 over 10 nights |
| catch-up: your-team big plays within 10 min = 100% | nfl | ❌ FAIL | 19/21 |
| catch-up: crunch plays aired during a replay = 0 | nfl | ❌ FAIL | 14 |
| your-team big plays seen = 100% | nfl | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl | ⏳ PENDING | 53.9% (ceiling: pending) |
| mid-play cuts = 0 | nba | ✅ PASS | 0; true rate <= 17.65% (rule of three, 17 trials) |
| entries at a dead ball >= 95% | nba | ❌ FAIL | 52.9% (9/17), 95% CI 20.0-75.0 over 4 nights |
| entries mid-play <= 5% | nba | ❌ FAIL | 47.1% (8/17), judged with the row above |
| entries mid-play never above today | nba | ✅ PASS | 8 vs today 10 |
| big plays seen >= 95% | nba | ❌ FAIL | 68.8% (11/16), 95% CI 55.9-100.0 over 4 nights; point estimate under 95 |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nba | ❌ FAIL | 62.5% (10/16), 95% CI 55.9-100.0 over 4 nights; point estimate under 99 |
| catch-up: your-team big plays within 10 min = 100% | nba | ⏳ PENDING | no your-team big plays in this slice (0/0) |
| catch-up: crunch plays aired during a replay = 0 | nba | ✅ PASS | 0 |
| your-team crunch live = 100% | nba | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba | ⏳ PENDING | 73.7% (ceiling: pending) |
| mid-play cuts = 0 | nhl | ✅ PASS | 0; true rate <= 3.09% (rule of three, 97 trials) |
| entries at a dead ball >= 95% | nhl | ⏳ PENDING | 96.9% (94/97), 95% CI 93.8-99.0 over 4 nights; needs 4/8 nights, lower bound 93.8 < 95 |
| entries mid-play <= 5% | nhl | ⏳ PENDING | 3.1% (3/97), judged with the row above |
| entries mid-play never above today | nhl | ❌ FAIL | 3 vs today 2 |
| big plays seen >= 95% | nhl | ⏳ PENDING | 95.7% (199/208), 95% CI 90.4-100.0 over 4 nights; needs 4/8 nights, lower bound 90.4 < 95 |
| catch-up: big plays seen live or replayed within 10 min >= 99% | nhl | ❌ FAIL | 74.0% (154/208), 95% CI 58.9-94.8 over 4 nights |
| catch-up: your-team big plays within 10 min = 100% | nhl | ❌ FAIL | 11/13 |
| catch-up: crunch plays aired during a replay = 0 | nhl | ✅ PASS | 0 |
| your-team crunch live = 100% | nhl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl | ⏳ PENDING | 85.5% (ceiling: pending) |
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

12 pass, 11 fail, 18 pending (pending never counts as passing).
