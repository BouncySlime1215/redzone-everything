---
id: P-BUILD
title: "The build plan: how the code becomes what the five planners want"
date: 2026-11-09
stream: planner
author: build analyst
status: draft
supersedes: []
sources: 0
verified: 0
affects: [docs/PLAN.md, redzone/plan.py, redzone/channels.py, redzone/discovery/providers.py, redzone/standby.py, redzone/engine.py, redzone/control.py, redzone/autonomy.py, redzone/fallback_ladder.py, redzone/governor.py, redzone/tuners/tuners.py, redzone/tuners/yttv_seek.py, redzone/tuners/yttv_label.py, redzone/decider/decider.py, redzone/decider/buffered.py, redzone/noggin/priority.py, redzone/reel.py, redzone/views.py, redzone/web/server.py, redzone/web/static/rz-now.js, redzone/web/static/rz-plan.js, redzone/userstate.py, scripts/buffered_jump_sim.py, scripts/fault_drill.py]
gate: []
---

# The build plan: how the code becomes what the five planners want

Planner ids: P1 = P-PRODUCT, P2 = P-BRAINCODE, P3 = P-QUEUE, P4 = P-MIND, P5 = P-MAJOR. Code read at worktree 73f696d8.

## Summary

1. Most of the queue already exists as parts. What is missing is one owner: ten code paths can write to the hidden stick, and three of them choose what it plays.
2. The Nov 8 routing bug is reproduced, and the cause is two lines of logic (section 2, row 1). It ships first.
3. Three planner requests would break repo rules as written: a tiered row inside frozen `lab/oracle.py`, a queue module at a path `contract_check` rejects, and moving eval data through protected files. Section 4 gives the legal version of each.
4. Two planner facts are out of date. P1-REVIEW-01 to -03 are already fixed, and `noggin.priority` is live for NFL and NBA through `league_overrides`.
5. Wave 1 is six file-disjoint packages, all without the TV. Waves 0-2 total about 70-80 hours and match P5's 72-84 hour list.

## 1. The build in one page

**What it is.** Two Fire TV sticks, each playing 60-90 s behind live. The feed already knows the next 40-65 s of every game, so most cuts are scheduled rather than guessed.

**Eight parts, in the order a game passes through them:**

| # | Part | Job | Kind |
|---|---|---|---|
| 1 | Route book | Game to app + ref, route class, alternates, "can't reach tonight" | Code. Facts |
| 2 | Queue | Readiness R0-R5, `prep_s`, authority ladder, reach, eviction. The only owner of the hidden stick | Code |
| 3 | Stick mechanics | Tune, buffer, park, typed failure, flip | Code |
| 4 | Moments | One Tier 1/2/3 list per league, live detectors, must-show debt with a deadline per route | Code |
| 5 | Show picker | `legal_options()` lists what is allowed, the arbiter picks, the baseline ladder runs when the brain is off | Code, with brain scores as input |
| 6 | Commit gate | One function at the flip, same for every non-tap kind, names every veto | Code |
| 7 | Brain | `estimate()` is trees (GameValue, Timing). `compose()` is plain code that fills `BrainOutput`. Words are written overnight | Numbers and words only |
| 8 | Phone | Queue card, Pin and Drop, can't-reach list, screen-time clock | Code |

**The brain/code line.**

- If it moves a stick or the TV, it is code.
- If it says what something is worth, it is the brain.
- If it picks between things code already allowed, the brain scores and code chooses.
- Inside the look-ahead the future is a fact, so code schedules it. Past the look-ahead it is an estimate, so the brain supplies `value_next_3m`.
- No LLM runs during games. No taps are learned. NHL gets a rule, not a model.

**Target flow per tick.** Feed updates go to moments and the delay book. `legal_options()` then the arbiter produce the decision. The queue, using the same facts, sets the hidden stick. `commit_gate()` runs until the flip. Everything is logged with a named reason.

**The order is forced by the evidence.** Stick speed comes first, then the gate, then the brain. PICK and Predict both looked useless when graded before the sticks could act.

## 2. Component table

Status: **USE** usable as is. **CHANGE** exists, needs the named edit. **MISSING** new code. Hours are estimates.

### Queue (P3)

