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
- 2026-11-04 (Nick, 15:06 UTC scale order): the Noggin data factory runs on free Kaggle CPU
  (`scripts/kaggle_noggin_factory.py`, resumable season shards + oracle self-play parts, all 4 loaded
  leagues back to 2002 where wall clocks exist). The `noggin-factory.yml` push trigger is removed (manual
  dispatch only), because the repo's GitHub Actions minutes are capped. Every lane 1 PR reports rows per league.
- 2026-11-04 (Nick, 5:00 PM data-scale order; lane 1): the factory plan grows to **NFL 1999+ (tokens only before
  2011), WCBB 2006+ (wehoop) and CFB 2004+ (ESPN's whole FBS board)**. CFB gets a scorecard cut,
  `EVAL_FIRST_NIGHT["cfb"] = 20250801` (the whole 2025 season stays unseen, like NFL), and a test now fails if any
  planned league has no cut. Sources and terms: docs/data-sources.md. CFBD waits on Nick's key; MLB Statcast on his OK.
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
- 2026-11-04 3:47 PM ET (Nick, via the overseer): **crunch = stay is ABSOLUTE**, over every score-based switch
  including the app's own. The only ways out of a game in crunch: (1) a higher-priority crunch by league order
  (NFL = CFB > NBA > NHL, MLB) or Nick's team (WSH/UNC) in crunch; (2) Nick's own button press (a control, not
  training). Two same-league crunches: the higher Watch Value / leverage one wins, and the other gets a replay
  within 3 min. Tests: `tests/test_crunch_floor.py`, one per exception. Lane 2 makes the default change. Two
  strict-xfail tests mark what it must fix: the core's own score-based leave (99 vs 55), and a neutral crunch
  timeout's break visit to a lower-league game. They turn red (XPASS) when it lands, so the marks come off with
  it. The same-league "replay within 3 min" for the losing crunch is not built yet.
- 2026-11-04 (lane 2, #160): **crunch floors (SAFE-09)** per Nick's 4:15 PM correction. `decider.crunch.floor: true`:
  while the screen is in a crunch window, no overlay or model (scenario, clairvoyance, PICK, later the Mind and
  watch value) turns the app's own decision into a leave for a non-crunch game; crunch vs crunch and ad-break
  round trips stay the app's (and later the value model's) call. Live default identical on dev and locked;
  candidate identical on dev (3 seeds, all leagues). The low-value floor `decider.crunch.floor_low_value`
  ships at 0 (off): at 50 on today's score it helped dev NFL (crunch live +0.3..1.2, mid-play entries 10 -> 8,
  crunch under a replay 70 -> 49) but cost locked NFL crunch live 64.2/61.2/64.2 -> 63.6/57.3/64.2 (locked read
  once). It waits for watch value, which is the value the floor should read.
