# Release gate report, RC 2 (integration/release, 2026-11-03)

RC 2 = integration/next after #38 (your team's missed plays replay first) and #39 (NBA whistle-wait
cap 180 s). Run: `python scripts/ci_scorecard.py --split test --robust --no-resync`, then
`scripts/release_gate.py`, on the **locked test split** of eval-data-v1 plus robustness cards
(sync +3 s / -3 s, feed 10 s stale) and the Nov 1 demo. Pending never counts as passing.

**Result: 59 pass, 10 fail, 27 pending** (RC 1: 58 / 11 / 27).

| Fails | RC 1 → RC 2 | Ships |
|---|---|---|
| NBA entries mid-play (locked, and both sync cards) | 5 of 21 → **3 of 21** (#39) | as is; better than today (10 of 20); no flag improves it |
| NFL, stale feed: big plays seen | 94.3 % → 94.3 % | as is; the train + valid nights pass at 96.1 %; not tuned on locked nights |
| NFL, stale feed: your-team big plays seen | 95.2 % → **100 %** (#38) | passes now |
| NBA, stale feed: entries / big plays seen | 6 of 13 → 4 of 13 mid-play; 68.8 % | as is |

**Flags off for anything unmeasured or leaky:** the brain (`brain.enabled: false`, its models saw the
locked nights), reasoner, multiview (#32), reels/highlight jump, auto-tune. FastCast only watches (#33).

| gate | scope | status | value |
|---|---|---|---|
| mid-play cuts = 0 | nfl | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nfl | ✅ PASS | 100.0% (430/430) |
| entries mid-play <= 5% | nfl | ✅ PASS | 0.0% (0/430) |
| entries mid-play never above today | nfl | ✅ PASS | 0 vs today 0 |
| big plays seen >= 95% | nfl | ✅ PASS | 95.3% |
| your-team big plays seen = 100% | nfl | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl | ⏳ PENDING | 73.4% (ceiling: pending) |
| mid-play cuts = 0 | nba | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nba | ❌ FAIL | 85.7% (18/21) |
| entries mid-play <= 5% | nba | ❌ FAIL | 14.3% (3/21) |
| entries mid-play never above today | nba | ✅ PASS | 3 vs today 10 |
| big plays seen >= 95% | nba | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nba | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nhl | ✅ PASS | 100.0% (137/137) |
| entries mid-play <= 5% | nhl | ✅ PASS | 0.0% (0/137) |
| entries mid-play never above today | nhl | ✅ PASS | 0 vs today 2 |
| big plays seen >= 95% | nhl | ✅ PASS | 99.0% |
| your-team crunch live = 100% | nhl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_sync+3 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nfl_sync+3 | ✅ PASS | 100.0% (430/430) |
| entries mid-play <= 5% | nfl_sync+3 | ✅ PASS | 0.0% (0/430) |
| big plays seen >= 95% | nfl_sync+3 | ✅ PASS | 95.3% |
| your-team big plays seen = 100% | nfl_sync+3 | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl_sync+3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_sync+3 | ⏳ PENDING | 73.4% (ceiling: pending) |
| mid-play cuts = 0 | nba_sync+3 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nba_sync+3 | ❌ FAIL | 85.7% (18/21) |
| entries mid-play <= 5% | nba_sync+3 | ❌ FAIL | 14.3% (3/21) |
| big plays seen >= 95% | nba_sync+3 | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nba_sync+3 | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_sync+3 | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl_sync+3 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nhl_sync+3 | ✅ PASS | 100.0% (137/137) |
| entries mid-play <= 5% | nhl_sync+3 | ✅ PASS | 0.0% (0/137) |
| big plays seen >= 95% | nhl_sync+3 | ✅ PASS | 99.0% |
| your-team crunch live = 100% | nhl_sync+3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_sync+3 | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_sync-3 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nfl_sync-3 | ✅ PASS | 100.0% (430/430) |
| entries mid-play <= 5% | nfl_sync-3 | ✅ PASS | 0.0% (0/430) |
| big plays seen >= 95% | nfl_sync-3 | ✅ PASS | 95.3% |
| your-team big plays seen = 100% | nfl_sync-3 | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl_sync-3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_sync-3 | ⏳ PENDING | 73.4% (ceiling: pending) |
| mid-play cuts = 0 | nba_sync-3 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nba_sync-3 | ❌ FAIL | 85.7% (18/21) |
| entries mid-play <= 5% | nba_sync-3 | ❌ FAIL | 14.3% (3/21) |
| big plays seen >= 95% | nba_sync-3 | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nba_sync-3 | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_sync-3 | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl_sync-3 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nhl_sync-3 | ✅ PASS | 100.0% (137/137) |
| entries mid-play <= 5% | nhl_sync-3 | ✅ PASS | 0.0% (0/137) |
| big plays seen >= 95% | nhl_sync-3 | ✅ PASS | 99.0% |
| your-team crunch live = 100% | nhl_sync-3 | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_sync-3 | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | nfl_stale | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nfl_stale | ✅ PASS | 99.7% (386/387) |
| entries mid-play <= 5% | nfl_stale | ✅ PASS | 0.3% (1/387) |
| big plays seen >= 95% | nfl_stale | ❌ FAIL | 94.3% |
| your-team big plays seen = 100% | nfl_stale | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl_stale | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nfl_stale | ⏳ PENDING | 68.4% (ceiling: pending) |
| mid-play cuts = 0 | nba_stale | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nba_stale | ❌ FAIL | 69.2% (9/13) |
| entries mid-play <= 5% | nba_stale | ❌ FAIL | 30.8% (4/13) |
| big plays seen >= 95% | nba_stale | ❌ FAIL | 68.8% |
| your-team crunch live = 100% | nba_stale | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba_stale | ⏳ PENDING | 81.0% (ceiling: pending) |
| mid-play cuts = 0 | nhl_stale | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nhl_stale | ✅ PASS | 98.4% (121/123) |
| entries mid-play <= 5% | nhl_stale | ✅ PASS | 1.6% (2/123) |
| big plays seen >= 95% | nhl_stale | ✅ PASS | 99.0% |
| your-team crunch live = 100% | nhl_stale | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl_stale | ⏳ PENDING | 91.9% (ceiling: pending) |
| mid-play cuts = 0 | demo_20261101 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | demo_20261101 | ✅ PASS | 99.0% (190/192) |
| entries mid-play <= 5% | demo_20261101 | ✅ PASS | 1.0% (2/192) |
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

59 pass, 10 fail, 27 pending (pending never counts as passing).