| Recommendation | Status | Files and functions | Hours |
|---|---|---|---|
| Q1 flagged route is never ready | CHANGE. Reproduced here: an unmatched Sunday Ticket slot and a `geo_ok=False` Prime link give `prime:...`, label "may be blacked out", status `ready`. Cause: `_rank_key` sorts tunable before risky, and `_status` ignores the risk | `plan._status`, `plan.build_plan`, `discovery/providers._rank_key`, `_pick`, `gather_sources` | 3 |
| Route class on every game, can't-reach list (P5) | MISSING, small | `channels.py` new `route_class`; `plan.build_plan`, `Tonight.view` | 3 |
| Q2 stage timing, warm flag | CHANGE. Medians exist per app and stick model. Scrub split and warm flag do not | `control.LatencyBook.record`, `control._scrub_into`, `tuners.Tuner._tune`, `TuneResult`, `yttv_seek.BufferResult.row` | 3 |
| Q3 levels R0-R5 and `prep_s` | MISSING as a concept. Inputs exist: `machines/control.STANDBY` states, `standby_ready`, `tune_estimate`, `_scrub_cost`, `deadline_cost`, `autonomy.tuner_health`, `ProviderBook.can_play` | New `noggin/gamequeue.py` | 3 |
| Q4 authority ladder, `reach`, `replace` | MISSING. Pure part is new. Wiring replaces `Decider.jump_next`, `standby.Planner` as first choice, and the urgent refill in `Engine._act.done` | `noggin/gamequeue.py`; `engine._preload_standby`, `_buffered_park`, `_jump_standby`; call `control.preload(rotate=True)` to pass `_scrub_protected` | 8 |
| Q5 brain `preload` glue | MISSING, after brain contract | `noggin/gamequeue.py`, `contracts/brain.py` | 2 |
| Q6 sweep before windows, idle-gap verify | CHANGE. `Tonight.idle_reason` refuses "games are on"; `plan.verify_lead_min` is 20 | `plan.Tonight.idle_reason`, `step`, `due`; `control.verify_hidden` USE | 3-4 |
| Q7 Sunday Ticket scan done by T-20 | CHANGE. Slots are 12:45 and 15:55, which is T-15. `_st_games` already looks 75 min ahead | `autonomy._jobs`, `_st_scan`; resumable state beside `_save_state` | 2 |
| Q8 scrub reads | CHANGE. Banner wait is already config (`scrub_after_tune_s`, 22). `_enter` reads 3 times before commit, plus 1 label read after | `yttv_seek._enter`, `ocr_overlay`; `yttv_label.LabelProbe._frame`, `overlay_reader`; `adb.screencap_band` USE | 4-5 |
| Calibrated press count | USE, unwired. `yttv_seek.enter_buffer(presses=)` and `join.plan_join` (16.8 k + 28.5) are not used by the queue path | `control._scrub_into` passes `presses=` | 1 |
| Q9 Prime confirm | CHANGE. `_watch_for` calls `_on_detail_page`, which runs OCR, inside its poll loop | `tuners.PrimeTuner._confirm`, `_watch_for`, `_on_detail_page`; `is_purchase_screen` stays on every press | 4 |
| Q10 typed failures, budgets, advance | CHANGE. `fallback_ladder` exists, flag off | `noggin/gamequeue.py` table; `control._preload`, `_preload_backoff`; `fallback_ladder.py` | 4-5 |
| Q11 ESPN link form | CHANGE | `tuners.EspnTuner.url` behind a flag | 2 |
| Q12 phone card and API | MISSING, narrower than P3 wrote. Drop = existing `mute`. Go = existing `watch`. Fix = existing `/api/override`. Only Pin and Move are new | `engine.action`, `engine.snapshot`, `web/server.py`, new `web/static/rz-queue.js`, `rz-now.js`, `userstate.py` | 5-6 |
| Q13 sim per-app prep | CHANGE | `buffered_jump_sim.MR`, `mr_items`, `maxrate_params` | 3-4 |
| Q14 fault drill: tune, scrub, route | CHANGE | `fault_drill.SCENARIOS`, `platform/faults.py` | 2 |
| Known moment inside L, authority 3 | USE then CHANGE. `noggin/lookahead.ahead` is pure and shared with sims; `Decider._clairvoyant` shows how it is used | `noggin/gamequeue.py` reads `ahead` | 1 |

### Shield (P2)

| Recommendation | Status | Files and functions | Hours |
|---|---|---|---|
| `commit_gate()` | CHANGE. `_flip_to` already polls `_exit_check` three times, set from `may_cut`. All logic is `Engine._act.cut_veto`, branched by kind. No control edit needed | New `decider/gate.py`; `engine._act.cut_veto`, `_entry_valid` | 5-6 |
| Adversarial and mutation tests | MISSING | New tests; `standby.ShadowStick` USE | 4-6 |
| `contracts/brain.py`, `valid()` | MISSING | New file; add to `contracts/__init__.py` so it is not an orphan | 3-4 |
| `legal_options()` | CHANGE. 12 stages in `Decider.decide` | New `noggin/legal.py`; `Decider.decide`, moved one rule at a time | 10-14 |
| Arbiter | CHANGE. `noggin/wire.pick` is the start | New `noggin/arbiter.py` | 4-6 |
| `priority.py` L1-L4, `MindRead` | USE | `noggin/priority.py` | 0 |
| Moment tier in `Ranked.key` | CHANGE | `priority.Ranked.key`, `GameInput`, `tier_of` | 1 |
| Must-show floor | CHANGE. Debt queue exists: `Decider.queue` of `Missed`, `_scan_missed`, `_due`, `_best_replay`. It runs on `.big`, not tiers | `decider.py` read through the arbiter | 2-3 |
| Per-route deadline, rewind before live look (P5) | CHANGE. `control._replay_scrubs` already knows which routes can seek | `decider._window`, `_due`; arbiter | 2-3 |
| Inside-L solver | MISSING. Must not import a grader | New `decider/horizon.py`, modeled on `lab/oracle.py` | 8-12 |
| Brain faults, tripwire, latency CI | CHANGE | `fault_drill.py` | 3-4 |
| Timing v1 inside a code rule | CHANGE, one line. See section 4 | `noggin/live.hook_for` | 1 |

