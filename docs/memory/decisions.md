# Decisions

Every decision Nick made, newest first, with the date and the reason. Times are ET unless they
say UTC. **A PR that changes a default (`redzone/default_config.yaml`) or a gate
(`docs/gate_baseline.json`, `scripts/release_gate.py`) adds a line here**; `scripts/check_memory.py`
enforces it in CI. Gate changes in architecture.md §7 need a line too (by hand).

Format: `- YYYY-MM-DD: **decision.** Why.`

## ArenaSwap ideas (Nov 4)

- 2026-11-04: **New `score.postseason` block ships OFF (`enabled: false`).** Postseason stakes (elimination game, Game 7 / one-game final, rounds left to the title, ranked college matchups) parsed from the ESPN scoreboard we already poll, offered to the scorer and the Noggin as hooks (`redzone/scoring/postseason.py`). Why: the stakes multiplier can't tell a Game 7 from a regular-season game; it stays off until a playoff-night scorecard shows no rise in mid-play entries, plays missed on entry or dead air. Idea from ArenaSwap (ideas only, our own code).

## Research bugs C18 (Nov 4)

- 2026-11-04: **The break blend uses per-league log-odds coefficients fitted on held-out nights (`brain.break_blend`)**, replacing the fixed 0.5 log-odds weight #51 shipped; leagues without a fit keep `break_weight`. Why: held-out ECE (forward folds on archive nights that are neither locked nor brain-training) NFL .063 -> .054 (Brier .167 -> .147), NBA .024 -> .023, NHL .095 -> .027; the original 50/50 mix was .169 / .126 / .144. `brain.enabled` stays false, so the scorecards are unchanged.

