# Status board

Updated on every merge into `integration/next` by INTEGRATION. Last update: 2026-11-03 17:00 UTC,
`integration/next` = 5f0e6a1 + #1 (trip budget) + the decision-core interface (23f30d0) +
contracts v1 + #7 (CI) + d0c775a (`core.SwitchPath` gains `stick`, `fastest_s`, `samples`).

## Decisions from the user

- 2026-11-03: **PR #4 (Hype v2) is held** until PR #2's dead-air fix lands. Then it is re-measured on `integration/next`.
- 2026-11-03: **PR #8 (sync predictor): its NBA trade (124 → 132) is accepted.** It is recorded as the measured reason, so it does not block the merge.
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
| DECISION | #1 trip budget merged here. Core interface (`core.py`) is in. | #3 NHL entry fix (mid-play entries ≤ 11, missed on entry ≤ 1); #2 dead-air fix (≤ 8.6 %); build `CoreState` from `WorldState` | none |
| MOMENT/BREAK | Hype v2 retrain PR #4 open, **held until #2's fix lands** (retarget to `integration/next`) | BreakModel quantiles; Brain v3 behind `MomentModel` | pinned data |
| LOCAL AI | watchdog rules + policy guard on the branch | reasoner `ReasonerView`; one LLM server ≤ 1.5 GB | none |
| SWITCHING | PR #5 instant input switching open | native `SwitchPaths`; `fastest_s` interface request | none |
| SYNC | PR #8 delay predictor open; NBA trade 124 → 132 accepted (retarget to `integration/next`) | move `DelayBook` into `redzone/sync/` behind `SyncModel` | none |
| PLATFORM | PR #6 resource governor open | faults/trace emitters, network awareness, deploy | none |
| INTEGRATION | `integration/next`, contracts v1 + 22 contract tests, architecture doc, #7 CI merged | pinned data, golden nights, e2e night test | eval archive (waiting on the Mac connection or the `eval-data-v1` release) |

## Latest numbers (baseline to protect)

Scorecards on the Nov 1 window (`EVAL_END 20261101`). Every line is re-run after each merge,
and the table in `docs/integration.md` has the per-merge history.

| Line | Today (codex/resume-redzone) | integration/next |
|---|---|---|
| Mid-play cuts | 0 | pending archive |
| NHL entries mid-play | 11 | pending |
| NHL plays missed on entry | 1 | pending |
| NBA entries mid-play | 38 | pending |
| NFL dead air (commercials on screen) | 8.6 % | pending |
| NFL big plays live / seen | 29.9 % / 95.4 % | pending |
| NFL crunch live | 70.0 % | pending |
| NFL your-team big | 100 % | pending |
| NHL crunch live / goals live / goals seen | 87.5 % / 41.7 % / 99.5 % | pending |
| NBA big seen / crunch live | 92.0 % / 96.3 % | pending |
| Switch seconds (median, per path) | not measured yet (SWITCHING) | |
| Sync error (median abs, s) | not measured yet (SYNC) | |
| RAM, service + LLM | ≤ 1.5 GB budget for models (LOCAL AI / PLATFORM) | |
| Tests | 1228 passed, 20 skipped, 0 failed | 1271 passed, 20 skipped, 0 failed; ruff clean |