### Product (P1)

| Recommendation | Status | Files and functions | Hours |
|---|---|---|---|
| One Tier 1/2/3 list | MISSING. `contracts/events.Primitive` is the shared vocabulary. Graders keep their own lists | New `moments/tiers.py`, `moments/tiers.yaml` | 3 |
| Live detectors | MISSING as one place. Pieces: `Timeline.events[].big`, `scoring/drive.py`, `scoring/engine.nhl_goal_imminent`. P1's `product/rates.py` is a working offline version | New `moments/detect.py` | 6-8 |
| Slate mode, relevance filter | MISSING. `jump_mode.leagues` and `buffered_jump.leagues` omit NHL and MLB, so `_own` sends those to the normal decider | New `moments/slate.py`; `Decider._jump_league`, `_jump_pool` | 4 |
| Scenes, anti-ping-pong, payoff check | CHANGE. `min_plays`, `max_plays`, `max_hold_s`, `cut_now(plays_seen)`, `_dwell` exist. Anti-ping-pong and payoff do not | `Decider._jump`, `_buffered_jump`, `decider/buffered.cut_now` | 6-10 |
| Basketball leave after a shot | CHANGE. `jump_mode.join.after_shot` covers landing only | `Decider.jump_may_leave`, `buffered._closed_window`, `cuts.DeadTracker.walks` | 3 |
| Fill ladder, montage, off-night | CHANGE. `Reeler`, `plan_reel`, `Decider._with_reel` exist | New `moments/fill.py`; `reel.py` | 7-9 |
| Product scorecard per league | MISSING. Must be a new file: scorecard scripts are frozen | New `scripts/show_scorecard.py` | 6 |

### Mind (P4)

| Recommendation | Status | Files and functions | Hours |
|---|---|---|---|
| Hold-set baselines | MISSING in repo. Script is in the scratchpad | New `scripts/holdset.py` | 4-6 |
| Value shards, leak check | CHANGE. `scripts/leak_check.py` is frozen, so the new check is a new file | `noggin/factory.py`, `scripts/kaggle_noggin_factory.py` | 6-8 |
| GameValue v0 table, v1 trees | MISSING. Forest runtime USE: `models_rt/forest.py`, `scoring/brain3.flatten` | New `noggin/gamevalue.py` | 9-11 |
| `estimate` and `compose` | MISSING | New `noggin/think.py` | 3-4 |
| Shadow log, 5 AM report | MISSING | `autonomy.py`, new script | 6 |
| Pre-game importance | MISSING | New | 8 |
| Morning meeting | CHANGE. `reasoner/` becomes the words | `reasoner/`, `autonomy._jobs` | 8-10 |
| Tiered oracle objective | MISSING. Cannot go in `oracle.py` (frozen) | New `lab/oracle_tiered.py`, same pattern as `oracle_v2.py` | 4 |
| Timing v1, existing models | USE | `noggin/live.py`, `models/` | 0 |

### Major considerations (P5)

| Recommendation | Status | Files and functions | Hours |
|---|---|---|---|
| ESPN Unlimited through YouTube TV | CHANGE, after one TV look. `youtubetv:ev:` target exists, `_espnplus` reuses `st_slots` | `channels._espnplus`, `autonomy._st_candidates`, `discovery/yttv.py` | 4-6 |
| Screen-time clock, spoiler-safe | MISSING. `phone.catchup_view` is off; `delays.get(t).mu` gives the offset | `views.py`, `rz-now.js`, `rz-games.js` | 4-6 |
| Mac preflight, footprint log | CHANGE. `governor.Sample.footprint` and `service_mb` are sampled, never saved | `governor.update`, new `scripts/preflight.py` | 2 |
| Version watch | CHANGE. `autonomy._app_version` exists | `autonomy._selftest` | 1 |
| Daily feed schema check | MISSING | New script | 2 |
| Visible-stick faults | CHANGE. `_visible_fault` has reboot and buffering only | `fault_drill.py`, `platform/faults.py` | 2 |
| Coverage audit | MISSING in repo | New `scripts/coverage_audit.py` | 2 |
| Eval data private | CHANGE, protected paths | `scripts/fetch_eval_data.py`, `.github/` | 3-4, Nick |
| Unknown input is no-cut | USE. `input_unknown` already refuses | `control.py` | 0 |

