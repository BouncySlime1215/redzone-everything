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
- 2026-11-08 (Nick N1): **SmartThings is dropped: new `two_stick.smartthings_cloud` false.** The $0 rule forbids a paid plan and free access ends 2026-12-15. Off = state "disabled by policy" in `SamsungTV` (`cloud_enabled`, set by `from_devices`): no cloud call, no probe, no OAuth file read or delete, no Health alert (`_hardened()` is false), `cloud_ready`/`has_readback` false so the planner stops counting it. Local key + CEC only; `paths()` is local first (DEV-05), `set_input` falls to the next method on a definite non-delivery (DEV-02), a backed-off local path is probed by the keepalive and backs off 45 s, not 300 (DEV-07). Why: docs/audits/2026-11-08-devices.md DEV-02..05, DEV-07.

- 2026-11-08: **Platform safety before the watchdog comes back (fix-plan package D, P-02 to P-17).** (1) The watchdog judges
  "hung" from the app's own heartbeat (`health.supervisor.heartbeat_age_s`, 120 s), not log silence: the log was quiet 4-17 min
  39 times on healthy live nights. (2) One adb reconnect owner: the app (120 s); the watchdog reconnects only after 4 min offline
  and never while a switch is in flight. (3) **`governor.swap_eco_mb` 1500 is replaced by `swap_growth_mb` 300 over `swap_window_s`
  600 (eco only while memory pressure >= warn) and `swap_critical_pct` 90 (critical)**: swap size alone is not a signal (this Mac
  sat at 5.9 of 7 GB). Eco no longer slows score-bug OCR (SCALE ocr eco 2.0 -> 1.0); only critical stops it. The app's memory is
  the larger of RSS and `footprint`. (4) **Deploy:** the test suite no longer starts on the Mac while a game is live or the governor
  is not normal (`test_when_busy: false`; run it on CI and use --skip-tests), automatic rollbacks wait for a break like a deploy
  (`rollback_wait_s` 7200, then go ahead), the rollback grace is 300 s, a feed counts as failing only when the same feed is down
  through the last 180 s and was not down before, and a rollback snapshots and names the settings it overwrites. (5) Phone Undo
  never wipes the saved settings; `server.port` is no longer phone-editable; `/api/settings` hides the ntfy topic. Nick N2/N4
  unaffected.

## Process and budget

