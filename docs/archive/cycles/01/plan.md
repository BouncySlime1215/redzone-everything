# Cycle 01 plan (Start Planner), Wed Nov 4, 3:00 AM ET (08:00 UTC)

**Window:** 08:00 to about 12:00 UTC (3-4 h). The End Planner / Integrator ("Run the integration train")
writes `docs/cycles/01/review.md` at the end; cycle 02 starts from it.

**Built from:** `docs/plans/2026-11-04-0245-planner.md` (on codex/resume-redzone), `docs/northstar.md`
(305f980), `docs/roadmap.md`, open PRs #13 #64 #75 #76, the lane threads' current state, and Nick's 3:00 AM
orders: **performance first, full-set grading, no shortcuts, every PR shows its North Star delta on dev
and locked nights.**

**Usage:** about 0.6%/h for the whole project, so about 2.4% for this cycle across all 5 lanes. Outside
research stays to one or two targeted lookups per lane. No subagents or workflows unless Nick asks.

## Where we stand (dev nights, live default vs the candidate in #76)

| line | target | live default | candidate (#76) | note |
|---|---|---:|---:|---|
| Mid-play cuts + entries / 100 landings | 0 | 2.45 dev, 1.75 locked | 0.35 dev, 0.33 locked | NBA candidate: 5 of 61 entries, 4 of them after leaving a finished game |
| Dead air | 0% (ceiling not computed) | 22.5% dev, 26.7% locked | 20.4% locked | |
| Big plays live NFL / NBA / NHL | ≥95% | 25 / 68 / 41% | see #76 | the biggest gap on the board |
| Live or replayed ≤10 min | ≥99% | 62.3% | see #76 | upper bound only |
| Locked regressions in the candidate | none allowed | | NHL crunch 89.4→85.5, your-team crunch 100→97.1, your-team big NFL 95.2→90.5 | must be fixed on **dev** nights only |
| Switch p95 / press-to-TV p95 | ≤3 s | 2.2 s (10 samples) / not measured | | first ≥60 real switches come from tonight's test |

**The one idea for this cycle:** the candidate Noggin is now the thing we grade. Every lane's work is
judged by what it does to the **candidate column**, with no candidate line worse than the live column.

## The 5 tasks

### ① Noggin training: Reflex Predict v1 (big play next), NBA first
- **Owner:** "Noggin training" (in flight: it just started Predict, so this continues it).
- **Goal:** a Reflex model that gives P(big play in the next 30 / 60 / 120 s) per live game, so the
  Priority L4 live value can send Nick to a game *before* the big play, not after.
- **What we're looking for:**
  - It is a real forecaster, not a re-ranker of the score: built on the shared `features.py` (numpy only
    on the live path), LightGBM, trained on whole-night splits from the factory.
  - **5-fold forward-chaining CV** on whole nights: report the mean and the worst fold; graded on the
    **full** held-out set, never a sample. Calibrated (ECE ≤ 0.10), and it beats a base-rate +
    game-state baseline on log loss and on PR-AUC for every fold.
  - Fast and small: p99 < 5 ms per tick for 8 games, RAM reported. The PR reports params, features,
    rows, RAM and latency (Noggin rule).
  - Plugged into the candidate as an L4 input behind `noggin.predict.leagues: []` (OFF in live).
  - Extend the factory to NHL (api-web wsc pbp with wallclock) if time allows; NHL Predict is cycle 02.
- **North Star:** `big_live_nba` in the candidate column, **expected +5 pts on dev** (68 → 73+), with
  `mid_play` and `dead_air` no worse. If the decider can't use it yet, report the offline gains and say so.
- **Files:** `redzone/noggin/predict.py`, `predict_fit.py`, `models/predict_nba.v1.npz`,
  `redzone/noggin/features.py` (additive only), `scripts/noggin_factory.py`, `tests/test_noggin_predict.py`.
- **Depends on:** nothing to start. Reads lane ②'s candidate profile; must not edit `profile.py`
  (lane ② owns it). Hand the profile line to lane ② or the Integrator.
- **Needs from Nick (Kaggle):** start Mind V6 (full test set, fp16, heartbeats; #13 at b9a681a +
  kaggle-inputs e499fd5) and send the log back. LightGBM training runs in the cloud container, not on
  Kaggle and never on the Mac.
- **Never idle:** if Predict finishes early, start NHL factory windows and Reflex Pick (land on the next
  whistle the buffer shows).

### ② Noggin logic/sync: zero mid-play in the candidate, no locked regressions, sync stack
- **Owner:** "Tune Noggin cutting and sync" (in flight: sync stack PR, plus the handed-over crunch-entry
  branch `claude/noggin-crunch-entry-wip`).
- **Goal:** the candidate is strictly better than live on every line.
- **What we're looking for:**
  1. **Finished-game exit:** 4 of the 5 NBA candidate mid-play entries come from leaving a game that
     ended and jumping into a live play. The fix waits for the target's next dead ball (the restart
     countdown from Timing v1 or the 180 s cap path), not a blind cap. Then the crunch-entry rule for the
     remaining one. **Target: NBA candidate entries mid-play 5/61 → ≤1, crunch live unchanged or better.**
  2. **The 3 locked regressions in #76,** diagnosed and fixed **on dev nights only** (NHL crunch, your-team
     crunch, your-team big NFL). The locked set is read once at the end to confirm, never to tune. If one
     can't be fixed, say which flag causes it and propose that flag OFF in the candidate.
  3. **Sync stack PR:** known stalls shift the filter instead of resetting it, network features widen σ,
     break anchors are logged (filter-fed behind a flag), drift alarm. Ready for tonight's probe data.