## 3. Work packages

**Rules for every package.** One writer per file, claimed with `scripts/claims.py`. New behaviour ships OFF behind a flag. The PR carries the before/after table for `entries_mid_play`, `plays_missed_entry`, `dead_air_pct`, and adds a decisions fragment if a default changes. New modules need a `Why:` header.

**Contract rules the planners did not account for** (`scripts/contract_check.py`, CI):

- K4: a function named `rank`, `pick`, `choose`, `best`, `order`, `score` or `decide`, or a `max` or `sorted` over candidates, is allowed only in `decider/core.py`, `decider/decider.py` and `noggin/`. So the queue is `noggin/gamequeue.py`, not P3's `redzone/gamequeue.py`.
- K5: a module nothing imports fails. Each new pure module ships with a small `scripts/` caller or an import.
- K2: timing numbers live in config.
- K3: device I/O stays in `tuners/`.
- `snapshot["queue"]` is already the catch-up list. The new key is `game_queue`.

### Wave 0: coordinator prep (30 minutes, do first)

**WP-00.** Copy the planner scripts out of the temp scratchpad (`queue/`, `mind/`, `product/`, `major/`, `boundary/`), which can vanish on a reboot. Add one OFF flag block to `redzone/default_config.yaml` for all of waves 1 and 2, so no package writes that file: `queue.*` (including `queue.r3_flippable: false`), `commit_gate.enabled`, `plan.sweep`, `discovery.st_wait`, `yttv_seek.skip_open_read`, `yttv_seek.overlay_band_from`, `tuners.prime_fast_confirm`, `tuners.espn_link_form`. Add one decisions fragment. Write the claims. Model: sonnet. TV: no.

### Wave 1: launch together. Queue first, no TV, file-disjoint

| WP | Goal | Why | Files (writer owns) | Tests | Exit check | Model | Hours |
|---|---|---|---|---|---|---|---|
| **01 Route truth** | Flagged route never ready; NFL Sunday waits for its slot; route class; can't-reach list; earlier sweep | Q1, Q6, P5 step 3 | `plan.py`, `channels.py`, `discovery/providers.py`, `tests/test_plan.py`, `tests/test_providers.py`, new fixture | Sanitized Nov 8 plan; the repro in section 2; sweep with fake clock | 0 flagged-ready on the fixture | sonnet | 6-7 |
| **02 Queue core** | Levels, `prep_s`, ladder, `reach`, `replace`, failure table, `view()`. Pure, no engine import | Q3, pure Q4, Q10 table | New `noggin/gamequeue.py`, `scripts/queue_replay.py`, `tests/test_gamequeue.py` | Table tests on every boundary; hostile property test | Under 1 ms for 32 games; `contract_check` adds nothing | opus | 7 |
| **03 Scrub speed** | One fewer read, band capture, per-read timing | Q8, Q2 scrub part | `tuners/yttv_seek.py`, `tuners/yttv_label.py`, `tests/test_yttv_seek.py` | Fake stick and overlay reader. Row-focused and lost-press cases | Flag on: 3 reads, not 4. Every focus guard still fails closed | opus | 5 |
| **04 App tuners** | Prime confirm without OCR in loops; ESPN link form; warm flag | Q9, Q11, Q2 warm | `tuners/tuners.py`, new `tests/test_prime_confirm.py` | Fake stick. A purchase screen is never pressed | At most 1 OCR per press with flag on; purchase test passes | opus (real money risk) | 6 |
| **05 Commit gate** | One gate at the flip for all non-tap kinds, named vetoes | P2 step 1 | New `decider/gate.py`, `engine.py` (`_act.cut_veto`, `_entry_valid` only), `tests/test_commit_gate.py` | Per-rule mutation; replay on touched leagues | Flag off: scorecards identical. Flag on: 0 mid-play, 0 high-leverage leaves | opus | 5-6 |
| **06 Ticket scan, versions** | Scan done by T-20, resumable; alert on app version change | Q7, P5 step 8 | `autonomy.py`, its tests | Fake channels and clock | All batches finish before T-20 with a stop in the middle | sonnet | 3-4 |

No dependencies inside the wave.

**WP-03 detail, differs from P3.** P3 says drop "the middle read". The knob read after the LEFTs is the one that catches a lost press before commit, so keep it. Drop the first read instead (`opened` in `_enter`), and only when a RIGHT probe at the live edge comes next: RIGHT moves nothing there, and the read after it still checks focus.