- 2026-11-04: **Switch costs have one source of truth: `decider.switch_cost` in default_config.yaml holds the measured numbers** (tunes = the Mac's LatencyBook medians pinned in eval-data-v1: YTTV 5.5, Prime 27, ESPN 7.5; flip = SmartThings median 1.98 s, 10/10, + 1 s settle = 3). The old guesses were 3 / 8 / 7 / 1. Offline code (jump/reel labels, Kaggle briefs) prices switches through `contracts/switching.ConfigSwitchPaths`, and the scorecards use that table instead of the pinned snapshot's (its tunes are identical; only its 4.0 HOME-era flip differed). Why: research C18, the two configs disagreed.

## Prime NBA multiview (Nov 3, 7:45 PM)

- 2026-11-03: **Prime NBA multiview ships OFF (`tuners.prime_multiview: false`).** It turns on only after a live Prime NBA night passes its gate: >= 95% right-tile switches under 2 s (>= 20 switches) and 0 mid-play entries. Why: the key paths are guesses until `scripts/prime_multiview_probe.py` records them on the real sticks.
- 2026-11-03: **No team names in code; exclusions and priorities live in the personal config** (`score.personal.multiview_exclude` / `multiview_pin`, "league:ABBR", shipped empty). Why: a build we sell must carry none of Nick's data.

## Release candidate 1 (integration/release, Nov 3)

- 2026-11-03: **Brain features ship off in RC 1 (`brain.enabled: false`).** The brain models were trained on seasons that include the locked test nights, so their numbers are optimistic. With the brain off, the locked NFL/NBA/NHL cards are identical, so no gate row depends on a leaky model. It turns back on after the time-cutoff retrain if the gate holds.
- 2026-11-03: **NBA's whistle-wait cap goes 120 → 180 s (`decider.entry.max_wait_s.nba`).** Chosen on the fit split (train + valid), never on the locked nights: 120/180/300/no cap gave mid-play entries 7/4/5/3, and no cap cost big plays seen 90.1 → 85.9 %. Full NBA card: entries mid-play 12 → 7, missed on entry 9 → 10, dead air 20.2 → 20.0 %.
- 2026-11-03: **NBA entries wait for a whistle, capped at 120 s (#21, over #28's rewind-only).** On the full NBA card, entries mid-play drop 30 → 14 and missed on entry 14 → 9, while dead air goes 19.4 → 19.9 %.
- 2026-11-03: **The release gate is judged on the locked test split plus robustness cards** (sync ±3 s, a 10 s stale feed), with `docs/gate_baseline.json` rebuilt from 5f0e6a1 on that split (the no-overfitting rule).

## Auto-fix (#30, merged Nov 3-4)

- 2026-11-04: **`autofix` config block added (enabled, at most 3 issues a day, refile after 7 days).** The watchdog files errors it can't fix as GitHub issues for the auto-fix brain. The brain reads `docs/memory/lessons.md` and the runbooks first, and adds a lesson with each fix PR. Kill switches: `autofix.enabled` on the Mac and the repo variable AUTOFIX_ENABLED=false on GitHub; the workflow runs only from the default branch, and fixes land only as draft PRs behind the guard and the gates.

## Error handling (PLATFORM, overnight plan items 8-12, Nov 4)

- 2026-11-04: **New `hardening:` config block (`smartthings`, `sticks`, `feeds`, `mac`), all false.** Each is read with a False default, so the code PRs for sticks/feeds/mac work in any merge order. `hardening.smartthings`: SmartThings 5xx/timeouts open a breaker (30 s doubling to 2 min, probed by the keepalive, not by a flip), a 429 waits its Retry-After (cap 300 s), and a refused sign-in stops the cloud path until `tv_setup.py --smartthings` rewrites the OAuth file, raising Health `tv_auth` and one phone push. Off by default because no live night has proven it yet (the overnight plan: anything unproven ships off). Drill `st_outage`: with it off, 3 of 10 flips during an outage wait out 216 s of timeouts; on, 1 flip waits 16 s and the rest go straight to HOME/CEC.
- 2026-11-04: **`hardening.watchdog: false` (C17).** The watchdog may ask the app (POST /api/agent/fix) to resume, clear a prompt on or re-tune the stick on screen while its screen has been dead 90 s+, refresh or restart the feeds, restart the push feed, or leave safe mode. Every fix goes through the supervisor's rate limits, and the watchdog judges each one (worked or failed within 2 checks), climbs a ladder and then tells a person. Off by default: no live night has proven it, so with it off the watchdog only notifies. Stick fixes act only on a screen that is already not playing, so they can't cut a live play.

## Process and budget

- 2026-11-04 (planned): **Move to a new Claude account Wed 5 PM.** Everything an AI needs lives
  in this repo ([new-environment.md](../new-environment.md)), so memory work lands before then.
- 2026-11-03 4:20 PM: **Pace usage to reach ~90% at 5 PM Wed.** Checkpoints ≤55% at 10 PM,
  ≤62% at 8 AM, ≤78% at 1 PM. At most 2 working threads (1 overnight, midnight to 8 AM).
  Priority: tonight's release, the decision core, memory systems, the Kaggle round-2 fix, the rest.
  Why: the old account has to last until the move.
- 2026-11-03 3:11 PM: **All Claude work stops at 88–90% of weekly usage**; Nick pauses the
  project at 88–89%. Why: he keeps the last 10% for school. From 75%: no new threads, finish and merge.
- 2026-11-03 3:38 PM: **Cut `integration/release` from a green `integration/next` by 7 PM**
  with the gate report attached; flags OFF for anything that misses.
- 2026-11-03 1:29 PM: **"Stop asking me questions, everything is run by you."** The coordinator
  decides trade-offs within the rules and pings Nick only for actions that need his hands.
- 2026-11-03: **Coordination first.** Manage and integrate before starting anything new; one
  integration branch (`integration/next`), one owner per area, max 5 working threads.
- 2026-11-03: **$0 budget; never enter passwords or tokens.** Nick adds secrets himself.
- 2026-11-02: **No build until he says "build" or "go".** "Lock in" means plan harder.

## Gates and quality

- 2026-11-04 11:00 AM: **The replay window is 2-3 MINUTES max, not 10.** North Star `big_seen_10min` becomes `big_seen_3min` (live or replayed within 3 min, >= 99%), plus a stretch row `big_seen_2min`; `your_team_big` counts replays within 3 min. Graded on dev and locked, live and candidate (`backtest.CATCHUP_3_S`/`CATCHUP_2_S`). Product: the reel/replay queue (T2-T4 collision replays) targets the very next dead window; a replay that can't air within 3 min is skipped or folded into a later montage; clairvoyance and possession-start entry are the main way to see moments LIVE.
- 2026-11-04 10:55 AM: **Gates raised and made honest against the hindsight oracle** (Nick: "95% was a compromise"). Big plays seen live per league: **>= 95% of oracle value** (the share of the DP ceiling on the same nights; ceilings now about NFL 91/93, NBA 93/100, NHL 85/90 dev/locked). Live or replayed within 10 min >= 99%. Mid-play cuts 0. Landings in dead time **>= 98%** (was 95%). `pct_of_oracle` rows gate at 95% (was 90%). Every oracle-backed row shows its gap to 100% of oracle. Pace brake lifted; hard stop at 89-90% of weekly usage.
- 2026-11-04 12:45 AM: **Timing gate rows added** (overnight plan item 6): "entries mid-play = 0" and
  "landings in dead time >= 95%" (cuts away + entries, from the new `landings_in_dead_pct`), with the
  landing-offset histograms (`leave_to_restart_hist`, `entry_to_restart_hist`) on every league card.

- 2026-11-03 1:18 PM (approved 1:20): **"95% good minimum" release gate** on the locked test set,
  fresh nights and the e2e night test (full table: [architecture.md §7](../architecture.md)).
  A part that misses ships OFF behind its flag. Why: he wants every measure at 100%; 95% is the floor.
- 2026-11-03 1:02 PM: **Target 100% on every measure** (0 mid-play entries, 0 missed on entry,
  0 dead air, 0 cuts, 100% your-team). Where 100% is impossible, chase the computed ceiling.
- 2026-11-03 1:29 PM: **No overfitting.** Split by time; a locked test set of ≥25% of nights,
  scored once per release candidate; fresh nights reported separately; 5-fold forward-chaining CV
  (mean and worst fold); leakage checks in CI; per-slice reports; robustness at ±3 s sync, a 10 s
  stale feed, one stick down.
- 2026-11-03: **No PR may raise mid-play entries, plays missed on entry or dead air** without a
  measured reason at the top of the PR. Why: he rejected #2/#3 drafts that traded these for headlines.
- 2026-11-03: **Self-review before each PR and list known risks in it.**

## Product

- 2026-11-04 1:15 AM: **NBA liveData play-by-play as the primary NBA source ships OFF (`sources.nba_primary: false`); its shadow log is ON (`sources.nba_shadow: true`, `polling.nba_pbp_s: 3`).** Each action has `timeActual` (real event time) and is published about 4-12 s later at the median (fixture game), against ESPN's ~45 s. The shadow writes its plays to plays.jsonl as source `nba_cdn`, so timing_report.py measures its real lag before it's turned on. Why: planner item 3 (faster feeds).
- 2026-11-04 12:35 AM: **NHL api-web live play-by-play as the primary NHL source ships OFF (`sources.nhl_primary: false`, `polling.nhl_pbp_s: 3` when on).** It turns on after a backtest on archived api-web nights and a live NHL night pass the 95% gate; until then `sources.nhl_shadow: true` polls api-web at the fast rate and logs each event's first-seen time (nhl_seen.jsonl) for that backtest. When on, it owns NHL stoppages while its copy is under 10 s old, and its plays reach the timeline bounded by a 2-60 s lag window. Why: Nick measured it at 16.7 s p50 behind the event vs ESPN summary 43.1 s and FastCast 25.3 s (ANA@VAN, Nov 3).
- 2026-11-04 1:15 AM: **Time-shift buffer, live control, ships OFF (`timing.behind_live_s.enabled: false`; B = 60 NFL/CFB, 90 NBA/CBB/NHL, cap 90 s; apps YouTube TV and Prime).** Each fresh live tune on the hidden stick is paused B s and resumed before it can be flipped to, and the delay book carries the held offset. It turns on after Nick's Wednesday live test (docs/live-test-wed.md). Why: the planner's biggest lever (sim at B = 90: NHL entries mid-play 12 → 0, NBA big seen 67.8 → 87.4 %, NFL big seen 77.9 → 97 %, 0 mid-play cuts); Nick accepts ~1 min behind live.
- 2026-11-04 12:00 AM: **The decider plans a TV input flip with the SmartThings numbers** measured
  on Nick's TV (10/10 confirmed, median 1.98 s, max 2.2 s; `two_stick.tv_flip_prior_s`) plus the 1 s
  HDMI settle, instead of HOME's 2-6 s flips; HOME and TV flips are timed apart (`tv_flip`/`home_flip`).
  Nick's overnight plan, item 4. Scorecards and the Nov 1 demo are unchanged (they don't use the controller).

- 2026-11-03 3:05 PM: **NFL commercials and dead air go to 0**, with replays when every game is
  in a break. #21 accepted NBA dead air +0.5 and demo commercials +0.5 for NBA mid-play entries 38 → 15.
- 2026-11-03: **Crunch overlaps:** top game live, the other time-shifted on the second stick,
  a highlights catch-up after the crunch, then rejoin live. League order NFL > CFB > NBA > NHL > MLB,
  your team first.
- 2026-11-03 17:06 UTC: **PR #2 ships as "Keep drive gain, 9.7% dead air".** Drives live
  13.1 → 19.6%, big plays live 29.9 → 33.6%, NFL dead air 8.6 → 9.7% (demo-day commercials 15.7 → 15.6%).
- 2026-11-03: **PR #8 (sync): NBA trade 124 → 132 accepted** as its measured reason.
- 2026-11-03: **PR #4 (Hype v2) held** until #2's dead-air fix; later folded into Brain v3 (#4 closed).
- 2026-11-03: **PR #1 (trip budget) approved as is.** Order #1, then #3 after its entry fix, then #2.
- 2026-11-03: **No Mac as the HDMI source.** Keep the two sticks and the TV.
- 2026-11-02: **Never Peacock.** NBC/NBCSN come through YouTube TV (Peacock can't be screenshotted for sync).
- 2026-11-02: **Sync is fully automatic:** no taps, no mic, no camera.

## AI

- 2026-11-04 10:55 AM: **The Noggin is fully self-taught: no training signal from Nick.** His 👍/👎, overrides, lock-ins and taps never feed any training, labelling, suggestion or grading path (crunch policy, Pick, Priority L2 suggestions, E23). Allowed sources: the hindsight oracle on archived nights, self-play/simulation, the Teacher's automated nightly review, objective pbp outcomes, stick/sync telemetry. His buttons stay controls only. North Star: `right_game` (agreement with his taps) is replaced by `pct_of_oracle` (% of hindsight-oracle value achieved, mean of NFL/NBA/NHL). `priority.suggest_from_feedback` removed.
- 2026-11-03 2:37 PM: **Two tiers.** REFLEX = a local ~2 GB distilled student (Qwen3-1.7B, 4B only
  if needed) that says "I don't know" when unsure. BRAIN = Claude auto-fix through a GitHub issue →
  `claude-code-action` draft PR on his subscription token (`CLAUDE_CODE_OAUTH_TOKEN`, he adds it),
  capped at 3 runs a day, live only from `main` after the release.
- 2026-11-03 1:20 PM: **Local AI ≤ ~2 GB RSS** (4-bit), lazy load, unload when idle, background
  priority. **All training on Kaggle (2× T4) or cloud, never the Mac.**
- 2026-11-03 2:38 PM: **REFLEX reasoning add-ons are ablated one by one**; ship only what moves the gates.
- 2026-11-03 3:01 PM: **Round 2 excludes brain features** until the leak-free brain retrain lands
  (the NFL brain had trained on locked 2025 nights).
- 2026-11-03 10:05 PM: **Deploy test step: 3600 s timeout, speed budgets skipped at background
  priority** (`RZ_TESTS_LOW_PRIORITY=1`), and the e2e night pins the host (governor) so the Mac's live
  app can't restart the night's engine. Every other test still blocks a deploy. RC 3 = RC 2 + these.
- 2026-11-04 4:30 AM: **Rate gates are judged per night with a lower bound** (research 05 B9,
  Director 11:30 PM queue). A rate gate (dead-ball entries, big plays seen, mid-play ≤5%) PASSes only
  with ≥8 nights, ≥60 decisions and a cluster-bootstrap (over nights) lower bound ≥ the bar; it FAILs
  when the point estimate (or the whole CI) is under the bar, and is PENDING otherwise. "0 mid-play cuts" also prints its
  rule-of-three bound. Reason: a pooled rate over 4 locked NBA nights said PASS with no error bar.
- 2026-11-04 4:30 AM: **breaks.blend mixes in log-odds and lets the brain stand alone when the rules
  see nothing** (research C18: the 50/50 mix took live ECE from ≤0.10 to 0.12–0.17). Brain is off,
  so no scorecard moves; refit the weight on valid nights after the leak-free retrain.
- 2026-11-04 6:15 AM: **Catch-up the RedZone way ships OFF** (`replay.redzone_way: false`; Nick's T1-T5 spec,
  Director 11:55 PM). New gate rows: big plays live or replayed within 10 min >= 99%, your team 100%, 0
  crunch plays aired during a replay. Valid split, flag on vs off: NFL within-10-min 90.7 -> 92.8%, NHL
  84.5 -> 86.9%, NBA 94.1 = 94.1%, every other line equal or better, so it stays off until the 99% row passes.
  Lull score 70+ or a 300 s half-life gave 1 crunch play on a replay: defaults are lull 45, half-life 600 s.
- 2026-11-04 6:45 AM: **NFL drive-in-20 priority ships OFF** (`redzone_mode.drive_in_20: false`, planner item 4).
  Valid split, on vs off: NFL big plays live 32.1 -> 34.9 %, within 10 min 90.7 -> 89.4 %, dead air 9.3 -> 9.6 %,
  0 mid-play cuts / entries either way. On together with `replay.redzone_way` it was worse (live 31.8, within
  10 min 85.4, 1 crunch play on a replay), so neither turns on until they're tuned together on more nights.
- 2026-11-04 1:45 AM: **The Noggin is the #1 focus; everything combines into it** (Nick). One decision brain,
  three layers (Reflex < 5 ms numpy timing/predict/pick; Mind 1.7B async priorities and words; Teacher 32B on
  Kaggle offline), one training pipeline, one scoreboard (North Star). One owner, NOGGIN; A3 moves from TIMING
  to Noggin Timing v1; the live time-shift buffer (#61) is the Reflex's input #1. Every PR names the North
  Star line it moves. Gates: beat the current brain on locked unseen nights, shadow mode, OFF behind a flag.
  Spec: `docs/noggin.md`.
- 2026-11-04 7:45 AM: **Noggin Priority wired into `Decider._best`, OFF** (`noggin.priority.enabled: false`; Nick's
  1:25 AM planner). On: L1 tiers (your team in a big moment > crunch by league order > open) and the Peacock
  ban decide first, then score. Valid split on vs off: NFL / NBA / NHL every line equal (NHL dead air 21.5 ->
  21.6, switches/h 5.5 -> 5.6): crunch and your team are already handled before `_best`. Mixed-league nights
  (demo card) are where it can move things; it stays off until the Mind / Reflex terms are wired.
- 2026-11-04 1:05 AM (Nick): **The North Star harness is the one answer to "where are we vs perfect" and
  "what next".** `scripts/northstar.py` writes docs/northstar.md (dev-test `fit` and LOCKED `test` nights,
  measured feed lag, night-bootstrap CIs, PASS/PENDING/FAIL as in release_gate.py) and appends
  docs/northstar-history.jsonl; `.github/workflows/northstar.yml` refreshes it on every push to
  integration/next (schedule only fires from main, which we never merge to). Every PR names the line it
  moves; CI shows the dev-test delta (never the locked set). docs/roadmap.md is ranked from
  docs/roadmap.json by (gap × impact) / effort, with the director's morning `priority` first.
- 2026-11-04 7:30 AM: **Burst polling ships OFF** (`polling.burst.enabled: false`; Director 1:35 AM sync item 5).
  When on, the on-screen game and its top challenger (max 2) poll ESPN summary + NBA/NHL pbp every 1.5 s
  (floor 1 s) while a stoppage is likely there (break forecast p >= 0.5) or it's tense (lock, pre-moment,
  crunch). Cadence wait on those games drops from U(0,3) to U(0,1.5) s: mean 1.5 -> 0.75 s, p95 2.85 ->
  1.43 s. Backtests don't model poll cadence, so the scorecards are equal by construction; it turns on
  after a live shadow night shows the measured detection lag drop in scripts/timing_report.py. The fetcher
  also sends If-Modified-Since when a host gives Last-Modified and no ETag.
- 2026-11-04 7:05 AM: **SYNC 1: flip only onto a steady PLAYING stream** (Nick, 1:30 AM sync plan). The preloaded
  path now needs the hidden stick to report PLAYING with no state change for `FLIP_STEADY_S` = 1 s (up to 3 s of
  buffering is waited out, else it's re-tuned). Buffering measured from the stick's own clock vs position is
  added to that target's delay (`held` -> DelayBook). A flip "settles" when the stick is PLAYING within 3 s
  (`settle_book`, the North Star row "flips PLAYING within 3 s >= 99%"). The #61 hold now re-anchors the stream.
  Not behind a flag: it only refuses unsafe flips and books measured lag; the sims don't model the sticks.
- 2026-11-04 8:10 AM: **SYNC probe ON by default** (`sync.probe.enabled: true`). Nick (1:45 AM): auto-anchors only,
  no manual work, and a probe that runs by itself on Wednesday. It is read-only: it presses nothing, changes no
  setting, uses its own adb process and skips every tick during a switch, flip or preload, in dry run, and when
  the governor blocks OCR. It writes `recordings/<day>/sync_probe.jsonl`. The visible stick's UI tree is not dumped
  (`sync.probe.visible_ui: false`) until a dump is shown not to stutter the picture. Decisions don't read it.
- 2026-11-04 8:20 AM: **Sync stack** (Nick, 1:50 AM: delay isn't stationary). (1) A hold or stall timed on the
  stick's clock now SHIFTS the sync filter (+-0.5 s) instead of re-syncing it, which threw a good estimate away
  for every 1 s buffering blip (#70 + #61 hold). (2) The probe's network features (stalls/min, RSSI, RSSI drop)
  scale each channel's process noise (q_scale 1-25): a weak link widens the error bar the Reflex uses.
  (3) Break anchors: ad-label edges on the hidden stick matched to the feed's dead ball, LOGGED to
  sync_anchors.jsonl (ts, target, stick, sample_kind, feed_wall); `sync.break_anchor.enabled: false` until the
  Wednesday data fits the whistle-to-break bias. (4) Drift alarm (>5 s since sync, or q_scale >= 9) logged;
  `sync.drift_alarm.resync: false`. The sims don't run these paths: scorecards unchanged by construction.
- 2026-11-04 3:15 AM: **Noggin Timing v1 ships OFF** (`noggin.timing.leagues: []`). NBA hazard + conformal lower
  bound: 0.56 % of restarts before the bound on 421 unseen public nights (today's FLOOR_S: 7.33 %) at the same
  tightness, ECE 0.053; on the NBA scorecard at B=90 (dev nights) entries mid-play 5/61 -> 5/61, so no gain to
  ship yet. The remaining NBA mid-play entries are entry policy (crunch_now live, 180 s wait cap), Noggin Pick.
- 2026-11-04 5:00 AM: **Hindsight oracle + `oracle_gap` lines** (`redzone/lab/oracle.py`, `eval_sports.py --oracle`,
  `northstar.py --oracle`, INFO rows, no gate changes). DP over 5 s steps with every hard rule (clean leave and
  landing, your-team crunch first), live-only with no rewinds, graded by the same `sportsgrade.grade`. Candidate dev
  nights: NBA big plays live ceiling 93.0 % (app 81.7), crunch 93.7 (90.9), dead air 11.2 (19.2); NHL goals live
  85.3 (44.7), crunch 87.9 (80.6), dead air 18.9 (22.4). The NBA >= 95 % big-plays-live gate is above the live-only
  ceiling on these nights.
- 2026-11-04 10:15 AM (lane 1, cycle 02): **new North Star line `pct_of_oracle_{nfl,nba,nhl}`, target >= 90 %**
  (Start Planner's cycle 02 plan): the app as a share of the hindsight ceiling on the same nights, the worst
  of big live, crunch live and live air (100 - dead air). NFL compares the oracle with the app's switches
  re-graded by sportsgrade (one grader on both). Plus INFO ceilings for dead air and crunch live. No default
  changes. Dev: NFL 27.4, NBA 73.9, NHL 47.5 %; big live is the worst line in every league.
- 2026-11-04 4:30 AM: **Noggin Timing v1 NFL and Predict v1 NBA ship OFF** (`noggin.timing.leagues: []`,
  new `noggin.predict.leagues: []`, `noggin.predict.points: 20`). NFL Timing: 1.43 % of restarts before the
  bound on 122 unseen nights vs FLOOR_S 9.1 %, scorecard neutral (0 mid-play). Predict: beats the game-state
  baseline on logloss and PR-AUC in all 5 forward-CV folds, but no big-live gain in the candidate NBA sim and
  crunch live -1.3 points on dev nights. Neither joins the candidate (docs/noggin.md §10).
- 2026-11-04 9:30 AM (noggin logic, cycle 01): **candidate profile: finished-game exits wait for the next dead
  ball and crunch entries rewind to the last whistle** (`decider.entry.max_wait_s={}`,
  `decider.entry.crunch_now=rewind`, candidate only, live defaults unchanged). Full sets, candidate column: dev
  mid-play 0.35 -> 0.23 per 100 landings, locked 0.33 -> 0.17; big live, crunch live, your-team and big seen
  unchanged on both. `crunch_now=wait` reaches 0 on NBA dev but costs big live NBA 81.7 -> 77.5 and crunch
  live 90.9 -> 90.0, and a bounded crunch wait (15-60 s) still costs a big play, so it is left for Nick.
- 2026-11-04 8:40 AM: **Pre-tune a game opened on the phone, OFF by default** (`two_stick.pretune_on_open: false`).
  Nick (3:05 AM): "GO TO THIS GAME NOW switches within ~3 s (pre-tune, flip)". Opening a live game's sheet tunes it
  on the hidden stick (the planner leaves it there 90 s), so the "Watch this" + "Go now" that follows is a flip.
  Controls drill (4 nights): open -> Go now median 8.36 -> 3.06 s, max 8.7 -> 4.34 s. OFF until real presses on
  Nick's TV show it, since every sheet opened costs a tune and takes the hidden stick off the planner's pick.
- 2026-11-04 3:20 AM: **Phone v2 added OFF** (`phone.v2: false`) and the Noggin L2 prefs got config defaults
  (`noggin.priority.prefs`, the same values as `Preferences` in redzone/noggin/priority.py, so nothing moves).
  Phone v2 only writes `feedback` rows (note `switch` / `missed`); the decider, tick loop and TV never read them.
  Rivalries stay config-only for now: the validator takes lists of names, not pairs.
- 2026-11-04 11:55 AM (noggin logic, cycle 02): **candidate: crunch_now=wait + clairvoyance (GAME-04,
  `noggin.lookahead.enabled`)**, candidate only; live defaults unchanged. Asked by the Start Planner pending Nick's
  call on wait (rule #1). Candidate mid-play 0 on dev and locked in NBA/NHL/NFL. Cost of wait, NBA: dev big live
  81.7 -> 77.5, crunch 90.9 -> 90.0; locked big live 100 -> 68.8 (16 plays), crunch 93.3 -> 84.9. Lookahead: NFL dev
  big live 50.3 -> 54.3 (locked 49.0 -> 52.9), dead air +0.4 / +0.5. Revert wait with one line in profile.py.
- 2026-11-04 10:00 AM: **TV-04 undo added OFF** (`hardening.tv: false`). When the TV jumps by itself
  (SmartThings readback, no command of ours) to a stick that isn't playing, while the stick it left still
  plays our game, the controller puts the TV straight back and no preload runs until it has. Night lab
  (`--faults redzone.lab.night.faults:tv --tv smartthings`, seeds 1-2): back on the game in 20.1 s / 15.3 s
  with it on; off, the TV stays on the rebooted stick's home screen until the next switch. It stays off
  until a real CEC steal is seen on Nick's TV (the Wed test), because a wrong undo would take the TV from a person.
- 2026-11-04 10:30 AM (noggin logic, playbooks): **Safety playbooks SAFE-01/02/03/05 ship OFF**
  (`playbooks.safe.enabled: false`, redzone/decider/playbooks.py). Each holds a switch and logs `playbook=<id>`.
  SAFE-02's threshold is 10 s, not the backlog's 3 s: the sims' normal delay doubt is 4-8 s (floor
  `delay.sync.min_sigma_s` 4, uncalibrated 8), and 3-8 s held normal entries (NHL dev crunch live 80.6 -> 66.0
  at 3 s, 72.4 at 6 s, 74.3 at 8 s; 80.6 at 10 s). At 10 s, dev and locked scorecards are identical on and off;
  the night lab (`--faults redzone.lab.night.faults:safety`, 3 seeds) passes with 0 mid-play cuts.
- 2026-11-04 11:15 AM (noggin logic, scenario tree): **scenario-tree lookup ships OFF** (`playbooks.game.enabled:
  false`, redzone/decider/scenarios.py, seeds GAME-01..03). Dev is mixed (NFL crunch live 68.0 -> 67.6, big seen
  98.7 -> 98.1, big live 50.3 -> 50.5, dead air 29.8 -> 29.4; NHL big live 44.7 -> 44.9, dead air 22.4 -> 22.6;
  NBA unchanged). Locked: NFL crunch live 68.1 -> 69.0, your-team crunch 26/27 -> 27/27; NHL big live 48.1 -> 48.6.
  0 mid-play cuts on both. The table format is the contract for the cloud-built tree (idea #19).
- 2026-11-04 10:50 AM (lane 2, planner ask: GAME-05 now, then 06/09/10/21/22/11): **new flags
  `playbooks.game.crunch_overlap` (GAME-05) and `playbooks.game.empty_net` (GAME-09), both OFF; GAME-21/22 are seed rows
  under `playbooks.game.enabled` (OFF).** Why off: GAME-05 is mixed (dev NHL crunch 80.6 -> 79.7, locked 76.2 -> 84.7 with
  every new flag on), and GAME-09 can't be graded on replays (ESPN has no empty-net signal). The scenario tree as a whole
  costs NFL big plays seen within 10 min 87.8 -> 84.3 (GAME-01 visits): keep `playbooks.game.enabled` off until fixed.
- 2026-11-04 6:30 AM: **`sources.trust_order` added OFF** (empty lists per league, cycle 02 lane 4, FEED-01/03). When set,
  the first fresh, unbenched, non-erroring source in the list owns a game's stoppage, the next takes over on its
  errors/silence, and disagreements are counted (`Merger.disagreements`). Empty = the old nhl/nba_primary behavior.
  `right_game` now reads `Store.taps()` from data/redzone.db when it exists (PENDING otherwise).
- 2026-11-04 12:00 PM (lane 1, cycle 02): **Noggin Reflex PICK v1 (NBA) is offline only**: a model plus
  `eval_sports --pick`, with no decider hook and no default changes. It imitates the hindsight oracle and is
  graded on the full factory test split (377 nights: big live 91.3 vs hand 80.1, oracle 96.3) and on the
  app's eval nights (dev vs the candidate: big live 81.7 -> 83.1, dead air 19.4 -> 13.7, mid-play entries
  3 -> 0). Next is a flag-off decider hook in shadow first, since the rollout assumes every switch lands
  as planned.
- 2026-11-04 11:55 AM (lane 2, scenario tree): **`playbooks.game.skip: [GAME-01]` by default, and a visit row never
  displaces a catch-up reel or crunch time.** Why: with the tree on, GAME-01 visits (and visits replacing reels) cost
  dev NFL big plays seen within 10 min 87.8 -> 84.3 and crunch 70.7 -> 68.7. With both changes the tree is no worse
  than off on any league: dev NFL big live 54.4 -> 54.7, crunch 70.7 -> 71.0, 10-min seen 87.8 = 87.8, dead air 30.4 =;
  locked NFL equal (10-min seen 88.3 -> 88.6); NBA/NHL equal. `playbooks.game.enabled` stays OFF.
- 2026-11-04 1:10 PM (lane 2, cycle 03, planner redirect): **new `delay.self_calibrate` block, OFF** (NoiseCal:
  the stated doubt learned from break-anchor innovations, floor 2 s, never wider than the aging model). Why off:
  sims only (live needs the residual pool persisted across nights), and the truth model it is graded against
  (per-tune U(0, 6) s, gap 15 +- 10 s) is an assumption until Thursday's probe data. Measured (honest sims, wait):
  dev NFL big live 54.4 -> 57.6, locked 53.3 -> 60.4, coverage 98.4-100%, 0 new mid-play entries.
- 2026-11-04 12:45 PM (controls, cycle 03): **the candidate profile turns `two_stick.pretune_on_open` on**; the live
  default stays off. Drill (4 nights, flaky taps): with it, press-to-TV p50 8.5 -> 3.7 s and pre-tuned presses
  p95 stays 8.75 s. Pre-tuned presses (game live on the hidden stick): 10, p50 3.02 s, p95 3.7 s, bounded by the
  ~2 s SmartThings flip. Cold presses (a tune, including a channel parked on a catch-up replay): 8, p50 8.5 s.
  The drill and the North Star line now report the two apart.
- 2026-11-04 1:30 PM (lane 1, cycle 03): **The Noggin PICK decider hook ships OFF** (`noggin.pick.enabled:
  false`, `shadow: false`; new knobs `mode: breaks`, `min_gap_s: 0`, `keep_kinds`). NBA candidate sim with
  the flag ON: 0 mid-play cuts and entries, your-team 31/31, dead air 19.4 -> 18.1 dev / 24.1 -> 23.4
  locked, but big live only ties (77.5 / 68.8) and locked crunch live drops 84.9 -> 81.0, so it misses the
  bar. pct_of_oracle is unchanged (83.3 dev, 68.8 locked). The switch-rate cap is off because 90-180 s
  cost dead air on dev with no change in the switch rate. docs/noggin.md §11b.
- 2026-11-04 2:30 PM (lane 2, planner ask, rule #1): **candidate gets `decider.entry.landing_lag_s=6` and
  `decider.entry.ended_strict=true`** (new keys, default 0 / false). Why: with an honest per-tune landing error the
  candidate made 1 NHL mid-play entry on every seed (BUF @ WSH, an urgent landing after a game ended). After: 0 on
  all three seeds, NHL crunch 79.9 -> 81.3 (seed 0), every other line within 0.2, locked equal. The 6 s is
  syncsim's upper end until Thursday's probe refits it.
- 2026-11-04: **two_stick.tv_flip_prior_s = {median: 1.89, max: 2.58}** (was 1.98 / 2.2, 10 samples). Nick's live
  lab: 64/64 SmartThings flips confirmed, 0 retries, p50 1.89 s, p95 2.18 s, max 2.58 s, ack 0.31 s. The upper
  bound stays the measured max because landing deadlines guard rule #1; decider.switch_cost.hdmi_flip
  (half-step of median + settle) is unchanged at its shipped value. The night lab's SmartThings TV draws from
  the same quantiles. Also STK-05 (no flag: a stale or paused standby was never a usable one): media_session must
  read active and speed>0, and verify_standby re-tunes a paused / stale hidden stick at once.
- 2026-11-04 3:05 PM: **report.nightly (default OFF): the night health report** (Nick's build (a), lane 4).
  scripts/night_report.py writes docs/nights/YYYY-MM-DD.md each morning from the night's published live
  summary (docs/live-nights/): the North Star lines one live night can grade (mid-play, landings in dead time
  >= 98%, press-to-TV p95 <= 3 s), every miss (ET time, game, end, reason), sync error per stick, switch and
  press latency, faults fixed. PENDING with no night. Off until it passes its gates; northstar.yml runs it.
- 2026-11-04 3:30 PM (lane 2, Nick's crunch_now decision): **new key `decider.entry.possession_start` ships
  `off`** (off | crunch | all). Why: on dev with tune jitter (3 seeds) `crunch` is identical to the candidate's
  `wait` (which already rewinds to the last dead ball when one is in reach) and `all` costs NBA big live
  77.5 -> 73.2 on seed 0; 0 mid-play either way, locked equal. New grade `possession_start_entry_pct`.
  The candidate profile takes `crunch` (Nick's default; same numbers as `wait`), live default stays off.
- 2026-11-04 4:15 PM (lane 2, rule #1): **new key `decider.entry.confirmed_dead_only` (default false, candidate
  true)**: a dead window opens only on a logged stoppage; a lone substitution (its stoppage missing from the data)
  opens none. Why: the candidate's last mid-play entry (NBA GS @ DEN, dev seed 2) was a 70 s rewind into such a
  gap. Dev jitter 6: NBA mid-play 1 -> 0 (seed 2), NBA crunch 87.9 -> 86.9 (seed 1), dead air +0.1..0.3, rest equal.
- 2026-11-04 5:00 PM (lane 2, live lab): **`two_stick.tv_flip_prior_s` = {median 1.89, p95 2.18, max 2.58}** (Superseded: the bound stays the measured max; CEC-off prior below.)
  (was 1.98 / 2.2 from 10 flips; the planning bound is now the p95) from Nick's 64 SmartThings flips. Sims gain
  `--flip-dist` and `--positions`. **Candidate: `sync.break_anchor.enabled=true`, `delay.sync.min_sigma_s=3`.**
  Why: with YTTV's playback position the honest sims hold 2-sigma coverage >= 98% at a 3 s floor (2 s fell to
  94% on NHL), 0 mid-play, NFL big live 54.3 -> 58.8 / 54.5 -> 59.5 / 54.7 -> 58.7, NHL +1.9..2.6, locked NFL
  53.5 -> 57.2, NHL 50.0 -> 54.8. New flag `sync.probe.logcat_anchors` (off): ad edges from YTTV's logcat.
- 2026-11-04 5:30 PM (lane 2, Nick's 3-minute replay window): **new `replay.window_s` (0 = off) and
  `replay.window_leagues`; candidate `window_s=180`, `[nfl]`**. A missed moment airs within 3 min or only in the
  montage. NFL, 3 seeds: big live +4, crunch +1.4..2.9; NHL lost 4-7 points of seen-within-3-min and gained
  2-3 points of dead air, so NFL only until that's understood.

- 2026-11-04 (planner, lane 2): `noggin.lookahead.leagues` (default [] = every league) scopes GAME-04 clairvoyance; the candidate runs it for NFL only. Honest sims (3 s sigma): NFL big live +5.4..5.5 on 3 dev seeds, +4.9 locked, 0 mid-play; NHL locked crunch live -0.9, so NHL and NBA stay off.
- 2026-11-04 (Nick 11:35 AM live lab, lane 2): **`two_stick.tv_flip_prior_s` = CEC-off flips {median 1.89, p95 1.98, max 2.02}** (30/30; CEC on was p95 2.18, max 2.58), so the planning bound (the measured max: landing deadlines guard rule #1) drops 2.58 -> 2.02 s. Hidden-stick pre-tune to PLAYING by model in `contracts.switching.PRETUNE_PRIOR_S` (4K Plus 2.33/3.06, 4K 2023 3.74/5.50; prefer the 4K Plus for hot pre-tunes). Sims: `--flip-dist` draws the CEC-off prior; `--pretune-dist` makes every switch a cold pre-tune (pessimistic): 0 mid-play, NFL big live -0.4, NHL crunch -1.0 vs the planner's 5 s.
- 2026-11-04 (lane 2): **the 3-minute replay window counts from when the play aired on screen**, not from when the feed had it (that was ~2 min earlier with the time-shift buffer, so the window was really ~1 min). NFL dev seeds 1/2: seen within 3 min +2.7/+1.5, big live -0.7/-1.3, 0 mid-play. NHL still loses 1 point of seen-within-3-min with the window, so it stays NFL only.
- 2026-11-04 (planner, lane 2): `decider.crunch.instant_replays` (default true); **candidate false**: a crunch stoppage takes only a play you missed, never an instant replay of one you saw. It answers the locked NFL crunch drop from the window-origin fix. Dev, 3 seeds: crunch live +0.8..2.2, crunch plays under a replay 19/13/21 -> 9/4/11, big live -0.6..+0.5; NBA/NHL equal. Locked, report only: crunch 71.6 -> 74.6, big live 60.7 -> 59.4, seen within 10 min 78.7 -> 86.6, 0 mid-play.
- 2026-11-04 (planner, lane 2): **`decider.leave.dead_windows`** (default [] = off); **candidate [nhl]**. NHL leaves also use the play-by-play's dead windows (cuts.DeadTracker, what entries already land on). A whistle whose faceoff arrived in the same poll never reached the game state, so the leave gate caught only 47% of on-screen dead time; the windows catch 71%, with 0 false-safe ticks. NHL dev 3 seeds: seen within 3 min 64.5/63.8/63.6 -> 76.4/78.0/75.2, big live +1.1..4.0, crunch +0.5..2.8, dead air -0.2..-0.5, 0 mid-play cuts or entries. Locked, read once: seen within 3 min 63.5 -> 72.1, crunch 78.7 -> 81.7, big live 51.9 -> 51.0, dead air 25.8 -> 24.8, 0 mid-play. NBA made 1-2 mid-play cuts and NFL gained nothing, so NHL only.
- 2026-11-04 (Nick 12:10 PM live lab, lane 2): **`replay.seek_step_s` 10 -> 15, new `replay.seek_wake_presses: 1`, `seek_stall_min_s: 2`, `seek_stall_max_s: 4`.** Measured reason at the top: YouTube TV steps 15 s a press, swallows the first press of each batch and stalls 2-4 s, so today's code lands entry rewinds where nobody checked. Honest sims with the real seek (`--real-seek`), today's code vs calibrated: NFL dev 32-34 -> 0 mid-play entries and 44-63 -> 0 cuts, NHL 32-37 -> 0 and 76-86 -> 0; locked NFL 12 -> 0 / 24 -> 0, NHL 14 -> 0 / 38 -> 0. **`decider.entry.cue`** (default `seek`); **candidate `hidden`**: rewind the hidden stick before the flip, so the live edge never shows mid-play first (NFL dev 270-345 glimpses -> 0). Dev: 0 mid-play, NFL big live +0.8..1.7. Locked: crunch -4.1 NFL, -4.7 NHL vs the calibrated seek; rule #1 first. `pause` loses (late flips cut mid-play). `timing.behind_live_s.hold_overreport_s: 1.5`: a hold is measured on the stick's clock vs position; the wall span less 1.5 s is the fallback.
- 2026-11-04 3:00 PM (live lab, #133): **`replay.seek_step_s` 10 -> 15 and new `replay.seek_wake_presses: 1`**.
  Why: measured on both sticks (YouTube TV, `data/livelab/2026-11-04/seekcal.jsonl`): the first press of a batch
  only shows the seek bar, each further press is 15 s, plus a 2-4 s stall. The old 10 s quantum made a 60 s replay
  rewind land ~80 s back.
- 2026-11-04: **calib.auto (new, default OFF).** Nick's 3:05 PM overseer queue item (c): each night's TV flip
  time and press-to-TV are re-measured from the live logs into data/calib/<night>.json (one shared
  file per night: a timing section, lane 2's sync section), with an outlier guard that refuses a value
  more than 3 MADs from the last 5 nights. The files are written every night so the guard has history; with
  calib.auto on, the next start plans the TV flip with the newest accepted median and max.
- 2026-11-04: **warmup.pregame (new, default OFF), warmup.lead_min 10.** Overseer queue item (d): from 10 min
  before the night's first listed start until a game goes live (listed starts run 5-15 min early), the
  likeliest first pick of that window (your team, then league order) is preloaded on the HIDDEN stick, so the
  first switch is a flip. Never the stick on screen, never while paused / dry run / a switch is in flight;
  the pre-game checks stand aside meanwhile. Night lab seed 1: first switch 7.52 s cold tune -> 1.92 s flip.
- 2026-11-04: **sync.probe.bursts true (new, logging only), sync.burst_anchor.enabled false (new), gap_s 15 /
  gap_sd_s 10 (priors).** YTTV MediaSession bursts are content-tied (live lab); their onsets are logged with the
  feed's last dead ball so a night can show whether they follow TV timeouts (scripts/burst_align.py). Off as an
  anchor until then. Also: the sync filter's origin is per TUNE (YTTV position restarts near 46,790 s at every
  tune), and `--positions` in the sims is now that per-tune clock (honest NFL big live ~5 points lower).
- 2026-11-04: **buffer.watchdog (new, default OFF); timing.behind_live_s.hold_cal_s -1.5.** Overseer queue item (e),
  playbook STK-06: a hidden-stick hold more than 2 s off its target (read off the stick's own clock since the
  hold re-anchored it) is rebuilt at its game's next dead ball, or after 90 s without one (the stick is off
  screen and not flippable while it rebuilds, so nothing on the TV is cut). Too close to live: pause the gap
  minus 1.5 s (Nick's hold calibration); too far behind: reload at the live edge and hold again. Every
  detection and rebuild goes to data/buffer.jsonl -> live-night summary -> northstar "buffer drift recovered".
- 2026-11-04 3:15 PM ET (Nick): **PROMOTION BAR = strict (B); cut rules; North Star mid-play line split.** A
  candidate flag goes ON in the next RC only if, on LOCKED nights: (a) mid-play entries = 0, hard PASS; (b) NBA
  and NHL crunch live PASS and are >= live; (c) your-team gates PASS; (d) every other line is >= live, where
  equal within the bootstrap CI counts as equal. Cut rules: never JOIN mid-play; leaving mid-play is OK at low
  leverage, never at a high-leverage moment; down the stretch is sticky (learned leverage score, self-taught
  from the oracle, rules only set the floors); league order NFL = CFB > NBA > NHL, MLB; MLB jumps in only for
  key moments; multi-game conflicts go to the scenario tree. CLAUDE.md Rule #1 now reads "never JOIN
  mid-play". northstar.py: `mid_play` counts entries only (gate 0), new `high_leverage_leaves` (gate 0, close
  + late or overtime per `redzone/lab/leverage.py`) and `low_leverage_leaves` (info). Scorecards without
  `cuts_high` read PENDING.
- 2026-11-04 (integrator, promotion step): **highlight jump `jump_when_it_fits` is NOT promoted; `reel.play` and
  the `highlight_jump` flag stay as they are.** Checked under bar (B) at the overseer's request. It is the naive
  baseline in the Kaggle distill jump task (`agent/kaggle/grade.py`): 99.8% of reels in dead time, 0 big plays
  missed. It can't pass bar (B) yet: (1) release_gate.py has no line for it, because the North Star sims never
  play a reel, so there is nothing to grade on locked; (2) the 99.8% is on the distill pack's own test split,
  which excludes the locked nights; (3) 99.8% is not 100%: about 1 jump in 500 comes back after the restart,
  which is a mid-play JOIN and fails bar (a) outright. Next: model the reel jump in the night sim (return time =
  reel + both switches vs the real break), grade it on locked as entries_mid_play, and require 0.
- 2026-11-04 3:30 / 3:40 PM ET (Nick): **North Star gains three lines, all PENDING until their builders land:**
  `wv_calibration_err` and `wv_rank_agree` (Heat 2.0 Watch Value, lanes 1+2; read from a card's
  `metrics.watch_value` {calibration_err, rank_agree_pct}; gates TBD) and `contract_violations` (gate 0; read
  from `docs/citymap.json` {"violations": [{rule, where}]}, which lane 4's city map checker writes). **Crunch
  floor in the promotion step:** `tests/test_crunch_floor.py` checks that no named profile, and no override
  layer at its most aggressive (Pick replace mode with no keep kinds, scenario tree, lookahead), leaves live
  crunch where the live default stays, and that Pick hands back the app's decision inside a crunch window.
  A candidate that fails it fails bar (B).
- 2026-11-04 4:15 PM ET (Nick; REPLACES his 3:47 PM "crunch = stay is absolute"): **crunch is a learned state,
  not a wall.** (1) The Noggin learns when a game ENTERS crunch and when it LEAVES it (lead blows out, game
  decided, clock gone, the key moment passes), with hysteresis so it doesn't flicker. New North Star line
  `crunch_exit_acc` (crunch exits matching the hindsight oracle, >= 95%; PENDING until a card carries
  `crunch_exits` / `crunch_exits_ok` per night). (2) Crunch protects against LOW-value games only: a "bullshit
  game" can never pull us out. Crunch vs crunch is decided by value (scenario tree + Watch Value, trained by
  self-play against the oracle), e.g. leave the Bills' last 3 min for a Bears 1-minute drive, then catch up the
  Bills from the hidden stick's buffer, or interleave at dead balls. (3) Hard rules are FLOORS only: never join
  mid-play; never leave a high-leverage moment for a low-value game. League order and WSH/UNC are strong priors,
  not walls; Nick's press is a control. Tests: `tests/test_crunch_floor.py` (floor: a low-value game never pulls
  us out, live or at a break except an ad-break round trip; priors, the press and value-based crunch vs crunch).
  The strict-xfail tests that forced an absolute stay are removed.
- 2026-11-04: **replay.crunch_block (new, default OFF; candidate ON), crunch_block_mine_leagues [] (candidate
  [nfl]), crunch_block_mine_after_s 90.** Cycle 04 review's promotion blocker: on locked, NHL crunch plays aired
  under a catch-up 2 -> 10. No catch-up starts while any live game is in crunch time or its run-up; your team's
  missed play waits too, except in NFL after 90 s (its 3-min window can't wait out crunch). Locked, 3 seeds,
  honest sims: crunch plays under a replay NFL 7-13 -> 0-1, NHL 0/10/9 -> 3/3/3; your-team plays = or better.
- 2026-11-04 (lane 1, cycle 04): new config keys `noggin.possession.{enabled,shadow,leagues,model}` (the learned
  jump / stay policy for lane 2's possession-start hook, PICK-cue trained on `oracle.solve_cue`) and
  `noggin.pick.model` (default `v1`). All are off or unchanged by default. Why off: on NBA dev the veto leaves the rule
  default unchanged (77.5 / 90.0) and the `all` scope adds a mid-play cut. The pure rollout beats the rule (85.9 / 91.9),
  but the decider path doesn't carry that gain yet. docs/noggin.md §13.
- 2026-11-04: **two_stick.shadow.* and demo.* (new keys, values unchanged).** ShadowStick flip/tune/keep and demo
  feed-lag/play-length/tune/sigma timings moved into default_config.yaml (two_stick.shadow.*, demo.*), values
  unchanged; reason: one source of truth for timing (Nick, one connected system). Moving to measured values is a
  separate decision.
- 2026-11-04 (lane 4, #168, one brain merge with integration/next): `control.STANDBY_REPAUSE_S` (APP-04, #164) moves
  to `two_stick.standby_repause_s: 600` in default_config.yaml, value unchanged; postseason.py's ROUNDS / DEFAULTS /
  _rank renamed ROUNDS_LEFT / WEIGHTS / _poll_rank (no behavior change). Reason: the contract kernel (K1/K2/K4)
  flagged them as new on merge; one definition per fact, timings in config (Nick, one connected system).
- 2026-11-04 (Nick 6:10 PM, via the overseer): **official direction** = docs/plans/2026-11-04-noggin-master-plan.md +
  docs/research/watch-science-playbook.md (copied from codex/resume-redzone 51908ba). All ideas live in ONE ranked
  queue, docs/experiments.md; Phase 0 starts now (suspense S = E[(ΔWP)^2] line + WV input, one event language,
  nightly shadow, off-policy grading). The playbook's §5 line is binding: the objective is "worth it", not watch
  time; **RedZone never adds betting features** (pre-game lines only as a hidden data prior); no artificial
  suspense, autoplay or pull-back nudges, streaks or viewer sensors. Nick's taps and ratings stay evaluation-only
  (self-taught rule). Baseball Savant skipped for now (terms unclear; MLB last and out of season). RC6 = #132 + #173.
- **2026-11-04 (lane 2, EXP-018): `decider.crunch.by_value` ships OFF** (with `value_league_prior` 5, `value_mine_prior`
  30, `value_margin` 10). Pre-registered bar failed with the score as the value: dev NFL crunch live −1.0/−2.1/−1.0,
  NHL crunch-in-replay 0/8/0 → 0/11/5. Retried once lane 1's watch value is wired in.
- 2026-11-04 (Nick 7:21 PM ET, via the overseer): **off-policy exploration approved with limits** (EXP-005). New flag
  `decider.explore.enabled`, off by default, and not started until Friday's v3 locked set lands. It may only pick
  between options that are both already safe (dead ball, no mid-play) and within a small value margin, on at most 5% of
  decisions, and never in crunch, down the stretch, your-team moments or the catch-up queue. Every decision logs its
  propensity so the doubly robust estimator is valid. Promotion goes through the normal bar (worth it ≥ live).
  Reason: grade new brains on logged nights without running them live.
- **2026-11-04 (lane 2, #132): seek-model timings moved into config, values unchanged** (onebrain K2: no timing
  literals in code). `replay.seek_press_s` 0.25, `replay.pause_keys_s` 0.5, `replay.pause_err_s` 0.25,
  `replay.pause_cue_grain_s` 0.5, `replay.hidden_seek_keys_s` 1.5, `decider.leave.tune_s` 6,
  `sync.probe.log_edge_sd_s` 1.0; the sim reads `two_stick.input_settle_s`. Unused `LIVELAB_FLIPS_S` dropped
  (samples stay in docs/livelab); the sim's position origin is 0 (any constant works).
- 2026-11-04 (Nick, 6:50 PM ET): RC6 may switch DURING games, but only at halftime/intermission of the game on
  screen. `scripts/deploy.py deploy --at-break` allows live games while the on-screen game's status is Halftime /
  Intermission (NHL "End of 1st/2nd"), polls every 30 s, and keeps the 10-min health watch + auto-rollback. The
  default (no flag) is unchanged: any live game defers. Reason: get RC6 on tonight without cutting into play.
- 2026-11-04 (Nick, 6:40 PM ET): "training needs to be SHARP, grading even SHARPER." These are the 14 standing
  grading and training rules in [docs/grading.md](../grading.md).
  - The integrator writes and freezes the graders (`docs/grader_freeze.json`, versioned per release). The
    training lanes never do.
  - A new CI job, `graders` (`scripts/grading_check.py`), runs three checks:
    - G1: a grader file differs from the freeze;
    - G2: a running or done experiment is not pre-registered;
    - G3: a running experiment moved its goalposts.
  - Reason: grading has to be harder to fool than training.
- **2026-11-04 (lane 2, #174): `replay.crunch_block_tail`** (default `false`, candidate `true`). The crunch block also
  holds until a game the feed ended in crunch time (an OT winner) has aired. Why: the OT winner reached the screen ~2 min
  after the feed dropped the game, and a catch-up reel ran over it. NHL locked crunch live +2.5..2.6 on 3 seeds,
  0 mid-play; dev big seen in 10 min −5..6 (catch-ups wait out the OT tail).
- **2026-11-04 (lane 2, cycle 07): `replay.when_alone`** (default `false`, candidate `true`). When no other game is worth
  switching to, the decider used to stay without checking for a catch-up, so a missed WSH goal waited while WSH's own
  game sat on a break (dev NHL night 20261028). Now your team's missed play still gets its catch-up then (other games'
  plays still wait). Dev NHL your-team 19/21/18 -> 20/21/20 of 21, crunch equal, 0 mid-play, dead air +0..0.4;
  NFL, NBA and locked unchanged.
- 2026-11-04 (lane 4, cycle 07 plan #176 "night report live tonight"): `report.nightly` default ON. The night health
  report (scripts/night_report.py, northstar.yml's 10:00 UTC run) writes docs/nights/YYYY-MM-DD.md from the published
  live-night summary. Its gates pass in tests/test_night_report.py: matches the live log through publish, PENDING with
  no night or no games, no private strings published. Report only: nothing on the TV changes. Off again: set it false.
- 2026-11-04 (Nick, 7:55 PM ET): women's basketball is OFF. Nick's leagues are NFL, CFB, NBA, CBB (men's), NHL and MLB
  only. The North Star and release gate never score WCBB: `scripts/northstar.py NICKS_LEAGUES`, with a test. The grader
  freeze was re-stamped. The default_config change (wcbb false, out of league_priority) is a separate PR.
- 2026-11-05 00:21 UTC (Nick 7:30 PM ET Nov 4, via the coordinator): **Colab Pro+ bought by Nick** (~$50/mo, ~500 CU,
  A100/L4, background runs). This is Nick's own spend; the $0 rule still binds every agent (no other spending
  without asking). **Master plan v2 Phases 0-2 are compressed to finish by Sun Nov 8**:
  docs/plans/2026-11-05-compressed-4day.md, planned ~256-261 CU, keeping ≥ 100 CU in reserve. Colab notebooks
  are self-contained: public inputs at a pinned commit, no Drive mount, no secrets, grades.json printed. The
  overseer launches them in Nick's browser. docs/grading.md still binds: each run is pre-registered, graders are
  frozen, locked is read once (Sunday).
- 2026-11-04 (lane 3, Cycle 06 #1, Start Planner #162): new flag `buffer.hold_left` (default **off**), plus
  `buffer.hold_left_apps` [youtubetv] and `buffer.hold_left_slop_s` 3.0 (playbook STK-07). When on, the game we leave
  stays paused on the stick we left, at the dead ball we left it on, so going back is a flip plus a catch-up from there.
  Past timing.behind_live_s.max_s (one definition, Nick's 90 s cap), or if it resumes by itself, it is reloaded at the
  live edge (the STK-06 guard). Why off: night lab seed 1 has 0 mid-play both ways and identical cuts (52), but no
  return came within the cap, so the catch-up path has only unit tests. It needs the lab rig first.
- 2026-11-05 (integrator, grader review): #145 is accepted. `press_to_tv_p95` now grades REAL presses from data/press_probe/:
  - PASS or FAIL once there are 20 or more presses;
  - any press that never reaches the TV is an immediate FAIL;
  - below 20 presses it shows PENDING, with the sim drill as the fallback, and the sim can still never PASS the line.
  Grader freeze v3. No number changes until a rig run saves presses.
- 2026-11-05 (Nick, 8:20 PM ET): "the gains are big; the gates are goals and targets, keep working to them, but take
  the wins along the way." There is a new PER-LEAGUE promotion rule:
  - A candidate change may go live in one league when, in THAT league, it has 0 mid-play entries on dev AND locked and
    beats live on both. Beating live means no key line is worse and at least one is better.
  - The candidate is graded with the EXACT settings set that goes live, through `/api/settings`, with an instant
    rollback. Other leagues stay untouched.
  - It doesn't wait for every league. Never-join-mid-play stays absolute, and the full gates still define "done".
  - The code is `scripts/release_gate.py league_promotion` (grader freeze v4).
  - It replaces bar (B)'s all-leagues requirement for per-league go-lives.
- 2026-11-05 (lane 3, EXP-084, #140 brought up to integration/next): `calib.sigma_floor_s: 2.0` replaces the
  module constant CALIB_SIGMA_FLOOR_S in decider/screen.py (value unchanged; calibration.sync_prior applies it).
  Reason: the contract kernel flagged it as a new K2 timing literal on merge; timings live in config.
- 2026-11-05 (lane 3, EXP-084, #140 brought up to integration/next): `calib.sigma_floor_s: 2.0` replaces the
  module constant CALIB_SIGMA_FLOOR_S in decider/screen.py (value unchanged; calibration.sync_prior applies it).
  Reason: the contract kernel flagged it as a new K2 timing literal on merge; timings live in config.
- 2026-11-05 (lane 3, EXP-084, #146 brought up to integration/next): the burst detector's timings move from
  sync_probe constants to config, values unchanged: `sync.probe.burst_gap_s: 300`, `burst_after_tune_s: 30`,
  `burst_confirm_s: 60`, `burst_sd_s: 0.3`. The sims' YTTV position origin is 0 instead of 46,790 s (any shared
  per-tune origin gives the same filter result). Reason: K2 flagged them as new timing literals on merge.