- 2026-11-04: **replay.crunch_block (new, default OFF; candidate ON), crunch_block_mine_leagues [] (candidate
  [nfl]), crunch_block_mine_after_s 90.** Cycle 04 review's promotion blocker: on locked, NHL crunch plays aired
  under a catch-up 2 -> 10. No catch-up starts while any live game is in crunch time or its run-up; your team's
  missed play waits too, except in NFL after 90 s (its 3-min window can't wait out crunch). Locked, 3 seeds,
  honest sims: crunch plays under a replay NFL 7-13 -> 0-1, NHL 0/10/9 -> 3/3/3; your-team plays = or better.
- 2026-11-04: **decider.leave.low_leverage (new, default [] = today's rule; candidate [nfl, nba]),
  decider.leave.sticky_closeness 0.5.** Nick 3:15 PM cut rules: never JOIN mid-play; LEAVING mid-play is OK at
  low leverage; down the stretch is sticky. The floor: never crunch or its run-up, a locked moment, your team's
  game, or a late game at closeness >= 0.5. NHL stays off (dev: 1-3 mid-play entries, big live -5). The learned
  leverage score comes next and may only make this stricter.
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
- 2026-11-05 00:28 UTC (Nick 7:28 PM ET, via the coordinator): **betting closing lines approved as a HIDDEN check only**:
  usable for win-probability calibration and evaluation (EXP-002), never as a model feature, never shown, never a
  nudge toward betting. Live line moves as a switching signal stay excluded (EXP-077, playbook §5). Reason: the
  calibration needs an outside reference, and the line forbids betting features.
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
- 2026-11-05 (integrator, grader review): #146 is accepted. `backtest._position_tick` treats YouTube TV's position as a
  per-TUNE clock, as the live lab measured: every fresh tune starts near 46,790 s. `grading.SwitchLog.pos_true0` records
  the true delay at the start of each tune. Before this the sim handed the filter each tune's unknown landing for free.
  It only affects `--positions` runs; NHL over 6 nights is identical. Grader freeze v5.
- 2026-11-05 (Nick via overseer, 8:30 PM ET): **rule #1 wins the NHL trade.** The RC6.1 candidate set,
  `/mnt/project-files/lane2/nba-candidate-settings.json`, is promoted for **NBA and NHL** under the per-league rule.
  - **NBA:** a clean PASS. Mid-play entries drop to 0 from 42 (dev) and 21 (locked), and every line is better.
  - **NHL:** a PASS by Nick's call. Mid-play joins drop 28 → 0 on dev and 8 → 0 on locked. Big live improves, from 41 to 46 on dev
    and from 42 to 53 on locked. The cost is about 1 point of crunch live (dev 81.2 → 80.3, locked 82.7 → 81.6) and
    0.6-0.8 more dead air.
  - **NFL:** FAILS. Big seen drops from 89.3 to 77.6 on dev and from 88.2 to 82.9 on locked. The keys are global, so before the first NFL or CFB
    game the set must be rolled back, or replaced by an NFL-passing set (EXP-200).
  - **Ungraded leagues:** CBB and MLB get the global keys without a grade.
  - **Deploy:** through `/api/settings` by the overseer only, with an instant rollback to the saved live settings.
  - **Follow-ups:** win back NHL's crunch point and dead air with no mid-play join, for example with an earlier pre-tune or
    better intermission timing.
- 2026-11-05 (integrator, 8:40 PM ET, grading EXP-203): the EXP-203 set (`/mnt/project-files/lane2/exp203-settings.json`,
  17 keys) is the promoted set minus its NFL-scoped keys. It was graded with `league_promotion`, plus big_seen_pct, on
  seed sums for dev and the one locked read:
  - **NBA: PASS.**
  - **NFL: PASS.** Mid-play entries go 4 → 0 and 1 → 0, big seen 89 → 94 and 88 → 99, dead air 35 → 30. Nothing is worse.
  - **NHL:** the same as the promoted set, so it FAILs only on the trade Nick accepted (rule #1 wins).

  It replaces the NFL rollback before Thursday Night Football. Removing a key through `/api/settings` must SET it back to its
  live default, not just leave it out. CFB, CBB and MLB stay ungraded under the global keys.
- 2026-11-05 (integrator): `league_promotion` now also compares `big_seen_pct`, after lane 2 caught NFL big seen falling
  89 → 78 on a set the old line list would have passed. Grader freeze v6.
- 2026-11-05 (overseer, 8:40 PM ET), **this replaces the two lines above about the bd3c88f set and EXP-203's live post.** No
  global settings set goes live through `/api/settings`. Per-league promotion ships as **league-scoped defaults in an RC**,
  through deploy.py (tests, health watch, rollback):
  - **RC7**, cut and deployed Thursday afternoon before TNF at 8:15 PM ET, carries:
    - EXP-203 for NFL only (NFL-scoped keys);
    - lane 2's league-scoped NBA/NHL set, graded on NBA and NHL;
    - the Prime pre-tune fix, if it's ready.
  - CFB, CBB and MLB keep live behavior until they're graded.
  - Every league-scoped set is graded with `league_promotion` on that league's dev cards and its one locked read.
- 2026-11-05 (integrator, grader v7): `scripts/eval_sports.py` and `scripts/eval.py` flatten
  `league_overrides[<graded league>]` onto the run's config, after `--set`
  (`redzone/lab/evaldata.flatten_league_overrides`). A single-league scorecard therefore grades exactly the
  league-scoped defaults RC7 ships. With no `league_overrides` nothing changes. Grader freeze v7.
- 2026-11-05 (integrator, grader v8, lane 2's catch): when no `--behind-live` is given, scorecards take the time-shift buffer
  from the flattened config (`timing.behind_live_s`: enabled, by_league, default), through
  `evaldata.config_behind_live`. A tree whose defaults turn the buffer on is now graded with it. On 2 NBA nights,
  `--set timing.behind_live_s.enabled=true` and `--behind-live 90` give identical cards. With the shipped defaults (off),
  nothing changes. Grader freeze v8.
- **2026-11-04 (Nick 7:55 PM, "TURN OFF WOMEN'S BASKETBALL"):** WCBB is out of the noggin factory plan, the training pack sources and therefore the data ledger (docs/data-sources.md row: dropped). Nick's leagues are NFL, CFB, NBA, CBB (men's), NHL, MLB only. Why: it isn't one of his leagues, so its tokens must not count toward his data scale. The live default (mode.leagues.wcbb=false) is lane 4's change.
- 2026-11-05 (lane 1, #169, Wave 1 #3): **MLB gets a factory cut: `EVAL_FIRST_NIGHT["mlb"] = "20250301"`**, so all of 2025
  is held out for a future MLB scorecard and locked set. Retrosheet event files (green, notice required) feed the
  factory's MLB event tokens, 1974-2024, via scripts/retrosheet_plays.py. They carry no wall clocks, so time tokens are
  UNK and never invented. Why: MLB had no cut (the `.get` default is no cut), and the first MLB pull must not be able
  to reach a future eval night.
- 2026-11-04 (lane 5, #185, EXP-035): new default `phone.why_tag_min: 70` (0-100). The phone's "Why we're here"
  label shows only when the game on screen is at or over it (learned watch value, else today's score), and
  lab/decision_records grades every switch over it for "alerted but nothing happened". Reason: the Start Planner
  (Cycle 08, #183) asked for one source for numbers; 70 is a starting point, to move only with an EXP result.
- 2026-11-04 (lane 5, EXP-036): new default `phone.commit: false`. Commitment controls on the phone (stay here, only
  my teams, stop after this game, end at a time) send the agreed `commit {mode, game_id?, at?}` action, which every
  mode applies at a dead ball, and a "Slate complete, 0 live games" state ends the night with nothing after it.
  OFF until lanes 2/3 ship the action. Reason: Start Planner Cycle 08 (#183), playbook §5.
- **2026-11-05 (lane 2, #212): `decider.entry.unknown_wait_leagues`** (default `[]`, off; EXP-089). In a listed
  league an entry never takes a landing the data can't call ("play may be live"): it rewinds to a known dead ball
  or waits. Why: RC6.1's 3 NFL live mid-play entries on the North Star card were all this case; dev 3 -> 0 and
  fresh seeds 8 -> 1 with plays missed on entry down. Off until the release read.
- **2026-11-05 (lane 2, #214): `decider.entry.conformal`** (enabled `false`, tau 0 for NFL/NBA/NHL; EXP-024). The
  conformal floor: no entry lands on less doubt than tau x sigma. Why: dev calibration put NFL's bound at 0.0012
  mid-play per timed entry with tau 0 (no floor needed); NBA and NHL are underpowered. Off; tau stays 0.
- 2026-11-05 (lane 5, EXP-040): new defaults `phone.worth: false` and `phone.worth_probe_days: 7`. One tap
  "Was tonight worth it?" (1-5) on the Slate complete card, its own flag so it can run with phone.v2 off; the
  agency / regret probes (playbook §6) at most once per 7 nights, only after the rating. Rows go to
  data/worth.jsonl and are EVALUATION ONLY: redzone/lab/worth.py grades "worth it >= live" (pre-registered bar in
  docs/lanes/experience.md); tests/test_worth.py fails if noggin, decider or scoring code reads them. Reason:
  Start Planner Cycle 09 (#200), Sunday Nov 8 promotion bar includes "worth it >= live".
- 2026-11-05 (integrator, grader review): #205 is accepted. The EXP-040 "worth it ≥ live" line (`redzone/lab/worth.py`,
  `scripts/worth_line.py`) joins the frozen graders. The bar is non-inferior by at most 0.25 on the 1-5 scale, with the
  95% night-bootstrap lower bound, at least 4 rated nights a side, and PENDING otherwise. Nick's ratings stay
  evaluation-only (`tests/test_worth.py` scans noggin/decider/scoring). Note: 4 nights is a thin minimum, so treat an early
  PASS as provisional until 8. Grader freeze v9.
- 2026-11-05 (lane 1, #169): the Kaggle noggin factory gets a data-plan gate, `RZ_DATA_PLAN`. **Default "B"**
  (Nick 11:20 PM ET Nov 4, logged on #241): every source, including hoopR NBA/CBB, ESPN NHL/CFB and NHL api-web for
  old NHL seasons, with `manifest.json` `"license": "personal-use, not for commercial release"`. "A" = green only
  (nflverse NFL, NBA.com cdn NBA, Retrosheet MLB) for a company version; a green run never copies in red shards.
- 2026-11-05 (Nick, Nov 4 9:45 PM ET, RC7 scope; integrator writes the lines): RC7 ships only if three gates pass on
  locked nights plus real hardware. (1) `press_to_tv_p95` ≤ 3 s on real presses, with a Prime sub-gate: presses tagged
  `app: prime` need their own p95 ≤ 3 s over at least 5 presses, and any Prime press that never reaches the TV fails the
  line. (2) `delay_err_p95` ≤ 2 s per stick, worst stick decides, from `data/sync_truth/*.jsonl` (est vs true seconds
  behind live from gold data, #140 and YTTV anchors; manual taps are dropped). PASS needs both sticks with ≥ 200 rows
  over ≥ 2 nights. (3) `mid_break` = 0: an entry that lands in a commercial break with more than 10 s of dead time left
  before the restart (`cutgrade.BREAK_TAIL_BAR`) is mid-commercial. Mid-play stays its own hard 0. Grader freeze v10.
- **2026-11-05 (lane 2, RC7):** new default `league_overrides` (redzone/default_config.yaml). NBA and NHL run the
  promoted set; NFL runs EXP-203; CFB, CBB, WCBB and MLB keep live behavior. Overrides are keyed by the FULL
  league (`cbb` never inherits `nba`'s). Why: Nick, Nov 4 8:20 / 8:40 PM, per-league promotion as
  league-scoped defaults shipped in RC7 (not global /api/settings posts). Graded dev + locked with grader v8:
  NBA and NFL pass `league_promotion`; NHL passes with the trade Nick accepted (mid-play 28 → 0 dev,
  8 → 0 locked). Rollback for one league: set that league's keys back under `league_overrides.<league>`,
  or redeploy without it.
- **2026-11-05 (lane 2, EXP-204, RC7 fast track):** new key `decider.mine_late_max_lead_wp` (global default 1.0 =
  off). `league_overrides` sets it to 0.95 for NFL and NBA. Your team late (and not garbage) holds the TV only
  while the leader's win probability is ≤ 0.95; crunch time always holds. Why: Nick, Nov 4 9:30 PM. ATL up 12
  with 9:09 left (WP ~0.99) held WSH all night, and his "close" is ~6 points / ~6 minutes (WP ~0.91). Dev
  (seeds 0-2): 0 mid-play; NFL and NBA identical on every line; NHL crunch live −0.5, so NHL stays at 1.0.
  Pre-registered arm pick: tie → 0.95. Not a strict promotion pass: no line improved. It ships as Nick's
  definition of "close", with no line worse.
- **2026-11-05 (lane 3, RC7 fast track, Nick 9:30 PM "implement please"): `two_stick.hand_back_s: 600`** (new, on). For 10 min
  after you pick a game on the phone, the game you left keeps the hidden stick (ranked with your game's return, below the
  decider's own NEXT and your focus game), so a tap back is a flip. Why: Nov 4 live, 16 phone presses to Prime ~2 min apart,
  11 cold (~30 s) because a planner guess took the hidden stick. Lab night ping-pong drill (32 presses, seeds 1-2): warm
  0/32 -> 31/32, Prime press 46 s -> ~3 s. 0 = off (instant rollback via /api/settings). Standby only: no switch timing change.
- **2026-11-05 (Nick, 10:55 PM ET): no deploys and no Mac-side experiments while games are live and Nick is
  watching; the Mac stays plugged in on game nights.** A standing rule for every lane. Why: tonight's mid-game deploys and
  rollbacks. Same message: RC6.2 (1bf4150) deploys with the TV off, and a second Go now tap switches at once, even
  mid-play (his explicit control; one tap still waits for a dead ball).
- 2026-11-05 (Nick, Nov 4 10:55 PM ET): RC6.2 (1bf4150) approved and deployed with the TV off. A SECOND "Go now" tap
  switches instantly, even mid-play: it's Nick's explicit control. One tap still waits for a dead ball. Graders count
  that forced entry on its own (lane 3's forced go-now line), never as a decider mid-play entry, and never as a training
  label. New standing rule: no deploys and no Mac-side experiments while games are live and Nick is watching. The Mac
  stays plugged in on game nights.
- **2026-11-05 (Nick, 11:10 PM ET): two 24/7 brain engines.** (1) A context and hype data engine (lane 1, cloud CPU
  only, never the Mac): importance and buzz signals per game for all six leagues; an LLM reader stores derived numbers and
  the URL, never article text, only from allowlisted sites whose terms allow it. **Betting lines and line moves become
  inputs to importance** (market expectation, upset odds), widening the Nov 4 hidden-check-only rule; they are never
  shown and never nudge betting. (2) A test factory (integrator): drafted experiments and graders, nightmare nights from
  dev history, off-policy grading of every logged night. YouTube TV DVR/screens are out (DRM). Plans:
  docs/plans/2026-11-05-brain-engines.md (EXP-090..093) and docs/plans/2026-11-05-test-factory.md (F1-F7).
- **2026-11-05 (Nick, 11:20 PM ET): data = B, "and way more of these sources, since it's a personal project."** The brain
  may train on ESPN-derived data (hoopR NBA/CBB, ESPN pbp), NHL api-web + shifts, Baseball Savant/Statcast and other
  public sports APIs with restrictive terms. **Personal use only:** every dataset and model trained on them is tagged
  "personal-use, not for commercial release"; a company version retrains on green data later. Guardrails that still hold:
  public endpoints only; no logins, paywalls, CAPTCHAs or bot-block bypasses; respect rate limits and robots (cache, back
  off); no pirated streams; articles read for derived numbers, never stored as full text; never touch Nick's credentials.
  Replaces the "skip Savant" call (6:10 PM). Teacher EXP-086 runs on B; Saturday's world model is sized for B (~100M).
- **2026-11-05 (Nick, 11:35 PM ET): a standing data scout loop**, "100x more unique data sources and data types, agents
  hunting 24/7" (lane 1 + a dedicated scout thread). Find, license-check (B rules for personal use), rank by new
  information per GB, add to the factory; the ledger in docs/datasources grows every cycle, to several billion tokens this
  month (EXP-097). Synthetic self-play is capped at ~3x real. Paid feeds need Nick's OK. Broadcast video/audio is out
  (company stage, licensing).
- 2026-11-05 (lane 5, EXP-038): new defaults `phone.orient: false`, `phone.orient_beat_s: 3`,
  `phone.orient_override_s: 30`. A 2-3 s orientation beat (score, clock, one situation fact, and the context line
  when late and close) in a corner of the phone after each switch. The TV has no overlay path on this rig (the
  sticks only switch inputs), so the beat is phone-only for now. Graded by redzone/lab/orient.py (override rate
  within 30 s, whole nights alternated on/off; bar in docs/lanes/experience.md). Reason: Cycle 11 (#217), playbook P12.
- 2026-11-05 (lane 5, EXP-039): new default `phone.also_close: false`. An "Also close" line under the phone's watch
  strip lists up to 2 other live games that are late and within one score (the EXP-037 context line), in league
  order, as plain text with no Watch button. Graded by redzone/lab/also_close.py (manual-switch share per night,
  whole nights alternated; bar in docs/lanes/experience.md). Reason: Cycle 11 (#217), playbook P15.
- 2026-11-05 (lane 5, EXP-026 display): new defaults `phone.catchup_view: false`, `phone.catchup_visible: 2`. With
  the flag on, the phone's catch-up queue follows the decider's own queue order (what plays next, whatever order the
  EXP-026 re-run picks), shows at most 2 at once ("+N more waiting"), and is spoiler-safe: "A play you missed", with
  no names, play type or score until it plays. Reason: Cycle 11 (#217) item 3, playbook P9/P17 (spoiler control,
  1-2 open threads visible).
- 2026-11-05 (integrator, grader v11): two changes. (1) A live cut whose trace says `"forced": true` (Nick's second "Go
  now" tap) is graded on its own line, `entries_mid_play_forced`, and never counts toward `entries_mid_play`. Its
  producer is lane 3: set `forced` on the trace of a double-tap switch. `scripts/collect_live_test.py` joins the frozen
  graders. (2) `PLAY_LEN` and `RESUME_LEAD` become `ByLeague` tables, so CFB and CBB read their pro league's row. That
  fixes the KeyError in oracle.py on college replays (lane 1). Pro leagues are unchanged. Grader freeze v11.
- **2026-11-05 (Nick, 12:05 AM ET): reorg, "THE BRAIN IS THE MAIN FOCUS".** A Data Division: one data planner per
  type (A play-by-play, B context and hype, C media metadata with no video downloads ever, D markets, E synthetic and
  teacher, F broadcast formats), each with a 24/7 crew that lands event tokens (vocab v2) in the factory and ledger.
  Workers' outputs are trusted with no second review, except a license line per source (B rules). 1-2 test writers write
  pre-registered experiments and graders. Lanes 1-4 keep the body. Pace to <= 85% weekly by Mon 8 PM ET (hard stop 89%).
  Org, id blocks and checkpoints: docs/plans/2026-11-05-data-division.md.
- **2026-11-05 (Nick, 12:30 AM ET): Data Division type G, attention and hype at scale** (ids 1700s): legal, public
  signals of what people watched and cared about per moment (Twitch Helix viewer curves, highlight view velocity, hourly
  pageviews, Trends, public minute ratings, excitement-index datasets, prediction-market volume, official-API subreddit
  volume, open academic engagement datasets) as learned labels for Watch Value and the reward model. Each source names the
  label or feature it feeds and its information per GB. Vision and antenna skipped. G labels are judged by lift on % of
  oracle and worth-it, never used to optimize watch time.
- **2026-11-05 (lane 2, EXP-023 wiring):** new key `noggin.belief.enabled` (default `false`). When it's on, the
  engine uses the belief tracker (#223) as its delay sync in place of the Kalman filter, and it logs the
  same data/sync.jsonl rows. It stays OFF: #223's dev bar failed on NBA and NHL (rule #1 clause, and NHL short
  of 2 s), though it cut delay error p95 in every league. Why wire it now: Nick's RC7 requirement 2 (sync
  p95 ≤ 2 s) gets graded from those logs.
- 2026-11-05 (integrator, Test Factory F8, for Nick's Nov 4 11:20 PM data-B call): `scripts/license_check.py` runs in
  CI (graders job). Any JSON ledger in docs/datasources, and any pack manifest or grades.json passed to it, must give
  every source row a license line. A source classed `restrictive` in `docs/datasources/terms.json` (ESPN, hoopR, NHL
  api-web and shifts, Statcast, StatsBomb) needs the tag "personal-use, not for commercial release". Unclassified sources
  WARN until the scouts classify them (`--strict` fails them). Grader freeze v12.
- **2026-11-05 (Nick, 10:55 PM ET): a second "Go now" tap switches at once, even mid-play.** It is his explicit
  control; one tap ("Watch this") still waits for a dead ball. Why: with the hidden stick preloaded (#229/#233) a forced
  press flips in ~2-3 s wherever the play is (ping-pong drill: forced mid-play cuts 2 -> 7, unexplained 2 -> 0). Rule #1
  covers autopilot entries; forced presses are graded separately (presses.classify_mid_play "forced_go_now").
- **2026-11-05 (lane 3, RC7 requirement 1, Nick 9:45 PM): `two_stick.pretune_on_open: true` for every league** (was on
  for NBA/NFL only through #227's league overrides). Opening a live game on the phone tunes it on the hidden stick while
  you read, so "Go now" is a flip. Controls drill (2 seeds, smartthings TV): open-then-go median 8.33 s -> 3.06 s, go-now
  p50 8.25 -> 3.06 s; 0 mid-play cuts of any kind and 0 dropped presses either way. Rollback: set it false in settings.
- 2026-11-05 (lane 5, EXP-043 display): new defaults `phone.follow: false` and `noggin.priority.prefs.followed_players: []`.
  With the flag on, the phone edits the followed-players list and shows a text-only "Following" line naming the live
  games they're in. Lane 1 owns the weight that reads the list (its own flag and grader). Reason: Cycle 12 (#236) item
  2, playbook P22.
- 2026-11-05 (lane 5, Last night card): new defaults `phone.last_night: false` and `phone.last_night_hours: 6`. With
  the flag on, Now shows a morning card (6 AM to noon ET) with last night's worth-it, mid-play entries, high-leverage
  leaves, dead air and which release ran, read from the night report, data/worth.jsonl and data/deploy/state.json.
  Reason: Cycle 12 (#236) item 3.
- 2026-11-05 (integrator, grader v13): `license_check.py` reads a top-level `"sources"` list of records as ledger rows.
  Before, it read the whole file as one model card, which failed on the scouts' scout.json. A model card still names its
  sources as strings. Grader freeze v13.
- **2026-11-05 (Nick, 11:50 PM ET, relayed 04:51 UTC): Data Division type H, web-scale sports text** (ids 1800s):
  FineWeb/Common Crawl streamed and filtered to sports (ODC-By), Wikipedia dumps (CC BY-SA), GitHub open datasets, open
  books (low priority). Feeds recap-aligned hindsight importance labels, matchup storyline features and the Mind's
  sports-language fine-tune. Kaggle CPU / HF only; stream, never mirror; personal-use tag; raw text only in a private
  Kaggle dataset, never in the public repo. First deliverable: a pre-registered EXP on whether recap-aligned labels raise %
  of oracle (dev first, locked once at the release read); if not, H stays small. H pauses first with C/D/F/G.
- 2026-11-05 (Nick, 12:28 AM ET; gate change, integrator writes it): the catch-up window is a ratchet, not a fixed bar.
  "Ideally 3 min comes down more and more as we get better." (1) Headline line per league `time_to_see_<lg>`: the
  median and p90 seconds from a big play airing to it being on screen (0 = live, never seen = never), plus the shares
  within 3 min, 90 s, 30 s and live. (2) Gate line per league `catchup_<lg>`: at least 99% of big plays seen within
  the league's window in `docs/catchup_ratchet.json`, which starts at 180 s for every league. The ladder is 3 min,
  90 s, 30 s, then live. After each release's locked read, `scripts/catchup_ratchet.py --record` steps a league down
  once it has held the next window on locked nights for 2 releases in a row, and the window never loosens. (3) The
  delay-and-react test (lane 2, arms A-D) reports these lines per arm. Grader freeze v14.
- 2026-11-05 (lane 5, EXP-040 on): default `phone.worth: false` -> `true`. The one-tap "Was tonight worth it?" (1-5)
  shows on the Slate complete card from tonight, so rated nights exist before Sunday's line. It stays EVALUATION ONLY
  (data/worth.jsonl, graded by the frozen lab/worth.py; tests/test_worth.py fails if noggin, decider or scoring read it).
  Reason: Cycle 14 (#261) item 2, Integrator's EXP-040 condition "phone.worth on in the next RC".
- 2026-11-05 (integrator, grader review): #266 is accepted. EXP-301's delay-and-react grader (`redzone/lab/reactgrade.py`,
  `scripts/reactgrade.py`, by lane 3) joins the frozen graders. It reads time to see through `backtest.time_to_see`
  with the v14 encoding and takes each league's window from `docs/catchup_ratchet.json`. It zeroes an arm for any
  honesty fault: a replay of an unheld play, a replay that starts too early, or a rewind with no buffer behind it.
  The time-from-broadcast column is secondary and never decides a verdict. It imports nothing from
  decider/noggin/scoring and reads no taps. Grader freeze v15.
- 2026-11-05 (integrator, grader v16): `license_check.py` FAILs any row that names a source of class `red` in
  docs/datasources/terms.json: its terms ban bots or AI use (scouts, #263: hockeytech/ahl/echl, the Sports-Reference
  family, fbref, stats_ncaa_org). Never pull it, even with the personal-use tag. Grader freeze v16.
- 2026-11-05 (integrator, grader review): #249 and #268 are accepted from the test writers. `scripts/data_landing_grade.py`
  (EXP-705) grades a landed shard before anything trains on it. It checks the license and personal-use tag, as-of
  leakage, dedupe ≤ 1%, splits that are disjoint and time-ordered with no locked night (read from
  redzone/lab/splits.py), synthetic ≤ 3x, unmapped tokens < 1%, single-feature AUC ≤ .98, and D8: one canonical
  game_key per game across sources. `scripts/family_holm.py` applies Holm correction over the families declared in
  `docs/families.json`. Both scripts and the families file join the freeze, so no family can gain or drop an arm once
  its results are in. Grader freeze v16, together with the red-source rule above.
- 2026-11-05 (Nick, 1:50 AM; integrator, grader v17): **CBB lock numbers are out of every scorecard.** The CBB hindsight
  lock label comes from the clock/crunch rule, and context alone predicts it (context-only AUC 0.960, full head 0.967),
  so EXP-086's CBB "head beats Teacher" row measured the rule. Lane 3 found it is wider: the graded truth target.lock_in
  is must_see whenever the current crunch flag holds (a fact the model sees), in every league (NHL in-window 0.976).
  So `agent/distill/evaluate.LOCK_EXCLUDED` drops `lock_acc` in ALL leagues until truth_lock comes from real outcomes
  (EXP-302 y_out, #278); EXP-086's lock rows (CBB, NHL +12.7, MLB) are not evidence. CBB Teacher tokens are aux-only. New CI check
  `scripts/label_leak_check.py`: any label that context-only features (period, clock, score gap, crunch/late/garbage,
  your team) predict with AUC ≥ 0.95 FAILs, cross-fitted by whole night; reports with derived numbers only live in
  docs/label_checks/. Lane 3 relabels lock from real outcomes in the next N minutes, re-grades head vs Teacher in all
  3 leagues on ≥ 600 blind moments per league with bootstrap CIs, and commits a report per league from dev nights only
  (never locked); a league's lock_acc comes back only when its report PASSes. evaluate.py and gold.py (the label) join the freeze. Grader freeze v17.
- 2026-11-05 (Nick, 2:00 AM): **priority is NBA first, then NFL.** Every queue re-ranks by NBA impact, then NFL; NHL,
  MLB and CBB work continues only where it's shared code or data. NBA's graded set grows until every NBA gate has
  ≥ 60 decisions (release_gate.MIN_DECISIONS = 60 already holds PASS back below that; locked NBA has 16 big plays
  over 4 nights today).
- 2026-11-05 (integrator, grader v18): #279 accepted from lane 3. `redzone/lab/syncgrade.py` + `scripts/sync_grade.py`
  grade EXP-800/801 on the sticks' own behind-live meter (E = wall - extrapolated media_session position). EXP-800:
  |belief - meter| p95 <= 2 s per stick on >= 200 rows; absolute latency PENDING per channel until an on-screen anchor
  is measured, never filled from an assumed lag. EXP-801: tunes land within 3 s of the channel's live edge, with the
  reference pinned per app (YTTV: landing position, measured constant by the overseer; others: E at landing), never
  picked from the data. Sticks print as stick1, stick2 (public repo). Grader freeze v18.
- 2026-11-05 (integrator, grader v19): **lock truth comes from real outcomes** (lane 3, EXP-302, #278/#283).
  `evaluate.truth_lock` is must_see when y_out (a lead change/catch or a >= 0.20 win-probability swing within the
  league's pre-registered horizon N: NBA/CBB 180 s, NFL/NHL 300 s, MLB 600 s), watch when y_out2 (2N), else none;
  never the crunch/clock rule (gold.py stays the student's training target only). Context-only AUC on train/valid,
  lock / watch: NBA 0.851 / 0.868, NFL 0.752 / 0.749, NHL 0.771 / 0.756, all < 0.95, so NBA, NFL and NHL lock_acc
  returns; CFB, CBB and MLB stay out until their reports pass, and rows without y_out never count. A test fails if a
  league is graded without PASSing lock and watch reports. `redzone/lab/outcome_label.py` joins the freeze.
  Grader freeze v19.
- **2026-11-05 (Nick, 2:00 AM ET): work priority is NBA first, then NFL** ("I really only care about NBA and NFL, mostly
  NBA because that's what's on most right now"). Every queue is re-ranked by NBA impact, then NFL; NHL/MLB/CBB work
  continues only where it is shared code or data. Grow the NBA graded set to >= 60 decisions per gate. This orders the
  work; the decider's league order on the TV (CLAUDE.md north star) is unchanged.
- **2026-11-05 (Nick, 2:10 AM ET): sync is the #1 priority, above everything except Rule #1.** One program: lane 2 owns,
  lane 3 grades, lane 4 integrates. (A) per-stick offset = provider latency + distance behind the live edge from Fire TV
  media_session (adb, every second), logged to data/sync_truth; (B) every tune forces and verifies the live edge, hidden
  stick preloaded at the edge; (C) fastest feed per league wins; (D) belief tracker switches only when its band says dead
  ball -> restart; (E) ground truth without HDCP capture; camera (scorebug OCR) and mic (audio fingerprint) need Nick's
  explicit OK first. Gates: per-stick delay error p95 <= 2 s, 0 entries mid-commercial, switch <= 3 s incl. Prime. Test
  only when Nick isn't watching. Rows EXP-800..804.
- 2026-11-05 (Nick 2:10 AM + overseer 2:30 AM, sync program steps A + B, lane 2): new `sync.edge` block. **`sync.edge.enabled: true`** (shadow, read-only): every 1 s, one `cat /proc/uptime; dumpsys media_session` per stick measures how far it plays behind its live edge (the overseer's meter: pos_now = position + speed x (uptime - updated), E = wall - pos_now, behind = E - E at the tune's landing; a deep-link tune lands on the edge to ~0.5 s) into data/sync_truth/edge/<night>.jsonl and `Controller.behind_live`; the 5 s probe rows now carry uptime_ms and pos_now_s. **`force_live: false`, `force_live_visible: false`** (`force_live_s: 2.0`): when on, a stick > 2 s behind its edge beyond the app's own hold is re-linked to live (hidden stick: a refresh preload; the stick on screen: a fresh deep link, only on a dead ball), at most once per game per 60 s; off, rows say `would_force`. Why: Nick's 'know where each stick is' and 'auto re-link whenever behind > 2 s'. The read presses nothing, so it is shadow ON; a re-link changes tunes, so it ships OFF until Nick picks on the RC7 sync card.
- 2026-11-05 (integrator, grader v20, before TNF): three grader changes from lane 3. (1) #284: reactgrade's WIN needs
  big live % not down by more than CRUNCH_PTS (bootstrap lower bound), Nick's "% live": a reacting arm may not win by
  trading live plays for late replays. Frozen only after EXP-209 closed (winner A on the resync read), so no open
  experiment's verdict moved. (2) #285: a Prime media_session position >= 1e12 is an epoch, so E is the measured
  absolute delay per stick (`absolute_by_stick`); a counter position never feeds it, and YTTV's ~20-30 s stays an
  estimate. (3) #288: `redzone/lab/primegrade.py` pre-registers the diamond plan's Prime tests (A4 flat/pause/sticks/
  landing, B1, B2 Key Moments) with sample floors: thin data reads PENDING, a conclusive FAIL still fires.
  primegrade joins the freeze. Grader freeze v20.
- 2026-11-05 (integrator, grader v21, EXP-735; Nick 2:00 AM "NBA first", >= 60 decisions per gate): **an older season
  can join a pin as DEV nights only.** A pin may carry `dev_before` {league: YYYYMMDD}; `splits.league_split` keeps
  nights before it in train/valid and locks the newest >= 25 % of the nights on or after it, so the locked set stays
  current-season and never reaches back over the added nights. eval-data-v3 (eval-data-v2.yml) adds every final
  2025-26 NBA game Oct 21 2025 - Jan 31 2026 (and NHL, shared; both end before the Noggin's Feb 1 locked start) with
  dev_before 20261001. A --season league publishes once its locked set is at least the default pin's size; the
  release gate's own floors (>= 8 nights, >= 60 decisions) are unchanged, so a thin locked set reads PENDING, never
  PASS. A pin without dev_before (eval-data-v1) splits exactly as before. Grader freeze v21.
- 2026-11-05 (Nick 3:30 AM, "Yes, read it", lane 2): code may screenshot the **Prime Video** picture to read the score bug (score + game clock) for sync. Only the numbers are kept, never frames. Prime isn't DRM-blanked in screencap; YouTube TV is, and we don't bypass that. New defaults: **`delay.scorebug_band: false`** (read only the score bug's lower third as raw pixels, skipping the stick's ~6.5 s PNG encode; falls back to a full screencap) and **`sync.edge.epoch_signal: false`** with `k_pair_s 30`, `k_min_reads 5`, `k_sd_max_s 0.5`, `epoch_every_s 10`: each score-bug delay sample paired with the Prime epoch read gives K = arena - frame clock (Nov 4: ~9.6 s), fused per target; rows log `k_s`, `k_sd_s`, `epoch_delay_s` = E + K always, and the filter gets an "epoch" sample (sigma 0.5) only with the flag. Why: Nick's sync order (1)-(3); both flags ship OFF until a live Prime night shows the band read works on HDMI1 and K holds (sd <= 0.5 after ~20 reads), then Nick turns them on.
- 2026-11-05 (planner cycle 18 order, test writers' EXP-728/736 pre-registration on #277, lane 2): new `decider.entry.break_tail: []` (leagues; **off**), `break_tail_s: 10`, `break_tail_q: q25`, `break_tail_chain_s: 3`. When on for a league, a switch whose picture would come up early in the destination's break holds on the game on screen until it would land at most break_tail_s before the restart: the logged restart, else the break kind's q25 length from EXP-1500's table (redzone/scoring/broadcast/break_lengths.json), on the early side of the delay. Graded on NBA dev (fit, real lag, plain + resync, seeds 0-2): mid-commercial entries ~50 -> ~47 per 100 against a bar of <= 5, and big plays live -2.8 on resync seed 0 (big seen held; lane 3 reproduced all 12 runs). **EXP-728 FAILS; the flag ships OFF.** Why it barely moves: about two thirds of the mid-commercial entries are rewinds onto a dead ball or urgent entries, which the hold doesn't touch, and the held ones still land 20-30 s early (the delay's 2 sigma plus the 10 s decide tick). That is sync precision, so it goes to EXP-803.
- 2026-11-05 (integrator, grader v22, before any EXP-803 arm runs): #297's `redzone/lab/delaygrade.py` +
  `scripts/delay_grade.py` grade EXP-803 (delay precision, pre-registered in #296) exactly as registered: A4 PASS at
  <= 10 mid-commercial entries per 100 with its night-bootstrap upper bound below A0's lower bound; guardrails per
  seed and harness (0 added mid-play entries, plays missed up <= 5% relative, big seen live and crunch live down
  <= 1.0, dead air up <= 0.5); a 2-sigma band covering < 95% of truth rows FAILs the arm; PENDING under 23 paired
  NBA nights (needs eval-data-v3). A1-A3 are one Holm family, `nba-delay-precision` in docs/families.json, declared
  now; A4 is unadjusted. #293's break_tail (EXP-728) FAILed (lane 2 and lane 3's own 12 runs) and ships OFF.
  Grader freeze v22.
- 2026-11-05 (planner cycle 19, EXP-803 arms pre-registered in #296, lane 2): new `decider.entry.break_tail_defer: false` (A1: a break tail due inside one decide tick is cut at its moment via `Decision.defer_s`, honored by the backtest's leave/arrive and the engine's may_cut), `break_tail_tv_split: false` with `break_tail_tv_timeout_s: 170` (A3: NBA full timeouts use an early-side TV-timeout length instead of the mixed "timeout" row; 170 = Data F's cdn q10, local 170.3 / national 176.7). A2 is a sim what-if (`league_overrides.nba.delay.sync.min_sigma_s=1`, `--sigma 1`) with no new key. Why: delay precision (EXP-728's failure trace). All off until EXP-803's one graded read on eval-data-v3 NBA dev.
- 2026-11-05 (integrator, grader v23): #298 changes the frozen backtest for EXP-803 A1. `_apply_defer` moves a deferred
  cut's `watching` entry together with leave_dt/arrive_dt, so every on-screen line keeps the old game on screen until
  the cut; defer is capped at one sim step, so `watching` stays time-ordered (asserted). defer_s is 0 unless
  `decider.entry.break_tail_defer` is on, so every grade is unchanged (A1 sanity run on v1 seed 0 identical).
  Frozen before the v3 grade. Grader freeze v23.
- 2026-11-05 (integrator, grader v24; lane 2's report): **`--set league_overrides.<lg>.a.b.c=v` now writes one flat
  key**, `league_overrides[<lg>]["a.b.c"]`, the shape `config.for_league` reads (`evaldata.apply_set`, used by
  eval.py and eval_sports.py). Before, the key nested and `flatten_league_overrides` then replaced the whole `a` group
  with it, so every other setting in that group was lost for the run. Any league_overrides --set read before v24 is
  void: EXP-803 A2's v1 smoke read (33 -> 30/61) was on a config with no delay group, so it is discarded and re-run.
  A plain --set (no league_overrides) still nests as before. Grader freeze v24.
- 2026-11-05 (planner cycle 21 #301: Rule #1 above sync; test writers' autopsy #302; lane 2): new `decider.entry.start_timed: false` (EXP-739). When on (per league via league_overrides), the night's first pick is timed like any entry, waiting for a whistle or rewinding, instead of going in live. Why: 6 of the 8 NHL dev mid-play entries are first picks that land 10-32 s after the opening faceoff. Exploratory read (v1 fit, real lag, plain, seed 0; lane 3 grades): NHL mid-play 8 -> 3 of 236/241, plays missed 2 -> 3, big live 41.6 -> 40.7, dead air 22.4 -> 22.8; NBA mid-play 0 -> 0, mid-commercial 33/64 -> 34/72, dead air 19.6 -> 19.9; NFL identical. Ships OFF until lane 3's grade. EXP-740 (NHL `decider.entry.crunch_now: wait`) needs no new key: NHL reads the global `decider.entry.crunch_now` (rewind) because league_overrides.nhl is {}, so the arm is `--set decider.entry.crunch_now=wait` on an NHL run. The default stays until Nick picks.
- 2026-11-05 (planner on EXP-303 FAIL, lane 2): new `decider.entry.lag_floor_s: 0` (EXP-211, off). When set, an entry plan treats an open stoppage (no restart in the data yet) as dead only as far as a feed at least this slow would already show the restart. Why: all 3 NHL mid-play entries left with EXP-739+740 on came from the coverage guess. A first pick's lag reads the 10 s default until an action play measures it (MTL@CHI Oct 23: the whistle arrived 67 s late and the faceoff was at +28 s), and a faceoff arrived 87 s late against a measured 59 s (COL@PHI Oct 19). The graded value is NHL's measured feed lag q90, 71.3 s (redzone/lab/feed_lag_profile.json, from the profile, not tuned). q95 (91 s) also reached 0 but cost resync dead air +0.57, over the +0.5 bar. Lane 3 EXP-303 (#304), 739+740+71.3 on NHL: mid-play 0 on 6/6 runs, plays missed 6->0 plain and 3->0 resync, dead air -0.17 plain and +0.17 resync, big live -1.81 [-2.98, -0.57] plain and -0.63 resync; NBA identical; NFL no breach. This is the third arm graded on the same dev nights, so the locked read at release decides shipping. Exploratory read (v1 fit, real lag, plain, seed 0, with 739+740 on): NHL mid-play 2 -> 0, plays missed 2 -> 0, dead air 22.7 -> 23.0, crunch live 82.3 -> 79.5, big live 40.7 -> 40.2. Ships OFF until lane 3 grades it. EXP-739 is to be scoped to NHL only (league_overrides.nhl) when it ships, since with it on everywhere NBA resync seed 1 gains one mid-play entry.
- 2026-11-05 (planner, restoring Nick's Nov 4 10:55 PM rule "one tap still waits for a dead ball"; test writers' EXP-741; lane 2): new `decider.entry.user_timed: false`. When on, a single phone tap waits until the joined game's picture would come up on a dead ball (decider.entry_plan, no rewind since the tap carries none), not only for a safe moment to leave the game on screen. The second "Go now" tap still cuts at once. Why: Wed Nov 4 live, 15 of 17 mid-play cuts were single taps the guard let through in 0.0 s (lane 3, live-1104). It ships OFF here; the planner wants it turned ON once lane 3 grades it, through the normal RC deploy (never while games are live).
- 2026-11-05 (lane 3's EXP-741 grade FAILED the 180 s wait guardrail: no-rewind taps waited > 180 s on 12 of 20 NBA taps, p50 262 s, because ESPN ran ~38 s behind the ~16 s picture; planner ranks a rewind fallback 2nd after nba_cdn timing, EXP-802; lane 2): new `decider.entry.user_rewind_after_s: null`. With user_timed on, a tap that has waited this long also takes a landing that needs a rewind to the last dead ball (YTTV), armed on the switch at the cut (`Controller.arm_entry_rewind`). It never goes live mid-play; with no landing in reach it keeps waiting (Go now still cuts). Ships OFF; graded arm 15 s against lane 3's same PREREG.
- 2026-11-05 (lane 3 arm D; lane 2): new `decider.entry.user_rewind_max_s: null`. A tap's rewind landing over this many seconds keeps waiting instead. Why: arm B rewound one YTTV tap 123 s behind live. Ships OFF; graded arm 60 s. Arm C (`sources.nba_primary` on, nba_cdn timing) is graded on an approximated feed; shipping it still needs the EXP-802 replay or a live night.
- 2026-11-05 (lane 3's EXP-741 arms C/E: Prime taps landed on live play 1/20 at the 40 s prior and 4/15 at the measured 16 s, from entry_plan's "play may be live" (unknown) landings, which a Prime target (no rewind) accepts; planner: use the score bug; lane 2): with user_timed on, a tap never lands on an unknown landing, only on a known dead ball (EXP-741 F, no new flag; user_timed stays off). New `decider.entry.user_bug_gate: "off"` (off | shadow | on, EXP-741 E): a tap onto Prime lands when the hidden stick's score-bug clock reads the same twice >= 1.5 s apart (newest <= 5 s old), waits while it runs, and uses the feed plan only when the bug is unreadable; reads are numbers only (data/tap_bug.jsonl), per Nick's 3:30 AM OK. The rewind grace counts from the hold, not each 25 s re-submit. Ships OFF; E needs a live night to grade (no frames from Wednesday).
- 2026-11-05 (planner, carrying out Nick's Nov 4 10:55 PM rule "one tap still waits for a dead ball" where the grade passes; lane 3's corrected EXP-741 re-run: YouTube TV taps with the rewind fallback 0 live landings, waits 0-63 s; Prime taps 0 live but p50 wait 199 s; lane 2): DEFAULTS CHANGED. `decider.entry.user_timed: true`, new `decider.entry.user_timed_targets: [youtubetv]` (Prime single taps go as before), `decider.entry.user_rewind_after_s: 15` (no cap, `user_rewind_max_s: null`), and `decider.entry.user_bug_gate: shadow` (every Prime score-bug clock read logged to data/tap_bug.jsonl as numbers, so lane 3 can grade arm E; taps never wait on it). The second "Go now" tap still cuts at once. Ships through lane 4's next RC under the normal deploy rules; only lane 3's grade turns E on.
- 2026-11-05 (integrator, grader v25; lane 4 #308, lane 2's report): `collect_live_test.redact` also masks Tailscale /
  CGNAT 100.64.0.0/10 (kept as ts.<last octet>), Tailscale IPv6 fd7a:115c:a1e0::/48 and link-local 169.254/16, so the
  sticks' tailnet addresses never reach the shared live-test folder. Redaction only; no grading line changes.
  Grader freeze v25.
- 2026-11-05 (integrator, grader v26; test writers EXP-744, #302): **NBA/CBB Rule #1 gets a second, independent
  grader.** `scripts/clock_truth_grade.py` labels each entry live/dead/ambiguous from the play-by-play game clock alone
  (lane 3's EXP-741 thresholds; no code shared with redzone.grading, cutgrade, espn.stoppage_for or the decider),
  because cutgrade and the brain share one definition of "dead". v1 NBA dev: 0 of 64 entries live on both (CI
  [0, 0]); the instrument does see live play (landings shifted -15 s: 10 live); at random moments the shared grader
  errs toward "live" (the safe side). An NBA "0 mid-play" claim now needs both graders at 0. It and
  `scripts/entry_autopsy.py` (its input) join the freeze. Grader freeze v26.
- 2026-11-05 (Nick, 9:10 AM ET): **the time-shift buffer is the main architecture.** "I approve this plan and I want it
  all done and ready for tonight." The show runs 60-90 s behind live (cap 90 s) and the brain becomes the editor.
  Tonight: shadow mode for every game, no screen change (lane 2); replay sim and per-league sweep 45/60/75/90 s
  (lanes 3 + 1); windowed-hindsight oracle teacher (lane 1); seek reliability per app (lane 2/4). RC8 turns the buffer
  ON only for leagues that pass the gate pre-registered in docs/plans/2026-11-05-rc8-buffer-gate.md (0 mid-play on
  every run and both harnesses, beats live on big plays shown from the start, guardrails, tonight's shadow confirms,
  locked once), and Nick flips it. Rule #1 never changes; hidden stick only while Nick watches.
- 2026-11-05 (integrator, grader v27; Data G EXP-1704, #314, Nick 9:05 AM): Watch Value on real engagement labels is
  pre-registered (docs/prereg/exp-1704.md) and its label (`scripts/attention_labels.py`) and harness
  (`scripts/wv_aux_label.py`) are frozen before any label is joined. Sources S1-S5 are one Holm family,
  `wv-attention-labels`; later sources (S6 Twitch) get their own row. Labels are training targets only, train nights
  only, never features; the objective stays "worth it" (no engagement traps). Locked read once by the integrator at
  the next release. Grader freeze v27.
- 2026-11-05 (integrator, grader v28; lane 3 #316): **new scorecard line "big plays shown from the start"**
  (big_from_start, crunch_from_start, my_crunch_from_start), the RC8 buffer gate's primary. A big play counts when the
  screen had its game from the moment the ball went live after the last dead ball (grading.restart_before =
  resume_after of the last stoppage or score) through the end of the play; football and baseball: the play's own snap
  (= big live). Both of lane 3's calls accepted: a made basket is a dead ball for the restart (it is one under the
  rules, and dead_at already treats it so), and the control is the same harness with `--behind-live 0`, not the old
  43 s scorecard lag, with one run at the old default lag kept in the sweep for continuity. Additive only: no existing line,
  default or bar changes. Frozen before any sweep or shadow number is read. Reason: Nick 9:10 AM "% big plays shown
  FROM THE START". Grader freeze v28.
- 2026-11-05 (Nick, 9:35 AM ET; integrator): **buffer size policy, adaptive per league, never 0.** "Make it adaptive
  per league: buffer = feed-lag p95 + sync error + tune/seek time + safety margin; ratchet down only after
  locked-night gates hold; set a hard floor (start ~10-15 s) so a play is always known before it's shown." Written
  into docs/plans/2026-11-05-rc8-buffer-gate.md: inputs measured on train+valid only, hard floor 15 s in RC8 (10 s is
  Nick's later call), down one step (<= 15 s) per release and only after a locked read held, straight back up on any
  mid-play night, RC8 value = max(sweep point, formula, floor).
- 2026-11-05 (Nick 9:05 AM order, faster feeds; lane 2): DEFAULTS CHANGED. `sources.nba_primary: true` (NBA liveData via the S3 mirror owns NBA stoppages for every game; Wednesday's tapped games: nba_cdn lag p50 12.2 s / p90 57.6 s vs ESPN 36.3 / 85 s, lane 3), `polling.nba_pbp_s: 1` with conditional GETs (ETag / If-Modified-Since, sources/http.py) on a new `polling.league_fast: true` loop (league pbp off the ~3 s poll loop, new plays straight to the timelines). nba_cdn plays stay logged to plays.jsonl while primary, so its lag keeps being measured. ESPN takes over NBA stoppages whenever the owner's copy is > 10 s stale (merge.py). Risk: nba_cdn's p99 was 226 s on Wednesday; decider.entry.lag_floor_s (EXP-211) is the lever if a long publish gap shows up as a mid-play entry.
- 2026-11-05 (Nick 9:10 AM, buffer = main architecture, item A; planner cycle 24; lane 2): new `timing.behind_live_s.shadow: true`. While the buffer is off on screen, an editor decider (redzone/buffer_shadow.py) runs every decide tick on every game believing each picture runs B s further behind (by_league, max_s 90) and logs each decision to data/buffer_shadow.jsonl with picture_at / mu / sd / arena_at on entries. It presses no key and changes nothing on screen. Lane 3 grades it vs live for the RC8 gate.
- 2026-11-05 (Nick 9:25 AM, TNF race: "the hidden Prime stick at the live edge reads the scorebug (score/clock/down via OCR, Nick-approved) as our fastest NFL 'feed'"; lane 2): `delay.scorebug_hidden: true` (was the code default false) and new `delay.scorebug_log: true`. Every bug read, numbers only (score, clock, period, and the new down & distance), goes to data/scorebug_reads.jsonl, so the race against espn_fastcast / summary and crew D's market first-seen is graded from first-seen score changes. Never frames.
- 2026-11-05 (lane 5, tracker item 5 on Nick's 9:50 AM order): new config group `fast_feeds` (`enabled: false`,
  `leagues: [nhl, mlb]`, `nba_s: 1`, `nhl_s: 2`, `mlb_s: 3`, `board_s: 15`, `max_games: 8`). With it on, NBA
  liveData (1 s), NHL api-web and MLB statsapi are polled in SHADOW (own Fetcher, ETag/304, per-host backoff) and each
  new event's first-seen time goes to data/recordings/<day>/fast_feeds.jsonl; nothing reaches the merger, decider or TV.
  scripts/fast_feed_race.py measures the lag race against ESPN's plays.jsonl for crew A. Reason: tracker item 5.
  Default `fast_feeds.leagues: [nhl, mlb]`: NBA's 1 s nba_cdn poll is lane 2's polling.league_fast (#326),
  so the shadow does not hit the S3 mirror a second time; the race reads those nba_cdn rows from plays.jsonl.
- 2026-11-05 (planner on EXP-303, lane 2): new `delay.feed_lag_prior_s: 10` (EXP-803 A5, default unchanged). It's the feed lag a game assumes until a play measures it, set per league through league_overrides. Before this it was a 10 s constant shared by every league (screen.LAG_EST_DEFAULT_S). Measured feed lag is far longer: q50 is 45 s for NBA, 44 s for NFL and 50 s for NHL. So an unmeasured game, typically the night's first pick, believes an open stoppage is dead long after play restarted (EXP-303's NHL first picks). The A5 arm sets the league's q90 (nba 69.7), is pre-registered in experiments.md, and is graded once on eval-data-v3.
- 2026-11-05 (Nick, 9:50 AM ET "NHL is re-enabled", his answer to the NHL Rule #1 card; integrator): **the EXP-303
  package is ON for NHL only** (league_overrides.nhl: `decider.entry.start_timed: true`, `decider.entry.crunch_now:
  wait`, `decider.entry.lag_floor_s: 71.3`). Global defaults unchanged, so NBA, NFL and the rest are untouched. Dev
  (lane 3, #304): NHL mid-play 0 on 6/6 runs, big live -1.8 [-3.0, -0.6] plain. Locked, read once at this release
  (4 nights, 29 games, seeds 0-2, both harnesses): mid-play 18 -> 1 (resync seed 0, Oct 31), plays missed 13 -> 0,
  your-team crunch 13/13, dead air within +0.5 on 5 of 6 runs (resync seed 1 +1.0), big live -1.9 to +1.0. Four
  locked nights are under the 8-night floor, so the rate reads PENDING; it ships because it strictly lowers NHL
  mid-play against tonight's live NHL behavior, and Nick chose it.
- 2026-11-05 (integrator, grader v29): **the RC8 shadow and gate graders are frozen before tonight's shadow night.**
  `scripts/buffer_shadow_grade.py` (#318, test writers: schema, hidden-stick-only, Rule #1 on every editor entry under
  both cutgrade and clock truth) and `scripts/rc8_gate_report.py` (#331, T9: applies docs/plans/2026-11-05-rc8-buffer-gate.md
  as written, shadow veto-only, < 8 nights PENDING, ship B = max(sweep, formula/ratchet, 15 s floor), cap 90) join the
  freeze, so no bar moves once shadow rows exist. Grader freeze v29.
- 2026-11-05 (T9, #331; Nick 9:10 / 9:35 AM, integrator gate v28, planner): **RC8 buffer-ON ships behind
  `timing.behind_live_s.rc8_on`, false everywhere**, honored only under `league_overrides.<league>`; Nick flips one
  league himself (`scripts/rc8_flip.py`, which also sets that league's hold key; `decider.entry.start_timed` ships only
  when the sweep verdict's arm needs it, via `--set`; rollback writes explicit false/0 and restores both to shipped). New `timing.behind_live_s.adaptive` (floor 15 s limited to 10-15,
  ratchet step 15 s limited to 0-15, feed-lag terms are p90 placeholders until T6) and `max_s` limited to 90 (Nick's
  cap). Ship B = max(sweep point, formula/ratchet, floor), back to 90 after a mid-play night. RC6.2's
  `timing.behind_live_s.enabled` (NBA/NFL hold) unchanged. `scripts/rc8_gate_report.py` reads the pre-registered gate
  as written (shadow = veto only; missing first-pick line = PENDING). No decision changes with the shipped defaults.
- 2026-11-05 (integrator, grader v30; T9 #331 0b08d96): `scripts/rc8_gate_report.py` refrozen after review. The
  changes are reporting plus one arm choice: NBA's verdict is read on lane 3's `st` arm (start_timed on with RC8, the
  planner's call after the autopsy). That choice came after the sweep was read; it changes no verdict tonight (NBA
  fails switches 1.58x and your-team crunch on every arm), and it applies to RC8's next read from fresh nights. The
  switch guardrail counts non-replay switches (integrator ruling on lane 3's sweep: NFL B=75 1.80x, LIVE). No bar
  moved. Grader freeze v30.
- 2026-11-05 (integrator, grader v31; lane 1 #322): the windowed-hindsight oracle `redzone/lab/oracle_window.py`
  joins the freeze beside oracle.py (which it reuses unchanged): measured per-league feed lag (p90 default), reserves
  grading.RESUME_LEAD, never plans without a revealed window, first pick on a dead ball. It is the RC8 report's
  descriptive ceiling ("% of oracle"), never a gate. `scripts/buffer_sweep.py` stays lane 1's descriptive sweep
  (lane 3's sweep is the graded one) and is not frozen. Grader freeze v31.
- 2026-11-05 3:15 PM ET (overseer default, Nick can override; reason "Nick 3:10 PM: decide for him within the
  rules"), logged by the integrator for the planner:
  1. **T4 YTTV key tests are DEFERRED** (no YTTV game airs before 6 PM, and a stick session needs Nick's own words).
     Auto-switching on Nick's screen is NOT paused; the read-only 10 PM tests run as planned.
  2. **Chrome "Allow JavaScript from Apple Events" stays OFF** (Mac security setting); ui_12 stays skipped.
  3. **fast_feeds.enabled: true for tonight, shadow polling only** (no screen change; tested in #323/#337). Lane 4
     sets it at the stage; the shipped default stays false.
  4. **NHL EXP-303 package stays ON as staged**; Nick's NHL card stays open as a veto.
  5. **Crew H daily runs are triggered by hand** (twice a day), derived numbers only; raw text goes to the private
     Kaggle dataset. A scheduled cron fires only from main, which we never merge to, so a cron on main needs Nick.
  6. **Fan-sentiment Reddit hook stays OFF** until Nick adds an official API key.
  7. **No prioritization model ships or gets a locked read tonight** (T8 stay/leave failed valid; WV attention arms
     below A0; GI v2 not pre-registered until #345). A0 stays.
  Still waiting on Nick only: keys, the Kaggle ID check, and the RC8 flip.
- 2026-11-05 (integrator, grader v32; Nick 4:50 PM / ~5 PM: audit the oracle before more Watch Value training):
  - **EXP-1720 (GI v3 NBA, #347) metric, bar and harness frozen** (`scripts/gi_v3_nba.py` joins the freeze): pair-weighted
    within-night agreement vs realized volume; pass = G3 − C0 lower 95% bound > 0 and point ≥ +2.0 on fresh 2026-27 NBA
    nights after this commit (≥ 8 nights / 60 pairs first read, ≥ 40 nights firm). The 2025-26 dev pass (+5.5, CI
    +2.7..+8.5) and the 60 already-seen 2026-27 games are not evidence (the metric was picked after seeing them).
  - **EXP-1715 (GI v2 NFL, #345) prereg accepted; its harness freeze waits** on `scripts/game_importance.py`, which
    lives on #317 (held for two leakage fixes: aux labels in valid/locked targets, split_nights instead of
    league_split). Fresh nights count only from that freeze.
  - **EXP-1721 (oracle reward v2, #348) prereg and audit accepted, not yet a gate.** The audit confirms the v1 oracle
    structurally favors closest-game in the NBA (57.5% of games worth 0; Spearman −0.83 vs final margin) and counts
    low-swing NFL plays. oracle.py v1 stays the frozen reference for every current line until v2's code lands as its
    own frozen file and passes its acceptance on fit nights (it needs the human highlight set first). Nothing in RC6.3
    or integration/release changes. Grader freeze v32.
- 2026-11-05 (integrator; WV loop #348): **EXP-1721 (oracle reward v2) FAILS, graded once on fit nights against
  crew H's recap highlight set.** NBA zero-value games 54.4% -> 1.5% (criterion 1 passes); NBA highlight recall
  52.2 -> 58.7, CI -4.9..+18.1, short of the +10 bar (92 plays over 9 nights); NFL recall 82.7 -> 67.4, CI -19..-11
  (recaps name nearly every score, as v1 does). `redzone/lab/oracle.py` v1 stays the frozen reference; no tuning on
  these nights. Next: EXP-1722 (NBA-only big_v2, NFL unchanged), pre-registered before eval-data-v3's new NBA nights.
- 2026-11-05 (integrator, grader v33; WV loop #350, Nick ~5 PM oracle-audit items 4-6; overseer default, Nick can
  override): **(4)** the nightly North Star runs `--oracle`, so pct_of_oracle stops reading n/a. **(5)** a buffered
  card also reports the windowed oracle as `<obj>_window`, INFORMATION ONLY; full hindsight stays the gate ceiling
  until the window's beyond-window value is a real model (its v1 placeholder scores below the app on NBA fit nights).
  **(6)** Watch Value gates set before any WV read: calibration error <= 6 pts (locked, reliability curve) and rank
  agreement with the oracle >= 65% in the worst league; PENDING until a card carries watch_value. A 0-1 calibration
  value is read as a fraction and scaled to points (errs toward FAIL). `scripts/sync_truth_wv.py` (#353, lane 2) is an
  offline ledger input, not a gate, and is not frozen. Grader freeze v33.
- 2026-11-06 (lane 4, Nick via the overseer 3:14 AM ET: "fix deploy test timing"): `scripts/deploy.py` runs its test step at
  `nice -n 10` by default instead of `taskpolicy -b`. Reason: on the Mac the suite took 30 min at nice 10 but timed out at
  60 min under taskpolicy -b (efficiency cores). `test_priority: background` in deploy/deploy.yaml restores taskpolicy -b;
  `deploy --test-timeout MIN` overrides the 60 min limit. The live app (nice 0) still wins the cores, and deploys stay in
  no-game windows.
- 2026-11-07 (Nick's order, item 7 "deploy test timeout"): the deploy test step runs on pytest-xdist workers
  (`--dist loadfile`, `test_workers: 0` = auto, half the cores, at most 4) at nice 10. Measured on 4 cores: whole suite
  7 min 13 s vs ~33 min serial in CI. A timeout now kills the whole process group (subprocess.run's timeout left xdist
  workers and test children running) and reports tests finished; output in data/deploy/tests.log. pytest-xdist==3.8.0 is
  added to requirements.txt, so the next deploy needs `pip install -r requirements.txt` + `--deps-ok`; without it the
  suite runs serially. A night test that read all of data/ as its own was made parallel-safe.
- **2026-11-06 (lane 2, Nick's YTTV order):** new flag `decider.leave.break_floor_s` (EXP-212), default `{}` (OFF,
  no behavior change). It adds measured break floors on top of `MIN_BREAK_S` per league; the candidate is NBA
  `{end_period: 120}` (fit min 148 s over 103 breaks, valid min 148 over 34). Why: on Nov 5, NBA timeouts and quarter
  breaks could never open the leave gate with a wide delay and the measured feed lag. It stays OFF until it's graded
  in replay on recorded YTTV nights; Nick flips it.
- 2026-11-06 (YTTV switching owner thread, Nick's 6:14 AM nudge, item 3 "plan buffer on the fast NBA feed"): the sim's
  `--real-lag` model gets a measured `sources.nba_cdn.nba` profile in redzone/lab/feed_lag_profile.json (dead-ball
  detection lag, Nov 4-6 recordings, n 1359: p50 11 s, p90 21 s, p95 31 s; ESPN's same nights p50 44 s, p95 92 s).
  Before this, every NBA real-lag replay drew ESPN's lag although `sources.nba_primary` is on. Sim input only; no
  live default changes, and the CI scorecards are unchanged (their pinned NBA nights are already at 100% big seen).
  On Nov 2-5 (31 games, seeds 0-2): 0 mid-play entries at every B; B60 matches B90 on value when sigma <= 8, B90 is
  needed at sigma 15. RC8 owns the per-league B; this is evidence for it, not a flip.
- 2026-11-06 (overseer default (Nick 3:10 PM Nov 5), Nick can override; planner + integrator, EXP-735/EXP-017):
  **eval-data-v3 carries no release locked read until its Nov 1+ locked block reaches >= 8 nights per league and
  >= 25% of nights (about Nov 20); until then releases use v1's locked set, and every gate stays on v1.** Reason: no
  clean unread 2025-26 nights (v3 dev North Star already read every added 2025-26 night, and T8's EXP-128 trained on
  NBA/NHL Mar-Jun 2026), and the >= 25% locked rule. The v1 -> v3 dev jump (NHL mid-play 0 -> 344/3340, NBA 0 -> 9/784,
  all on 2025-26 nights) goes to the test writers for an autopsy before any v3 dev number is used for a gate.
- **2026-11-06 (lane 2, RC8 switch count, coordinator):** new flags `decider.dwell.min_s` (0) and
  `decider.dwell.long_visit_min_score` (0), EXP-213, both OFF (no behavior change). They are for the RC8 sweep:
  with the buffer on, NBA B=90 and NFL B=75 switch 1.58x / 1.80x as often as live (bar 1.5x). Fit nights, seed 0,
  dev only: NBA B=90 resync `long_visit_min_score: 20` gives 73 -> 61 switches (B=0: 46, so 1.33x) with big plays
  from start, crunch and mid-play unchanged, dead air +2.1 pt. NFL B=75 `min_s: 30` gives 1078 -> 908 non-replay
  (B=0: 679, so 1.34x), big from start 47.8 -> 47.0, crunch unchanged, dead air +1.7 pt. Lane 3 re-sweeps them on
  all seeds and harnesses; Nick flips them with RC8 per league.
- 2026-11-06 (integrator, grader v34; T9 #366 after EXP-213 #363): `scripts/rc8_gate_report.py` refrozen. Report-only:
  it prints lane 3's verdict_caveat next to any ON verdict and maps the NFL need `dwell_min_s_35` to
  `--set decider.dwell.min_s=35` in the flip command. No gate logic or bar moved. NFL's ON at B=75 + dwell 35 s is
  dev-tuned (35 s chosen after 30 s missed the 1.5x switch bar on the same train+valid nights, which the gate allows
  for picking a setting), so it carries the caveat and only the locked read at the RC8 cut plus the shadow veto decide;
  Nick flips. Grader freeze v34.
- **2026-11-06 (integrator, test writers #367): every NHL penalty type is a whistle in `espn.stoppage_for`.**
  The classifier is shared by the decider and the cut grader, so this changes the live NHL decider. Reason: ESPN typed
  every 2025-26 NHL penalty "Penalty", and 200 of 983 2026-27 penalty rows use spellings the old list missed
  ("Cross checking", "Delaying Game - ..."). "Penalty shot*" is excluded (live play). NHL dev, `eval_sports.py nhl
  --split fit --real-lag`, base -> this PR: v1 entries 246 -> 251, entries_mid_play 0 -> 0, plays_missed_entry 0 -> 0,
  dead_air_pct 22.8 -> 22.6; v3 entries 3340 -> 3300, entries_mid_play 344 -> 3, plays_missed_entry 0 -> 0,
  dead_air_pct 20.3 -> 20.5 (+0.2: the decider now holds through 2025-26 penalties it used to treat as live).
  The 3 residual v3 rows are "Period Start" (shootout start x2, pre-face-off x1), a later grader freeze item.
- 2026-11-06 (integrator, grader v35; test writers #368, EXP-746/747, from the v3 autopsy and #365's trace):
  `redzone/grading.py` gains `held_stop` for a row the classifier calls NONE. R1: a row in the same wallclock second
  and at the same game clock as a review or substitution row (one logging batch) doesn't end the stoppage (HOU@SA
  Oct 23 Q4 8:17: a challenge-result turnover logged with subs while the clock never ran). R2: "Period Start" is dead
  until the next action (NHL shootouts and opening face-offs). Before/after on fixed autopsy rows: v1 NBA 0->0, NHL
  0->0; v3 NBA 9->5 (all 4 flips clock-truth dead), v3 NHL (with #367) 3->0; cutgrade-dead/clock-truth-live stays 0
  everywhere. The decider never calls dead_at, so no runtime change. Frozen before #365's sim-input change is read.
  Grader freeze v35. Note on #365: its nba_cdn lag profile is measured on Nov 4-6 recordings, some inside v3's
  Nov 1+ locked block; it is a sim input (lag distribution), never a label, and B is still picked on train+valid.
- 2026-11-06 (integrator, grader v36; WV loop #351): **EXP-1722 (NBA-only oracle big_v2) PASSES as pre-registered**
  on 101 new v3 NBA fit nights (zero-value games 59.2% -> 1.2%; highlight recall 52.0 -> 58.8, +6.8, CI 3.5..9.8;
  your-team crunch 506/506 and mid-play 0 under both). `redzone/lab/oracle_v2.py` and its read harness `scripts/exp1722_read.py` (which imports it) join the
  freeze as INFORMATION reported beside v1; oracle.py v1 stays the gate reference until Nick says otherwise (card to
  follow). `oracle_v2.register()` adds "v2" to oracle.OBJECTIVES at runtime and must never be called on a gate path.
  EXP-1723 and EXP-1724 (Watch Value as the windowed oracle's beyond-window value, at p90 and primary feed lag) FAIL;
  the windowed oracle stays information only. Grader freeze v36, then v37 once the harness moved to scripts/ and
  was re-run through the frozen module (identical JSON).
- 2026-11-06 (integrator request, lane 2, EXP-214): **`decider.crunch.mine_first: true` (new; overseer default ON, Nick
  can override).** Your team holds the TV: in its final period a catch-up return never leaves it (unless garbage
  time), and while it is late and close it is never left for another game's crunch or a big-play chase with less
  personal stake. Why: eval-data-v3 NBA fit nights showed the hard rule "your-team crunch live" at 91.3%: WSH @ DET
  Nov 10 2025 (a return to a Q1 visit's origin at 8:37 left, back at 0:01 of OT) and CLE @ WSH Dec 12 2025 (left
  late and close for a CHI crunch; resync: a chase). v3 fit (111 nights), off -> on: shipped harness (B90, feed lag
  70) 493 -> 535/537, entries mid-play 4 -> 4, crunch live 86.8 -> 86.5, dead air 22.0 = ; real-lag resync B90
  seed 0 473 -> 521/537, mid-play 10 -> 10, crunch live 87.0 -> 86.3, dead air 21.6 = ; plain B0 unchanged. v1
  pinned scorecards (all six): no change except NHL crunch live -0.2 / -0.8 (resync). Left: 20251231 (2 plays at
  arrival, never left) and 20261024 (NBA leave gate; decider.leave.low_leverage, awaiting Nick's RC6.3 pick).
  Integrator ruling: ships ON below the 100% bar set for it because neither left miss is on a path the flag touches
  and mid-play entries / plays missed are identical on every harness; crunch live -0.3/-0.7 overall is the price of
  never leaving your team for a neutral crunch (north star: his teams first). v1 locked read once at the next release.
- 2026-11-06 (integrator, T9 + red team item 1): **No RC8 flip card until EXP-601 is read.** The replay treats the
  time-shift buffer as free; live, the hidden stick needs tune (Nov 4 p50 21.6 s, p95 44.7 s) + B before a switch is
  buffered, and NFL dw35 switches every ~131 s (resync median). EXP-601 (docs/prereg/exp-601.md) adds a hidden-stick
  readiness model behind a flag (off = today's grader), frozen before lane 3 re-sweeps NFL B75 dw35 and NBA B90.
- 2026-11-06 (integrator): **Grader freeze v38: EXP-601 hidden-stick readiness (#372) in backtest.py / eval_sports.py,
  verdict arm B (live edge).** Flag off = byte-identical scorecards. Integrator additions at merge: the stick we leave
  must re-hold before it is ready again if its game was airing at the live edge, and eval_sports refuses
  --hidden-stick unless --split is train, valid or fit. **Locked exposure on record:** the builder's pre-freeze smoke
  ran v1 NBA's last 3 nights (locked) at B90: mid-play 0 -> 7 in both arms, 18/23 switches to Prime (B = 0). Not a
  gated read; nothing is tuned on it; NBA B and dwell stay where #363 put them until EXP-601 reads train+valid.
- 2026-11-06 (integrator, EXP-601 read): **EXP-601 FAILS for NFL B75 dw35 and NBA B90; RC8 stays OFF for every league,
  and the earlier NFL "ON" (sweep addendum 5) is withdrawn.** With the hidden stick's real tune + hold, only ~15% of
  NFL switches air buffered, and the unbuffered ones carry every added mid-play entry (48/37/49 -> 107/103/90 shipped).
  Known pessimisms, as registered: on an unbuffered switch the sim's app still believes B (live, the delay book says
  held 0), and the cut is graded at the tuned time without control's 25 s safe-moment re-check; the B0 controls with
  the model on also rise (NFL 37-49, NBA 10-11). Lifting them needs a new EXP id, registered before it runs. Nothing on
  Nick's screen changes: RC6.2's NBA/NFL hold stays as it is.
- 2026-11-06 (integrator): **Grader freeze v39: rc8_gate_report.py prints one line per follow-up EXP arm (#373).**
  Report-only: EXP-601's holds / fails, prereg, grader and verdict arm appear under each league; no gate changes.
- 2026-11-06 (Nick 20:38 UTC, "update #372 grader assumptions only with measured values"): **EXP-602 registered to
  carry one measured stick hold into the readiness grader.** VOID at 21:45 UTC, see the next line; never run.
- 2026-11-06 (Nick 21:45 UTC, overseer correction): **EXP-602 is VOID and its hold value is struck from the record;
  RC8 is not scheduled on the assumption that a stick can hold a buffer until a stick test proves it.** The hold
  reading was invalid (the meter stays flat when the session stops updating; age not logged). Plain pause does not hold:
  30-90 s pauses were back near live 20-30 s after resume, 120 s reset to 0, also with auto-switching OFF (so not
  STK-05). No seek path works yet. The EXP-601 grader keeps its registered defaults; no refreeze, no runs. EXP-601
  itself assumed a held stick stays held, which is now unproven, so its FAIL is if anything optimistic for RC8.
- 2026-11-07 (Nick's Nov 6 10:15 PM order, item 1; overseer's scrub-buffer spec a60c9cbd): new `yttv_seek.*` group,
  **enabled false**. `redzone/tuners/yttv_seek.enter_buffer` puts a HIDDEN YouTube TV stick N x 15 s further behind
  by scrubbing the overlay (CENTER, LEFT x N, CENTER), because pause does not hold a buffer and rewind keys do not
  seek (Nov 6 stick tests). It presses nothing unless the stick is hidden, YouTube TV is in front and playing; no
  LEFT unless the overlay shows the bar focused (scrub label, no row button focused: the #356 guard); no commit
  unless the knob label moved N x step_s +- step_tol_s; on any doubt it re-links to live. Behind-live comes from
  the label reader (item 2) or the knob estimate, never media_session (wrong after a seek). Frames are deleted
  as soon as they are read. The switch path that calls it is item 4's.
- 2026-11-07 (integrator, Nick's Nov 6 10:15 PM order item 5): **Grader freeze v40: EXP-603 overlay-scrub entry in the
  hidden-stick model (#374).** Measured only (a60c9cbd scrub spec): ready keys 10 s + read 8 s after the tune, lands
  15 N + 15 s behind live (B75 measured, B90 extrapolated). Both arms must pass. A pass is not a flip card: RC8 waits on
  a live stick test of a held scrub, per Nick.
- 2026-11-07 (integrator): **Grader freeze v41: EXP-604 post-scrub re-check (#380), verdict arm wait + re-check.**
  Registered after EXP-603's interim NBA FAIL and a fit-night smoke (disclosed in the prereg). Known optimism: the
  re-check judges dead balls with the grader's dead_at on delivered plays, not the engine's may_cut, and skips the
  leave check; so a pass is a model pass that the shadow run must confirm before any card.
- **2026-11-07 (Nick's Nov 6 10:15 PM order, item 3): new flag `delay.edge_model.enabled`, default false (OFF).**
  With it on, a YouTube TV tune scrubbed back (yttv_seek) has delay = D_edge(channel) + behind_live, behind_live read
  off the overlay time label (`Engine.label_behind`); D_edge = tonight's at-the-edge estimate, else calib.auto, else
  `delay.edge_model.default` (youtubetv 25 s, sd 3, Nov 6 research). media_session's position (wrong after a seek)
  and the pause-hold timing no longer move a labelled tune, and sync B never re-links it off media_session. The
  supervisor's delay_doubt re-anchors a measured channel on its edge + behind_live instead of resetting it to
  `delay.default` (that reset dropped a scrubbed tune's 60 s behind_live). Interval floor stays 4 s (Rule #1).
- **2026-11-06 22:15 ET (Nick, scrub-buffer order item 2): new flag `timing.behind_live_s.label_reader`, default
  false.** YouTube TV's overlay time label is the behind-live truth meter after a scrub seek (media_session is wrong
  after a seek). Reader: screencap -> private temp file -> local Vision OCR -> numbers only, file deleted in a
  `finally`; a read whose frame could not be deleted is discarded. No keys pressed by the reader, no DRM bypass.
  Code: redzone/tuners/yttv_label.py. No live config change; nothing reads the flag on Nick's screen yet.
- **2026-11-07 (item 4 of Nick's Nov 6 10:15 PM order, scrub entry):** new flags `timing.behind_live_s.scrub_entry`
  (false, per league via league_overrides) and `scrub_fallback` (keep), plus `scrub_cost_s` 28, `scrub_read_s` 8,
  `scrub_over_s` 15, `scrub_retry_s` 120. With it on (and `yttv_seek.enabled`), the hidden stick enters the buffer by
  the overlay scrub (yttv_seek.enter_buffer) before the flip, never by a pause; `held` is the overlay label's reading,
  not media_session; the flip still waits for a dead ball on the delayed picture. A failed scrub re-tunes at the live
  edge and keeps the current game. Ships OFF: a flip onto a scrubbed stick with a person watching is not yet proven
  (spec "Still to prove"). No live config change. Rule #1 unchanged.
- 2026-11-07 (PR #381, item 6 of Nick's Nov 6 order): new default `decider.lock_wait` {enabled: false, retry_s: 20,
  max_s: 90}. A locked/held game we can't reach (no channel, or a tune that failed) says why, retries after retry_s,
  and after max_s the lock and hold are dropped with a toast. Off by default: Nick flips it. Nov 6: a hold on the ACC
  Network game sat 96 s and 261 s on "switch: you locked this game" (617 instant "no channel" attempts). Rule #1 unchanged.
- 2026-11-07 (overseer's live hidden-stick runs, #377): `yttv_seek` follows the stick. After a tune a LIVE/title banner
  looks like the overlay (it has CC too) and keys do nothing, and the scrub label shows only after a LEFT lands on the
  bar, so the first LEFT is the probe: no label = close, reopen, retry, fail closed after `open_tries: 4`
  (`reopen_wait_s: 1.5`, `overlay_wait_s` 0.8 -> 1.3). Live 3 of 3 entered at 60 s. A close after an unread probe may
  commit that step, so then behind-live is reported unknown (None) unless the label reader measured it. Still OFF.
- **2026-11-07 (item 4 follow-up, Nick's live stick notes 04:07Z):** a scrubbed hidden stick is protected from
  planner/refresh preloads until its flip (only a game opened on the phone replaces it), under `scrub_entry`. New
  `two_stick.unavailable_backoff_s` (default 0 = the usual 120 s): a standby tune stopped at an account/payment/
  UNAVAILABLE screen keeps that game off the standby that long (Nick saw the standby pick a Prime game showing
  UNAVAILABLE). Both off by default.
- 2026-11-07 (integrator, EXP-603 read): **EXP-603 FAILS in both arms for NBA B90 and NFL B75; RC8 stays OFF.** NBA:
  mid-play ~10 -> ~24 per run, ~21 of them Prime switches at B = 0 (registered pessimism; YouTube TV only ~2-3, at or
  below the control). NFL: 45 -> 71 (live edge), 43 -> 96 (wait) shipped, and switches 1.72x (4.6 -> 7.9 per hour), a
  guardrail breach by itself. S2 (+-3.4 s) moves nothing material. EXP-604 (#378's re-check) is the next read.
- 2026-11-07 (integrator, EXP-604 read): **EXP-604 FAILS in both leagues; RC8 stays OFF.** NBA: every added mid-play
  entry is a Prime switch at B = 0; YouTube TV-only mid-play is lower than the control (2.0 -> 1.3 shipped). NFL:
  switches 1.72x and plays missed 139 -> 424. Next, both registered before they run (coordinator go-ahead): EXP-605
  NBA YouTube TV-only RC8 with Prime at B0 (T9), and EXP-606 NFL B75 dwell grid 35/60/90/120 (integrator). Neither is
  a card until a live stick holds a scrub.
- 2026-11-07 (integrator): **Grader freeze v42: EXP-605 (#384) NBA YouTube TV-only RC8, Prime planned at its live
  edge as the live code does, measured scrub 15 s + one 7.5 s read.** Flags off = EXP-604 byte for byte. Model pass
  only; no card until a live stick holds a scrub and the shadow run confirms.
- 2026-11-07 (integrator, EXP-606 read): **No NFL dwell passes; NFL RC8 stays OFF and no more NFL dwell points on these
  nights.** D120 gets switches inside 1.5x (1.41x) but resync mid-play rises on every seed, plays missed on entry is
  ~2.6x the control at every D and your-team crunch is 91%. Plays missed doesn't fall with dwell, so the cost sits in the
  scrub wait + re-check delaying cuts, not in the switch rate.
- 2026-11-07 (integrator, EXP-605 read): **EXP-605 FAILS; NBA RC8 stays OFF.** Shipped harness is close: no added
  mid-play on the main grader, YouTube TV-only 6 -> 1, switches 1.08x, plays missed 19 -> 16, but clock-truth adds 1 on
  two seeds and the primary CI touches 0. On resync, Prime entries double (54 vs 23 over 3 seeds) for a reason not yet
  shown; T9's guess is the re-sync prior pulling Prime's belief back to the buffered lag. A diagnosis is info-only;
  any grader change from it is a new EXP, registered before it runs. No card until a live stick holds a scrub.
- 2026-11-07 (integrator): **Grader freeze v43: EXP-607 (#386) pins Prime's re-sync prior at its live edge.** T9's
  trace: resync believed Prime 116 s vs true 26 s on 34/36 decisions because _attach_resync pinned every target at the
  buffered lag. Diagnostic of the sim only; NBA stays FAIL on EXP-605's shipped breaches.
- 2026-11-07 (Nick 05:26Z order; integrator freeze): **Grader freeze v44: EXP-608 (#387) Prime buffered by pause on
  the hidden stick, ready B + 1 s after its tune; the "Prime can't buffer" replay assumption is withdrawn for it.**
  Measured once (60 s pause, 4-min hold, stick .150); B90 extrapolated; multiview tiles still 0 live.
- 2026-11-07 (integrator, EXP-607 read): **The EXP-605 resync failure was a sim artifact (resync mid-play 19.0 -> 7.7
  per run with Prime pinned at its edge), but NBA stays FAIL:** clock-truth adds 1-2 on two seeds and the primary CI
  touches 0 on both harnesses. EXP-608 (Prime by pause) is the next read.
- 2026-11-07 (integrator, T9 trace of #378 at 8452ba4): **Rule #1 risk on the scrub path: timing.behind_live_s.scrub_entry
  must stay OFF for NBA (and every hold_only league) until EXP-610's live fix lands.** After the scrub the flip never
  checks the destination's delayed picture for a dead ball (NBA isn't cut-timed; the whistle wait runs before the
  22.5 s+ scrub), so live would land later than the sim's already-lenient re-check. EXP-610 pairs the live fix
  (clock-stopped dead ball on the delayed picture, behind the scrub flag) with the same rule in the replay.
- 2026-11-07 (overseer's 90 s stick test): two scrub fixes, flags still off. (1) A same-channel deep link does NOT
  bring a stick that is behind back to live (a stick left behind measured 23 s after asking 90). A stick the scrub
  pressed keys on is marked `behind_on`; the next tune of that same target first tunes another channel
  (`yttv_seek.reset_channel`, "" = any other coded channel), or fails without claiming a live edge. (2) The picture
  stands still while the overlay is up (asked 90, 6 LEFTs, 37.6 s -> 130.8 s behind): enter_buffer now plans the
  LEFTs after the probe from what is left (ask - one step - time in the overlay - one more read) and adds the overlay
  time to its estimate.
- 2026-11-07 (Nick ~1:00 AM stick run; integrator): **yttv_seek.boundary_retries: 1 (new, behind yttv_seek.enabled,
  which stays off).** A knob check that fails because the label reset at a new program ("knob moved 39s, not 60s")
  retries the scrub once from the live edge, only after a re-link to live with nothing committed; otherwise it fails
  closed as before. Merged with #385 + #391 so --label gives measured reads.
- 2026-11-07 (overseer's hidden-stick check before college football: channels.json fox/cbs are the Sunday Ticket
  mirror codes, 'SIGN OFF | NFL ST - FOX'; 'NBC' search never started; ABC had no route; no local leaks a provable
  watch code; station-name search -> channel page -> DOWN -> Select played FOX 50, CBS 17, WRAL, ABC 11 live, 4/4;
  Select alone from the search page 0/4): **new flag `tuners.yttv_local_search: false`**. On: abc/cbs/fox/nbc tune by search (ABC 11 / CBS 17 /
  FOX 50 / WRAL), accepted only on a media-session subtitle naming the station AND a live playhead (>= 30 min),
  else BACK; fox/cbs ST mirror codes skipped except on Sundays. Off: routing unchanged. Nick flips it.
- **2026-11-07 12:40 ET HOTFIX (Nick's live "no switches": CBSSN sigma 15 > decider.uncalibrated_sigma_s 8 kept the leave
  gate shut, "delay not calibrated yet"): new setting `delay.edge_prior.enabled`, default false; Nick turns it on
  at runtime (/api/settings, undoable).** On: every YouTube TV channel without its own tap / score-bug sample tonight
  starts at `delay.edge_prior.prior.youtubetv` (34.9 s; Nick's hidden-stick probe, 28 reads on 11 channels, 34.3-35.5,
  reruns 34.96 / 35.09) with stated doubt 4 s growing at most `drift_cap_s` 2 s (stays under 8 all night). Prior only;
  the probe refresh is PR #408 (integration/next, off).
- 2026-11-07 (Nick, 1:02 PM ET: the catch-up rewind landed on the big play but he SAW it, and the app logged 'replay
  seek failed' 3x and left the menu up; the MEDIA_REWIND batch ran in one shell call with a 20 s timeout):
  **`replay.yttv_seek: scrub`** (new default; `replay.enabled` stays as Nick set it). A YouTube TV catch-up is seeked
  only on the HIDDEN stick by yttv_seek.enter_buffer (k = ceil((back - 3 + 1.5 + 4) / 15) LEFTs), its landing READ
  by the label probe, then it plays to `park_lead_s` 1.5 s before the snap and pauses; the flip un-pauses it.
  Never rewound on screen (a single-stick replay is refused). No label probe / no read / past the snap -> kept the
  current game. yttv_seek.py ported from integration/next (#403 RIGHT probe, lost-press guard). "keys" = old path.
- 2026-11-07 (Nick 12:40 PM live report: UNC@CONN stuck in the end-of-Q1 ads, leave gate said "no fresh data for
  103-128s" and "delay not calibrated yet"): **new `decider.leave.quiet_break` (enabled: true, floor_s
  {period_end: 120, score: 40})**. When the newest entry on a game is a period end / 0:00 clock, or a score in a
  stop-start sport, and nothing newer has arrived, the leave gate treats it as an official stoppage: leavable from
  seen + mu + 2 sigma + 3 s to seen + floor + mu - 2 sigma - tune_s, ahead of the stale-data and uncalibrated rules.
  enabled: false = old behavior; read every poll, so a settings change applies to games already on. Opt-in
  `near_end_clock_s` (0 = off) guesses the clock ran out when the last play left <= N s before a quarter break
  (not Q4) and the feed has been silent near_end_quiet_s; today's UNC@CONN data stopped at 0:22 of Q1 with no
  period end, so only this opt-in would have fired there.
- 2026-11-07 (Nick 12:40 PM, runtime via /api/settings on the Mac, undoable; recorded here at his request):
  decider.switch_margin 10 -> 4, decider.return_tolerance 5 -> 2, decider.break_destination_min 25 -> 10,
  score.personal.my_team 25 -> 10, decider.uncalibrated_sigma_s 8 -> 25, decider.sticky_visit_needs_calibration
  true -> false. Live settings only; the tree defaults are unchanged.
- **2026-11-07 13:10 ET, Nick ("should be 4-5 switches every minute", "i want real redzone autopilot"): JUMP MODE,
  new group `jump_mode`, `enabled` default false; Nick turns it on at runtime (/api/settings, undoable).** A rotation
  over every live tunable game in `jump_mode.leagues` (cfb, nfl): at each dead ball on screen it flips to the game whose
  snap is coming or that just had a big play; a game gives the screen up after `max_plays` 2 (crunch time and a scoring
  drive inside the 20 keep it). Leaves use the play-by-play dead window or the normal gate (incl. low_leverage); joins
  must land on a proven dead ball (re-checked at the flip). Every jump to data/jumps.jsonl. Also: the duplicate
  `decider.leave` block merged (dead_windows / tune_s were silently dropped by YAML; defaults unchanged).
- 2026-11-07 19:05 ET (overseer, first live jump row 18:55: ALA@LSU -> ORE@OSU on CBS 17 tuned on the spot by search,
  30 s, lost the dead ball; the standby sat empty): **new `jump_mode.ready_only` true** (with two sticks a jump goes
  only onto the hidden stick's ready game; otherwise it waits for the standby and jumps at the next dead ball) and
  **`jump_mode.search_penalty` 20** (the standby prefers code channels, ~3-4 s, over locals by search, ~11-15 s,
  unless the local's moment is that much bigger). The standby pick also skips targets the hidden stick can't tune
  now (a failed tune's backoff), which had kept it empty. jump_mode.enabled itself unchanged (Nick's live setting).
- 2026-11-07 (Nick 11:50 local: the app's tuner back to back, espn2 -> fs1/accn with no gap, failed 1/10 "video never
  started" and was slow 2/10; a 3 s gap fixed the delay probe, #408 9/9): new shared `tuners.relink_gap_s`, **0 (off)**.
  When set, it waits between the reset tune and the tune back in control's reset through another channel (scrub
  fallback, watchdog re-seek), and between the canary's tune_away and tune. scripts/watchdog_try.py takes --gap=S.
- 2026-11-07 (overseer's FOX 50 joins just after kickoff: label 5 s at the live edge, 'knob moved 5s, not 30s
  (5s -> 0s)'; another run label 52 s went fine): **`yttv_seek.program_start_guard: true`** (flag `yttv_seek.enabled`
  still off). The scrub bar starts at the program start, so when the LEFTs need more than the label the scrub fails
  closed before pressing any (back to live), with `room_s` (the label) in the result and the join row. No boundary
  retry for it. Capping the target at room - 15 instead is left to the planner (not done here).
- 2026-11-07 (Nick, 12:08 PM ET "You can deploy whatever"; overseer's suggestion (a)): **new deploy gate option
  `scripts/deploy.py deploy --if-paused` (deploy.yaml `if_paused`, default false = gate unchanged).** With it, live
  games and games starting soon don't defer the switch while `/api/state` reports `mode.paused: true` (the phone's
  Pause; only a literal true counts, and a "Watch this" hold is not a pause). The journal logs a `paused_gate` row
  naming the live game it let through. Snapshot, 10-min health watch and auto-rollback run as usual. Reason: a paused
  app switches nothing, so a restart moves nothing under its control, and the pause survives the restart; today's
  RC6.3.1 deploy had to bypass busy() by hand.
- **2026-11-07** New gate `tuners.yttv_local_search_live` (default **false**; `tuners.yttv_local_search` default
  unchanged, false). On: the engine re-reads `tuners.yttv_local_search` every decide tick, so a POST /api/settings
  change or Undo applies within ~0.5 s with no service restart (a tune in flight keeps its ref). Reason: turning the
  locals flag on for RC6.3.1 needed a restart mid-game-day (overseer suggestion b). PR #412.
- 2026-11-08 (overseer's stick re-run of the replay park on fd1ba542: to_snap_s 1.5, but ok false on 'player is none
  after PAUSE' while media_session read PAUSED right after and at +10/+20 s): **new defaults
  `replay.pause_settle_s: 3`, `replay.pause_poll_s: 0.4`.** After the park's MEDIA_PAUSE the player is re-read every
  0.4 s for up to 3 s and the park counts as held on the first 'paused'. Reason: the first read after PAUSE can be a
  transient 'none' (#356 family). PR #419.
- 2026-11-07 20:05 ET (overseer, first clean live measurement 0.2 jumps/min; preloaded jumps 2.7-4.0 s every time, so
  readiness is the bottleneck): **new `jump_mode.go_when_ready` true** (when the standby's ready game lands on a dead
  ball, jump at this dead ball once `min_plays` 1 aired here, not after `max_plays` 2). A jump now only ever flips to
  the ready standby (control `preloaded_only`: a standby that stopped playing is refused as "standby not ready", never
  re-tuned on the spot, which is what made the 20:01:50 "tuned" jump). After every jump the standby is refilled at
  once (preload skips the 5 s gap). jumps.jsonl adds fail_detail (IPs redacted), "standby" status rows (every ~30 s:
  standby game, ready, why a preload was refused) and split hold codes (standby_not_ready / ready_not_dead /
  no_game_at_dead_ball). jump_mode.enabled itself unchanged (Nick's live setting).
- 2026-11-08 01:16Z (overseer, Nick's #1 live complaint: a timeout on MIA@ND held the screen 46 s, "stay: timeout",
  with OU@FLA ready on the standby): in jump mode a break on the game on screen (timeout, end of quarter, injury,
  review, score) that the fresh feed is still in, once it has reached the screen, is a free leave for a jump, with
  no visit-and-return length needed. The arrival on the other game still has to land on a dead ball (Rule #1).
- 2026-11-08 (overseer's live report on 2df593ea, replay ON: a UGA@MISS catch-up failed 'replay seek failed; kept
  current game' about every 0.5 s until a restart; the park's 120 s backoff stopped keys, not the decision):
  **new defaults `replay.fail_retry_s: 120`, `replay.max_fails: 2`.** Every failed replay (any path) is not decided
  again for 120 s and leaves the catch-up queue after 2 failures; a YouTube TV replay that can't be parked (scrub
  backing off, or more LEFTs than yttv_max_lefts) is refused before the hidden stick is tuned (screen untouched);
  a replay decision is never acted on while replay.enabled is off. PR #419.
- 2026-11-08 (Nick 20:45 + 21:05 ET via the overseer, "5-10 jumps per min ... idc about the decisions rn i just care
  about the code"): **new flag `buffered_jump` (default OFF, needs jump_mode)**: both YouTube TV sticks scrubbed ~75 s
  behind live (label read), the hidden stick PAUSES on the line-up just before its next logged snap (`park: true`),
  and every dead ball on screen flips to it (PLAY rolls the snap; the pause joins its behind-live). Rule #1 kept at the
  2-sigma margin. Sim on 37 NFL slates: 0.8 cuts/min, 1.35 plays/min shown (one game 0.79), 0 mid-play; 5/min only
  huddle to huddle (`park: false, eager: true`). Stick test: scripts/buffered_jump_try.py. PR claude/buffered-jump.
- 2026-11-08 (overseer 21:55 ET, 3778a401 live: 'scrub_failed youtubetv:secn ... focus guard: no scrub label after the
  LEFT', 62 s, then a 10-min supervisor backoff): **new `timing.behind_live_s.scrub_after_tune_s: 22`** (the first
  overlay key of a scrub entry waits that long after the tune played: YouTube TV's post-tune banner swallows keys) and
  **`scrub_banner_retry_s: 30`** (a focus-guard miss is retried soon, others keep scrub_retry_s 120); the supervisor
  no longer counts "buffer entry failed" as the channel's failure. Seek thread agreed (it owns enter_buffer).