- **North Star:** `mid_play` candidate dev **0.35 → ≤0.10 per 100**; candidate `crunch_nhl`,
  `your_team_crunch`, `your_team_big` ≥ live column on locked; `delay_err_p95` stays PENDING until
  tonight's probe logs (no fake numbers).
- **Files:** `redzone/noggin/profile.py`, `redzone/decider/*` (entry/exit paths), `redzone/decider/sync.py`,
  `redzone/sync_probe.py`, `scripts/northstar.py` (candidate column only), tests.
- **Depends on:** #76 (its own) and #75 (truthful NHL lag) merging first; lane ① adds Predict to the
  candidate only after item 1 lands. Don't touch the poll loop or source HTTP code (#73 owns it).
- **Never idle:** next is dead air in the candidate (buffer + reels), with its ceiling computed.

### ③ Performance + controls: every phone press reaches the TV, fast
- **Owner:** "Make phone controls instant and reliable" (in flight: #64 waiting on CI, controls drill
  started).
- **Goal:** Nick's definition of performance: when he presses a button, it does it.
- **What we're looking for:**
  - **LOCK IN** holds until released: the decider honors it right after the L1 safety rules, across
    restarts (persisted in user state), proven by a test that replays a night with a lock on.
  - **GO TO THIS GAME NOW:** pre-tune the hidden stick, flip, and confirm on the phone; ≤3 s in the drill.
  - **pause / resume / skip / override never dropped:** each command gets an id, an ack and a retry, and is
    idempotent; an e2e test drops and duplicates commands on purpose and all of them land exactly once.
  - **press-to-TV p95** becomes a North Star line (`press_to_tv_p95`, target ≤3 s), PENDING until real
    presses exist, measured from the drill in sim.
  - Tick-loop p99 time, startup time and RSS reported in the PR (no regression; RSS inside the 2.5 GB AI
    budget plus the app's own).
  - **Research spike (shadow only, flag OFF):** the Samsung TV local WebSocket API (port 8002, e.g.
    `samsungtvws`) can send KEY_HDMI1/KEY_HDMI2 on the LAN without the SmartThings cloud round trip. Add it
    as an optional fast path with SmartThings as fallback, and add one optional step to the Wed test
    script to time both. No live stick tests before Wed evening.
- **North Star:** new line `press_to_tv_p95` (sim drill) **≤3 s**; `recovery` stays ≥95%; switch p95
  unchanged until real samples.
- **Files:** `redzone/control.py`, `redzone/userstate.py`, `redzone/web/server.py` (command endpoints
  only, no UI), `redzone/tv.py`, `redzone/decider/` (lock-in hook only), `scripts/northstar.py` (new line
  only), `docs/live-test-wed.md` (one optional step), tests.
- **Depends on:** lane ⑤ builds UI on these command endpoints, so publish the command API (id, ack,
  status) early in the PR description. Lock-in in the decider must not conflict with lane ②'s entry
  changes; the Integrator merges ② first if both touch the same file.

### ④ App + integration hardening (new lane): make PASS reachable and the app sturdy
- **Owner:** new worker thread (the coordinator starts it). Not the Integrator: this lane builds, the
  Integrator merges.
- **Goal:** several lines can never PASS today because of thin data or known leaks, and three
  reliability PRs from yesterday are stalled.
- **What we're looking for:**
  1. **eval-data-v2 (EVAL2):** NBA and NHL have 4 locked nights each and PASS needs ≥8. Build a pinned
     `eval-data-v2` with ≥8 locked nights for every league and more your-team nights in the dev slice,
     split by time (locked = newest, never tuned on), with a leakage check in CI. Don't switch
     northstar.py to v2 in this cycle: it lands next to v1 so all lanes keep comparable deltas; the
     Integrator switches it at the start of cycle 02.
  2. **B10 leaks:** remove the 2 locked-set games from the e2e night (NHL 401892626, NBA 401909906) and the
     `parse_summary` leaders leak.
  3. **Platform stack #20 → #23 → #24** (dark feed flagged within one poll, health invariants,
     crash-safe state): rebase onto integration/next, re-run the scorecards, and get them merge-ready or
     recommend closing them with a reason.
  4. **Tonight's test readiness:** the sync-probe log and `data/tv_input.jsonl` upload automatically after
     the test (artifact or repo), so switch p95 and delay error get their first real numbers Thursday.