**WP-05 detail.** Do not re-fix P1-REVIEW-01 to -03. Commit 30e6ca4d closed them. The tests must prove the gate keeps those behaviours.

### Wave 1b: same time if weekly usage allows, file-disjoint from wave 1

| WP | Goal | Why | Files | Tests | Exit check | Model | Hours |
|---|---|---|---|---|---|---|---|
| **07 Moments** | Tier list and detectors | P1 steps 1-2 | New `moments/`, `scripts/moment_rates.py`, `tests/test_moments.py` | Dev nights only | Rates within 5% of P1's table per league | sonnet | 8-9 |
| **08 Brain contract** | `contracts/brain.py`, `valid()`, Null, Random and Adversarial brains | P2 step 3 | New `contracts/brain.py`, `contracts/__init__.py`, `tests/brains.py` | NaN, wrong tick, unknown ids, stale | `valid()` rejects each bad case | sonnet | 3-4 |
| **09 Audit scripts** | Coverage audit, reach grid, hold-set in repo | P5 step 11, P4 step 1 | New `scripts/coverage_audit.py`, `scripts/reach_grid.py`, `scripts/holdset.py` | Dev-only filter test | Reproduces the 33% reach row | sonnet | 3 |
| **10 Mac and drills** | Preflight, footprint log, visible and queue faults | P5 steps 7, 10, Q14 | `governor.py`, new `scripts/preflight.py`, `scripts/fault_drill.py`, `platform/faults.py` | The drill itself | New scenarios pass, 0 mid-play | sonnet | 4-5 |
| **11 Sim mix** | Per-app prep and real slate mix | Q13 | `scripts/buffered_jump_sim.py`, `tests/test_maxrate_sim.py` | Existing sim tests | Reports a Prime-heavy NBA row | sonnet | 3-4 |

### Wave T1: hidden-stick measurements (coordinator, about 75 minutes in pieces, TV yes)

No code dependency, so it runs beside wave 1. Needs Nick's `data/.agent_hw_ok`. Never while it would disturb what Nick is watching.

ESPN Unlimited rows and codes in YouTube TV; League Pass on both sticks; local flip success without SmartThings; L1 Prime steps; L2 banner clear; L3 band read and pause; L6 Prime seek; L10 HDMI list; L11 left-stick drift; L12 DIAL read-only. Results set the config for WP-03, WP-04 and WP-22. Model: sonnet.

### Wave 2: after wave 1 merges

| WP | Goal | Depends on | Files | Tests | Exit check | Model | TV | Hours |
|---|---|---|---|---|---|---|---|---|
| **12 Queue wiring** | Queue owns the hidden stick behind `queue.enabled`; `Planner` is the fallback; `game_queue` in the snapshot; Pin and Move actions | 02, 05 | `engine.py`, `standby.py`, `tests/test_queue_wire.py` | Replay with `ShadowStick` | Flag off identical. Flag on: 1 owner per tick in `standby_m` | opus | no | 5-6 |
| **13 Control failures** | Typed failures and budgets, no 75 s waits, `presses=` from `plan_join`, stage rows | 02, 03, 04 | `control.py`, `fallback_ladder.py`, `tests/test_scrub_entry.py`, `tests/test_hot_standby.py` | Fault table, parametrized | Each failure advances inside its budget | opus | no | 6-7 |
| **14 Phone queue** | Card, API, pins survive a restart, can't-reach list. Shows no known outcomes | 02 (shape), merges after 12 | `web/server.py`, new `rz-queue.js`, `rz-now.js`, `rz-plan.js`, `userstate.py`, `tests/test_queue_api.py` | API tests, restart | Top 5 rows with level and reason | sonnet | no | 6-7 |

12, 13 and 14 run in parallel. Only one writer of `engine.py` and of `control.py` at a time.

The other hidden-stick writers (`Tonight._warmup` and `_check`, `_peek`, `_refresh_left`, `scan_sunday_ticket`, Prime multiview build) keep working. They take the stick through a named lease the queue respects.

**R3 stays not flippable at first.** `fallback_ladder.skip_rungs` defaults to `[live, other_stick]` because replay showed entering at the live edge adds NFL mid-play entries. P3's "R3 flippable by a strict gate" is a separate flag, off, until it passes.

### Waves T2 and T3

- **T2** (after 12-14, TV yes): L4 scrub end to end, L5, L7, L8, L9, then rerun the throughput grid with measured numbers.
- **T3 dress rehearsal, Sunday Nov 15** (TV yes, hidden stick only): full window, Nick watches normally, footprint logged, agents stopped during the games.

### Wave 3: shield and show (no TV to build, after wave 2)

