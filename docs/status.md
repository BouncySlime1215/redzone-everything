# Status board

Updated on every merge into `integration/next` by INTEGRATION. Last update: 2026-11-04 05:45 UTC.
`integration/next` has #1-#3, #5-#12, #14, #15, #17 (reasoner, off), #19-#36, #38-#56 and the RC 3 deploy fixes
(see `docs/integration.md`). Tests: 1843 passed, 11 skipped; ruff clean; leak_check 15 pass / 0 fail / 13 pending.
#51 eval honesty (night-bootstrap gate; locked gate 41/10/45), #52-#56 timing (SmartThings 2 s cost, behind-live scoring, landing histograms, source race, NHL 120 s break floor).
**Shifts (Nick, Nov 4 12:45 AM, handoff.md §6b):** day shift 9 AM-11 PM is Claude's threads; night shift 11 PM-9 AM is Nick's cloud sessions.
**Release: RC 4 on `integration/release` = 8a47053** (RC 2 + deploy test-step fixes + the e2e night pinning
the host and the network; no app change, so the gates are RC 2's: 59 pass, 10 fail, 27 pending, `docs/gate-report-rc3.md`).
**RC 5 candidate f1034b5** (not cut): locked gate 49 pass / 16 fail / 52 pending, triage in `docs/gate-report-rc5.md`.
The draft release PR #37 is closed (Nick, Nov 3 10:45 PM); the deploy fetches `integration/release`.

## Usage rules from the user (hard)

- **All RedZone work stops at 90 % of the user's weekly Claude usage.** The last 10 % is for school.
  The user pauses the project at 88-89 %. Kaggle runs need no Claude usage and continue during a pause.
- From 75 %: no new threads; finish and merge what's open; keep this file current so a pause is resumable.
- Pacing (Nick, Nov 3 10:45 PM ET, at 48 %): work non-stop to **89 % at 5 PM ET Wed**. Checkpoints
  **62 % at 8 AM, 78 % at 1 PM**. Behind pace: **3 working threads overnight, up to 5 when needed**; more than
  3 points ahead, drop threads. Lean turns: scorecards only for the leagues a change touches, no idle polling.
- Overnight focus: **switch timing** and **error handling everywhere**. No live TV or stick tests tonight
  (Nick tests Wed evening). Mac light. 95 % gates on locked unseen nights; anything that misses ships OFF.

## Board (one owner per area)

| Area | Owner | Now |
|---|---|---|
| **NOGGIN** (Nick, Nov 4 1:45 AM: #1 focus; one brain, three layers, `docs/noggin.md`). Reflex: data factory, Noggin Timing v1 (absorbs A3), Predict, Pick (absorbs Brain v3, decider weights, auto-tune #16). Mind: distill #13 / Kaggle V5, live reasoner, watchdog adapter. Teacher: Kaggle 32B labels and night reviews. Reflex input #1: the live time-shift buffer (#61) | NOGGIN ("Noggin training" thread); sub-tracks: LOCAL AI/distill (Mind), TIMING (buffer #61, feed lag, SmartThings) | Phase 1: data factory + Timing v1 (NBA entries mid-play 9 -> 0 at B=90, ECE <= 0.10) |
| Switch timing (Nick's items 1-7: dead-ball lag per source, live-delay offset per stick, restart prediction, SmartThings ~2 s trip cost, dwell/hysteresis, landing-offset scorecard line, e2e SmartThings latency) | TIMING thread (new, Nov 3 night) | working |
| Error handling (items 8-12: SmartThings breaker + HOME/CEC fallback, stick recovery, feed fallback, Mac sleep/network/launchd, watchdog rate limits) | ERRORS thread (new, Nov 3 night) **Done:** #44 SmartThings, #48 sticks, #49 feeds, #50 Mac wake and #45 watchdog cap merged. All OFF behind `hardening.*` except #45's hourly action cap (on). Turn each on after Nick's TV test. **Open:** #57 (rebooted on-screen stick, launchd exit codes), C17 watchdog actions PR (`hardening.watchdog`, off), #63 Wed live-test script. **Next:** why FastCast never logged on the Mac (needs Mac log read) |
| Integration train, CI, release, housekeeping (items 13a-d, 14, 16) | INTEGRATION #41-#43 merged; #44-#50 merged; #47 TIMING honest feed_lag + NHL lag-cap merged; #51 eval honesty up (idle until 8 AM) |
| Kaggle teacher (item 13e: `pip uninstall torchao` on kaggle-inputs; V4 running with Nick's patch) | LOCAL AI (#13) | **D19 ready on kaggle-inputs; V5 running** |
| Prime multiview live-calibration plan (item 15, #40 flag off) | SWITCHING | **Done:** #40 merged, flag off; calibration plan is queue row 9b |
| Memory systems (item 14) | MEMORY (#36 merged) | keep current |
| Paused with a dependency | auto-tune (#16 salvaged into #135, lab only), highlight jump #18 (closed; OFF: NBA 82% < 95%), live reasoner (queued; Kaggle adapter), brain leak-fix retrain | see queue |

## Research queue A-E (Nick, Nov 3 11:30 PM; `docs/research/2026-11-03/00-PLAN.md`, #46)

It merges into the overnight list; timing comes first. Architecture: GBMs and rules own timing; the 1.7B LLM
writes words only, async. Gates only on unseen nights.

| Item | Work | Owner |
|---|---|---|
| A1 | Honest baseline: `feed_lag` exposed in `scripts/eval.py` / `eval_sports.py`, re-run with measured lags (NBA 38, CBB 35, NFL 45, NHL 49 s) | TIMING |
| A2 | FastCast on every league + league-origin feeds (NHL `clock.running`, NBA `timeActual`, MLB pitch times), per-source lag logged | TIMING |
| A3 | One shared restart countdown (KM, then boosted hazard, then conformal lower bounds) replacing `jump.py`'s p10 and the 0.6x/1.6x quantiles | NOGGIN (Timing v1; moved from TIMING Nov 4) |
| A4 | Rule features: national vs local break lengths, full TV-timeout rules per league | TIMING |
| A5 | Per-(app, channel, tune) stream-delay Kalman filter fed by Prime clock OCR | TIMING (with SYNC) |
| A6 | Commit a cut only at P_safe >= 99.5 % over the ~3.5 s flip + settle window; SmartThings ~2 s trip cost | TIMING |
| A7 | Scorecard lines: landing/leave-offset histograms, % landings in dead time, mid-play per 100 switches (Wilson CI) | TIMING |
| A8 | Hidden-stick prediction (next target in 180 s) | TIMING (after A3) |
| B9 | Cluster-bootstrap over nights; PASS needs >= 8 nights, >= 60 decisions and lower bound >= 95 %, else PENDING | INTEGRATION |
| B10 | Leaks: 2 locked games in the e2e night (NHL 401892626, NBA 401909906), `parse_summary` leaders, `scripts/leak_check.py` | INTEGRATION |
| B11 | Reasoner gate split (rule conformance, calibration, ON/OFF non-inferiority, abstain) | LOCAL AI |
| B12 | Realistic night lab (lag jitter, late plays, drops, resyncs from real traces) | INTEGRATION (after B9-B10) |
| C13-C16 | Calibrated safe-now with abstain; next big play; stakes layer; reel ranker | MOMENT/BREAK (C13 with LOCAL AI) |
| C17 | Watchdog 5 -> 12 guarded actions, all 13 kinds | ERRORS (PR open, off behind `hardening.watchdog`) |
| C18 | Bugs: `breaks.blend` 50/50 calibration, two JSON parsers, switch-cost constants disagreeing | INTEGRATION (switch costs with TIMING) |
| D19-D21 | Kaggle student recipe (fp16 base + fp32 LoRA r16-32), teacher throughput, KD | LOCAL AI (under NOGGIN: Mind) |
| D22 | Historical label factory (data factory) | NOGGIN |
| E23-E25 | Decision records + phone 👍/👎, shadow mode + SPRT rollback, BRAIN auto-fix on dev-test only | DECISION (E25 PLATFORM) |

## Queue (refreshed Nov 4, 3:30 PM ET, per Nick's 3:05 PM order; brake lifted, all 5 lanes run)

Gates: big plays live >= 95% of oracle per league, live or replayed within 3 min (stretch 2 min), mid-play 0,
dead-time landings >= 98% (#121). Everything new ships flag OFF until it passes on locked + fresh nights.

**Open PRs, in merge order**

| # | PR | Work | Lane | Resume point |
|---|---|---|---|---|
| 1 | #133 + #132 | YouTube TV seek as measured (15 s a press, first press swallowed) + entries that use it | ② (#133 Nick) | merge together: #132 contains #133, and #133 alone double-counts the wake press and makes mid-play entries on locked (NFL 12, NHL 14); #132 takes them to 0. Waiting on #132 to be green |
| 2 | #116 | Possession-start entry + no landing in a data gap (candidate) | ② | conflicts with integration/next; lane 2 to merge it in (read the merged northstar table) |
| 3 | #125 | Honest sync from the live lab: YTTV positions, real flips, 3 s floor | ② | draft; after #132 |
| 4 | #131 | NHL leaves on pbp dead windows (candidate) | ② | draft |
| 5 | #119 | Shadow probe for the fastest free NFL/CFB score feed | ② | draft |
| 6 | #129 | Possession-start oracle + learned possession policy (PICK cue), flag off | ① | draft; after #116 |
| 7 | #122 | Noggin data factory on Kaggle CPU (resumable shards, oracle self-play) | ① | draft; Nick launches Kaggle |
| 8 | #130 | Replay and resume (flag off): a catch-up costs buffer, not live play | ②/③ | draft; hardware gate in docs/timing.md (lane 3 night lab) |
| 9 | #135 | Decider auto-tuner salvaged from #16 (locked split held out) | Integrator → ② | draft; lab tool, then hook to % of oracle |
| 10 | #112, #113 | No-Nick-signal and FastCast shadow clock | ①, ② | likely superseded by #111 / #117 (merged); the owner confirms and closes |
| 11 | #13 | Kaggle teacher/student (Mind) source: agent/kaggle, agent/distill | ① | salvage into a fresh PR on integration/next (V7 runs from kaggle-inputs' code pack, the source lives only on this branch), then close #13 |
| 12 | #26 | Brain v3 | ① | old draft; absorbed by Noggin Reflex; owner decides |

**New builds (Nick, 3:05 PM), queued in this order, flag off until gated**

| # | Build | Lane |
|---|---|---|
| a | Night health report every morning: last night's North Star lines, misses, sync p95, press-to-TV, auto-written to docs/nights/ | ④ |
| b | Smarter stick assignment: hottest game on the 4K Plus (.150); hidden-stick prediction feeds it | ② |
| c | Self-calibration: seek step, flip time and per-channel delay re-measured each night from live signals, no Nick work | ②/③ |
| d | Pre-game warm-up: tune and buffer both sticks 10 min before the first tip/kick | ③ |
| e | Buffer watchdog: detect a lost pause buffer or drift > 2 s and rebuild it | ③ |

**Closed in triage (Nov 4):** #20, #23, #24 (superseded on integration/next, nothing to salvage), #16 (salvaged
into #135), #18 (never met its bar; branch kept for the reel jump/stay model).
**Standing:** eval-data-v3 locked switch Fri Nov 6 (④); Prime NBA multiview calibration (PR #40, next Prime NBA night).

## Latest numbers vs targets

All numbers come from the full scorecards on pinned `eval-data-v1` (every card reports
`"eval_data": "eval-data-v1"`). "Today" is `codex/resume-redzone` 5f0e6a1, and "now" is
`integration/next` after #1, #3, #7, #10, #11, #12, #14, #2 and #8.

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

## Release gate (older held-out slice; RC 1 on the locked set is in docs/gate-report-rc1.md)

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