- 2026-11-09: **Kaggle fetch scripts built for the 9 passing sets plus KuaiRand-27K and KuaiRec; Aalto keystrokes and SoccerMon left out.** Aalto is 168,000 people's typing rhythm (biometric); SoccerMon is athlete health data with no anonymisation stated. Open Bandit, NHL_DB, Yambda and the other fails stay out. Why: unclear licence or identifiable people counts as no.
- 2026-11-08: **Process gates G-02 to G-06 and G-09 built from the wave-1 reports.** A fix needs a regression test and a
  repro; a Stop hook runs fast checks (it gives up with BLOCKED on a repeat, and under the harness's cap of eight); one
  plan is checked in CI; `docs/handoff.md` targets 80 lines, ratcheted from 305 because the director edits it elsewhere.
  Guard policy: a hook that can't start or times out blocks, a crash inside it allows and logs. Changes to `.claude/`
  and `.github/` wait in a patch for Nick to apply (`docs/agents/patches/2026-11-08-process-gates.patch`). Why: rules with no check are advice.
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

- 2026-11-08 (Nick, "build a layer of fixes and safeguards", "proper mapping and coordination rules"): agents
  default to 3 builders + 1 verifier (5 only for independent cards), claim files first (scripts/claims.py, CI),
  and hard rules move from prose to enforcement: .claude/hooks/guard.py G1-G7 (secrets, branches, hardware,
  heavy jobs, deploy, protected paths, Peacock). Reason: research (docs/research/2026-11-08/agents/) shows
  rule files are advice, review capacity caps useful parallelism, one writer per file keeps facts.
- 2026-11-08: every explicit state-machine move needs a why and is saved to data/transitions.jsonl
  (redzone/fsm.py); tests fail on a move without a why or an undeclared edge, production logs it.
- 2026-11-08 (Nick, via coordinator; audit R5/R13): **deploy rules written down.** Agents may deploy, including during live games (Nick Nov 7 12:08 PM ET), but keep the 10-minute health watch and auto-rollback and never disturb the TV while he watches; the Mac runs whatever branch was last deployed (runbooks/deploy.md step 0). **The only usage cap is 88 %** (CLAUDE.md, usage-pause.md); the 90 % and Nov 3-4 pacing text is removed.

## Gates and quality

- 2026-11-08 (card S, timing accuracy; Nick: know exactly when a play ends on each picture): new delay knobs, all OFF, wired by docs/agents/patches/s-sync.patch: `delay.scorebug_midpoint`, `delay.scorebug_clock_fit`, `delay.sync.robust`, `delay.feed_bias.{enabled,apply}`, `sync.edge.{midpoint,meter,meter_every_s,meter_sd_s}`, `delay.sync.app_min_sigma_s: {}`. The 4 s sigma floor is unchanged: `DelayBook.set_app_floor` lowers it per app only with a forward-chaining replay where no fold's mid-play cuts rose (`delaymodel.replay_allows_floor`). Why: the filter wasn't the limit, the timestamps and the single-read anchors were; a too-narrow interval cuts plays mid-play, so narrowing needs its own replay. Live error log is numbers only: data/sync_truth/delay_error/<night>.jsonl.

- 2026-11-08: **Grader and release gate re-frozen at v47: they see every league and can only get stricter** (package E of
  the P1 sprint; Nick N5, pass bar: 0 mid-play, 0 commercial seconds, every red-zone trip). Gate: `entries mid-play = 0`
  is now a hard row on the per-night path too (a card with 1 mid-play entry in 300 used to PASS); a card not graded on
  the locked split FAILs; a MEASURED crunch miss (and a real 0% of your-team crunch) FAILs, PENDING only when n = 0;
  new rows "entries mid-commercial = 0" and "dead air = 0"; every one of NFL, CFB, NBA, CBB, NHL, MLB has a row, PENDING
  (not absent) when it has no scorecard; league promotion needs `mid_play_cuts == 0` and a PENDING for a dropped line.
  `docs/gate_baseline.json` entries_mid_play ceilings re-based to 0 (they were NBA 10 of 20, NHL 2, demo 2). Grader:
  `lab/leverage.py` and `grading.is_big/is_crunch` read `league.base` + `pro_period` (CFB, CBB, WCBB were never
  high-leverage or crunch); `outcome_label` gets WCBB rows. Numbers that get worse by design: NFL crunch live 72.7%
  now FAILs (was PENDING), the demo's 76% crunch FAILs, NFL/NBA/NHL mid-commercial and dead air FAIL, and CFB/CBB/MLB
  read PENDING. Also: brain splits are the time split by whole night (`splits.league_split`), not a crc32 hash; a
  backtest knows no team records (end-of-game leak); NHL playoff OT is 20:00 with no shootout. Why: the audit
  (docs/audits/2026-11-08-feeds-scoring.md, -rules-and-config.md) found gates that could not fail.
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
- 2026-11-09: **GameValue trains men's college basketball (`cbb`) as a personal-use model, and `scripts/kaggle_gamevalue.py` never downloads raw files.** `GV_LEAGUES` gains `cbb`, `GV_PERSONAL_SOURCES` gains `hoopr_espn_cbb`; `--personal-nba` (alias `--personal-hoopr`) covers NBA and CBB, and the model and manifest carry the personal-use tag. `fetch_raw` only checks that raw seasons are staged and names what is missing; every raw source is a github.com release path and github.com's robots file disallows those, so Nick's respect-robots ruling means no download. NFL training also refuses until its raw files are attached or another route is decided. Why: Nick's robots ruling; CBB is a shadow-only model like NBA.
- 2026-11-09 (MORE-DATA): **the value shards use every fetched season, and CFB and WNBA join as personal-use leagues.** NFL seasons run 1999-2024 (was 2011-2024): nflverse has no `time_of_day` for 1999 and part of 2000-2010, so a game with no snap clock is skipped and a season with none is an empty shard whose sidecar says `no_wall_games`; nothing is invented. `cfb` (source `espn_cfb`, 2002-2024, football rules) and `wnba` (source `wehoop_espn_wnba`, four 10 min quarters, cut 20261020) are built only from the attached outputs of fetch_sports_cfb.py / fetch_sports_wnba.py (raw.githubusercontent.com, never github.com releases), only with `--personal-nba`, tagged personal-use; they are in `GV_LEAGUES` and the leak check's personal sources, and pass the raw audit. MLB stays unbuilt: Retrosheet and Statcast carry no wall clock; the MLB Stats API feed (`startTime`, personal-use per the Nov 5 ruling) plus a plate-appearance adapter is what it needs. Statcast fetch now retries each day, fetches by week, and refuses an HTML body. Why: Nick asked for more data; Nov 5 personal-use ruling; robots ruling.

## Product

- **2026-11-08 (P1 fresh Rule #1 review, N4 + the coordinator): four entry defaults.** `decider.entry.user_timed_targets: []` (was `[youtubetv]`): one phone tap waits for a dead ball on every app and with no play-by-play; a second tap forces. `decider.entry.cue: hidden` (was `seek`): nothing rewinds on the visible screen, a rewind runs on the hidden stick before the flip, and with no hidden stick the entry waits for a landing that needs none (rewinds cost 1.5 s of presses; a narrow huddle may be missed and wait). New `decider.entry.no_data_wait_s: 45`: an entry into a game whose feed can't prove a dead ball waits this long, then the next game with a provable landing gets the pick; alone, it keeps waiting (never in blind) and tells the phone once. Why: P1-REVIEW-02/03/05; overrides the Nov 5 YouTube-TV-only tap scope.
- **2026-11-08 (Nick, via the coordinator): a basketball landing in the walk right after a made or missed shot is allowed, and is not a mid-play join.** `scripts/buffered_jump_sim.py` counts it as `joins_walk`; `joins_mid_play` stays the strict Rule #1 counter. Why: "for basketball I am ok with a switch to a game mid play but exactly after a made or missed shot".
- **2026-11-08 (P1 package A, Nick N2/N3/N5 + the audit): Rule #1 entry defaults for every league.** `decider.entry.crunch_now: wait`, `max_wait_s: {}`, `unknown_wait_leagues: [nfl, nba, nhl, mlb]`, `start_timed: true`, `hold_only` + mlb, new `allow_live_fallback: false` (every go-in-live exit, D-01), `thin_margin_leagues: [nba, nhl, nfl]` (D-08), `buffered_jump.leagues` without wcbb (N2), `jump_mode.mine_prior: 30`, `league_prior: 4` (D-04), NFL = CFB only when both are in crunch (N3, D-09). Why: audit D-01..D-11 found five ways to join mid-play; Rule #1 is the one hard rule.
- **2026-11-08 (Nick landing bars, via the coordinator): `jump_mode.join`.** NFL joins after a play, preferably after the replay (`after_play_s: 18`), seconds before the snap; basketball joins mid-possession only in the walk right after a made shot or defensive rebound (`after_shot: [nba, cbb]`, `walk_s: 14`, `transition_s: 7`), a transition play diverts to the nearest earlier safe point; never a late join, on YouTube TV and Prime, with the delay margin (jump/buffered modes only: normal mode keeps its graded landings).

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
- 2026-11-08: **Peacock is no longer a tunable app** (DEV-01, tuner side): `PeacockTuner` and its ref origin are removed from `redzone/tuners/providers.py`, so no `peacock:` target can be tuned even with the Noggin ranker off. Why: CLAUDE.md says never Peacock; only an off-by-default ranker enforced it.
- 2026-11-02: **Sync is fully automatic:** no taps, no mic, no camera.
- 2026-11-08 (Nick, N2 via coordinator; audit R8): **women's college basketball is OFF everywhere** (docs and policy now say so). The `default_config.yaml` change (`mode.leagues.wcbb`, `teams.wcbb`, `college.groups.wcbb`, `league_priority`, `buffered_jump.leagues`) belongs to the config card, not this one.
- 2026-11-08 (Nick, N3 via coordinator; audit R7): **league order is NFL first; NFL = CFB only when both are in crunch.** Order NFL > CFB > NBA > NCAA basketball > NHL > MLB elsewhere. Docs updated; code (`decider._value_league_rank`, `league_order`) to read one source (decider card).
- 2026-11-08 (Nick, N5 via coordinator): **pass bar restated:** every touchdown and 95%+ of red-zone trips live, cuts as events need (about 5 per 10 min, up to 9-14 when busy), 0 mid-play joins and leaves, 0 commercial seconds. Replaces "75 jumps in 10 minutes". In the docs the mid-play-leave rule is 0 (the "low-leverage leave allowed" line is superseded).
- 2026-11-08 (Nick; DEV-01/D-07): **Peacock is never a source.** Dropped from `TUNER_APPS`, `LINK_APPS`, `LABEL_ONLY` and `LAST_RESORT` in discovery and filtered by `BANNED_APPS` in `gather_sources` and `best_source`, whatever the Noggin flag says; Peacock games still map to NBC/NBCSN on YouTube TV (`peacock_on_yttv`). Feature flags `flags.*` are not wired (audit R3); docs now name the real per-subsystem keys.
- 2026-11-08 (audit R12): `jump_mode.leagues` default stays `[cfb, nfl]`; NBA/CBB join through `buffered_jump.leagues`. NHL and MLB are NOT in the jump rotation by default; the pass bar is checked per sport only where jump mode runs. Add them to the default when their graded cards exist.

- 2026-11-08 (N4, switch-path audit S-4): **a phone tap (Back, Focus, Replay, like Watch this) waits for a dead ball unless
  tapped twice.** Before, those three cut mid-play at once. North Star: never leave a high-leverage moment.
- 2026-11-08 (audit S-3): a jump's leave margin is max(jump_mode.flip_s, the measured slow flip on the current path)
  instead of a fixed 3.2 s: the old game plays on until the input changes, and HOME flips run 2-6 s.
- 2026-11-08 (audit DEV-09): `two_stick.identity_check` (default true): each stick's HDMI port and serial are checked
  against devices.json at start and on reconnect; a definite mismatch turns two-stick mode off with a phone alert.
- 2026-11-08 (audit S-11): a replay is only ever seeked on the hidden stick, for every app: one-stick replays are
  refused (Nick, Nov 7: the viewer never sees a rewind).
- 2026-11-08 (P1 root causes, card H; Nick's N2, N4): **`two_stick.max_safe_wait_s` 12 s for planned cuts and `two_stick.tap_safe_wait_s` 25 s for taps (was 25 s for all); `mode.leagues.wcbb: false`, `wcbb` out of `core.league_priority`; `two_stick.identity_check: true`, `buffered_jump.roll_wait_s` 1.5 and `buffered_jump.roll_poll_s` 0.15 written down at their code defaults.** Why: on the Nov 7 slate useful safe-moment waits peaked at 10.7 s and 0 of 23 full 25 s waits cut, while the wait held the control lock; a tap keeps the longer wait so N4 (taps wait for a dead ball) doesn't fail sooner. Women's college basketball is off everywhere (N2). No margin is lowered: Rule #1 is unchanged, and a planned cut decides with the same flip cost the flip checks. `HANDSHAKE_S` 3 s -> 8 s (adb, code constant): a Mac stall counted as a dead shell, equally on both sticks.

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
- 2026-11-07 (overseer's stick numbers): `yttv_seek.step_tol_s` 4 -> 6. Scrub entry went 47 of 48 at 60 s across ESPN,
  TNT, NBA TV, BTN, ACCN, FS1, ESPN2 and TBS (about 15 s each); the one failure failed closed on "knob moved 40s, not
  45s", one second outside the old tolerance. 6 is still well under one 15 s step, so a missed or extra LEFT is caught.
  Flag stays off.
- 2026-11-07 (Nick's 12:26 AM order 2): new `prime_pause.*` group, **enabled false**. `redzone/tuners/prime_pause`
  puts a HIDDEN Prime stick behind live with MEDIA_PAUSE, a hold, then MEDIA_PLAY, because on Prime pause holds
  (overseer: 60 s pause, timecode 02:20:54 -> 02:24:44 over 230 s wall, no snap in 4 min). It never uses
  MEDIA_REWIND (continuous on Prime: 214 s / 665 s back) or media_session (stale). Prime's overlay timecode is the
  truth: the paused timecode must not move during the hold, and the gain after PLAY must match the hold within 3 s,
  else it fails closed (un-pause, then the caller's relink to live). A stick that turns visible mid-hold is
  un-paused, not re-linked. The entry-path wiring and the regrade are other threads.
- 2026-11-07 (integrator): **Grader freeze v43: EXP-607 (#386) pins Prime's re-sync prior at its live edge.** T9's
  trace: resync believed Prime 116 s vs true 26 s on 34/36 decisions because _attach_resync pinned every target at the
  buffered lag. Diagnostic of the sim only; NBA stays FAIL on EXP-605's shipped breaches.
- 2026-11-07 (Nick 05:26Z order; integrator freeze): **Grader freeze v44: EXP-608 (#387) Prime buffered by pause on
  the hidden stick, ready B + 1 s after its tune; the "Prime can't buffer" replay assumption is withdrawn for it.**
  Measured once (60 s pause, 4-min hold, stick .150); B90 extrapolated; multiview tiles still 0 live.
- 2026-11-07 (integrator, EXP-607 read): **The EXP-605 resync failure was a sim artifact (resync mid-play 19.0 -> 7.7
  per run with Prime pinned at its edge), but NBA stays FAIL:** clock-truth adds 1-2 on two seeds and the primary CI
  touches 0 on both harnesses. EXP-608 (Prime by pause) is the next read.
- 2026-11-07 (integrator, EXP-608 read + v45 freeze): **EXP-608 FAILS both leagues; grader freeze v45 registers EXP-609
  (Prime pause at the measured B + 4.3 s) as the verdict for the Prime-pause model, 608 report-only.** NBA: the main
  grader's mid-play falls 9.7 -> 3.7 and big seen rises 67 -> 78%, but clock-truth adds entries on every seed and plays
  missed doubles; the two graders disagree and T9 diffs them, info-only. NFL unchanged in kind (switches 1.72x).
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
- 2026-11-07 (integrator, EXP-609 read): **EXP-609 (Prime pause at the measured B + 4.3 s) FAILS both leagues, as
  EXP-608; RC8 stays OFF.** NBA's blockers are clock-truth entries the re-check's lenient dead ball lets through, plays
  missed ~2.3x and (resync) your-team crunch 54.8%; EXP-610 addresses the first. NFL's are switches 1.72x and plays
  missed ~3x.
- 2026-11-07 (integrator): **Grader freeze v46: EXP-610 (#392). Live: on the scrub path only, a basketball flip in a
  hold_only league waits for a clock-stopped dead ball on the delayed picture (decider.entry.whistle_band_s: 3);
  sim: the re-check uses the same rule, worst measured 90 s scrub 37.6 s.** scrub_entry stays off; the scrub's
  landing overshoot (130.8 s for 90 asked, Nick 05:54Z) waits on the SEEK_TO fix and is not covered by a pass.
- 2026-11-07 (integrator, EXP-610 read): **EXP-610 FAILS on the resync harness only; the shipped harness holds every
  gate.** With the clock-stopped flip, NBA B90 drops dev-grader mid-play on every seed (11/10/10 -> 4/3/4) and plays
  missed falls 16 -> 11. Resync fails on your-team crunch 17/31 (the Oct 24 WSH leave-gate night already known from
  the #363 sweep; decider.leave.low_leverage=[nba], #258, took it to 31/31 and waits on Nick's RC6.3 pick) and plays
  missed 12 -> 14. Unmodelled: 14-19 re-checks/run past 25 s (live drops and re-picks) and the live landing overshoot.
- 2026-11-07 (integrator, correction to the EXP-610 lines): **the scrub overshoot fix is #391 (in integration/next at
  d936ff31).** Nick's stick run at 28286690: asked 90, landed 91.3 / 91.5 s in 17.5-28 s, 3 of 3 ok (one "probe close
  may have committed"). Those landings were estimated from the knob, not label-read; the label-measured landing waits
  on a run with #385 + #391 together (b490ab86 / d936ff31).
- 2026-11-07 (integrator, EXP-610 diagnostic, report-only, not a verdict): **the EXP-610 resync failure is the known NBA
  leave-gate blocker.** Trace: on 20261024 a GS@PHX Q1 leave gate held 930 s while WSH played 2:09 -> 0:02 of Q4.
  The resync arm re-run with decider.leave.low_leverage=[nba] on both arms holds every gate (dev mid-play 10/9/11 ->
  6/4/3, clock-truth 9/8/9 -> 2/2/0, your-team crunch 100%, switches 1.29x, primary +8.9 [1.5]). Read after the
  verdict, so it decides nothing; the default stays on Nick's RC6.3 card. NBA RC8 still needs: Nick's low_leverage
  call, the shadow run, live drop-and-re-pick past 25 s modelled, and a label-measured #391 landing.
- 2026-11-07 (Nick's goal via the overseer, 2:07 AM ET: "join a chosen spot accurately; the model handles which plays
  later"): new `join.*` group, **enabled false**. `redzone/tuners/join.py` takes a target behind_live, plans it, runs it
  on the hidden stick, and logs target, predicted landing, measured landing and both errors to data/joins.jsonl.
  YouTube TV: n = round((T - 10) / 15) LEFTs (fixed, passed to enter_buffer as `presses`), predicted 15 n + 11.7 s
  (9 label-measured runs landed T - 3.3 s on average, +-3 s); targets are 15 s steps and the overlay is never held
  to hit one in between (that measured +2..+10 s and one -18.7 s). Prime: pause for the gap (prime_pause), +-1-2 s.
- 2026-11-07 (Nick's sync order via the overseer, 2:33 AM ET, items 1 and 3; build thread "Verify every join with a
  watchdog"): new `join.watchdog.*` and `join.drift.*`, both **enabled false**. Watchdog: every entry is checked on the
  label / Prime timecode (never media_session); |read - predicted| <= 6 s YouTube TV / 4 s Prime and |read - target|
  <= 10.5 / 5 s, else ONE re-seek from the live edge (through another channel, #391), then fall back to live.
  control.held (what the phone shows) is written only from a verified reading. Drift: the HIDDEN stick only, first
  read 5 min after the join, then x2 up to 20 min, at most 30 s of reads per stick per hour (4 YouTube TV reads);
  each YouTube TV read is expected to add 7.5 s (soak); off band, at the edge or unread -> not buffered, re-entered.
- **2026-11-07 (Nick's Nov 7 07:33 UTC sync-hardening order, item 4 session safety): new flags
  `session_guard.enabled` and `session_guard.act`, default false (OFF).** redzone/tuners/session_guard.py classifies
  each stick session (post-tune banner, YouTube TV / Fire OS profile pickers, Still watching, auto-play prompt, app
  crash, reboot, adb drop, Prime detail page, program-boundary label reset, unexpected dialog text) and fails closed:
  no key on an unknown screen, keys only on the hidden stick except prompts that already cover the picture, the
  auto-play prompt only ever gets "Ask later", no account settings. With `enabled`, the scrub waits out the banner
  and refuses on a broken session (no key pressed). Runbook: docs/memory/runbooks/stick-session.md. Rule #1 unchanged.
- 2026-11-07 (Nick's sync order, items 6 + 7): **new `supervisor.mac_health` and `supervisor.night_ops` blocks ship OFF (`enabled: false`).** Mac health alerts to the phone (battery <= 20% / 10%, on battery 2 min with games, slept 60 s+, caffeinate missing, memory critical or swap >= 8 GB, paused 10 min, no switch row 20 min with 2+ live games on autopilot) and a 'Sync and health' section in the nightly report (joins, fallbacks, drift, faults, Mac alerts). Why: every one of these happened Nov 4-6 (battery 13-19% during games, 10 GB swap, 66-145 min with no switch and up to 10 live games) and nobody heard until morning. They see and say only; Nick turns them on (all flags off per his order). Thresholds from the Mac's own logs: swap 3-6 GB is this Mac's normal evening, so 8 GB.
- 2026-11-07 (Nick's 2:33 AM ET sync order, items 5, 8, 9): new `join.calib.*` group, **enabled false**, and a new
  `canary.*` group (a manual / launchd script, nothing runs it by itself). Calibration: per app, app:channel and
  app:channel:stick, the YouTube TV scrub lead (shipped 11.7 s) and the Prime pause bias (shipped 0) are re-estimated
  from measured rows of data/joins.jsonl (label / timecode only, never an estimate or media_session): last 20 rows,
  rows off by > 12 s counted not used, residuals clipped to +-2x the expected error, shrunk toward the parent with
  4 pseudo-rows, clamped to shipped +-5 s (YouTube TV) / +-3 s (Prime); a SHIFT alert when the newest 3 all land > 5 s
  off the same side. At the shipped lead the plan is exactly Nick's n = round((T - 10) / 15). Canary: daily on the
  HIDDEN stick only, never with a game live or within 90 min, tunes away then ESPN, joins 60 s with the label reader,
  must land within 6 s of the predicted 56.7 s, then puts the stick back on live; FAIL / WARN go to the phone loud.
  Why: Nick's order "self-calibration ... with bounds" and "a daily stick self-test before games that fails loud".
- 2026-11-07 (Nick's sync order item 2, fallback ladder thread): **fallback_ladder.enabled: false (new).** Per stick,
  a join that can't be made as planned climbs buffered (measured only) -> live edge on the same stick -> other stick
  in place (other_stick_apps [youtubetv]) -> hold, each step in data/ladder.jsonl and on the phone; a rung that failed
  2x in 10 min on a stick is skipped there 5 min. Every flip still waits for may_cut. Off until a hidden-stick night.
- 2026-11-07 08:05Z (coordinator, Rule #1): **fallback_ladder.skip_rungs: [live, other_stick] (default).** Replay with
  the 25 s dead-ball re-check on the live rung (scripts/ladder_replay.py, B75 scrub_measured, fit, seeds 0-2): NFL
  mid-play keep -> ladder 59.3 -> 61.0 (10% scrub failures), 66.0 -> 68.7 (25%); NBA 24.67 both. A failed buffered
  entry holds; the live and other-stick rungs stay off until a grader shows 0 added.
- 2026-11-07 (Nick's watchdog stick test FAIL, 11:35Z: hidden stick, NBA TV, target 75, planned 71.7, read 79.52): new
  `join.yttv_read_s` 7.5 (in join.plan_join, the ONE place: predicted = before + 15 n + lead + read; the canary and
  the watchdog both read predicted_s; JoinPlan.read_s goes into each join row). The label probe's reading includes its
  own ~7.5 s frozen overlay (Nick's 4 runs: measured - predicted +5.9, +6.5, +6.5, +7.8 without it). join_calib takes it
  off youtubetv rows logged without read_s. The watchdog moves only the TARGET by read_s. Margins stay 6 s. Flags off.
  Open: landings run ~+4.7 s over the target (Nick's 3 reruns) -> a later lead trim.
- 2026-11-07 (Nick's canary run 1, 11:50Z: 3 presses, landed 52.12 vs predicted 64.2, row said ok: true): a join
  row's `ok` now means the READ landing passed the watchdog verdict (join_watchdog.verify, same 6 s margin either
  way); a new `verdict` field says why. Off target -> ok false, reason names "a lost press?" when one 15 s step short.
  The watchdog still re-seeks such a READ landing once (it is a measurement, not a failed join), and join_calib keeps
  it as a measurement (its gross filter decides). The pre-commit knob guard is the seek thread's. Flags off.
- 2026-11-07 (Nick's cross-channel run 13:02Z: 25/26 joins verified at 61.2-67.5 s for target 60, the 1 miss an empty
  post-commit label read): new `join.watchdog.reprobe_unmeasured`, **false**. On, an unmeasured COMMITTED join reads the
  label once more before falling back to live. One re-probe per join, never on a failed join or a visible stick.
  Nick's #404 run 13:19Z (12/12 buffered, but ESPN2 re-probe 55.44 re-seeked to 88 s): the re-probe is judged on the
  target band only (target + half a read_s, +-10.5 s for YTTV), not the plan margin, because the empty first read's
  freeze may or may not have pushed the stick back. 55.44 and 65.83 for target 60 both pass. Flag still off.
- 2026-11-07 (Nick's drift stick runs 13:53Z and 14:11Z, ESPN target 60: buffered 66.14 / 64.03, reads drifted +1.3,
  +1.0, +1.7 s with a 7.5 s freeze assumed, reads took 9.8-10.4 s, yet the monitor re-entered: first on the target band,
  then on target + 20 after two reads): **the depth the reads add is planned**. A drift read re-enters only when
  |read - (landing + read_adds_s per read)| > `join.drift.drift_margin_s` **3** (was 6, re-anchor), past join max_s,
  snapped or unread. `read_adds_s.youtubetv` 7.5 -> **8.5**, `read_cost_s.youtubetv` 7.5 -> **10.5**, `backoff` 2 -> **3**
  (reads at 5, 20, then every 20 min), `budget_s_per_h` 30 -> **45**. Control spends the read's measured wall time.
  Flags off.
- 2026-11-07 (Nick's #405 sweep 14:03Z, 16.8 s per LEFT: T120 plans k=5 -> 112.5, landed 112.0 / 113.6, both re-seeked
  to live): with a plan, the watchdog verdict is |read - predicted| <= margin_s (6 YTTV, 4 Prime) plus a gross target
  check, new `join.watchdog.target_gross_s` **{youtubetv: 20, prime: 8}** (nearest step up to 8.4 s from the target;
  T30's one-LEFT floor lands ~46 s). Without a plan (control's scrub entry, the re-probe) target_band_s still applies.
  Flags off.
- 2026-11-07 (overseer's canary on 5f80eb94, run 1: espn target 60, 3 presses, landed 52.12 vs 64.2 predicted, one
  15 s step short, row said ok): lost-press guard in `yttv_seek` (flag still off). At the live edge the probe is now
  a RIGHT (`probe_key: RIGHT`), which shows the scrub label without moving the knob (pinned at live), so the knob
  check covers every LEFT; behind live, or if RIGHT shows no label, the LEFT probe is used as before. Before the
  commit, a knob one whole step short gets one more LEFT (`topup_max: 1`), a re-read and the same check; anything
  else off still fails closed. The watchdog's off-target re-seek (#401) stays as the backstop.
- 2026-11-07 (overseer's #403 stick run, 0/10 via the watchdog): **`yttv_seek.probe_key` back to LEFT** (8/8 on
  5f80eb94); RIGHT is opt-in. Mode B: with the RIGHT probe all LEFTs move, landing one step deeper than the shared
  join predictor (78 vs 64.2), because the LEFT probe at the live edge usually only reveals the label. A fixed press
  count after a RIGHT probe now sends n-1 LEFTs, the same motion as the LEFT probe. Mode A ("knob moved 0s", 7/10)
  is not explained yet; its reason now carries both labels for a trace. The top-up fires only for a knob exactly one
  step short (+-2 s) and only if that extra LEFT moves exactly one step; a 10 s app or a label reset still fails closed.
- 2026-11-07 (overseer's 12f405ab runs: RIGHT probe 8/8, mean 63.0; LEFT probe 6/6, mean 62.1; predicted 64.2):
  **`yttv_seek.probe_key` is RIGHT again** (flag `yttv_seek.enabled` still off). Model fixed: at the live edge
  NEITHER probe moves the knob (the LEFT only reveals the label), so a fixed count n moves n-1 steps on both and the
  join predictor (yttv_lead_s 11.7 + yttv_read_s 7.5) is unchanged. Behind live a LEFT probe is still assumed to
  move one step (unmeasured). Rig note in runbooks/join-watchdog.md: retune before every run.
- 2026-11-07 (overseer's depth sweep, ESPN, #403 RIGHT probe, retuned before each join: k = 1, 2, 4, 6 real LEFTs
  landed 45.3, 62.1, 95.7, 129.3 on average; T30 sent zero LEFTs and landed 10-14 s): **YouTube TV join model is
  16.8 s per real LEFT** (`join.yttv_left_s: 16.8`, `join.yttv_lead_s` 11.7 -> 21.0, `yttv_n_offset_s` removed;
  `join.enabled` still off). predicted = before + 16.8 k + 21.0 + 7.5 read; k = round((T - before - 28.5) / 16.8),
  at least 1, so the shallowest scrub join is ~45 s and achievable() says so. Each LEFT also keeps the frozen overlay
  up ~1.4-1.8 s; 15 n + 19.2 drifted -3.9 .. +4.9 s with depth (the T120 off_target). Calibration converts old rows.
- 2026-11-07 (overseer's hidden-stick check before college football: channels.json fox/cbs are the Sunday Ticket
  mirror codes, 'SIGN OFF | NFL ST - FOX'; 'NBC' search never started; ABC had no route; no local leaks a provable
  watch code; station-name search -> channel page -> DOWN -> Select played FOX 50, CBS 17, WRAL, ABC 11 live, 4/4;
  Select alone from the search page 0/4): **new flag `tuners.yttv_local_search: false`**. On: abc/cbs/fox/nbc tune by search (ABC 11 / CBS 17 /
  FOX 50 / WRAL), accepted only on a media-session subtitle naming the station AND a live playhead (>= 30 min),
  else BACK; fox/cbs ST mirror codes skipped except on Sundays. Off: routing unchanged. Nick flips it.
- **2026-11-07 (overseer's hidden-stick reads, Nov 7 ~10:55-11:00 ET: YouTube TV live edge on 11 channels, ESPN, ESPN2,
  FS1, SECN, ACCN, NBA TV, TNT + FOX 50, CBS 17, ABC 11, WRAL by search, 28 reads 34.3-35.5 s, mean 34.9, sd ~0.35;
  3 empty label reads skipped): new flags `delay.edge_prior.enabled` and `delay.edge_prior.refresh.enabled`, both
  false.** On: every YouTube TV channel without its own tap / score-bug sample tonight starts at
  `delay.edge_prior.prior.youtubetv` (mu 34.9, sd 0.6) + behind_live, in place of other nights' per-channel taps
  (`youtubetv:fs1` 42 +-8), calib.auto and the edge model's 25 s default. The stated doubt keeps the 4 s floor (the
  gates' tested floor; the probe measures the edge against the EPG schedule clock, not the arena). Refresh: Nick's probe
  (fresh live edge through another channel, CENTER, RIGHT, one label read, BACK; delay = (capture - EPG start) - label,
  start = the :00/:30 boundary) on the HIDDEN stick at the live edge only, at most every 30 min; empty reads and reads
  further than 10 s from the prior are skipped; median of the newest 6 reads, back to the prior after 3 h.
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
  current game. "keys" = old path.
- 2026-11-07 (Nick 12:08 PM ET: "You can deploy whatever" / "You can do whatever you want"; overseer deployed 12:11:34):
  **RC6.3.1 = a8bf14f on integration/release** (tree claude/live-b490-locals 8841ebd0 = the live dev build b490ab86 +
  #406), live on the Mac, with `tuners.yttv_local_search` ON through the Mac's settings API (undo history; the
  shipped default stays false). Reason: 8 of 16 CFB games on ABC/CBS/FOX/NBC locals today; the smallest change on top
  of what was live (an RC6.3-based point release would have rolled back boundary_retries). The deploy's live-game gate
  was bypassed once, with auto-switching paused.
- 2026-11-07 ~12:40 PM ET (Nick live, RC6.3.1: "havent seen any switches / i want more switches", "do whatever you
  want"; stuck on UNC@CONN through the Q1-end ads): the overseer changed **runtime config on the Mac only** via
  /api/settings (undo history); **the tree defaults are unchanged**: `decider.switch_margin` 10 -> 4,
  `decider.return_tolerance` 5 -> 2, `decider.break_destination_min` 25 -> 10, `score.personal.my_team` 25 -> 10,
  `decider.uncalibrated_sigma_s` 8 -> 25, `decider.sticky_visit_needs_calibration` true -> false. Causes: FOX didn't tune
  (UCLA@MINN, best game), the leave gate waited on an uncalibrated CBSSN delay (sigma 15 > 8), and the stale-data guard
  ("no fresh data 103-128 s") blocked leaving during the commercial with the quarter end never flagged as a break.
  Rule #1 note: the last two loosen calibration guards (more switches land on a less certain delay); they are Nick's
  call for today and are not graded. Code follow-ups (hotfix/local-search-joinlive): YouTube TV edge prior 34.9 s on,
  stale data after a period end / 0:00 / scoring play counts as a break, the FOX fix.
- 2026-11-07 (Nick, live CFB 1:10 PM: 'fix the jump logic... maybe the games identify safe points to jump'): default_config.yaml defined decider.leave twice; YAML kept only the second block, so decider.leave.dead_windows and decider.leave.tune_s never existed at runtime and the play-by-play dead-window leave path (cuts.DeadTracker) could not be turned on. Merged into one block; defaults unchanged (dead_windows [], tune_s 6). Nick's Mac turns dead_windows on at runtime for cfb, nfl, nhl, nba, cbb so autopilot leaves between plays. Rule #1 (joins) unchanged.
- 2026-11-07 (Nick via the overseer, after RC6.3.2's deploy at 14:49 ET: the full suite at 3 workers pushed the Mac's
  load to 18-21 and starved the app during live games): **during games, never run the full test suite on the Mac.**
  Run it elsewhere (CI, the integrator's cloud run) and deploy a commit that already passed there. The 1:10-2:50 PM
  stall was mostly the Mac in clamshell sleep on battery (~56 of 88 min dead, sticks dropped on every wake).
- **2026-11-07 12:40 ET HOTFIX (Nick's live "no switches": CBSSN sigma 15 > decider.uncalibrated_sigma_s 8 kept the leave
  gate shut, "delay not calibrated yet"): new setting `delay.edge_prior.enabled`, default false; Nick turns it on
  at runtime (/api/settings, undoable).** On: every YouTube TV channel without its own tap / score-bug sample tonight
  starts at `delay.edge_prior.prior.youtubetv` (34.9 s; Nick's hidden-stick probe, 28 reads on 11 channels, 34.3-35.5,
  reruns 34.96 / 35.09) with stated doubt 4 s growing at most `drift_cap_s` 2 s (stays under 8 all night). Prior only;
  the probe refresh is PR #408 (integration/next, off).
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
- **2026-11-08 (Nick 22:15 + 22:17 ET Nov 7, via the overseer): the jump PASS BAR.** A 10-minute window of the real
  TV passes only with **75+ on-screen jumps in that window (7.5/min)** AND all four hard rules in it: **0 commercial
  seconds on screen**, **every shown play in full** (no play cut off at a cut), **0 mid-play joins**, **0 mid-play
  leaves**. "Can't leave mid play" is stricter than Rule #1's low-leverage exception: with buffered_jump on, the
  rotation leaves only at a dead ball (`Decider._jump_gate` refuses the low-leverage gate). Measured by
  `scripts/buffered_jump_try.py watch|report` (one PASS/FAIL line per 10-min window, rules audited ~2.5 min later
  against ESPN's play-by-play) and `scripts/buffered_jump_sim.py` (per-window verdicts on recorded slates; commercials =
  a break window's inside, 10 s in from each end, an assumption). Applies to every sport.
- **2026-11-08 (overseer 22:15 ET Nov 7, live 0 cuts on 9f3412d): buffered_jump.leagues default += nba, cbb, wcbb**
  (fouls, free throws, timeouts, violations, reviews and period ends are the dead balls; a made basket's inbound is too
  short for the delay margin). With buffered_jump on: no catch-up replay queue (it took the standby: 'yields to a queued
  replay'), no scrub inside a switch (tune + banner wait + scrub + safe wait held the command lock 156 s), and a screen
  game that left the feed is a free cut onto a ready standby that lands dead.
- 2026-11-08 (overseer's live run on 879d99ed, replay ON: 0 replays ran, every catch-up refused at the stick
  as too deep, '197s back needs 14 LEFTs (> 8)' up to 694 s): **new default `replay.scrub_max_play_age_s: 110`.**
  With replay.yttv_seek scrub, a YouTube TV play older than 110 s is never queued or picked (8 LEFTs reach about
  117 s back at the park; the tune and the live-edge delay are counted); keys mode and other apps are unchanged.
  The decider's generic selection tests set it to 0. PR #419.
- 2026-11-08 (overseer's 3778a401 report: a scrub right after a tune failed 'focus guard: no scrub label after the
  LEFT' in 62 s, YouTube TV's post-tune banner swallows keys ~20-30 s): **new default `replay.park_after_tune_s: 22`**
  (a replay park on a freshly tuned stick waits this long from the tune, no keys, then scrubs that much further
  back) and **`replay.scrub_max_play_age_s` 110 -> 95** (the wait makes the park ~22 s deeper; 8 LEFTs still
  reach it). PR #419.
- 2026-11-07 (Nick, live, 22:15 + 22:17 ET): **pass bar for jumps = 75 jumps in 10 minutes** (7.5/min) with, in that
  window, 0 commercials on screen, every shown play in full, 0 mid-play joins and **0 mid-play leaves** (leaves only at
  dead balls; stricter than Rule #1's low-leverage leave). Earlier the same day: 4-5/min (12:55), then 5-10/min
  "play by play" (20:45); "no forces, real autopilot"; mechanics first, the when-to-jump model later (21:05).
- 2026-11-07 (Nick, 12:08 ET): "You can deploy whatever" / "You can do whatever you want"; 22:35: the overseer fixes
  code itself when it can instead of waiting on threads. Deploys still go through scripts/deploy.py's health watch.
- 2026-11-07 (overseer 22:25 ET, runtime only): jump_mode.leagues narrowed to [cfb, nfl] for the buffered-jump test
  (buffered_jump.leagues is nfl/cfb, so NBA/NHL standbys could never be cut to). Restore [cfb, nfl, nba, nhl, cbb] when
  NBA/CBB join buffered mode or the test ends.
- 2026-11-07 (overseer 23:20 ET): new defaults buffered_jump.hold_chunk_s 25 and timing.behind_live_s.resume_settle_s 6.
  A Prime standby is pause-held in short pauses (one 75 s pause read 'stopped' after PLAY 3/3 on the hidden stick) and
  a hold's resume waits up to 6 s for 'playing' before reloading at the live edge.
- 2026-11-07 (overseer 23:35 ET, runtime, Nick: "if no jumps in the first min it's broke"): sources.fastcast on (shadow
  check 107 paired plays, 100% coverage, 19 s median lead over polling, 0 disagreements); timing.behind_live_s.scrub_fallback
  live (a failed scrub leaves a ready live-edge standby, not an unusable one); jump_mode.leagues restored to
  [cfb, nfl, nba, nhl, cbb] (basketball is in buffered mode now).
- 2026-11-07 (overseer 23:50 ET): new default buffered_jump.keep_standby_s 120: with the screen game gone or unknown, a
  'start'/'ended' switch to another game waits up to 120 s so it can't re-tune a ready buffered standby away.
  delay.scorebug_hidden set on at runtime (Prime standby delay synced by its score bug).
- 2026-11-07 19:05 ET (overseer, first live jump row 18:55: ALA@LSU -> ORE@OSU on CBS 17 tuned on the spot by search,
  30 s, lost the dead ball; the standby sat empty): **new `jump_mode.ready_only` true** (with two sticks a jump goes
  only onto the hidden stick's ready game; otherwise it waits for the standby and jumps at the next dead ball) and
  **`jump_mode.search_penalty` 20** (the standby prefers code channels, ~3-4 s, over locals by search, ~11-15 s,
  unless the local's moment is that much bigger). The standby pick also skips targets the hidden stick can't tune
  now (a failed tune's backoff), which had kept it empty. jump_mode.enabled itself unchanged (Nick's live setting).
- 2026-11-08 (process gate G-08, W2-LOCKED; Nick asked for "testing and results all locked and hidden"): **public CI shows locked-split gates as PASS/FAIL/PENDING only, and the nightly North Star no longer scores locked nights (`--split dev`).** Why: the repo is public, so per-night locked scores on every PR and in `docs/northstar.md` were tuning feedback on the test set. Gates and thresholds are unchanged; a release-PR FAIL still fails. `docs/northstar.md` loses its locked section. Results now go in a hash-chained ledger (`scripts/results_ledger.py`).
- 2026-11-09 (build plan P-BUILD, WP-00; Nick: "i just want the ability to queue games fast rn"): **new flags all ship OFF**: `queue.enabled`, `queue.r3_flippable`, `commit_gate.enabled`, `plan.sweep`, `discovery.st_wait`, `yttv_seek.skip_open_read`, `yttv_seek.overlay_band_from` (null), `tuners.prime_fast_confirm`, `tuners.espn_link_form`. One block so no parallel package writes the config; each turns on only after its gate.
- 2026-11-09 (build plan WP-06): **`autonomy.st_scan_early` and `autonomy.app_version_alerts` ship OFF.** Today's scan starts at 12:45, past T-20 for a 1:00 kickoff; early mode needs a hidden-stick run before it goes on.
- 2026-11-09 (W2-LOCKED-07, Nick: "testing and results all locked and hidden"): **`scripts/buffered_jump_sim.py --split` defaults to `fit`** instead of `all`, so a plain sim run can no longer read the locked nights. Numbers from before this date that used `all` are not comparable.
- 2026-11-09 (build plan WP-10): **`governor.footprint_log` defaults on** (one capped line in data/footprint.jsonl every 5 minutes). It only records memory use, and the Nov 15 dress rehearsal needs it.
- 2026-11-09 (Nick: "yes" to starting the brain's data work now; "at least 20 majors with 50 mil each"): **the build plan's scope cap on wave 4 is lifted for data work only** (WP-24, WP-25 and a cross-field data search). The brain still cannot affect the TV before a live Sunday holds and 7 of 7 shadow nights pass. Target: 20 or more university fields, about 50 million real data points each, only where the license allows training and the field has a real link to the show.
- 2026-11-09 (Nick: "i have a kaggle subscription 50$ plan that u will use when we get to that point"): **the brain's cloud training may use Nick's existing $50 plan**, with no new spending. Nick launches runs himself; agents never sign in or handle its credentials. Earlier records name this plan as Colab Pro+ (bought Nov 4); which service it is needs confirming before planning GPU hours.
- 2026-11-09 (Nick: "i want the actial live brain to get like to 1-2 B"): **target for the live brain is now 1-2 billion parameters, up from 40-100 million.** Built by fine-tuning an open base model, since our data can't justify 1 billion from scratch. It runs every 30-60 s beside the small per-tick model and still only scores legal options. It goes live only if test 3c passes on the real Mac (<= 2 GB, the CLAUDE.md local AI limit); otherwise it steps down in size.
- 2026-11-09 (Nick: "no the $50 was kaggle"): **correction: the $50 plan for brain training is Kaggle.** This replaces the note above saying it might be Colab Pro+. The 2026-11-05 record of a Colab Pro+ purchase is unconfirmed; ask Nick before counting on those compute units.
- 2026-11-09 (data ledger, D-LEDGER): **the earlier 565 million from-scratch estimate is withdrawn.** It assumed data targets that real counts did not reach. Estimates now: 28 million justified on directly relevant data, 248 million counting all usable data, about 4.6 weeks of free GPU. The 1-2 billion live brain target stands, since it fine-tunes an open base model rather than training from scratch.
- 2026-11-09 (data job 14): **NHL api-web stays out of bulk fetch.** Its robots.txt carries `Content-Signal: ai-train=no`, and Nick's Nov 5 ruling says to respect robots. NHL training data stays on the factory's existing ESPN summary path.
- 2026-11-09 (build plan WP-17): **`arbiter.enabled` ships OFF**, code default. Sim on fit nights: NFL touchdowns seen 39.8% to 94.2%, must-show 35.1% to 63.1%, cuts per 10 minutes 15.0 to 9.0, 0 mid-play joins, 1 low-leverage mid-play leave. Turning it on needs Nick's call and a real TV night.
- 2026-11-09 (build plan WP-17, 18, 19, 21, job 17): **`arbiter.enabled`, `slate.enabled`, `fill.enabled`, `labels.enabled` and `views.screen_clock` are in default_config.yaml, all false**, because Config.update rejects unknown keys and they could not be turned on otherwise. `slate.enabled` misses its exit bar in NBA: 3 mid-play entries landing 8-11 s after a shot, which Nick's rule allows but the frozen grader counts. Nick decides.
- 2026-11-09 (build plan WP-22): **`discovery.espn_on_yttv` ships OFF.** The route is unproven; the first TV night has to answer the ten unknowns in `ESPN_ROW_UNKNOWNS`.
- 2026-11-09 (QUEUE-TRACE): **planner-rung ticks stay with `standby.Planner`.** The queue's NFL gain was 426 of 433 cuts on the RedZone channel, which the replay's planner could not list; with the channel in the planner, the queue is no better (37.8% vs 38.1%). The queue adds Lock, Next, Pin and Tier moves on top of the planner.
- 2026-11-09 (MUST-LIVE): **`arbiter.plan` and `arbiter.save_leave` ship OFF.** NFL sim at the real 95 s rewind, 12 seeds: no plan 71.1 must-show, 97.0 TD; plan with leaving 83.8, 98.7, 283 low-leverage leaves; plan without leaving 82.1, 94.7, 0 leaves. Nick decides whether touchdowns are worth the leaves.
- 2026-11-09 (record keeping for the decisions rule on PR 423): the Nov 8 sprint packages (A-H, R, S, M, the fixes before Wave 1) and tonight's build added or moved these config keys without naming them here: behind_s, crunch_leave_s, diagnose_timeout_s, fastest_s, flip_fast_s, held_max_age_s, launch_timeout_s, lead_max_s, lead_min_s, long_dead_s, margin_sigmas, max_delay_s, max_rewind_s, min_behind_s, mine_late_closeness, no_wait_leagues, park_max_age_s, park_schedule_s, prep_s, relaxed_margin_s, restart_hold_s, resume_poll_s, rotate_dead_s, rotate_every_s, sweep_lead_min, tv_local_lock_wait_s, tv_maintain_timeout_s, tv_verify_deadline_s, unauthorized_retry_s, urgent_margin_s, visit_min_s, walk_after_s, walk_guard_s. Each one's reason is in its merge commit and the comment beside it in default_config.yaml. This line records them; it changes no value.
- 2026-11-09 (Nick, approving the morning list): **(1) download the small model files from Kaggle to the Mac so they can be compared with today's Planner; (2) NFL and CFB data may use GitHub's REST API asset route; (3) `arbiter.save_leave` is approved in principle, and stays off until a TV test passes; (6) the two eval-data test skips are allowed, because they run only when the pinned data is not fetched.** Items 4 (NBA after-shot scoring) and 5 (research-only non-sports data) are still open.
- 2026-11-09 (NFL-ROUTE, Nick's approval of the REST API route): **NFL and CFB bulk fetch use api.github.com release assets, no token, one request per 62 s (60 per hour), robots checked on api.github.com and the redirect host; CFB 2002-2021 comes from raw.githubusercontent.com (cfbfastR-data).** github.com/.../releases/download is never requested. **NFL value shards are now built from the attached fetch_sports_nfl.py output, like NBA, with no download fallback**, so the NFL model can be retrained with the fixed labels. CFB shards are not built: no CFB adapter, label or leak-check source exists, which is more than a small change.
- 2026-11-09 (Nick: "i want it joining right afeyr a bucket in transition"): **basketball may join right after a made basket, including during the transition that follows it.** This replaces his earlier "divert to the prior safest point" for that case. It applies to made baskets only: joins after a miss, a rebound or a live-ball turnover stay under the old rule. The frozen grader still counts these as mid-play, so a grader change stays his explicit call once the numbers are in.
- 2026-11-09 (Nick: "no 5"): **research-only non-sports data stays out of training** (Yambda, GiftEvalPretrain and similar).
- 2026-11-09 (NBA-BUCKET): **`basketball.join_after_bucket` ships OFF.** NBA arrival window make+1 s to make+8 s (CBB +9 s), or next action minus 2 s. On fit nights: 4 NBA and 2 CBB bucket joins, 0 after the next shot, but 0 joins when timing uncertainty is 3 s or more, and live sigma is configured at 3-4. It needs tighter stick timing or Nick's choice of `slate.bucket_sigmas`.
- 2026-11-09 (Nick: "change it"): **the locked scoring will count a basketball entry as clean from a made basket until min(8 s NBA / 9 s CBB, the next shot), on the entry side only.** A grader change, approved by Nick. Leaves and every other sport are unchanged. Reported alongside the old scoring so past results stay readable.
- 2026-11-09 (Nick, five directions): **no rewind limit in design: any missed must-show should be shown, using DVR or whatever works, but never 3 or more minutes late** (a hard cap of 180 s behind). The addicting experience is to be the most invested part and the product's edge. The 0-100 value should grow toward hundreds of thousands of grading criteria, with far more data. Prediction must draw on frontier 2026 methods. Target: code good enough that what the brain wants goes, with Rule #1 still enforced.
- 2026-11-09 (TIMING): **`delay.timing.app_sigma` ships OFF; `sync.edge.meter` and `delay.sync.robust` stay off pending a hidden-stick test.** Recorded nights: Prime's error is a per-game constant, 14.0 s on a game's first anchor and 0.35 s after one anchor plus the position meter (2 nights, small sample).
- 2026-11-09 (GRADER-BUCKET, grader v49): **`cutgrade.grade` counts a basketball entry as clean from a made field goal until min(8 s NBA / 9 s CBB, the next shot); `entries_mid_play_strict` keeps the old count.** `landed_dead_pct`, `landings_in_dead_pct`, the entry histogram and leaves stay on the old rule. A made free throw opens no window, because the approval says made baskets and the 8 and 9 s were measured on field goals. Fit nights, `basketball.join_after_bucket` on, sigma 2: NBA old 4, new 1 (an and-one's free throw), CBB old 2, new 0; at sigma 3 the flag changes nothing. The flag stays off.
- 2026-11-09 (Nick, personal data): **the RedZone phone app may track Nick's own phone and data to personalize the show, for local use only ("for local purposes, I give you full access to all my data").** This lifts the Nov 4 "no viewer sensors" and "taps stay evaluation-only" lines for Nick's own data; the rest of the Nov 4 line (no betting, artificial suspense, autoplay or pull-back nudges, streaks) is unchanged. Personal data stays on the Mac in ignored paths and is never committed, because the repo is public. Passwords, keys and tokens stay off-limits. Why: Nick wants the content built around him, and later around whoever pays (a bar owner's teams first). A sold version tracks a customer only with that customer's opt-in; nothing is built for it yet.
- 2026-11-09 (Nick, personal data, scope): **the phone app's reach is metadata ("the app should be able to look at all the metadata on my phone"): which apps, when and how long, not message or file contents.** Same limits as the entry above: local only, never committed, no passwords, keys or tokens. Why: Nick narrowed "full access" to metadata right after granting it.
- 2026-11-09 (VETO-AUDIT): **`arbiter.last_game_to_live` ships OFF.** `arbiter:last_game` refused the cut from a finished game to the last live one. With it on, simulated must-see seen rose 27.4 to 29.7% (hindsight brain) and 23.5 to 27.2% (heuristic), with 0 mid-play entries and 0 high-leverage leaves, but dead air rose (NHL 35.4 to 38.6, MLB 25.8 to 29.0, cause unconfirmed). Why off: the dead air rise is unexplained and nothing has run on the TV.
- 2026-11-09 (personal intake): **`personal.enabled` ships OFF.** It gates `POST /api/personal/app-open` (iOS Shortcuts app-open events: `{app, timestamp}` only; 404 and no write when off) and the store under `data/personal/` (git-ignored) that `scripts/personal_import.py` also fills from Nick's own exports. The endpoint uses the web app's existing LAN-only guard, plus a token header only if Nick sets `REDZONE_PERSONAL_TOKEN` himself. Scoring and the decider do not read it. Why: Nick's 2026-11-09 personal-data ruling (metadata only, local only); he sees the data before anything uses it.
- 2026-11-09 (LEAVE-AT-FLIP): **`legal.leave_at_flip` ships OFF (with `legal.leave_wait_s` 45).** On, a cut stays listed when the leave gate is shut or no landing is ready, is remembered (`decider.leave_wait`), and goes only when the flip's leave rules and a dead-ball landing both allow it; `no_landing` and `gate:leave` are not relaxed. Dev nights, simulated, stand-in brains (hindsight, heuristic): must-see seen NBA 39.5 to 59.9 and 14.0 to 58.7, NHL 16.8 to 36.5 and 12.3 to 31.2, NFL 32.1 to 35.0 and 30.8 to 33.1; 0 mid-play entries, 0 high-leverage leaves. Value lost to leave and landing refusals 8253 to 4510, not 0. Why off: with today's no-opinion brain the NFL scorecard got slightly worse (big plays 62.2 to 61.3, dead air 16.7 to 16.9), and nothing has run on the TV.
- 2026-11-09 (ZERO-LOSS): **`arbiter.class_bonus` ships OFF** (`arbiter.class_bonus_pts` 20: your team +40, crunch +20, open 0; a judgment, not fitted). On a brain's pick the L1 class adds points instead of filtering, so a much stronger moment elsewhere can win; within 2 points the north star order decides (your team, crunch, league). The must-show floor keeps its filter. Simulated, fit nights: `arbiter:l1_class` lost 378 to 131, all refusals lost 11,278 to 11,175, must-see seen 27.4 to 28.0% (hindsight) and 23.5 to 23.8% (heuristic), 0 mid-play entries and 0 high-leverage leaves, but NBA hindsight seen fell 340 to 295 and MLB 110 to 100. Why off: mixed by league, nothing has run on the TV, and the league-order tie needs the decider to fill `Show.league_rank` (not wired).
- 2026-11-09 (ZERO-LOSS): **`arbiter.last_game_to_live` stays OFF; its dead air is explained, not a defect.** NHL hindsight, 10 fit nights: 35.4 to 38.6% is 8,660 more break seconds on the last live game (6 of 10 in intermissions), and the rate only counts time inside a game: seconds on a blank or finished screen count as nothing, so they fell out of the percentage. Counting those as dead too, the screen went 79.3 to 75.6% dead. Nick decides; it has not run on the TV.
- 2026-11-09 (contract baseline 490): **`redzone/personal/profile.py` DEFAULT_DUR_S joins the contract baseline.** It estimates how long an app-open lasted for a personal usage summary; it is not a switching or sync time, so the K2 timing-literal rule doesn't apply. Why: a false positive, and moving a viewing estimate into the timing config would put it next to values that must be measured.
- 2026-11-09 (D-ADDICT M1): **`threads.enabled` and `threads.closure_first` ship OFF** (with `threads.max_hold_*_s`, `about_to_close_s`, `bonus_pts`, `cut_after_s`, `cut_penalty_pts`, `feed_lag_s`, `reopen_guard_s`, `flicker_s`, `chain_gap_s`, `run_pts`; all judgments, not fitted). The ledger keeps one open question per live game; an optional cut waits for the answer to air, capped per sport at 75-180 s and at twice that for a chain; a thread about to close adds at most 12 points to its game. A hold never applies over a must-show elsewhere, a crunch or your-team target, or a break. `close_run` is a fact only: it never holds or ranks (report 2.6, section 8). Dev replay, all fit nights (NFL 12, NBA 9, NHL 10, MLB 10), default config, flag on vs off: closures seen 47.2 to 47.3 NFL, 90.8 flat NBA, 45.5 to 47.3 NHL, 71.2 flat MLB; must-show seen NFL 1341 to 1356 of 4022, NHL 173 flat; NHL cuts per hour 2.9 to 3.2, entries 175 to 195; 0 mid-play entries and 0 high-leverage leaves both ways. Why off: the effect is small, holds fired 8 times in 41 nights because the show rarely proposes an optional cut with a question open on screen, and with `arbiter.enabled` on NHL lost must-shows (181 to 177 of 429). It needs real nights judged per section 6 of the report.
- 2026-11-09 (EXITS): **`legal.leave_low_leverage` ships OFF (`[]`).** A held pick (`legal.leave_at_flip`) may also leave mid-play at a low-leverage moment by every rule of `Decider._leavable` (the `redzone/lab/leverage.py` floor live as `scoring.engine.high_leverage`, your team, crunch and its run-up, a locked moment, late and close), per league. Dev nights, two stand-in brains, four leagues, simulated: value lost to rules 10,024 to 7,313 with leave_at_flip on, `gate:leave` 3,667 to 944, 0 high-leverage mid-play leaves, 0 plays missed on entry. NFL cuts per hour 6.3 to 13.9 (Nick's call: that is a real rise in whip). `entries_mid_play` 0 to 1 (NBA heuristic, a landing 11 s before a substitution row at the end of halftime; the frozen grader reads the sub as action). Off until a TV night judges it.
- 2026-11-09 (EXITS): **`legal.leave_wait_s` stays one number (45).** Reviewed per sport as asked: 30, 45, 60, 90 s moved total lost by under 1% in every league (NFL 3,566 to 3,584; after catch-up flat). The longer wait trades 'waited too long' for 'no longer worth it'. It can be set per league through `league_overrides` (the wait is the slower of the two games); no league is set.
- 2026-11-09 (CATCHUP-ROUTE): **`arbiter.catchup_route` ships OFF (with `arbiter.catchup_route_reach_s` 95); `redzone/noggin/catchup.py`.** A missed must-see airs late on the hidden stick in the dead time of the game on screen, in pieces over consecutive dead balls or a commercial, with one game live or a better class on screen (a held or high-leverage game only in a commercial). Never 180 s or more late (`HARD_CAP_S`, a constant: no setting may raise it); past it the moment is dropped and counted lost. A result the screen already showed airs marked as a replay. Simulated only: with `legal.leave_low_leverage` on, lost after catch-up 6,570 to 5,940 (95 s), 5,573 (150 s), 5,454 (180 s); must-see seen 42.5% to 56.4 / 60.0 / 61.4%; latest air 179.6 s. Off until the TV proves the deep rewind (measured reach 95-150 s).
- 2026-11-09 (EXITS-CATCHUP, Rule #1 note): **`legal.leave_low_leverage` and `arbiter.catchup_route` ship OFF.** With both on, simulated value lost after 180 s catch-up fell 10,524 to 5,454 with `entries_mid_play` 0 to 1 (NBA, heuristic stand-in brain, landing 11 s before a substitution row at the end of halftime that the frozen grader reads as action) and NFL cuts per hour 5.7 to 13.9. Why off: Rule #1 count rose by 1 and the cut rate needs Nick's judgement on the TV; rewind beyond 150 s is unproven.
- 2026-11-09 (contract baseline 491): **`redzone/noggin/catchup.py` HARD_CAP_S = 180 joins the contract baseline.** It is Nick's rule ("pls dont be 3 or more min late"), not a measured timing, and it is deliberately not tunable from config. Why: a false positive for the K2 timing-literal rule.
- 2026-11-09 (D-ADDICT arrival): **`threads.arrival.enabled` ships OFF** (with `threads.arrival.bonus_pts`, `min_lead_s`, `full_lead_*_s`, `fade_s`, `use_answered`, `snap_s`, `yards_a_snap`, `possession_s`, `at_bat_s`, `hazard_s`; all judgments, not fitted; needs `threads.enabled`). A game whose open question is about to be answered on its picture reads up to 25 points higher, by how soon and how much it matters, from what the feed has delivered only (a real-night check: changing every later play left earlier decisions identical on all 41 fit nights). Points only: nothing added with a must-show owed elsewhere, never lifted past a must-show, crunch, high-leverage or your-team game. Dev replay, all fit nights (NFL 12, NBA 9, NHL 10, MLB 10), flag off vs on: closures seen NFL 47.2 to 47.7, NHL 45.5 to 49.8, NBA 90.8 and MLB 71.2 flat; asked-then-answered NFL 89.2 to 88.8, NHL 92.6 to 92.2; must-show NFL 1341 to 1347 of 4022, NHL 173 to 176 of 429; cuts per hour NFL 26.4 to 26.6, NHL 2.9 to 3.5; dead air NHL 25.6 to 25.9; entries NFL 609 to 614, NHL 175 to 210; 0 mid-play entries, 0 high-leverage leaves, 0 plays missed on entry both ways. Why off: NFL barely moves, NHL arrival cuts land a median 108 s before the answer (not a play or two) and 32 of 75 see no answer within 3 minutes, and with `arbiter.enabled` on NHL must-show fell 181 to 179 of 429. It needs real nights judged per section 6 of the report.
- 2026-11-09 (grader freeze re-stamped): **`redzone/lab/backtest.py` builds `ShowDecider` and passes `show_states` so replays can measure the thread flags.** No scoring code changed; with `threads.*` off, decision hashes match on all 41 fit nights (NFL, NBA, NHL, MLB, arbiter on and off). Why: the thread ledger has to be replayable; this is a harness change, not a grading change, so the freeze stamp moved to v50 with no scoring change.
- 2026-11-09 (ZERO-LOSS-4): **`arbiter.catchup_route_short` ships OFF** (needs `arbiter.catchup_route`). With another missed must-see waiting and still on time, a catch-up clip is only its first piece (3 s lead-in, the whole play, 1 s), so the one hidden stick clears more of its queue; a clip alone stays 30 s. Same openings, same margin, same 180 s cap (`HARD_CAP_S` untouched; latest air 179.6 s on and off). Simulated on dev nights with `legal.leave_at_flip`, `legal.leave_low_leverage` (all leagues), `arbiter.catchup_route` and `arbiter.last_game_to_live` on (both stand-in brains, reach 180 s): lost after catch-up 3,456 to 2,804 (NFL 2,316 to 1,724, NBA 355 to 355, NHL 405 to 385, MLB 380 to 340); `entries_mid_play` 1 to 1 (the known NBA heuristic one), high-leverage leaves 0 to 0, `plays_missed_entry` 0 to 0. The rung before it: `arbiter.last_game_to_live` took 5,462 to 3,456 (the 5,454 rung re-measured at 5,462 on this base). Why off: a shorter highlight is Nick's call on the TV, and the route itself is still simulated only. `scripts/veto_audit.py` now prints `residual`: what is still lost by why the route did not air it (info only).
- 2026-11-09 (Nick, 8:15 PM, "yes bruh thats the whole fucking point"): **`arbiter.catchup_route_short` default false -> true.** A catch-up highlight is just the play when another one is waiting. Reason: Nick's call on taste; simulated value lost after catch-up 3,456 -> 2,804 (both stand-in brains, all leagues, reach 180 s), mid-play entries 1 -> 1, high-leverage leaves 0 -> 0. No live effect yet: it needs `arbiter.catchup_route`, which stays OFF until the TV proves the hidden stick loads a clip in time (the sim assumes 15 s to tune and seek; test queue items 3a, 3d, 3e).
- 2026-11-09 (memory check, names only): **the per-sport thread keys behind `threads.*` (OFF) are spelled out.** `threads.max_hold_nfl_s` 150, `threads.max_hold_cfb_s` 150, `threads.max_hold_nba_s` 75, `threads.max_hold_cbb_s` 75, `threads.max_hold_nhl_s` 120, `threads.max_hold_mlb_s` 180; `threads.arrival.full_lead_nfl_s` 50, `threads.arrival.full_lead_cfb_s` 50, `threads.arrival.full_lead_nba_s` 25, `threads.arrival.full_lead_cbb_s` 25, `threads.arrival.full_lead_nhl_s` 30, `threads.arrival.full_lead_mlb_s` 50. No value or default changes; the decisions above (SHOW-THREADS, THREAD-ARRIVAL) named them only as `max_hold_*_s` and `full_lead_*_s`, which the CI memory check cannot match. All are judgments, not fitted.
- 2026-11-09 (VALUE-V2, plan steps 1-2): **The `value2_next_3m` shard label ships OFF (`RZ_GV_LABEL` unset = v1; `RZ_GV_LABEL=v2` adds the column).** `redzone/moments/value2.py` grades football and basketball moments as base x leverage x stakes from `value2.yaml`; nothing in the app, the trainer or the grader reads it. Default shard rows hash the same as at e5562138 (test). Why off: every model, the 95% gate and the frozen grader are on the v1 label, and the v2 numbers are the plan's starting priors plus an interim margin-by-time leverage table, not fitted (plan section 5) and not yet read by Nick. On 207 NFL and 68 NBA dev games, 3-minute-window rank agreement with v1 is 0.739 (NFL) and 0.516 (NBA). The label's only stake is the playoff flag, because a pre-game spread exists only in archive summaries. The function is `grade`, not `score`: contract rule K4 keeps that name for the decision path.
- 2026-11-09 (VALUE-V2B, Nick's calls of Nov 9 night): **New `value2.yaml` keys `replay_leverage_exponent` (0.3) and `football.bonus.two_point_ties_or_leads` (2.0) ship with value v2 (off by default, nothing reads it at runtime).** Each graded moment gains `replay` = base x leverage^`replay_leverage_exponent` x stakes (dead value 0); `value` (the live value and the `value2_next_3m` label's source) is unchanged. A two-point try whose play text says "ATTEMPT SUCCEEDS" (and not "ATTEMPT FAILS") and that ties the game or takes the lead earns `two_point_ties_or_leads`; a failed try keeps its 3.0. Both priors are not fitted (fit on what RedZone and league highlight lists replay, plan section 5). Nick: garbage-time interception is "a notable play, replay worthy"; "TD + failed two-point try stays top 3". On 44 NFL and 68 NBA dev games only 4 moments' live value moved (made tying or go-ahead two-point tries); the MIA @ NYJ garbage-time interception reads live 1.50, replay 3.48.
- 2026-11-09 (Nick, 11:35 PM, "Yes"): **`scripts/value_leak_check.py` may accept the value v2 label.** The one protected-file change on `value-v2`: `value2_next_3m` is an allowed column only when a shard's sidecar says `label_version: v2`, and a v2 label that is not a number fails the check. Nothing about v1 shards is loosened. Reason: Nick approved the change to the protected leak check when asked; the v2 label cannot train on Kaggle without it.
- 2026-11-09 (TIMING-TRUTH): **`timing.measured` ships OFF**, with the stick timings Nick's TV measured on Nov 9 (small samples, busy Mac): `timing.flip_s` 1.9, `timing.flip_max_s` 2.2, `timing.tune_s` 3.0, `timing.tune_max_s` 4.3, `timing.clip_prep_s` 19.0, `timing.clip_prep_max_s` 25.0, `timing.seek_base_s` 28.7, `timing.seek_per_press_s` 16.5, `timing.reach_s` 175 (one reader: `redzone/measured_timing.py`). On, the catch-up route (`arbiter.catchup_route`, itself OFF) plans flips at 2.2 s, clip loads at 19 s and seeks to 175 s, and `scripts/veto_audit.py --timing measured` does the same. Off, every cost is what it was (flip `buffered_jump.flip_s` 3.2, clip load 15 s, reach `arbiter.catchup_route_reach_s`). Why off: the numbers are a handful of tries on a busy Mac (TV test 3e re-times the load on a quiet one), and the route they feed is still simulated only.

- 2026-11-10 (Nick, 1:25 AM, "use smart things"): **`two_stick.smartthings_cloud` default false -> true.** The app may flip the TV input through SmartThings again. Reason: on the Nov 9 TV session it was the only flip that worked on his TV (9 of 9 confirmed in 1.8-2.2 s; local key 0 of 3), so deploying with it off would leave the app unable to cut. This reverses the Nov 8 ruling (N1) that dropped it. Still true: free access ends 2026-12-15 and the $0 rule forbids a paid plan, so a second working flip path is needed before then.
- 2026-11-10 (Nick, 2:50 AM, "focus on NBA more tbh. i have redzone and sunday ticket. so ur a basketball, and MNF and TNF application"): **product scope: basketball first (NBA above all), plus Monday Night Football and Thursday Night Football.** NFL Sunday multi-game switching is no longer a target: he watches those on NFL RedZone and Sunday Ticket. Reason: his call. What follows: work, tests and the reliability bar are judged on NBA nights and on MNF / TNF nights; the Prime Video path matters more (Thursday Night Football and many NBA games air there) and is still untested on the TV; no config default changes with this line.
- 2026-11-10 (TV session 2, measured): **`tuners.app_change_confirm` ships OFF.** After another app was in front on a stick (Prime, then a YouTube TV channel link), the tuner reported "video never started" in 8 of 11 tries (about 31 s each) while the asked channel was playing: Android answers "brought to the front", the tuner re-sends the link as a new task, the app restarts through the first 15 s window, and the retry then snapshots the asked channel already on, so a watch that waits for a change cannot pass. On: the plain link gets up to 6 s to confirm before any resend (it confirmed in 2.5-3.8 s, 8 of 8, by the tuner's own new-stream check), and a retry that finds the asked channel on, when it was not on before the first link, is a success. An unchanged other channel still never confirms. Why off: measured on one stick (4K 2023), YouTube TV after Prime only; Prime after YouTube TV and a live Prime event are untested. Within one app nothing changes (30 of 30 switches that night).

- 2026-11-10 (YES-1; Nick: "the code keeps saying no... a no is rare, like 95% yes", "ofc switch if its better", "once the play ends u switch... and hold the other one until then"): **Eight new flags, each shipped at today's behaviour: `gate.leave_unaired_row_only_ended` false, `gate.leave_margin_measured` false, `gate.leave_dead_window` false, `legal.lock_dead_ball` false, `legal.facts_urgent` false, `gate.ending_last_play` false, `cuts.floor_from_marks` false, `arbiter.caution_rules` true.** Every number below is SIMULATED on recorded nights (`scripts/veto_audit.py --split fit`, 41 nights, both stand-in brains, with `legal.leave_at_flip`, `legal.leave_low_leverage` for every league, `arbiter.catchup_route` and `arbiter.last_game_to_live` on); nothing was run on the TV. Baseline: the hindsight brain's wanted game was on screen within 20 s on 18.6% of the ticks it wanted one (both brains 11.7%), a catch-up clip of it within 60 s on another 10.1%; 17,868 refusals; value lost after catch-up at the 180 s reach 2,781; 1 mid-play entry, 379 mid-play leaves, 0 high-leverage leaves. One step at a time, a step that raised a Rule #1 counter left off for the later runs: `gate.leave_unaired_row_only_ended` 19.7%, 383 leaves (left off: on hockey 2 of the 17 leaves it newly let through the plain window were mid-play); `gate.leave_margin_measured` nothing alone (the unaired-row check refuses first); `gate.leave_dead_window` 20.6%, 391 leaves (left off: on hockey 12 of 37 dead-window leaves were mid-play); `legal.lock_dead_ball` 18.9%, 382 leaves (left off); `legal.facts_urgent` 18.6%, 379 leaves; `gate.ending_last_play` 19.1%, 376 leaves, lost 2,751; `cuts.floor_from_marks` not one tick changed; `arbiter.caution_rules` false 19.5% (both brains 13.9%), 16,154 refusals, 412 leaves, lost 2,875 (left off). The four that raised nothing, with the true picture delay 3 s and 6 s off what the app believes: 1 and 6 mid-play entries, and today's code under the same 6 s error also has 6. Every step on at once: 23.4% (both brains 16.4%), 15,033 refusals, lost 2,604, 0 mid-play entries, 415 leaves; steps 2-8 without the caution change: 21.5%, lost 2,534, 2 mid-play entries, 386 leaves. 0 high-leverage leaves in every run. They stay off because none is TV-tested and four raise mid-play leaves in the sim. The yes-rate stays near 20% because what refuses now is no dead ball to join on (`legal:no_landing`, 4,300 to 4,600 ticks), the must-show floor and the class order: Nick's guarantees, not caution.

- 2026-11-10 (NBA-YES; Nick: "focus on NBA more tbh... ur a basketball, and MNF and TNF application", "like 95% yes", and Nov 9 "i want it joining right after a bucket in transition"): **One new flag, shipped off: `basketball.join_after_bucket_crunch` false (with `basketball.join_after_bucket`, the made-basket window also takes a join into a crunch or high-leverage game; Nick decides). One fix inside `basketball.join_after_bucket` (still off): a made last free throw no longer opens the made-basket window. New audit options, no product behaviour: `scripts/veto_audit.py --sigma S` (the timing doubt the replayed app states, default 3), `--sigma-hold` (that doubt does not grow through the night), `--landings` (what a refused landing would really have shown, and the wait to the next legal one), `--set audit.flip_at_landing=true` (a what-if with no product flag: a waiting cut goes at the first second inside the 10 s tick its landing and the gate allow), and the `jumps` lines every audit table now ends with (cuts that went per minute, and per 5, 10, 20, 30, 45 and 60 minute window).** Every number below is SIMULATED on recorded nights (`scripts/veto_audit.py --split fit --leagues nba`, both stand-in brains, with `legal.leave_at_flip`, `legal.leave_low_leverage` for every league, `arbiter.catchup_route` and `arbiter.last_game_to_live` on); nothing was run on the TV. Baseline, hindsight brain then heuristic brain: the wanted game was on screen within 20 s on 12.4% and 3.4% of the ticks it was wanted, must-see seen 72.1% and 71.5%, 19 and 50 jumps in 1,844 minutes with two or more games live (0.6 and 1.6 an hour; the median 10-minute window has 0 jumps), mid-play entries 0 and 1, mid-play leaves 0 and 16, high-leverage leaves 0. The replay's stated doubt is not 3 s at crunch time: it grows 1 s per 15 minutes to 9 s, so `--sigma` alone changed nothing for the hindsight brain; the numbers after this use `--sigma-hold`. `basketball.join_after_bucket` with the doubt held at S and the true delay wrong by S either way (the worse arm): S 3: 13.0% and 3.6%, 0 and 6 bucket joins; S 2: 12.5% and 3.5%, 0 and 11; S 1.5: 12.8% and 3.6%, 0 and 11; S 1: 15.1% and 3.8%, 1 and 13, must-see seen 75.0% and 72.1%, 21 and 54 jumps; S 0.5: 15.4% and 3.8%, 1 and 13. Mid-play entries by the grader stayed 0 and 1 in every arm after the free-throw fix (before it, 2 at S 1.5 and 2: a join 4 to 8 s after a made last free throw); the strict count rises by the bucket joins, as grader v49 says it should; mid-play leaves 0 and 15 to 18; high-leverage leaves 0. On top of S 1 (these four runs had the delay known exactly and were made before the free-throw fix): `arbiter.class_bonus` 14.0% and 4.0%, must-see seen 73.8% and 76.2%, 21 and 58 jumps (`arbiter:l1_class` 5 -> 0 and 309 -> 184: what is left are picks that lose on the class points, not a filter; `arbiter:floor` untouched); `basketball.join_after_bucket_crunch` 17.2% and 3.8%, seen 77.3% and 72.1%, 5 and 16 bucket joins, 22 and 54 jumps; the what-if `audit.flip_at_landing` 16.7% and 3.7%, seen 82.0% and 72.1%, 22 and 57 jumps, mid-play leaves 0 and 21; both 19.8% and 3.9%, seen 83.7% and 73.3%, 23 and 57 jumps. The set `basketball.join_after_bucket` + `basketball.join_after_bucket_crunch` + `arbiter.class_bonus` at S 1 with the true delay off by 1 s (worse arm): 15.7% and 4.0%, must-see seen 76.2% and 76.2%, 22 and 58 jumps (0.7 and 1.9 an hour), mid-play entries 0 and 1, mid-play leaves 0 and 18, high-leverage leaves 0. The floor Rule #1 itself sets (`--landings`, the grader's facts): when the brain first wants a game, its next legal landing (a dead ball, or 8 s after a made basket) is a median 1 s away, 16 s at the 75th percentile and 32 s at the 90th (heuristic 0, 5, 19); 74% and 88% are within the 15 s that "on screen within 20 s" allows, 87% and 92% within 25 s. Reason for shipping everything off: the gain is small and needs the TV to know its picture delay to 1 s all night, which is not measured; the stand-in brains ask for a new game only 1.7 and 5.9 times an hour on NBA nights, so no landing rule can lift jumps past that.
- 2026-11-10 (Nick, "bruh theres a UI in the repo already"): **`show.ui` default false, `show.state_dir` default "", `show.eyes_dir` default "" (new).** The new show's state files (state.json, events.jsonl, eyes_state.json) get a "Show" tab in the existing phone app (redzone/web/show.py, static/rz-show.js) instead of a separate dashboard page. Read-only: off means no route, no markup and no script, so decisions are identical; on, only the contract's fields leave, as numbers, on/off values and short public names. Ships off until Nick turns it on for a show night.
- 2026-11-10 (POSITION-FIX, from the TV session's position findings, research L18): **`timing.label_stamp_at_pause` ships OFF.** On: a YouTube TV label read is timed at the media session's pause event (read with the stick's uptime), and behind-live is the session's position after the commit against that pause, on the tune's own counter. Reason: the label is an exact whole-second floor of the frozen frame, and the old screenshot stamp read 1.9-5.3 s too far behind (n=8 on 3 tunes). Why off: one stick, two channels, one night.
- 2026-11-10 (POSITION-FIX): **`timing.playback_with_package` ships OFF.** On: the sync probe's and the scrub's playback reads for a YouTube TV target pass the app's package. Reason: a paused YouTube TV session is invisible without it and another app's idle session comes back instead; with it the session was right after 102 of 102 seeks and pauses. Why off: not run on a live night.
- 2026-11-10 (POSITION-FIX): **`timing.no_right_probe_at_live` ships OFF.** On: at the live edge the scrub's probe is a LEFT, booked as exactly 15.000 s (69 of 69), and the label probe commits that LEFT instead of pressing RIGHT. Reason: at the live edge a RIGHT does not undo a LEFT and the commit jumps to about live (2 of 2). Why off: n=2 for the RIGHT case; it changes the keys sent at the live edge.
- 2026-11-10 (POSITION-FIX): **`timing.per_tune_offset` ships OFF.** On: a fresh YouTube TV tune adds its own doubt (espn 1.24 s, fs1 1.98 s, default 2.0 s) to the delay filter, apart from the channel's, until one absolute read on that tune (label 0.29 s, Live Latency 0.19 s). Reason: tunes do not land at a fixed lag (range 5 s, n=21 and 10). Why off: one night; it widens stated doubt, which changes what the gates allow.
- 2026-11-10 (POSITION-FIX): **`timing.stats_for_nerds` ships OFF.** On: a reader can switch the Stats for nerds panel on (14 keys, one command), read Live Latency 11-23 s after a fresh tune at the live edge, switch it off and BACK; the label probe and the flip refuse a stick whose panel may still be up; the playback id line is masked. Reason: no position lost, agrees with the label to sd 0.19 s (10 tunes). Why off: it presses keys on the hidden stick, and nothing schedules it yet.
- 2026-11-10 (POSITION-FIX): **`timing.key_constants` ships OFF.** On: a snap park sends the pause key early by the stick's own pause delay, and the pause length of a snap roll or a hold without stick positions comes from the key times and each stick's constants (2023 stick 0.70 / 0.69 s, 4K Plus 0.60 / 0.53 s). Reason: the two sticks differ by about 0.1 s and the round-trip span adds its own error. Why off: n=11 and n=6.
- 2026-11-10 (POSITION-FIX, ledger L29-L30): **`timing.game_offset` ships OFF, `timing.game_offset_file` empty.** On: a per-game K (feed time minus YouTube's clock) is read from that JSON file for that game on that night, its spread carried into the doubt; with `timing.per_tune_offset` a tune's read plus K becomes a delay sample. No K for a game means the delay is unknown, never K = 0. Reason: K was -2.78 s on one espn2 game and +2.85 s on an espn game two hours later. Why off: two games measured.
- 2026-11-10 (PARK-CORE, INTEGRATION-PLAN unit 1): **The PARK planner and simulator core moved from `scripts/` into `redzone/noggin/park_plan.py` and `redzone/noggin/park_core.py`; the scripts import them and keep every old name.** No default, gate or flag changed and nothing in the app imports the new modules yet (wired in later behind `park.enabled`). The timing constants became `park_core.ParkParams` (defaults = the old values, `from_config` reads `park.<name>`) so the package follows rule K2. Proof the move changed nothing: `park_sim`, `park_plan`, `park_table` and `park_compare` outputs on two NBA dev nights at the three timing cases are byte-identical apart from their wall-clock columns, and `tests/test_park_core.py` pins the planner's flips second for second. The game-facts builder (`park_ceiling.truth_of`) stays in the script: it imports `redzone.lab` (rule K6) and the plan gives it to the PARK-FACTS unit.

- 2026-11-10 (PARK-FACTS, INTEGRATION-PLAN unit 5): **A live game's PARK facts are built row by row (`redzone/noggin/park_facts.py`) and each game's timing is plain data (`redzone/noggin/park_timing.py`); nothing in the app imports them yet.** What the builder calls final equals the grader's whole-game facts (`park_ceiling.truth_of`) at every row on the two NBA dev nights with the most games (20261023, 20261028: 24 games, 12,112 rows, 0 mismatches, all 24 equal once over); a stoppage is never open once a later row shows the clock moved, and the 395 recorded moments where the grader on the rows so far said "still stopped" though play ran are all play in the builder's view. A restart dated by a clock drop counts as final only once a play row follows (a game can end with no row). Unknown K is the show runner's 13 s +- 12 s prior (padding 48 s), not the plan's older 35 s +- 4 s. No default, gate or flag changed.
- 2026-11-10 (SOUND-GUARD; Nick: "Volume must be 1000% off"): **New block `sound_guard`, all shipped off: `enabled` false, `must_be_silent` false, `check_s` 20, `unreadable_max` 3, `silence_tries` 4, `on_incident` off_tv.** `must_be_silent` makes `redzone/tv.py` refuse every key and SmartThings command that can raise the volume or unmute (KEY_VOLUP, KEY_MUTE, volumeUp, unmute, setVolume above 0) and locks `SamsungTV.volume_duck`. `enabled` attaches `redzone/platform/sound_guard.py` (the rules of the Nov 10 test-session script `tvctl.py`): silence and read back before the first input switch, re-check after every switch and every 20 s; not silent, or unreadable 3 times in a row, powers the TV off and blocks every switch and key until cleared by hand (machine `platform.sound_guard`). Off: no guard exists and no sound call is made. Why off: not yet run on the TV (its readback must match `tvctl.py state` 20 of 20 first). Known gap: the controller's HOME/CEC fallback flip (`control.py _flip_to`) does not go through `tv.py`, so a blocked guard does not stop it yet; that one-line check belongs to the controller's owner.
- 2026-11-10 (DEAD-V2, integration plan unit 12, research ledger L1/L5): **`timing.dead_v2` ships OFF; only the dead-time table v2, its reader (`scoring/dead_left_lite.guaranteed_left`) and `Event.stop_class` are salvaged from branch `anchor-mode`; anchor mode, `anchor.*` and `arbiter.catchup_route_alpha` are not.** No app code reads the flag yet; a recorded NFL dev night decides identically with the class read or blank. A stricter risk level is now capped by every looser one (monotone in alpha). Coverage (`scripts/dead_left_coverage.py`, valid nights held out, train nights rebuild the table exactly): held out, NFL timeouts 99.3% / 95.7% and NBA timeouts 99.4% / 95.7% at the 99% / 95% levels; three held-out cells fall short (NFL end of period 98.9% at 99%: 1 of 89 stoppages; halftime 92.9% at 95%: 3 of 42; two-minute warning 94.1% at 95%: 6 of 84), so use 0.01 for those kinds.
- 2026-11-10 (integration plan unit 0b, the local stack `park-stack`): **`park.enabled` ships OFF and `eyes.enabled` ships OFF; both are new and nothing reads either yet.** They are added now, empty, so the units that follow (PARK-HANDS, PARK-CONTROLLER, EYES-PACKAGE, EYES-LINK) each edit inside their own block instead of all appending to the end of `default_config.yaml`, where three of tonight's branches already conflicted. The phone refuses a setting that is not in that file, so the keys must exist before anything can turn them on. Off, the app decides exactly as before (no code path reads them).
- 2026-11-10 (the local stack `park-stack`, record keeping for POSITION-FIX's two tables): **`timing.per_tune_offset_sd_s` ships as `{default: 2.0, espn: 1.24, fs1: 1.98}` and `timing.key_constants_by_stick` ships with two stick models (the 2023 4K stick: pause 0.70 s, resume 0.69 s, sd 0.13 s, n=11 each; the 4K Plus: pause 0.60 s, resume 0.53 s, sd 0.08 s, n=6 each).** Both came in with POSITION-FIX and its own lines did not name them, which the decisions check requires. Each is read only with its flag on (`timing.per_tune_offset`, `timing.key_constants`), and both flags are OFF, so nothing changes. The numbers are one night on two channels and two sticks (research ledger L18, position FINDINGS sections 5e and 9).
- 2026-11-10 (the local stack `park-stack`, STACK-CLEAN, contract check back to its baseline 491): **POSITION-FIX's and SOUND-GUARD's measured numbers move into `default_config.yaml` with their values unchanged: `timing.yttv_left_step_s` 15.0, `timing.nerds_shots_s` [11, 17, 22], `timing.nerds_latest_s` 23.0, `timing.nerds_bias_s` 0.34, `timing.nerds_sd_s` 0.19, `sound_guard.settle_s` 0.7.** Each module reads them once from the shipped file (a user setting does not change them); the label read's 0.29 s is now computed (a whole-second floor, 1/sqrt(12)); `tune_offset`'s default spread is `timing.per_tune_offset_sd_s.default` and its read doubts come from the two readers; `yttv_nerds.FLAG` is `NERDS_FLAG`; `yttv_timing.EDGE_READ` is `adb.EDGE_READ`. Why: the contract check refused 15 new timing literals, duplicate names and an orphan; every flag stays off and the flag-off night test still decides identically.
- 2026-11-10 (the local stack `park-stack`, STACK-CLEAN, integration plan S6/S7): **K has one reader, `redzone/noggin/k_table.py`, for all three file shapes (the calibration/watcher table keyed by channel, `timing.game_offset_file`'s `{"games": ...}`, the watcher's `"<channel>|<league>|<game id>"` map); `park_timing.k_for` and `tune_offset.GameOffsets` both read through it, and `timing.game_offset_file` now accepts any of the three.** A row that names a game id applies to that game only; a row that names no game is never borrowed for another game; a missing date, a wide row or a bad number is unknown (wide), never zero. The sign is K = YouTube-clock time of a frame minus the feed time on it (+24.30 s for a picture 24.30 s after the feed stamp, pinned by tests/test_k_table.py); the POSITION-FIX line above that calls K "feed time minus YouTube's clock" has the words backwards, its code and numbers were right. Why: three shapes and no converter meant the watcher's K could not reach the per-game offset; `timing.game_offset` still ships OFF.
- 2026-11-10 (the local stack `park-stack`, PARK-HANDS, integration plan unit 7): **the PARK device steps live in the controller (`Controller.park_tune`, `park_pause`, `park_jump`, `park_finish`, `park_stale`; `ParkHands` in `redzone/control.py`, machine `control.park`, standby state `park_held`) and refuse unless `park.enabled` (still false), `sound_guard.enabled` and `sound_guard.must_be_silent` are all on.** Their numbers are the Nov 10 measurements on the real sticks and TV (ledger L13-L21, L41-L51), values as the local runner used them: `park.max_park_s` 300, `park.late_cap_s` 145, `park.steps.min_real_pos_s` 60, `live_lag_s` 20, `first_report_s` 8, `poll_s` 0.25, `tune_timeout_s` 90, `read_steps` 2, `left_step_s` 15, `center_wait_s` 1.3, `left_gap_s` 0.6, `label_wait_s` 0.7, `label_timeout_s` 25, `land_wait_s` 4, `settle_s` 2.5, `replay_wait_s` 6, `read_age_min_s` 5, `read_age_max_s` 240, `grid_s` 300, `base_lag_min_s` 5, `base_lag_max_s` 90, `kept_lag_max_s` 600, `pause_lead_s` 0.70, `pause_lead_plus_s` 0.60, `back_after_s` 0.4, `confirm_s` 1.6, `resume_lead_s` 0.3, `hold_key_s` 1.35, `flip_methods` [smartthings], `input_rereads` 2, `input_reread_s` 0.7 (all under `park.steps`). A stream found already on the game is never taken to be at its live edge (kept only when its place is read, else tuned fresh); a flip that does not land keeps the picture up and degrades nothing. With a sound guard (S10): a switch the guard refuses sends no HOME fallback and does not degrade two-stick mode, and no tune goes out while an incident is open. Why: the in-app controller must drive the sticks the way tonight's runner did; flag off, nothing calls the steps and a recorded night decides identically.
- 2026-11-10 (the local stack `park-stack`, PARK-CONTROLLER, integration plan unit 8): **with `park.enabled` on (it ships OFF) the app runs the two-stick PARK show itself; with it off nothing changes (one recorded night's decisions hash the same before and after, `tests/test_park_flag_off.py`).** New keys, all read only with the flag on (the park loop reads `park.idle_poll_s` while it sleeps): `park.leagues` (`[nba, cbb]`), `park.session_min` (240), `park.idle_poll_s` (5), `park.start_retry_s` (30), and the `park.show.*` block (`show_delay` 35, `bonus` 3.0, `min_look` 3.0, `warrant` need, `crunch` true, `stops` true, `land_wait` 20, `long_break` 90, `tune_stick` 1.0, `late_cap` 145, `jump_real` 3.0, `read` 10.5, and the `stop_min` table), each the value the local show runner ended Nov 10 on. Nick's rulings of Nov 10, as built: "If we jump the warrant has to exist" = `park.show.warrant: need` (every jump's warrant is worked out before it and written in its decision record; never a landing that shows no play); "4+ games id say goal of 7 at peak. But always depends. Can't be hard rules" = pace is only the planner's `bonus`, which chooses among warranted jumps and never passes a guard. How: `Engine._park_loop` -> `redzone/park_run.py` (`ParkRun`, `ControllerRig`) -> `redzone/noggin/park_show.py` (`Show`: facts from the engine's own summaries, the planner, the hard guard `park_facts.refusal`) -> `Controller.park_*`; the decider's new owner `park` turns every other decider's switch into a stay while a show holds the sticks (a user, safe-mode or replay owner still comes first); the standby plan stands down. The show's planner knobs were folded into the one search in `redzone/noggin/park_plan.py` (defaults unchanged, `tests/test_park_plan.py` unedited). Three park steps were added to unit 7's `redzone/control.py` because the show cannot run without them: `park_start` (place the stream on screen by a read, or tune it afresh), `park_resume` (a parked stick plays on), `park_tune(fresh=True)` (reload a stream held too long). Differs from the integration plan: a PARK jump goes through `Controller.park_jump` with its own guard, not through `decider/gate.py`'s commit gate (that file is unchanged); the normal decider's hard switches (crunch, ended) do not go first while a show runs (the show's own crunch anchor and game-over warrant cover them). Proof so far is a replay on FAKE sticks (`scripts/park_replay.py`, night 20261023: Rule #1 0 / 0 / 0, every jump warranted): not measured on the TV.
- 2026-11-11 (PARK-CONTROLLER, the Rule #1 pass; coordinator's rulings of Nov 11 after the frozen grader was put on the replay): **the in-app PARK show cuts only in the frozen grader's dead time, for leaves and for joins; `park.max_park_s` 300 -> 180; new keys `park.late_row_s` 60 and `park.show.stop_max` (whistle 240, unknown 240, timeout 300, review 420, injury 900, end_period 300, halftime 1500).** Why: with the simulator's any-shot rule (4 s after ANY shot counted as legal) the replay's own judge said 0 / 0 / 0 while `cutgrade.grade` found joins after misses and leaves in live play (night 20261023, 4 games: 2 entries mid-play, 4 strict, 6 leaves), because a shot's 4 s joined a stoppage or another shot's 4 s into one run and the padding was measured to that run's ends. So `park_show.engine_setting` never sets `shots`, the show's feed hands its arrays no shots, and the any-shot rule is only a labelled what-if in the scripts (`--any-shot`, `--what-if-any-shot`: outside Rule #1). The made-field-goal JOIN window (Nick, Nov 9) is NOT in the show: it is a later, separately measured change behind its own flag. `scripts/park_replay.py` now prints the frozen grader's entries mid-play / strict / leaves mid-play on every row; the bar is 0 / 0 / 0. `park.late_row_s`: a feed row stamped more than 60 s after a row that follows it is a late edit and takes the stamp of the row before it (live run 4, flip 4 of Nov 10 left a game in live play because two substitution rows re-stamped half an hour late dragged every later row up to their stamp; regression fixture `tests/fixtures/park_live_flips_2026-11-10.json`, public play-by-play only). `park.show.stop_max`: the sanity gate, apart from that repair: a dead stretch longer than a stoppage of its kind can plausibly last is unknown past the cap (plain caps set above one recorded night's longest real stretches, not a model). `park.max_park_s` 180: the longest hold whose place was read back after its resume (ledger L74); a stream held longer is tuned afresh and never jumped onto. Also in this pass: the padding in whole grid seconds is never rounded down and each dead stretch loses 1 s at its start and 2 s at its end for the grid itself (`park_facts.GRID`); after every resume the stream on screen has its place read again and the look gets a verdict (held / loose / drifted / unread; a drifted stream is not cut on until read again); the read gesture never sends its committing CENTER with a row button in focus. All replayed on FAKE sticks; nothing measured on the TV.
- 2026-11-11 (PARK-CONTROLLER fix, found by PRIME-PARK's replay): **a feed row stamped more than park.late_row_s past what the feed has been read up to is a late edit (it takes the stamp of the row before it and is marked dragged), and new `park.possession_cap_s: 30`.** The first rule is the fix (it alone takes the night below to 0 / 0 / 0); the second is a guard for the live form of the same trap, where the late row is simply missing. In the PARK show's basketball facts, a dead stretch that holds a review's ruling (the stoppage is over: an inbound and play follow), with no row after its last one yet or the next stamped more than park.late_row_s later, is dead for 30 s from its last row (24 s shot clock + 6 s for the inbound and stamp slack); the rest is UNKNOWN, never a place to leave or join. A narrower first version (any short dead ball) cost a quarter of the pace and was dropped. `park.show.stop_max` still caps explained long stoppages (timeouts, period ends, halftime, injuries, reviews under way). Why: night 20261023 LAC @ LAL Q1 7:21: a timeout, a challenge ruled 32 s later, then 149 s of silence (the next play row had been re-stamped 19 minutes late) read as dead up to the review cap (420 s), and with a slower second tune the show left the game mid-play by the frozen grader. Fake sticks.
- 2026-11-11 (PRIME-PARK; Nick, Nov 10: "thats why we need amazon prime"): **new `park.prime.enabled` ships OFF.** On, the PARK show also takes live games with a Prime event link (`prime:<gti>`), reusing the repo's Prime pieces: the Prime tuner (event link, purchase guard), `decider.switch_cost.prime` (27 s) plus a pause-hold through `prime_pause.enter_buffer_pause` in `buffered_jump.hold_chunk_s` chunks, no back route (`replay.apps`), the place from the epoch position (`sync_edge.EPOCH_POSITION`), and each Prime game's K from score-bug reads of its own paused frames (`decider.sync.clock_sample` into `sync_edge.EdgeTracker`, `sync.edge.k_min_reads` / `k_sd_max_s`); never BACK on Prime. Off: the replayed show hashes byte for byte as at a705a906 (`tests/test_park_prime.py`). Proof is fake sticks and replay only; the acceptance is `scripts/park_prime_accept.py` on the TV with Nick. Why: TNF (Nov 12) and Friday NBA are on Prime Video and PARK took YouTube TV channels only.
- 2026-11-11 (PARK-NFL; Nick's scope: basketball plus Monday and Thursday Night Football): **PARK can read football, but `park.leagues` stays `[nba, cbb]` (football off).** With nfl listed, `park_facts.GameFacts` reads football with the grader's own rule (`grading.dead_at`: each action row covers the 7 s before its stamp, the rest is dead; no shot window), an open football stoppage is trusted to the feed's edge less 15 s (`park.football_lead_s`), and no dead second is trusted more than 45 s after an action row (`park.play_clock_s` 40 + `park.snap_slack_s` 5) unless a stoppage, marker or score row explains it (the Nov 10 late-row trap; it trims about 2.3% of the grader's dead seconds on 12 recorded NFL games). Football padding about 8 s from the new `park.timing.stamp_sd_football: 2.0` (not measured: no football watcher pairs yet). Proof: replay with fake sticks on an NFL night game time-shifted onto recorded NBA nights; not measured on the TV. Why: PARK had no football and TNF WSH @ NYG is Thursday.
- 2026-11-11 (PARK-NFL-LAG; coordinator after the replay showed football almost never joined at its real feed lag): **`park.feed_lag.enabled` and `park.prime.bug_gate` ship OFF; football facts follow the picture model.** feed_lag on: a game of `park.feed_lag.leagues` ([nfl, cfb]) starts from its league's `prior_q` (90) feed lag in redzone/lab/feed_lag_profile.json (nfl 62.5 s, n=150, Nov 2-3), its feed edge uses that lag, its stream is parked (lag - park.show.feed_lag) deeper (Prime: a longer pause hold in hold_chunk_s chunks; YouTube TV: more LEFT presses), and the lag is then measured live from each new row's first sight: `park.feed_lag.min_samples: 5` (the measured lag replaces the prior only once at least 5 rows have arrived after the first poll, so one late or early row cannot move the depth) and `park.feed_lag.move_s: 5` (and only when the measured lag differs from the one in use by 5 s or more, so the feed edge and the depth do not churn on every poll; 5 s is small next to the 36-55 s of depth a football feed needs). bug_gate on: a parked Prime football frame whose score-bug play clock reads 40 is resumed, not kept. Picture model (the watcher, Nov 11, one Thursday replay on Prime: ESPN's NFL stamp is the snap, +0.57 s sd 0.23 n=4; dead ball +5.49 s sd 0.35 n=3): no football park within `park.play_after_s` (15 s, cautious) after a snap stamp; it cuts the usable dead time per gap after 8 s padding from a median of 20 s to 5 s. The frozen grader is unchanged; replays print a second "stamp = snap" judge. Proof is fake sticks only. Why: Thursday's TNF on Prime.
- 2026-11-11 (Nick, via the coordinator: "for NFL nights have it be a like slider / one end is max football plays other sports backfill in / and the reverse and then splits across the middle"): **new `park.football_slider: 50` (0-100, 50 = value decides, unchanged).** Football's value is weighted 4^((s-50)/50) against the others (0 -> 1/4, 100 -> 4); within 10 of an end the anchor switches (90-100: a live football game, the others fill its dead time; 0-10: never football). Rule #1 and warrants are untouched. It appears in Settings > How it picks ("Football night", ends "More everything else" / "More football") only with park.enabled and a live football game. Also new `park.show.stop_min` football floors from BRAIN's Kaggle table (505 games, 2024-25; timeout 43 s n=10,728, two-minute warning 78 s n=1,011, quarter end 168 s n=1,041, half 811 s n=505; 1st percentile from the gap's action row). Why: Nick's ruling for football nights.
- 2026-11-11 (PARK-NFL, from Eyes' Prime TNF replay JAX @ BAL, play clock read on the picture): **football `park.timing.stamp_sd_football` 2.0 -> 0.40 s and `park.play_after_s` 15 -> 12 s; new `park.play_after_special_s: 15`.** ESPN's NFL stamp is the snap (snap - stamp mean +0.02 s, sd 0.40, worst +1.39, n=41); scrimmage plays last at most 9.85 s snap to dead ball (n=30); kickoffs, punts, field goals and extra points were not measured and keep 15 s. Football padding drops from about 8 s to about 3 s. One replay: the live spread is unknown until Thursday. Why: measured values replace the cautious stand-ins, as the coordinator asked.
- 2026-11-11 (coordinator, Thursday is on Prime): **new `park.prime.pin_football: true` (it acts only with `park.prime.enabled`, which ships OFF), and the football slider's ends become value weights.** Pinned: a Prime football game on screen at the show's start stays on its stick, is never held paused when left (a held Prime stream can never catch up: the first version piled up 577 s of "too far behind live" in 30 minutes), plays on unseen, is parked again before the way back, and is re-tuned only at the lateness cap; the other stick roams. Replay (fake sticks, 3 nights, football + 3 NBA, lag 44 s): football snaps 0.11 -> 0.19 at slider 50 and 0.26 at 100; all value 0.46 -> 0.52; both judges 0 / 0 / 0; behind live max 143 s. Slider: football's value x 16^((s-50)/50) with no anchor at either end, so at 100 the other games still fill football's long breaks (YouTube TV, + 3 NBA: 0.25 jumps a minute and 2,417 backfilled seconds a night, against 0.04 with the old anchor). Why: re-tuning Prime cost about 100 s per football visit, and "others backfill in" was Nick's word for the end.
- 2026-11-11 (PRIME-PARK, contract kernel K2): **`park.prime.pair_offset_s: 60.0` and `park.prime.stop_match_s: 1.0` move from module constants in `redzone/noggin/park_prime.py` into the config, same values.** pair_offset_s shifts a paused Prime frame's epoch before `decider.sync.clock_sample` (which only returns delays of 2-180 s) and takes it off again, so a K from -58 to +120 s fits; stop_match_s is how close (s) a parked frame's score-bug clock must be to the stoppage's own clock to count as inside it. Why: timing numbers belong in the config (rule K2); decisions unchanged (flag-off hash identical).
- 2026-11-11 (PARK-UNIFY, the coordinator's follow-up list before Thursday's first real-TV run): **new `park.read_guard: false`, `park.reread_in_place: false` and `timing.behind_live_s.prime_buffer: false`, all OFF, every decision unchanged with them off.** `park.read_guard`: the read gesture gets the focus guard the other overlay gestures have (`yttv_seek.front_and_playing` before any key: YouTube TV in front and playing, #356; no committing CENTER on a frame whose focus was never read). `park.reread_in_place`: the show's re-read of a drifted stream on screen puts the knob back (RIGHT once per LEFT) before the commit instead of sending the picture about 30 s back in front of the viewer, and a failed re-read never reloads that picture. `timing.behind_live_s.prime_buffer` (S-9): the fresh-tune buffer's app list says "primevideo" while targets say "prime:", so Prime never matched there; on, both spellings match (`control.app_listed`), which gives Prime tunes up to `max_s` of pause: judged unsafe on the Nov 7 record, so it stays Nick's call.
- 2026-11-11 (PARK-VERIFY-GAME, the coordinator's live guard after the Nov 11 wrong-game finding): **new `park.verify_game: false`, OFF; Thursday's run turns it on.** After every tune PARK reads the words on the picture with the expected game's team names and every other live game's: another game's two teams named = the wrong game (the stream is refused, the show re-picks without that game-and-channel pair for `park.verify_hold_s: 1800` s, 30 min); nothing readable = unknown (the game's K is kept back, so the wide padding, until the picture confirms it; a guessed channel such as Peacock -> NBCSN counts the same). YouTube TV reuses the read gesture's own frame text (no added screenshot); Prime takes one screenshot and one text read. Off: no word is read and every decision is as before (the recorded-night replay is byte-identical).
- 2026-11-11 (PARK-TIMING-CONFIG, the coordinator's padding-config bug before the first real-TV PARK run): **`park.pad_k: 4.0` and the group `park.timing` (`read_sd: 0.29`, `tune_sd: 1.5`, `k_sd_min: 0.5`, `stamp_sd_nba: 0.33`, `stamp_sd_college: 0.5`, `stamp_sd_football: 0.4`, `key_sd: 0.13`, `eyes_sd: 0.3`, `prior_delay: 33.0`, `prior_sd: 12.0`, `live_lag: null`, `k_stale: 1800.0`) enter default_config.yaml at the code's own values.** `TimingParams.from_config` already read them, but the Config dropped them as unknown, so no data/config.yaml could change a game's padding. Same values, so a default config decides exactly as before (the recorded-night replay is byte-identical, also with the timing read from the YAML). New limits: `park.pad_k` 4-20 (never tighter than shipped from a file), `park.timing` 0-3600 s. Why now: the real TV runs at padding 8 s for games with a K until measured whistle pairs exist (coordinator, Nov 11, ledger L87), which is `park.timing.k_sd_min: 1.91`.
- 2026-11-11 (PARK-SPEED, Nick: "all the code times down"; tonight's real-TV hands test measured tune 16.0 s, park 4.2 s, jump 6.9 s): **new `park.fast_steps: false` and `park.steps.fast_poll_s: 0.1`, OFF, every decision unchanged with it off (replay 20261028 byte-identical).** On: a jump returns once the new picture is up, playing, on the new input and silence is read back after the switch (the sound guard's own after-switch read is used instead of a second read queued behind it; the TV's mute, volume, power and input are read in one round; the new stick's playing report and the left stick's pause are read during the flip); the stick left's BACK and read-back run after the jump, the show holds that stream as not ready until they end (`ControllerRig.confirming`), a jump onto it meanwhile is refused with nothing sent, and a new tune or finish ends them before their next key (the place is then dropped). Reads poll every 0.1 s; a read-back reads the stick and its foreground app at once. Bench (calibrated to the TV test): jump 6.9 -> 2.7 s, park from its key 3.7 -> 3.4 s, tune 15.9 -> 15.8 s, jump-to-jump back to back 27.2 -> 22.6 s. Why: the jump and the park sit on the critical path to the next jump; every check (silence, input, focus, Rule #1) stays, only where it waits moves. Real-TV proof is Thursday's.
- 2026-11-11 (SEEN-DIRECTOR, phase 2C "what you have seen" and Director v1, the coordinator's go order): **new `park.seen: false` and `park.director_v1: false`, both OFF, with the `park.director` numbers `big_w: 3.0`, `big_look: 90`, `fresh_w: 1.0`, `stale_ref: 600`, `taste: true`.** `park.seen` tracks, per game, when it was last on screen, the score then, what the feed reported since, a leverage-weighted staleness and the big plays missed (`redzone/noggin/seen.py`; the catch-up engine reads `SeenTracker.missed_since`), and puts "Seen 7 min ago" plus a templated recap on the Show tab (no AI text). `park.director_v1` multiplies what the planner values per game by (a) `big_w` with a close-and-late moment pending, (b) up to 1 + `fresh_w` by staleness, (c) `score.league_weight` over the slate's best; Rule #1, the padding and the warrant are unchanged and each warrant names its goal. The numbers were picked on tuning nights 20261024-26 (more freshness pressure made staleness worse there), not on the 3 report nights. Flag off: every stick command, jump record and say line hashes identical on 20261021/23/28 and the NFL + Prime pinned case. Why: the base for the catch-up engine and the AI director, and the rule-based baseline that director must beat.
- 2026-11-12 (PARK-CONFIG-KEYS, the systems study's config blocker before Thursday): **every park.* setting the code reads is now a key in default_config.yaml, each at the code's own default, so nothing changes until one is set.** New: `park.picture_grade` (PR 432, OFF), `park.picture_check.live` (OFF), `park.catchup` (PR 434, OFF; keys only, the catch-up move still has no caller in the show), the FactsParams numbers at park top level and the rest of `park.show` (ShowParams). Keys: `picture_grade`, `picture_check`, `catchup`, `enabled`, `tool`, `python`, `args`, `live`, `min_gap_s`, `cool_s`, `leave_wait_s`, `join_wait_s`, `replay_dead_s`, `look_ahead_s`, `hot_look_s`, `plan_wait_s`, `max_plays`, `min_plays`, `cap_s`, `min_worth`, `min_value`, `lead_s`, `tail_s`, `possession_s`, `snap_s`, `merge_s`, `seg_max_s`, `big_w`, `lead_w`, `tie_w`, `run_w`, `run_pts`, `max_age_s`, `multi_max_age_s`, `delay_s`, `rewind_rule`, `reach_s`, `seek_base_s`, `seek_per_s`, `seek_gap_s`, `reel`, `reel_lag_s`, `reel_play_s`, `reel_open_s`, `reel_gap_s`, `moments`, `moments_lag_s`, `moments_play_s`, `moments_open_s`, `moments_gap_s`, `high_play_s`, `shot_window_s`, `ghost_s`, `open_lead_s`, `football_lead_s`, `play_clock_s`, `snap_slack_s`, `play_after_s`, `play_after_special_s`, `unseen`, `dead_w`, `stale`, `back_spots`, `roll_same`, `wide_pad`, `wide_prospect`, `confirm`, `hold_off`, `read_sat`, `jump_slack`, `pause_reach`, `hold_ok`, `hold_drift`, `lag_sane`, `lag_read_max`, `lag_early`, `refresh`, `cool`, `tune_retry`, `keep_margin`, `stay_say`, `clip_reach`, `clip_steps_max`, `crunch_look`, `feed_lag`, `poll`, `tune0_code`, `tune0_search`, `my_pref`. A test now fails when the code reads a park key the YAML lacks. The recorded-night replay is byte-identical.
- 2026-11-12 (F-01 CONFIG-TRUTH, WORKPLAN phase 0, the systems study's config blocker): **every dotted config key the app's code reads by a literal name is now a key in default_config.yaml, each at the value the code already fell back to, so no decision changes; the 15-key `flags:` block and `eyes.enabled` leave the YAML because nothing reads them.** Added (24 literal reads, plus the 12 per-league numbers `score.context` reads through an f-string): `decider.fail_cooldown_s: 90`, `decider.safe_auto_resume_s: 600`, `decider.leave.high_leverage_recent_s: 30.0`, `delay.boundary: true`, `delay.scorebug: true`, `delay.timing.app_sigma: null` (off, as absent), `governor.footprint_log: true`, `legal.leave_silent_s: 5.0`, `queue.warn_within_s: 180.0`, `replay.pause_poll_s: 0.4`, `scoring.watch_value: false`, `supervisor.ntfy_server` (the supervisor's own public default), `two_stick.break_p: 0.5`, `timing.behind_live_s.shadow_stay_leave_every_s: 15.0`, the new group `control` (`max_switch_s: 150`, `max_start_switch_s: 75`) and the group `score.context` (`lead_change_cap: 8`, `lead_change_fade_s: 150`, `fresh_flip: 0`, `upset_min_spread: 3`, `upset_full_spread: 10`, `overtime: 4`, and per league `nfl`, `nba`, `nhl`, `mlb` the numbers `wp_leverage`, `lead_change_each`, `upset`, `late_within_6`, `late_one_goal`). Removed: `flags` (`brain_v3`, `hype_v2`, `break_model`, `reasoner`, `watchdog_model`, `reel`, `highlight_jump`, `drive_priority`, `trip_budget`, `nhl_rules`, `tv_flip`, `preload`, `sync_predictor`, `auto_tune`, `fastcast`: `contracts.flags.enabled()` has no production caller, audit R3) and `eyes` (`enabled`: read by nothing; EYES-PACKAGE adds it back with its reader). Why: the Config rejects every setting its YAML lacks, so these could never be set from data/config.yaml or the phone (`governor.footprint_log: false`, the footprint log's documented off switch, was refused), and `flags.*` promised kill switches that switched nothing. Not added: `sync.break_anchor.true_gap_s` and `true_gap_sd_s`, read with an inline fallback inside the frozen grader `redzone/lab/backtest.py` (a YAML default needs a grader change, Nick's call; allow-listed in the test). No existing key changed value; the live data configs set none of the removed keys. `score.context.lead_change_fade_s` gets a 0-1800 limit because the generic `score` limit (100) would have refused its own default. tests/test_config_literal_keys.py fails a literal read whose key the YAML lacks.
- 2026-11-13 (F-01b CONFIG-TRUTH 2, WORKPLAN phase 0; the list PR 443 left open): **every config key the code reads by a COMPUTED name is now a key in default_config.yaml, each at the value the code already fell back to, so no decision changes (106 keys); the nine shipped defaults the Config's own limits refused are legal settings now, by widening the limits and one rule, never by changing a value.** Added, by group: `fill` (`max_age_s`, `montage_s`, `montage_min_clips`, `montage_max_clips`, `montage_min_points`, `montage_max_age_s`, `montage_per_game`, `off_night_s`, `off_wait_s`); `slate` (`leagues`, `whip_max`, `filter_over`, `filter_to`, `block_size`, `hold_s`, `catchup_s`, `anchor_leagues`, `late_narrow`, `return_gap_s`, `payoff`, `leave_after_shot`, `walk_safe_made_s`, `walk_safe_rebound_s`, `bucket_after_s`, `bucket_guard_s`, `bucket_sigmas`, `bucket_floor_nba_s`, `bucket_floor_cbb_s`); `arbiter` (`trigger_order`, `floor_extra`, `decide_last`, `late_narrow`, `visit_budget`, `visit_window_s`, `visit_reserve`, `stay_replay_s`, `repick_max_rank`, `last_game`, `ceiling_per_min`, `brain_margin`, `flip_margin_s`, `catchup_s`, `plan_clip_s`, `class_tie_pts`); `queue` (`tune_s`, `buffer_s`, `start_budget_s`, `hold_apps`, `retry_apps`, `behind_s`, `reach_margin_s`, `evict_margin`, `evict_per_sunk_s`, `tier1_value`, `tier2_value`, `scrub_budget_s`, `banner_retry_s`, `backoff_s`, `up_next`); `supervisor` (`every_s`, `screen_confirm_s`, `screen_frame_gap_s`, `screen_after_fix_s`, `stuck_grace_s`, `idle_s`, `fail_window_s`, `backoff_after`, `backoff_s`, `mute_after`, `app_mute_targets`, `app_mute_failures`, `safe_s`, `safe_resumes_per_hour`, `stale_s`, `stale_hard_s`, `adb_offline_s`, `adb_server_s`, `doubt_s`, `doubt_max_s` null, `finished_hold_s`, `log_every_s`, `max_log_bytes`, `keep_log_bytes`, `max_jsonl_bytes`, `keep_jsonl_lines`, `max_imgcache_bytes`, `keep_imgcache_bytes`, `max_wal_bytes`, `paused_alert_s`, `watchdog_beat_s`, `watchdog_alert_repeat_s`); `labels` (`max_read_gap_s`, `break_min_s`, `stale_s`, `pair_window_s`, `min_gap_s`); `timing.behind_live_s` (`label_step_s`, `label_overlay_wait_s`, `label_open_tries`, `label_key_gap_s`, `label_max_behind_s`, `label_settle_s`); `buffered_jump` (`park_guard_s`, `stuck_s`); `canary.poll_s`; `join.watchdog.max_s`; `decider.entry.conformal.tau.mlb`. Limits widened in redzone/config.py (old limit -> new, value unchanged): `score.nfl.wp_swing.fade_s` 120 (-100..100 -> 0..1800), `score.nba.run_fade_s` 120 (-100..100 -> 0..1800), `decider.wait_for_my_team_s` 1800 (0..1000 -> 0..7200), `tuners.unauthorized_retry_s` 300 (0..10 -> 0..3600), `tuners.launch_timeout_s` 30 (0..10 -> 0..120), `governor.swap_critical_pct` 90 (100..16000 -> 0..100), `replay.montage_age_s` 14400 (0..7200 -> 0..86400); and a list of numbers is a legal setting when its default is one, each number inside the limit: `timing.nerds_shots_s` [11.0, 17.0, 22.0] (was refused as "not a list of names"; limit 0..120). Why: the Config rejects every setting its YAML lacks, so these could never be set from data/config.yaml or the phone, and a default its own limits refuse means the file the app ships cannot be saved back as it is. Not added: the open per-league and per-app maps whose names come from the data (`decider.entry.max_wait_s.<league>`, `decider.switch_cost.<app>`, `decider.crunch.shortest_break_s.<label>`, `brain.<league>`, `reasoner.<league>`), and the two aliases `park.show.resume_lead` and `park.show.step` (read first on purpose, STEP_HOME). No existing key changed value; the flag-off replay of 20261028 (4 games, seed 0) gives a byte-identical decision digest before and after. tests/test_config_computed_keys.py fails a computed read whose key the YAML lacks, a YAML value that is not the code's default (named exceptions only), and a shipped default outside the Config's limits.
- 2026-11-13 (E-01 WIRE CATCH-UP, V2 WORKPLAN row E-01): **the catch-up engine (PR 434) is wired into the app's show, still OFF: `ParkRun.begin` builds `CatchupShow` only when `park.catchup.enabled` is on; new device step `Controller.park_cue` (via `ControllerRig.cu_cue` and `ParkHands.cue`) and two new keys under `park.steps`: `cue_lead_s: 8` and `cue_wait_max_s: 45`.** The cue sends the HIDDEN stick back with the overlay scrub (yttv_seek.enter_buffer), takes its new place only from the label reader's measured behind-live (never the media session after a seek), then parks it on the clip's frame; it refuses with no label reader, past the scrub's reach, on Prime or on the stick on screen, and drops the place after any press that does not end parked. With the app's rig (`cue_on_screen = False`) a package is one clip (a close pair played through counts as two plays under Nick's Nov 11 age rule) and no catch-up needs the replay stick re-cued. Flag off: the 3 reference nights decide byte-identically (gym2_parity digests c723ef83, 9877e86f, 2d353034). Why: a merged feature that cannot be switched on is not a feature (WORKPLAN 4.10); it stays off until E-02 proves the cue on the real TV with Nick (only at the proven 175 s rewind, L118).
- 2026-11-13 (E-01b CATCH-UP FIXES, the E-01 verifier's D3-D7; flag still OFF): **two new keys, `park.catchup.step_timeout_s: 30` and `park.steps.cue_relock_s: 2`; the cue never aims past the measured 175 s rewind (`timing.reach_s`).** A catch-up flip or live join not back within `step_timeout_s` is cancelled and the show stops (where the TV is is unknown); a catch-up flip that is refused ends that catch-up and the show goes on (it no longer reads as an outside input change). `Controller.park_cue` holds the controller lock for the scrub and for the park but not for the wait for the frame; it takes the lock again `cue_relock_s` before the pause key and sends nothing if another step moved the stick meanwhile. The cue's total depth behind live is capped at min(`timing.reach_s` 175, `park.catchup.reach_s`) before the scrub, again after the resume, and on the measured landing. Why: a catch-up must never stop the show for a false reason, never hold the TV's other steps for 45 s, and never go deeper than what was proven on the TV (L118). The age cap is unchanged (Nick's ruling pending, coordinator item D1).
- 2026-11-13 (A-04 DIRECTOR SEAM, V2 WORKPLAN row A-04): **new flag `park.director_v2: false`; with it on, `park_show.Show` uses a director object handed to it (`Show(..., director_v2=True, director_obj=obj)`) instead of building Director v1.** The object's shape is `redzone/noggin/director_protocol.ShowDirector`: exactly what Show uses today (`at`, `now` with each game's `w`, `weigh(t, on, arrs, edges, seen)`, `why(g1, g2, base_why)`); Director v1 satisfies it unchanged; an object without it is refused at construction. Flag off, or on with nothing handed over: Director v1 from its params exactly as before, and a handed-over object is never touched. Nothing in the app hands one over yet (`park_run.py` is under the open E-01B claim), so the live show is unchanged either way. Flag off: the 3 reference nights decide byte-identically (gym2_parity digests c723ef83, 9877e86f, 2d353034). Why: track A's trained director needs one seam in the live loop; the guard, padding and warrant stay outside it.
- 2026-11-13 (A-04 follow-up, verifier note): **`park.director_v2` is inert today: nothing in the live config path reads it and passes it into `Show` (`ParkRun.begin` does not call `director_v2_on`), so setting it in data/config.yaml changes nothing until A-05 or the runner wiring hands `director_v2` and a director object to `Show`.** `Show` turns the seam on only for a literal `True` (as `director_protocol.director_v2_on` reads the config), so a stray truthy value cannot switch it on. Why: say plainly that the flag has no live effect yet, so no one believes the trained director can be switched on from the config.
- 2026-11-13 (A-05 PLANNER UNKNOWN SEAM, V2 WORKPLAN row A-05): **new flag `park.unknown_rate_v2: false`; with it on (a literal True), `park_show.Show(..., unknown_rate_v2=True, unknown=obj)` hands `obj` to its planner as `Planner(unknown=...)` (`bind(arrs, setting)`, `rate(game, last reported second, now)`), wrapped in `park_plan.ClampedUnknownRate`: each rate is held to 0.1x..4x the planner's own guess for that second (`unk_w` x the game's leverage), and a rate that is not a finite number gives the guess itself.** Per-league scaling stays the hook's job. Flag off (or any value but True), or on with nothing handed over: the planner's own guess exactly as before, and a handed-over object is never touched; an object without `bind`/`rate` is refused at construction. The flag is INERT today: nothing in the live config path reads it (`ParkRun.begin` does not call `park_plan.unknown_rate_v2_on` and hands no hook to `Show`), so setting it in data/config.yaml changes nothing until the runner wiring does. Same PR: `Planner._search` is split into named steps with the code moved verbatim (`_frame`, `_options`, `_best_option` = the candidate scoring a learned scorer will replace, `_keep`, `_hold`) plus `Planner.options(t, S)`; a characterization test recorded on the old code (tests/test_park_plan_options.py, 5 digests over 3 hand-made nights and 2 replayed dev nights) passes unchanged. Flag off: the 3 reference nights decide byte-identically (gym2_parity digests c723ef83, 9877e86f, 2d353034). Why: track A's learned value of an unreported second (stage 6's `ModelRate`, A-03) needs one seam into the live planner, held to the clamp stage 6 was measured with, and the option list must be a single replaceable call before any learned scorer touches it.
- 2026-11-13 (F-03 JOB RUNNER, V2 WORKPLAN row F-03): **training jobs run on Colab (through Nick's signed-in Chrome) or Kaggle as a pinned JobSpec (`tools/jobs/spec.py`: full 40-hex commit, seed, step range, manifest hashes, outputs, budget) executed by `tools/jobs/worker.py` in resumable units, and only a `RZ-RESULT` line whose spec, line and output hashes check (`tools/jobs/parse.py`) counts as a result.** The Colab cell (`scripts/job.py cell`) fetches the worker at the pinned commit and checks its sha256; the Kaggle backend is a stub until Nick creates an API token. No config key; nothing in the app imports `tools/jobs`. Why: every later training job (B-03, A-02, B-01) needs one runner that resumes after a kill with the same bytes (local smoke: interrupted == uninterrupted) and stops itself before the session cap; the Colab path itself is not yet exercised.