| WP | Goal | Depends on | Files | Model | Hours |
|---|---|---|---|---|---|
| **15** Adversarial replay and mutation | 05, 08 | `tests/` only | opus | 4-6 |
| **16** `legal_options()` one rule at a time | 05, 15 | new `noggin/legal.py`, `decider.py` | opus | 10-14 |
| **17** Arbiter, must-show floor, per-route deadline, tier in `Ranked.key` | 07, 08, 16 | new `noggin/arbiter.py`, `priority.py`, then `decider.py` | opus | 8-12 |
| **18** Scenes, slate modes, NHL and MLB in the rotation, basketball leave | 07, 17 | new `moments/slate.py`, `decider.py`, `buffered.py` | opus | 12-16 |
| **19** Fill ladder, montage, off-night | 07 | new `moments/fill.py`, `reel.py` | sonnet | 7-9 |
| **20** Product scorecard, info only | 07 | new `scripts/show_scorecard.py` | sonnet | 6 |
| **21** Screen-time clock | 14 | `views.py`, `rz-games.js`, `rz-now.js` | sonnet | 4-6 |
| **22** ESPN events through YouTube TV (TV yes) | T1 pass, 01, 06 | `channels.py`, `autonomy.py`, `discovery/yttv.py` | opus | 4-6 |
| **23** Eval data private (Nick) | Nick's token | `scripts/fetch_eval_data.py`, `.github/` | sonnet | 3-4 |

16, 17 and 18 run one after another because all write `decider.py`. 19-23 run beside them.

### Wave 4: brain (Kaggle only, after a live Sunday holds)

| WP | Goal | Depends on | Model | Hours |
|---|---|---|---|---|
| **24** Hold-set on public seasons, real `standby.Planner` baseline | 07, 09 | sonnet | 4-6 |
| **25** Value shards and new leak check. Nick launches on Kaggle | 24 | opus | 6-8 |
| **26** GameValue v0 and v1, NFL and NBA | 25 | opus | 9-11 |
| **27** `estimate`, `compose`, queue brain order, shadow log, 5 AM report | 08, 12, 26 | opus | 11-12 |
| **28** Live through preload only, after 7 of 7 shadow nights | 05, 12, 27 | sonnet | 2 |
| **29+** Other leagues, NHL rule, importance, morning meeting, tiered oracle, LambdaRank, NFM-S, inside-L solver | 28 | mixed | 45-55 |

**Scope cap.** Waves 0-2 plus T1-T3 are the near-term list. Nothing in wave 4 starts until a live Sunday holds, as P5 asked.

## 4. Delete, merge, freeze

### Conflicts between planners and code

