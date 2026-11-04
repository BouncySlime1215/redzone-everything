# Status board

Updated on every merge into `integration/next` by INTEGRATION. Last update: 2026-11-03 21:50 UTC.
`integration/next` has #1, #2, #3, #5, #6, #7, #8, #9, #10, #11, #12, #14, #15, #17 (reasoner, off), #19, #20,
#21, #22, #23, #24, #25, #26 (Brain v3), #27, #28, #29, #31, #32, #33, #34, #35 and the no-overfitting gate. Tests: 1704 passed, 0 failed (one e2e test is
timing-sensitive under parallel load and passes alone); ruff clean. **Tonight's release candidate is RC 3** (RC 2 + deploy test-step fixes, no app change; `docs/gate-report-rc3.md`). RC 2 was on
`integration/release`** (integration/next after #38 and #39; gate report in `docs/gate-report-rc2.md`: 59 pass, 10 fail, 27 pending). CI now also runs on pushes to integration/release.

## Usage rules from the user (hard)

- **All RedZone work stops at 90 % of the user's weekly Claude usage.** The last 10 % is for school.
  The user pauses the project at 88-89 %. Kaggle runs need no Claude usage and continue during a pause.
- From 75 %: no new threads; finish and merge what's open; keep this file current so a pause is resumable.
- Pacing (Nov 3, 4:30 PM ET, at ~44 %): reach ~90 % at 5 PM ET Wed. Checkpoints: <= 55 % at 10 PM,
  <= 62 % at 8 AM, <= 78 % at 1 PM, ~89 % at 5 PM. **Max 2 working threads (1 overnight, midnight-8 AM).**
  Lean turns: scorecards only for the leagues a change touches, no idle polling.
- Spend order: (1) tonight's release + checklist; (2) decision core merge (done); (3) Memory systems
  (#36, lands before 5 PM Wed); (4) Kaggle round-2 notebook fix; then the queue below.

## Board

| State | Work |
|---|---|
| Working | INTEGRATION (release cut, then #36 after 23:00 UTC); Memory systems (#36) |
| Waiting on the user | TV setup tonight with `docs/tonight-checklist.md` (deploy line, TV, multiview probe, FastCast check) |
| Paused, resumes when | auto-tune #16: decision core merged, so on the queue; highlight jump #18: Brain v3 break-length quantiles land; reasoner (#17 merged, off): the Kaggle adapter returns |
| Folded | Hype v2 #4 into Brain v3 #26; watchdog model into distill (#13) |
| Merged | see the list above and `docs/integration.md` |

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
| 9b | **Prime NBA multiview calibration** (PR: Prime multiview tuner, flag `tuners.prime_multiview` off) | SWITCHING | Needs Nick at home on a Prime NBA night, ~10 min, app paused: (1) add `multiview_exclude: ["nba:WSH"]` under `score.personal` in data/config.yaml; (2) `.venv/bin/python scripts/prime_multiview_probe.py --gti <the game's gti> --rounds 20` (writes data/prime_multiview_keys.json + data/lab/prime_multiview/<time>/probe.json); (3) if its bench gate passes, turn the flag on for that night and grade the night: >= 95% right tile under 2 s and 0 mid-play entries, else it stays off |
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
