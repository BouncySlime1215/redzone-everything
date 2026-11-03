# Integration train

`integration/next` starts at `codex/resume-redzone` 5f0e6a1. Finished PRs merge ONE AT A TIME in
the user's order. After each merge, the full test suite and ALL scorecards (NFL, NBA, NHL and the
Nov 1 mixed demo) run on the pinned archive. A PR that makes the combination worse is dropped,
even if it won alone. Conflicts in `decider.py`/`engine.py` are resolved by hand, never with "take
ours". The end state is one release PR, `integration/next` → `codex/resume-redzone`.

Order: #1 trip budget → #3 NHL (after its entry fix) → #2 drive priority (after its dead-air fix)
→ Hype v2 → Brain v3 → auto-tune → sync → reasoner/watchdog. Infrastructure that changes no
decision (contracts, CI) merges whenever it is ready.

| Step | Merged | Commit | Tests | Mid-play cuts | NFL big live / seen | NFL crunch live | NFL your-team | NFL dead air | NHL goals live | NHL entries mid-play / missed on entry | NBA crunch live | NBA entries mid-play | Nov 1 demo | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | base 5f0e6a1 | 5f0e6a1 | 1228 pass / 0 fail | pending | 29.9 / 95.4 (prior run) | 70.0 | 100 | 8.6 | 41.7 | 11 / 1 | 96.3 | 38 | pending | baseline |
| 1 | #1 trip budget | dbb33cc | 1241 pass / 0 fail | pending | pending | pending | pending | pending | pending | pending | pending | pending | pending | approved by the user; scorecards pending the archive |
| 1a | decision-core interface (23f30d0) + contracts v1 | 418ffc5 | 1263 pass / 0 fail | no decision change | | | | | | | | | | infrastructure |
| 1b | #7 CI on every PR + d0c775a SwitchPath fields | 48b5948 | 1271 pass / 0 fail, ruff clean | no decision change | | | | | | | | | | infrastructure; CI scorecards need the `eval-data-v1` release |
| 2 | #3 NHL (entry fix e21a0b0) | (merge) | 1285 pass / 0 fail, ruff clean | pending | pending | pending | pending | pending | pending | 5 / 0 (thread's run) | pending | pending | pending | merged clean (no conflicts with #1 in decider.py; reviewed the overlap by hand); crunch trade stated in #3; scorecards on the combination pending the archive, and it is reverted if the combination is worse |
| 2a | #11 SyncEstimate v2 | 0ed7a1b | 1286 pass / 0 fail | no decision change | | | | | | | | | | contract, additive |
| 2b | #10 traces + nightly report | fe02385 | 1309 pass / 0 fail | pending | | | | | | | | | | merged clean; touches engine/control/supervisor |
| 2c | #12 network awareness | 565767e | 1339 pass / 0 fail | pending | | | | | | | | | | merged clean |

Scorecard numbers marked "prior run" or "thread's run" come from the threads' own runs on the same window. Every
pending cell gets filled from the pinned archive.