| Planner said | Code says | Resolution |
|---|---|---|
| P2: P1-REVIEW-01 to -03 are open | Closed in 30e6ca4d (markers at `engine.py:2101`, `2721`, `control.py:2696`). Not re-run here | Gate keeps them, tests prove it |
| P2: `noggin/priority` is flag off | `league_overrides` turns it on for NFL and NBA. `Decider._best` reads it per league | It is live in `_best`. The rotation does not use it |
| RC7 config: `noggin.timing.leagues: ["nba"]` under NBA and NFL | `live.hook_for` reads global `cfg.get`, not `for_league`, so the override does nothing | Timing v1 is not live. Decide per league in WP-16 and replay before changing |
| P3: one call from `_preload_standby` | Ten writers of the hidden stick | Queue owns three, lease for the rest |
| P3: R3 flippable | Ladder default holds, measured reason | Off behind a flag |
| P3: `web/phone_v2.py` for the card | The UI is `web/static/rz-*.js`; `phone_v2.py` is view helpers | Use the JS files |
| P4: tiered row in `OBJECTIVES` | `lab/oracle.py` is in `docs/grader_freeze.json` | New file, `oracle_v2` pattern |
| P1: product gate per league | Scorecards and release gate are frozen; grader changes are Nick's | Info-only scorecard until he approves one |
| P5: footprint never logged | Sampled by the governor, not saved | Small change, WP-10 |
| Three types called `Option` (`standby`, `core`, P2's) | Name clash | Import `contracts.brain` by namespace |

### Merge

| Duplicates | Into | When |
|---|---|---|
| `Decider.jump_next`, `standby.Planner` as first choice, urgent refill | `noggin/gamequeue.py`; `Planner` stays as fallback | WP-12 |
| `_jump_value`, `jump_next.val`, `_best`, `standby.options` values, `_pick`, `_clairvoyant` | Arbiter plus one value source | WP-17 |
| Jump and buffered modes | Buffered is the two-stick show. Jump stays until `queue.r3_flippable` is decided, then it becomes the fallback | After T3 |
| `_scrub_protected`, `rotate_due` | Inside `replace()` | WP-12 |
| `control._hold_standby` and `prime_pause.enter_buffer_pause` | One Prime hold | WP-13, or later |
| `_scrub_into` press planning and `join.plan_join` | `plan_join` numbers | WP-13 |
| `cut_veto` branches | `decider/gate.py` | WP-05 |
| Must-show meanings: `BIG_KINDS`, `.big`, supervisor moments | `moments/tiers`, with a test mapping to each frozen grader's list. Graders stay as they are | WP-07 |

### Delete, after the named condition

Importers checked with `grep` and by reading. Nothing here can go today. A delete that removes tests is its own PR, with the F1 exception stated.

| Item | Evidence | Condition |
|---|---|---|
| `DecisionCore.decide`, `guarded`, `NoMidPlayGuard`, `contracts/guard.traced` | Not on the live path | After WP-16. Keep `HARD_RULES` as the gate's rule names |
| `noggin/predict.py`, `predict_fit.py` | P4: `p_tier1_60s` replaces it. Live path imports it in `live.py` | After WP-26 |
| The score term from `scoring/brain.py` (the `brain.enabled` path) | Off. The module itself stays: frozen graders import it | After WP-26 |
| `noggin.pick` `mode` paths and `possession` | Off, needs the arbiter | After WP-17 regrade |
| `noggin/belief.py` | Offline only, behind a flag | Keep for now. Delete if Kalman holds at T3 |
| Dead override key `noggin.timing.leagues` | See above | WP-16 |

### Keep, despite looking unused

- `buffer_rc8.py`: `engine.py` and frozen `scripts/rc8_gate_report.py` import it.
- `sync_edge.py`: `sync_probe.py` imports it.
- `scoring/brain3.py`: `flatten` is used by `noggin/timing.py` and `pick.py`.
- `tuners/multiview.py`: P5's possible crunch fallback.
- `reasoner/`: becomes the morning meeting.

### Frozen

All 44 files in `docs/grader_freeze.json`, including `redzone/grading.py`, `lab/leverage.py`, `lab/splits.py`, `lab/oracle*.py`, `lab/sportsgrade.py`, `scripts/ci_scorecard.py`, `scripts/release_gate.py`, `scripts/northstar.py`, `scripts/leak_check.py`, `scripts/eval*.py`. Also the G6 protected paths: `.claude/`, `.github/`, `tests/conftest.py`, gate baseline, results ledger, `scripts/fetch_eval_data.py`.

## 5. Replacement text for docs/PLAN.md sections 3-5

PLAN.md must stay under 150 lines (`plan_check`). This text is 50 lines against 34 today, so the file goes from 128 to about 144 lines.

```markdown
## 3. P1 queue and switching (coordinator)

Goal: any reachable game parked on the hidden stick fast, cut only at dead balls. Build:
[P-BUILD](agents/planner/2026-11-09-build-plan.md), waves 0-2 and T1-T3.

Nick's bars (2026-11-08, unchanged):
- Rule #1: never join mid-play. Leave mid-play only at low leverage; never a high-leverage moment.
- NFL: join after a play, preferably after the replay, seconds before the snap. Never late into a play.
- Basketball: join mid-possession only right after a made or missed shot. If that moment is a transition play, use the prior
  safest point (previous bucket, transition or dead ball, whichever is soonest).
- Pace: 2-4 games for a few minutes at a time, 60-90 s behind live, 0 commercials on screen, 0 late joins. Rotating across
  many games and catching up on the rest is section 4 work.
- Football must-show: every touchdown and major play: turnover, 10+ yard gain, field goal, big 3rd down, 4th down, large EPA
  (5-point win-probability swing until nflverse EPA). Plays the live rotation can't reach become catch-up clips.
- Both HDMI inputs, both Prime Video and YouTube TV.

Queue bars (P-MAJOR section 12, all p90 unless noted):
- YouTube TV ready: 45 s is the gate, 30 s the target, 20 s the stretch. Scrub failure under 10%. Flip 2.5 s p50.
- Prime: tune 25 s, long lead only (about 90-105 s to ready) until a test shows it can seek. NBA plans around this.
- A failed tune advances the queue inside its budget. Nothing waits 75 s or more.
- No route flagged "may be blacked out" is ever ready. The phone lists what can't be reached tonight.
- Model ceiling on 2-4 games: 13-15 jumps per 10 min. Must-see seen is the bar; cut rate is an output.

Phases, each exits on its check:
1. Queue core, routing, scrub, Prime, commit gate (waves 0-1). Exit: tests pass, flags off, scorecards unchanged.
2. Queue owns the hidden stick, phone card (wave 2). Exit: L4 meets the YouTube TV gate on a hidden stick.
3. Full NFL Sunday, hidden stick, app alone on the Mac (T3). Exit: 7 h with no stuck stick; failures listed.
4. Live on screen, flags on one league at a time. Exit: 95% gates on the locked set plus fresh nights.

## 4. P2 the show and the brain

Show first, then brain. One Tier 1/2/3 list per league. Tier first, league order as tiebreak, your teams first, crunch first.
Every Tier 1 airs live or rewound by its route's deadline: 2-3 min on YouTube TV, clip lag on routes that can't seek.
A cut lands before one play, ends when the play ends, and needs a payoff. Stops and failed 4th downs count.

The brain outputs numbers and words, never an action. Code lists the legal options, the arbiter picks, the commit gate has
the last word at the flip. Baseline runs when the brain is off, late or invalid. Models: small trees live under 100 MB, no
LLM during games, large models offline on free Kaggle only, nothing learned from Nick's taps. First live surface: preload.
Exit per model: beats the state table on the worst fold, 7 of 7 shadow nights, then locked once plus fresh nights.

## 5. Standing order, audits and planners (P3)

Order of work: Rule #1 and its facts (sync, delay), then stick speed, then the gate, then the brain. No model is graded
before the sticks can act on it. Only the near-term list runs until a live Sunday holds.
Always: one writer per file; new behaviour ships off; frozen graders stay frozen; locked set once per release; no TV test that
disturbs Nick; hidden stick first; no training on the Mac; game nights the Mac runs RedZone only; all Claude work stops at 88%.
After P1 and after P2: auditors report to planners; planners write only the section below. At most 2 audit rounds when code
and prod work, then 1-2 loops of safety measures, then move on.

### Planner section (planners write here only)
```

Also update the P1 row in section 1 to read "Queue and switching", with "Section 3 phases" as its exit.

## 6. Nick decisions

The coordinator takes the default on everything not about money. Nick decides money and what he subscribes to, and does the physical checks.

| # | Decision | Who | Default or action |
|---|---|---|---|
| 1 | What "every league, every night" promises | Coordinator | Reachable games on working routes; phone lists the rest |
| 2 | Buy MLB.TV | **Nick (money)** | No. Needed by March only |
| 3 | Game nights: Mac runs only RedZone | Coordinator sets the rule, agents stop. **Nick plugs in and closes Chrome** | Yes |
| 4 | Move public ESPN eval data private | Coordinator decides yes. **Nick supplies the Kaggle token and approves the protected-path PR and release removal** | Yes |
| 5 | Moment tier first | Coordinator | Yes |
| 6 | Basketball leave after a shot | Coordinator | Yes, low leverage only, because Rule #1 still bars high-leverage leaves |
| 7 | All games on break | Coordinator | Recap or highlight on the hidden stick |
| 8 | Brain on game nights | Coordinator | Small trees, no LLM |
| 9 | Self-taught rule | Coordinator | Keep |
| 10 | Exploration on live shows | Coordinator | No |
| 11 | Lounge test | Coordinator, **after Nick answers whether YouTube TV is his main Google account** | One 10-minute test, seek only |
| 12 | Pin vs known touchdown | Coordinator | Pin beats the brain and rotation; only Lock beats everything |
| 13 | Third stick | **Nick (money)** | Not now |
| 14 | Tier values 5, 2, 0.5 | Coordinator | Yes, as a setting he can change |
| 15 | Kaggle only, cancel Colab Pro+ | **Nick (money)**. He launches the Kaggle run, about 2 minutes | Yes |

**Only Nick can do these:**

1. Decide on Colab Pro+ (about $50 a month), the third stick, and MLB.TV by March.
2. Say which League Pass tier he has, Standard or Premium.
3. Say whether his YouTube TV login is his main Google account.
4. Count the HDMI ports on the back of the TV.
5. Create `data/.agent_hw_ok` so agents can run hidden-stick tests.
6. On game nights, plug in the Mac and close Chrome.
7. Turn on a Sports Focus mode on his phone.
8. Launch the waiting Kaggle run.
9. Supply the Kaggle token for the eval data move.
10. Approve any change to the release gate or graders. An example is making the per-league product scorecard a gate.

## Risks and unverified

- Reproduced or run here: the routing bug, and 130 tests in `test_plan`, `test_standby`, `test_providers` (pass). The full suite was not run.
- Read, not exercised: the P1-REVIEW fixes, the Timing override finding, and the writers of the hidden stick. `control.py` and `decider.py` were read in the parts named here, not line by line.
- All hours are estimates. All throughput numbers are model output from the planners.
- Wave 1 and 1b together are 11 agents on an 8 GB Mac, against a 5-writer norm and the 88% weekly stop. Run wave 1 first if usage is above about 60%.
- `data/lab/navigation_frames` is not in agent worktrees, so WP-03 tests use fake readers. Real frames wait for L3.
- A local CBS or FOX game in Raleigh is not on Sunday Ticket, so `discovery.st_wait` must keep the local route for those.

## Bibliography

None. This report reads only this repo.
