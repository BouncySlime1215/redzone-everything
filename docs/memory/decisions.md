# Decisions

Every decision Nick made, newest first, with the date and the reason. Times are ET unless they
say UTC. **A PR that changes a default (`redzone/default_config.yaml`) or a gate
(`docs/gate_baseline.json`, `scripts/release_gate.py`) adds a line here**; `scripts/check_memory.py`
enforces it in CI. Gate changes in architecture.md §7 need a line too (by hand).

Format: `- YYYY-MM-DD: **decision.** Why.`

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
- 2026-11-04 6:30 AM: **`sources.trust_order` added OFF** (empty lists per league, cycle 02 lane 4, FEED-01/03). When set,
  the first fresh, unbenched, non-erroring source in the list owns a game's stoppage, the next takes over on its
  errors/silence, and disagreements are counted (`Merger.disagreements`). Empty = the old nhl/nba_primary behavior.
  `right_game` now reads `Store.taps()` from data/redzone.db when it exists (PENDING otherwise).
