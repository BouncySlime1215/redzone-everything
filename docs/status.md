# Status board

Updated on every merge into `integration/next` by INTEGRATION. Last update: 2026-11-03 17:00 UTC,
`integration/next` = 5f0e6a1 + #1 (trip budget) + the decision-core interface (23f30d0) +
contracts v1 + #7 (CI) + d0c775a (`core.SwitchPath` gains `stick`, `fastest_s`, `samples`) +
#3 (NHL, with the entry fix) + #11 (SyncEstimate v2) + #10 (traces + nightly report) + #12 (network
awareness). Tests: 1339 passed, 20 skipped, 0 failed; ruff clean. Resumed 18:01 UTC; #14, #2 and #8 merged since, tests 1417 pass.

## Pinned eval data

Every owner fetches the pinned archive with `python scripts/fetch_eval_data.py` once PR #14 is
merged. It downloads release `eval-data-v1` into `data/archive` + `data/eval_config.yaml`. Every
scorecard must report `"eval_data": "eval-data-v1"`. Nobody copies archives from the Mac anymore.

## Next steps for the integration train (resume here)

1. Merge #14 (fetch_eval_data) so CI's scorecards grade.
2. Run the full scorecards (NFL/NBA/NHL + the Nov 1 demo) on the base and on `integration/next`,
   and fill the pending cells in `docs/integration.md`. Revert any merge that made the
   combination worse.
3. Merge #2 right after #14, and keep it inside the scorecard checks. The user chose "Keep drive
   gain, 9.7 % dead air" on #2's decision card at 17:06. Then re-measure #4.
