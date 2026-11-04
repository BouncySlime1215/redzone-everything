# Status board

Updated on every merge into `integration/next` by INTEGRATION. Last update: 2026-11-04 05:45 UTC.
`integration/next` has #1-#3, #5-#12, #14, #15, #17 (reasoner, off), #19-#36, #38-#56 and the RC 3 deploy fixes
(see `docs/integration.md`). Tests: 1843 passed, 11 skipped; ruff clean; leak_check 15 pass / 0 fail / 13 pending.
#51 eval honesty (night-bootstrap gate; locked gate 41/10/45), #52-#56 timing (SmartThings 2 s cost, behind-live scoring, landing histograms, source race, NHL 120 s break floor).
**Shifts (Nick, Nov 4 12:45 AM, handoff.md §6b):** day shift 9 AM-11 PM is Claude's threads; night shift 11 PM-9 AM is Nick's cloud sessions.
**Release: RC 4 on `integration/release` = 8a47053** (RC 2 + deploy test-step fixes + the e2e night pinning
the host and the network; no app change, so the gates are RC 2's: 59 pass, 10 fail, 27 pending, `docs/gate-report-rc3.md`).
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
| Switch timing (Nick's items 1-7: dead-ball lag per source, live-delay offset per stick, restart prediction, SmartThings ~2 s trip cost, dwell/hysteresis, landing-offset scorecard line, e2e SmartThings latency) | TIMING thread (new, Nov 3 night) | working |
| Error handling (items 8-12: SmartThings breaker + HOME/CEC fallback, stick recovery, feed fallback, Mac sleep/network/launchd, watchdog rate limits) | ERRORS thread (new, Nov 3 night) **Done:** #44 SmartThings, #48 sticks, #49 feeds, #50 Mac wake and #45 watchdog cap merged. All OFF behind `hardening.*` except #45's hourly action cap (on). Turn each on after Nick's TV test |
| Integration train, CI, release, housekeeping (items 13a-d, 14, 16) | INTEGRATION #41-#43 merged; #44-#50 merged; #47 TIMING honest feed_lag + NHL lag-cap merged; #51 eval honesty up (idle until 8 AM) |
| Kaggle teacher (item 13e: `pip uninstall torchao` on kaggle-inputs; V4 running with Nick's patch) | LOCAL AI (#13) | **D19 ready on kaggle-inputs; V5 running** |
| Prime multiview live-calibration plan (item 15, #40 flag off) | SWITCHING | **Done:** #40 merged, flag off; calibration plan is queue row 9b |
| Memory systems (item 14) | MEMORY (#36 merged) | keep current |
| Paused with a dependency | auto-tune #16 (paused: overfitting), highlight jump #18 (OFF: NBA 82% < 95%), live reasoner (queued; Kaggle adapter), brain leak-fix retrain | see queue |

## Research queue A-E (Nick, Nov 3 11:30 PM; `docs/research/2026-11-03/00-PLAN.md`, #46)

It merges into the overnight list; timing comes first. Architecture: GBMs and rules own timing; the 1.7B LLM
writes words only, async. Gates only on unseen nights.

| Item | Work | Owner |
|---|---|---|
| A1 | Honest baseline: `feed_lag` exposed in `scripts/eval.py` / `eval_sports.py`, re-run with measured lags (NBA 38, CBB 35, NFL 45, NHL 49 s) | TIMING |
| A2 | FastCast on every league + league-origin feeds (NHL `clock.running`, NBA `timeActual`, MLB pitch times), per-source lag logged | TIMING |
| A3 | One shared restart countdown (KM, then boosted hazard, then conformal lower bounds) replacing `jump.py`'s p10 and the 0.6x/1.6x quantiles | TIMING |
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
| C17 | Watchdog 5 -> 12 guarded actions, all 13 kinds | ERRORS |
| C18 | Bugs: `breaks.blend` 50/50 calibration, two JSON parsers, switch-cost constants disagreeing | INTEGRATION (switch costs with TIMING) |
| D19-D22 | Kaggle student recipe (fp16 base + fp32 LoRA r16-32), teacher throughput, KD, historical label factory | LOCAL AI |
| E23-E25 | Decision records + phone 👍/👎, shadow mode + SPRT rollback, BRAIN auto-fix on dev-test only | DECISION (E25 PLATFORM) |

## Queue (in order; at most 2 working threads)

| # | Work | Owner | Resume point |
|---|---|---|---|
| 1 | Memory systems #36 (CLAUDE.md, docs/memory/, context_pack.py) | Memory | merge after the release cut; afterwards every PR that changes `default_config.yaml`, `gate_baseline.json` or `release_gate.py` adds a dated line to `docs/memory/decisions.md` |
| 2 | Kaggle round-2 notebook fix + round-2 data builder | Distill (#13) | teacher run healthy (2,048/81,372 at 55 min) |
| 3 | Brain leak-fix retrain (train only before the locked cutoff), then `brain.enabled` back on if the gate holds | MOMENT/BREAK | brain (v2 + v3, #26 merged) ships off in RC 1 |
| 4 | Follow-up commits on #20 / #23 / #24 (feed staleness, health, crash-safe) since they merged, and the stale-feed gate rows | PLATFORM | branches have 3 / 5 / 6 new commits; retarget them as one PR on integration/next |
| 5 | #30 auto-fix loop | PLATFORM | green; config conflict with integration/next; after #36, also add the memory steps to `.github/auto-fix/prompt.md` and a decisions.md line |
| 6 | Watchdog model, step-400 checkpoint | LOCAL AI | branch agent-training d9d1813; checkpoint in /mnt/project-files/watchdog/run1/ (PEFT + MLX); next: grade step 400, verify conversion on 20 prompts, MLX server test, peak RSS on the Mac, RESULTS.md, draft PR (agent/train/NEXT.md). Watch any run until it finishes: the container restarts when idle |
| 7 | Stick roles (D) and stick tuning | SWITCHING | after the user's TV setup tonight |
| 8 | **RedZone as teacher** (legal: only the user's own YouTube TV, live or DVR; no downloaded episodes, no DRM workaround, no mic) | DECISION + SYNC | **Gate: a 5-minute check this Sunday — are YTTV screenshots DRM-black on the sticks? If yes, report and stop.** Then (1) `scripts/redzone_logger.py`: the hidden stick plays RedZone when not needed, every ~2 s score-bug OCR (vision_ocr/scorebug) logs which game is on; (2) align with ESPN PBP into labeled decisions; (3) imitation dataset + "RedZone agreement %" scorecard line (our gates still win: your teams, no commercials); (4) now: encode RedZone's public principles as gold rules (every TD live, red-zone drives, 2-minute drill, never commercials, whip-around at scores); (5) the same for whip-around specials (NHL Frozen Frenzy, NBA whip-arounds) |
| 9 | Auto-tune #16, highlight jump #18, reasoner on | DECISION / LOCAL AI | see Board |
| 9b | **Prime NBA multiview calibration** (PR #40, flag `tuners.prime_multiview` off) | SWITCHING | Next Prime NBA night, Nick at home, ~15 min: probe before tip (readable player? bench >= 95% right tile under 2 s), flag on for that night only, grade next morning with `scripts/prime_multiview_grade.py` (>= 20 switches, >= 95% under 2 s, 0 mid-play). Plan: [docs/prime-multiview-calibration.md](prime-multiview-calibration.md). Not tonight |
| 10 | Crunch feasible ceiling (NFL > CFB > NBA > rest), golden nights in CI, eval-data-v2 (GitHub Actions on the eval-data branch) | INTEGRATION | after the queue above |

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