- **North Star:** no metric moves; the result is that `big_live_nba/nhl`, `crunch_*` and the locked lines
  can reach PASS (≥8 nights), and `recovery` stays ≥95%. Report the v1 vs v2 numbers side by side.
- **Files:** `data/eval/` pins, `scripts/eval_*.py`, `scripts/release_gate.py`, `tests/test_leakage*.py`,
  `redzone/sources/` (feed staleness only, via #20), `redzone/platform/`, `.github/workflows/` (upload
  step only).
- **Depends on:** coordinate with the Integrator before touching workflows. Lanes ①-② keep measuring on
  v1 this cycle.

### ⑤ Features (lowest priority): phone page v2 with 👍/👎 decision records
- **Owner:** "Build the deferred experience features" (in flight: phone page v2).
- **Goal:** Nick can see why each switch happened and say 👍/👎, which creates the decision records the
  `right_game` North Star line needs (E23).
- **What we're looking for:** a reason chip per switch (from the Priority reason string), 👍/👎 and
  "I missed that", each saved as a decision record (`ts`, game, reason, tap) that `northstar.py` can read.
  Flag OFF. No change to switching behavior.
- **North Star:** `right_game` goes from "no records" to measurable (stays PENDING until Nick's taps
  exist). Nothing else may move.
- **Files:** `redzone/web/static/*`, `redzone/web/server.py` (new read/record endpoints only),
  `redzone/store.py` (records table), tests.
- **Depends on:** lane ③ owns the command path; ⑤ calls ③'s endpoints and doesn't change them. Never
  preempts ①-④: pause if usage gets ahead of the line.

## Cross-lane order for the Integrator
1. #75 (truthful lag), then #76 (candidate column): everything else is measured against them.
2. #64 (watchdog actions), then lane ② (entry fixes), then lane ③ (lock-in hook), in that order if they
   touch the same decider files.
3. Lane ①'s Predict lands with its flag OFF in live; it joins the candidate only after lane ②'s fix.
4. Lane ④'s eval-data-v2 lands last and becomes the default only in cycle 02.
5. Lane ⑤ any time after ③'s command API, flag OFF.

## Every PR in this cycle
- Says which North Star line it moves, the expected delta, and the measured delta on **dev and locked**,
  for **both** the live and candidate columns.
- No line in the candidate column gets worse than live without a measured reason at the top.
- Full test set every time. Self-review and known risks in the body.