4. Merge #8 (sync; rebased on f2a494d, green at 163c61c, 1385 tests, scorecards identical to base) once the DECISION review of screen.py is done. Then #15 (platform; green, scorecards unchanged), #5, #6 and #9.
5. Engine glue: build `WorldState` in `Engine.decide` and fill `Screen.switch_in_flight` with
   `trace.switch_id(cmd_ts)` (from #10).

## Decisions from the user

- 2026-11-03: **PR #4 (Hype v2) is held** until PR #2's dead-air fix lands. Then it is re-measured on `integration/next`.
- 2026-11-03: **PR #8 (sync predictor): its NBA trade (124 → 132) is accepted.** It is recorded as the measured reason, so it does not block the merge.
- 2026-11-03 17:06: **PR #2 ships as "Keep drive gain, 9.7 % dead air".** Drives live 13.1 → 19.6 % and big plays live 29.9 → 33.6 %, while NFL dead air goes 8.6 → 9.7 % (on demo day, commercials drop 15.7 → 15.6 %). This is the measured reason the merge rule needs.
- 2026-11-03: PR #1 is approved as it is. Order: #1, then #3 after its entry fix, then #2 after its dead-air fix.

## Thread cap and queue

Once the threads running now finish, **at most 5 threads run at once**. The rest wait here in
dependency order (see `docs/architecture.md` §5). A thread starts only when everything it
depends on has merged into `integration/next`.

| # | Queued work | Owner | Waits on |
|---|---|---|---|
| 1 | Pinned eval dataset (`eval-data-v1` release + `data/eval_config.yaml`) | INTEGRATION | nothing; this blocks every scorecard |
| 2 | Golden nights in CI (contract tests already run in CI with #7) | INTEGRATION | pinned data |
| 3 | Decider builds `CoreState` from `WorldState`; H3/H4 move into core guards | DECISION | contracts v1 (done) |
| 4 | BreakModel quantiles (q10/q50/q90) behind `BreakModel` | MOMENT/BREAK | contracts v1 (done) |
| 5 | SwitchPaths native implementation (measured p90, `fastest_s`; fields added in d0c775a) | SWITCHING | #5 merged |
| 6 | Health/Trace emitters (`redzone/platform/faults.py`, `data/traces.jsonl`) | PLATFORM | contracts v1 (done) |
| 7 | End-to-end replayed night test (fake sticks/TV, degraded modes) | INTEGRATION | 3, 5, 6 |
| 8 | Highlight jump as `ReelJumper` | DECISION | 3 |
| 9 | Reasoner lock-in via `ReasonerView` | LOCAL AI | 3, LLM server RAM budget |
| 10 | Deploy + rollback, nightly report, error budget | PLATFORM | 6, 7 |

## Owners

| Owner | Done | Next | Blocked |
|---|---|---|---|
| DECISION | #1 trip budget and #3 NHL merged here (NHL entries mid-play 11 → 5, missed on entry 1 → 0; crunch 87.5 → 86.3 standard, 85.9 → 87.2 with randomized arrival, per the thread). Core interface (`core.py`) is in. | #2 dead-air fix (≤ 8.6 %); build `CoreState` from `WorldState` | #2 waits on the user's dead-air setting |
| MOMENT/BREAK | Hype v2 retrain PR #4 open, **held until #2's fix lands** (retarget to `integration/next`) | BreakModel quantiles; Brain v3 behind `MomentModel` | pinned data |
| LOCAL AI | watchdog rules + policy guard on the branch | reasoner `ReasonerView`; one LLM server ≤ 1.5 GB | none |
| SWITCHING | PR #5 instant input switching open | native `SwitchPaths`; `fastest_s` interface request | none |
| SYNC | #11 contract v2 merged; #8 delay predictor open, NBA trade 124 → 132 accepted | move `DelayBook` into `redzone/sync/` behind `SyncModel` | none |
| PLATFORM | #10 traces + nightly report and #12 network awareness merged here; #6 governor and #9 deploy open | merge #6, #9 | none |
| INTEGRATION | `integration/next`, contracts v1, architecture doc; merged #1, #3, #7, #10, #11, #12 | the steps above, then golden nights and the e2e night test | scorecards wait on #14 |

## Latest numbers vs targets

All numbers come from the full scorecards on pinned  (every card reports
). "Today" is  5f0e6a1, and "now" is
 after #1, #3, #7, #10, #11, #12, #14, #2 and #8.

The target is the user's "100 % on every measure, no errors". Where 100 % is physically
impossible, the feasible ceiling is computed from the data and the gap is measured against it.
Crunch conflicts follow the user's league order **NFL > CFB > NBA > the rest**: the winning game
counts and must be 100 % live, and a lower-priority overlap that gets a replay is not a miss.
**The ceiling computation is not built yet (INTEGRATION, next up), so every ceiling below reads
"pending".**

| Line | Today | Now | Target | Feasible ceiling | Gap owner |
|---|---|---|---|---|---|
| Mid-play cuts (all cards) | 0 | 0 | 0 | 0 | DECISION |
| NFL entries mid-play / missed on entry | 0 / 0 | 0 / 0 | 0 / 0 | 0 | DECISION |
| NBA entries mid-play / missed on entry | 38 / 18 (of 97) | 38 / 18 (of 95) | 0 / 0 | pending | DECISION (biggest gap) |
| NHL entries mid-play / missed on entry | 11 / 1 | 5 / 0 | 0 / 0 | pending | DECISION (drop the imminent-chase skip: 5 → 4) |
| Demo entries mid-play | 2 | 4 | 0 | pending | DECISION (the 2 added are #3's imminent chases) |
| NFL dead air | 8.6 % | 9.7 % (accepted for #2) | 0 % | pending | DECISION + MOMENT/BREAK |
| NBA / NHL dead air | 19.4 / 24.3 % | 19.4 / 24.1 % | 0 % | pending | DECISION + MOMENT/BREAK |
| Demo commercials on screen | 15.7 % | 15.3 % | 0 % | pending | DECISION |
| NFL big plays seen / live | 95.4 / 29.9 % | 96.4 / 33.6 % | 100 / → 100 % | pending | DECISION, SYNC (re-sync modelling shows live 54.6 %) |
| NBA big plays seen | 92.0 % | 92.0 % | 100 % | pending | DECISION |
| NHL goals seen / live | 99.5 / 41.7 % | 99.7 / 41.5 % | 100 / → 100 % | pending | DECISION |
| NFL / NBA / NHL crunch live | 70.0 / 96.3 / 87.5 % | 73.5 / 96.3 / 86.3 % | 100 % of the priority winner | pending | DECISION |
| NFL drives live | 13.1 % | 19.6 % | → 100 % | pending | DECISION |
| Your-team crunch live / big seen | 100 / 100 % | 100 / 100 % | 100 / 100 % | 100 | DECISION |
| Switch seconds (preloaded) | not measured | not measured | < 3 s on ≥ 95 % | | SWITCHING |
| Sync error | not measured | not measured | < 2 s on ≥ 95 % | | SYNC |
| RAM, service + LLM | | | ≤ 1.5 GB for models | | LOCAL AI / PLATFORM |
| Tests | 1228 pass | 1417 pass, 0 fail, ruff clean | 0 fail | | INTEGRATION |

## Release gate on held-out nights (scripts/release_gate.py, now)

| gate | scope | status | value |
|---|---|---|---|
| mid-play cuts = 0 | nfl | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nfl | ✅ PASS | 100.0% (93/93) |
| entries mid-play <= 5% | nfl | ✅ PASS | 0.0% (0/93) |
| entries mid-play never above today | nfl | ✅ PASS | 0 vs today 0 |
| big plays seen >= 95% | nfl | ✅ PASS | 99.0% |
| your-team big plays seen = 100% | nfl | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nfl | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nfl | ⏳ PENDING | 74.7% (ceiling: pending) |
| mid-play cuts = 0 | nba | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nba | ❌ FAIL | 50.0% (10/20) |
| entries mid-play <= 5% | nba | ❌ FAIL | 50.0% (10/20) |
| entries mid-play never above today | nba | ✅ PASS | 10 vs today 10 |
| big plays seen >= 95% | nba | ✅ PASS | 100.0% |
| your-team crunch live = 100% | nba | ⏳ PENDING | no your-team crunch plays in this slice (0/0) |
| crunch live >= 95% or feasible ceiling | nba | ⏳ PENDING | 94.4% (ceiling: pending) |
| mid-play cuts = 0 | nhl | ✅ PASS | 0 |
| entries at a dead ball >= 95% | nhl | ✅ PASS | 97.8% (133/136) |
| entries mid-play <= 5% | nhl | ✅ PASS | 2.2% (3/136) |
| entries mid-play never above today | nhl | ❌ FAIL | 3 vs today 2 |
| big plays seen >= 95% | nhl | ✅ PASS | 99.0% |
| your-team crunch live = 100% | nhl | ✅ PASS | 100.0% |
| crunch live >= 95% or feasible ceiling | nhl | ⏳ PENDING | 90.2% (ceiling: pending) |
| mid-play cuts = 0 | demo_20261101 | ✅ PASS | 0 |
| entries at a dead ball >= 95% | demo_20261101 | ✅ PASS | 98.0% (197/201) |
| entries mid-play <= 5% | demo_20261101 | ✅ PASS | 2.0% (4/201) |
| entries mid-play never above today | demo_20261101 | ❌ FAIL | 4 vs today 2 |
| big plays seen >= 95% (NFL) | demo_20261101 | ✅ PASS | 137/137 (100%) |
| big plays seen >= 95% (NHL) | demo_20261101 | ✅ PASS | 36/36 (100%) |
| crunch live >= 95% or feasible ceiling | demo_20261101 | ⏳ PENDING | 76.6% (ceiling: pending) |
| reels >= 95% in dead time, 0 live big plays missed on a reel | system | ⏳ PENDING | reel scorecard line not built yet |
| reasoner >= 95% agreement, unsafe leaves <= 0.5%, 100% valid JSON | system | ⏳ PENDING | no gold labels yet |
| watchdog >= 95% correct, every kind >= 90%, 0 unsafe | system | ⏳ PENDING | no held-out error set yet |
| sync error < 2 s on >= 95% of samples | system | ⏳ PENDING | needs real screen samples (Mac) |
| preloaded switches < 3 s on >= 95% | system | ⏳ PENDING | TV path not working yet |
| fault-injection night: >= 95% minutes correct picture, all faults recovered | system | ⏳ PENDING | e2e night test not merged |

19 pass, 4 fail, 12 pending (pending never counts as passing).

The FAILs are NBA entries at a dead ball and NBA entries mid-play (50 %), and entries mid-play
above today on NHL (3 vs 2) and on the demo (4 vs 2). The last two
both come from #3's imminent-chase skip.
