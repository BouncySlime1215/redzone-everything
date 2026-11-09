# Lessons

Bugs we hit and why, so nobody hits them twice. Newest first. **Add one after every fix**; the
auto-fix brain and the watchdog read this file before acting
([memory hook](runbooks/README.md#memory-hook)).

Each lesson is a `## ` heading plus four lines: `Symptom:`, `Cause:`, `Fix:`, `Signals:`. `Signals:` holds the log
words that point to it (comma separated); the watchdog matches them against error signatures, so keep them literal.
New lessons also add `Repro:` (the command and its output before the fix) and `Bisected-to:` (the sha and subject,
or why it wasn't bisected), per [debugging.md](runbooks/debugging.md), and name the regression test in `Fix:` as
`Test: tests/...`.

## A fixed blend weight left the NHL break odds miscalibrated
- **Symptom:** after #51's log-odds fix, NHL break-forecast ECE was still 0.095 on held-out nights (brain alone 0.041).
- **Cause:** the NHL rules say 0.70 for every owed TV timeout but only 18 % of those plays reach a break within a minute, so any fixed weight on the rules pulls the odds up; in the NFL it is the other way round (rules 0.75 -> observed 0.78, brain 0.18).
- **Fix:** fit (a, b, c) per league on held-out nights only (`scripts/eval_break_blend.py`, `redzone/lab/breakblend.py`); ship a fit only when it beats the brain alone on the mean and the worst fold. Test: tests/test_break_blend.py.
- **Signals:** break_weight, break_blend, ECE, break_soon_p, calibration

## Your team's missed TD lost every replay slot to other games' last chances
- **Symptom:** release gate `nfl_stale` (10 s stale feed): your-team big plays seen 95.2 % (20/21). The TD was queued for a replay and never shown.
- **Cause:** `Decider._best_replay` ranked any urgent play (in its last `replay.urgent_s`) above everything else, so with a stale feed's extra misses there was always another game's last chance ahead of your team's TD until its window closed.
- **Fix:** rank your team's missed plays first (`replay.yours_first`), then earliest deadline, then value.
- **Signals:** your_team_big_seen, nfl_stale, replay queue, yours_first

## Kaggle auto-extracts uploaded archives
- **Symptom:** round-2 notebook can't find `rz_code.tar.gz`; the files sit under `code/` instead.
- **Cause:** Kaggle unpacks .zip and .tar.gz datasets on upload.
- **Fix:** notebooks look for the extracted folder first, the archive second.
- **Signals:** rz_code.tar.gz, No such file, kaggle/input

## Chrome upload tool caps files at 10 MB
- **Symptom:** a dataset upload from the browser silently fails or truncates.
- **Cause:** the Chrome upload tool's 10 MB per-file limit.
- **Fix:** split big files into `.bin` parts (`split -b 9m`) and join them in the notebook.
- **Signals:** upload, 10 MB, .bin

## Kaggle teacher read a .gz as text
- **Symptom:** Kaggle round 1 Version 1 died at 255 s.
- **Cause:** `run_teacher.py` opened `teacher_requests.jsonl.gz` with `open()`.
- **Fix:** open `.gz` with `gzip.open(..., "rt")`; the CPU smoke test (`tests/test_kaggle_e2e.py`) now runs the notebook in CI.
- **Signals:** UnicodeDecodeError, gzip, .gz, invalid start byte

## Brain trained on locked nights (leakage)
- **Symptom:** brain scores looked too good on the release nights.
- **Cause:** the NFL brain trained on 2025 (locked) and the NHL break heads on locked archive nights.
- **Fix:** retrain on nights before `test_start` only (`agent/distill/split.py`); a CI leakage check; round 2 excludes brain features until the retrain lands.
- **Signals:** leakage, locked, test_start

## A broken config fell back to defaults (dry run)
- **Symptom:** the app kept running but stopped controlling the TV.
- **Cause:** an unreadable `data/config.yaml` fell back to the built-in defaults, and the defaults are dry run.
- **Fix:** #24 restores the newest `config_history` version instead, and sets a corrupt `redzone.db` aside (never deletes it).
- **Signals:** config.yaml, YAMLError, dry run, OperationalError, database disk image is malformed

## Tailscale steals the home routes
- **Symptom:** sticks and TV unreachable from the Mac while at home; network TV control "failed".
- **Cause:** Tailscale's 100.64/10 routes overlap the home LAN 100.110.165.x.
- **Fix:** quit Tailscale, or `python -m redzone.network --fix`. `redzone.network` flags it.
- **Signals:** utun, No route to host, route_issue, 100.110.165

## Sticks "offline" because the Mac is at school
- **Symptom:** both sticks look offline during the day.
- **Cause:** the MacBook travels to campus (10.23.x); the sticks are fine.
- **Fix:** check the Mac's network before blaming stick power. The watchdog sets `away` and stays quiet.
- **Signals:** away, 10.23., Connection refused, unable to connect

## deploy.py refused the Mac's checkout
- **Symptom:** the deploy stopped on "working tree not clean".
- **Cause:** untracked local data (`data/`, `agent/model/`, `.venv-agent/`, plans) counted as changes.
- **Fix:** #34: refuse only when *tracked* files are modified. The Mac repo is `/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone`, not `~/redzone-everything`.
- **Signals:** working tree, not clean, untracked

## HOME + CEC input switch is slow
- **Symptom:** 5–50 s switches showing the Fire TV launcher; Prime reloads.
- **Cause:** HOME is the only proven switch on these sticks, and it kills Prime's player.
- **Fix:** TV-side input switching (`scripts/tv_setup.py`) and the YouTube TV Multiview path (flag off until probed).
- **Signals:** HOME, launcher, Prime, switch slow

## Cloud sessions: GitHub GraphQL and parallel git are blocked
- **Symptom:** `gh pr view`/`gh pr list` return HTTP 403; a second clone returns 429.
- **Cause:** the session proxy allows only the REST API and 2 concurrent git transfers per repo.
- **Fix:** use `gh api repos/BouncySlime1215/redzone-everything/...`; one clone at a time.
- **Signals:** GraphQL, 403, 429, too many concurrent

## The e2e night read the Mac it ran on
- **Symptom:** the deploy's tests failed `two_stick_recovers` (two_stick_at_end false) on the Mac; CI and every cloud rerun passed, even with thread work slowed at random.
- **Cause:** the night pinned the clock, sticks and TV but not the resource governor. Its watchdog read the real `ps`, saw the live RedZone app over the 1200 MB cap and restarted the night's own engine every few minutes (23 restarts in a repro). Battery or load (load avg ~14 during the deploy) also changed OCR pacing. The night's virtual clock makes CPU speed itself irrelevant.
- **Fix:** `pin_host()` in redzone/lab/night/run.py makes every governor in the night see a calm machine with no RedZone processes. The end check holds a two-stick gap still open at the end to the same 420 s as every other episode.
- **Signals:** passes in CI, fails on the Mac, deploy tests, watchdog restart, governor, ps

## A SmartThings outage made each flip wait ~72 s
- **Symptom:** (fault drill `st_outage`, flag off) with the cloud timing out, one flip tried discover, the command and 8 readback reads, each waiting out its own 8 s timeout, before falling back to HOME.
- **Cause:** every SmartThings call was independent: no breaker, the readback loop kept reading a dead cloud, and a 429 counted as an ordinary failure.
- **Fix:** `hardening.smartthings` (redzone/tv.py): breaker shared by every call, readback stops when it opens, Retry-After honoured, auth failures blocked until the OAuth file changes. Off until a live night.
- **Signals:** SmartThings, timeout, 429, 503, flip slow, HOME fallback, tv_auth

## launchd job dies with EX_CONFIG (78) under ~/Documents
- **Symptom:** the login service (and the watchdog agent) never start; `launchctl print` shows last exit 78.
- **Cause:** macOS privacy (TCC) closes ~/Documents to launchd itself, so a `WorkingDirectory` or log path inside the checkout fails before Python runs.
- **Fix:** no WorkingDirectory; `python -c` changes into the checkout and runs the script; launchd logs in ~/Library/Logs/redzone; Full Disk Access for the venv's python3.12.
- **Signals:** EX_CONFIG, exit 78, launchd, WorkingDirectory, Documents, TCC

## A new switch path was invisible to the cut grader
- **Symptom:** Prime multiview tile switches (path `prime_tile`) would never show up in data/supervisor_cuts.jsonl, so their mid-play entries couldn't be counted.
- **Cause:** `trace.build` only sets `cut_at` for a flip or for path `single`; any other in-place path got `cut_at` None and the supervisor skips those.
- **Fix:** `trace.IN_PLACE` lists the paths that change the stick on screen (`single`, `prime_tile`); a new in-place path must be added there. Test: tests/test_prime_multiview.py.
- **Signals:** new Outcome.path, cut_at None, ungraded cuts, supervisor_cuts.jsonl missing rows
## The scorecard's 8 s feed hid a 35-50 s real one
- **Symptom:** every scorecard showed 0 mid-play cuts, but graded with the feed lag the flight recorder measured (`--real-lag`), NHL cut 5 plays mid-play and NBA entries landed mid-play 26 times.
- **Cause:** the backtest delivered every play 8 s after it happened; ESPN play-by-play really reaches us 35-50 s after the whistle (median, [docs/timing.md](../timing.md)), longer than a YouTube TV channel's 22-26 s delay. The gate's lag estimate capped each sample at 45 s, under the NHL median, so it believed its data covered the screen.
- **Fix:** samples cap at 180 s; a game with fewer than 3 samples uses the measured league p90 (`FEED_LAG_PRIOR_S` in redzone/decider/screen.py). Feed realism knobs from measurements, and never cap an estimate below what was measured.
- **Signals:** feed lag, lag_est, real-lag, data running behind your screen, NHL mid-play cut

## A rebooted stick on screen was never given its game back
- **Symptom:** (fault drill `reboot_visible`, `hardening.sticks` off) the stick on screen drops off adb and comes back on the Fire TV home; for the whole 60 s drill nothing restores it.
- **Cause:** `Controller._forget` cleared `on_target` for the visible stick, and both repairers (the visible-stick watchdog and the supervisor's `screen_not_game` rule) need `on_target` to know what to restore. Health only logged `screen_unknown_silent`.
- **Fix:** with `hardening.sticks`, keep the target and check the stick at once: still playing → nothing; not playing → the usual resume/re-link on that stick (10 s in the drill). The watchdog also reads launchd's last exit code while the app is down and, for 78/126/127, says what to fix instead of looping on restarts.
- **Signals:** reboot, adb drop, Fire TV home, on_target None, screen_unknown_silent, exit 78, restart loop

## A replayed moment could see the end of the game
- **Symptom:** none on a scorecard yet; found by research 05 §3. A replay at play N carried the summary's FINAL leader board, and the e2e night replayed 2 games from LOCKED test nights.
- **Cause:** `espn.parse_summary(upto=N)` filled `leaders` from the whole-game boxscore; the night's GAMES were picked before the splits existed.
- **Fix:** leaders are filled only when `upto is None`; the night uses train-night games (NHL 401892578, NBA 401909860). `scripts/leak_check.py` (in CI) fails on locked games in the night, end-of-game fields in a replay, model stamps past the test start and expired KNOWN_ERRORS.
- **Signals:** a replayed GameState with leaders or W-L records, a fixture id on a test night, `leak_check` FAIL

## "Go now" on a preloaded game still waited a second it had already proven
- **Symptom:** (controls drill) "Watch this" + "Go now" on a game already playing on the hidden stick took 4.1 s to the picture, not the ~3 s a flip costs.
- **Cause:** the flip asks for two playback reads >= FLIP_STEADY_S apart with no state change. The memo of the first read was only filled during a switch, so every flip started the 1 s clock from zero even though verify_standby and the preload's own confirming read had just seen the same steady stream.
- **Fix:** verify_standby and a finished live preload feed the same memo (`_note_steady`), so a flip needs one more read, never less evidence. Preloaded go-now median 4.1 -> 3.0 s.
- **Signals:** FLIP_STEADY_S, `_steady_playing`, preloaded flip ~1 s slower than flip + settle

## A retried "Watch this" was a "Go now", and a Skip expired before the TV left
- **Symptom:** (controls drill) a lost answer resent by the phone would run the tap again: a second "Watch this" means "Go now" (cuts at once, even mid-play), a second Skip skips two games. Separately, a Skip during a hockey power play waited ~5 min for a stoppage, the 5-minute skip expired first and the TV never left (seed 4).
- **Cause:** /api/action had no tap identity. `engine.skipped` counted from the tap, not from when the TV left.
- **Fix:** every phone tap carries an id (redzone/commands.py): a repeat id gets the first answer back, never a second run; the phone retries a lost answer with the same id. The skip's five minutes start once the TV is off the skipped game. Drill with taps lost and duplicated on purpose: 83 taps, 65 retried, 0 ran more or less than once; skips that never left 2 -> 0.
- **Signals:** double tap, retry, duplicate, "Go now" mid-play, skip ignored, skip_stuck, not_once

## "Go now" on the game on screen, tapped mid-switch, was ignored
- **Symptom:** (controls drill, `mid_flip` press, 4 of 4) "Watch this" + "Go now" on the game you're leaving, tapped while the flip away from it is landing: the phone said "Holding on X", the TV landed on the other game and stayed there.
- **Cause:** `_hold` saw `decider.current == gid` (current only moves once the flip lands) and treated it as already there; `_hold_check` then cleared "force" for the same reason, so the way back waited for a dead ball.
- **Fix:** playbook=BTN-05: a hold of the current game while a switch away from it is pending queues the way back (it runs as soon as the flip lands; a flip is never cancelled halfway) and keeps "force". Drill: 4 of 4 land, ~10 s.
- **Signals:** "Holding on" while the TV shows another game, mid_flip dropped, current vs pending target

- 2026-11-04: **CI now checks playbook coverage and posts a RAM/CPU line.** Tests claim entries with
  `@pytest.mark.playbook("ID")`. `scripts/playbook_coverage.py --junit` fails CI only when an entry marked
  `done` has no passing test, or a test names an unknown id, so the 79 entries can be built top-down.
  `scripts/perf_probe.py` times the real `Engine.decide()` on 8 synthetic live games (300 ticks): it was
  p50 1.0 ms, p99 5.3 ms, 56 MB peak RSS at 1f5e751. Engine needs a running event loop, so the probe runs
  inside `asyncio.run`.

## The TV-04 undo found nothing to go back to
- **Symptom:** (night lab, cec_steal, hardening.tv on) seed 1: `_flip_to` refused the way back; seed 2: the undo
  read the stick we left as playing ESPN, waited 4.5 s for the lock, then found it on FOX.
- **Cause:** a stale `_exit_check` from the last switch still gated `_flip_to`; and once the readback adopted the
  rebooted stick as main, the stick we left became "hidden", so the planner preloaded another game over it.
- **Fix:** the undo clears `_exit_check` around its own flip, and `preload()` refuses while the undo is pending.
- **Signals:** playbook=TV-04, "was re-tuned meanwhile", TV jumped by itself, CEC steal, rebooted stick

- 2026-11-04: **The merge of #83 and #76 dropped the North Star candidate column without any error.** Both
  PRs edited the same loop in scripts/northstar.py, and git merged them cleanly. #83's line reassigned
  `results[split]` after #76 had added `["candidate"]` to it, so `--profile candidate` scored the profile and
  then threw it away. Now the drill is passed to both tables and a test covers the column. Lesson: when two PRs
  edit the same loop, read the merged loop, not only the conflict list.
- 2026-11-04 (playbooks SAFE-05): "has every feed been heard from since the clock jump?" compared timestamps,
  so after a BACKWARD jump every old stamp already looked later than the jump and the hold cleared at once
  (night lab: a replay started 8 s after a -30 s jump). Compare each timeline's fresh_at with its value at
  the jump instead. Also: a fault that widens the sync filter does nothing while a tap-calibrated base is
  tighter (DelayBook only overlays a filter that is better), so the night fault widens the Delay itself.
- 2026-11-04: **Prime multiview gate scored a 0.0 s switch as slow.** `pmv.gate` read `float(took or 1e9)`, so
  a rounded 0.0 became 1e9. Found by the stick-free rehearsal (`prime_multiview_probe.py --fake`), where tile
  switches are instant. Fix: only a missing time counts as slow. Use `is None`, never `or`, on numbers that can be 0.
- 2026-11-04: **Clairvoyance replaced a crunch pre-tune with a big-play one.** While the leave gate held us on a
  quiet game waiting for crunch time elsewhere (`want=<crunch game>`), GAME-04 turned the stay into its own wait
  with `want=<big-play game>`, so the hidden stick would tune the wrong game. Dev scorecards didn't move (the gate
  was the binding constraint), found by tallying decisions made while a crunch game sat elsewhere. Fix: a stay that
  already wants a crunch game is left alone (crunch first). Check every layer that rewrites `want`.

## "Still watching?" graded as missed on a stick nobody could reach
- **Symptom:** (e2e night, seed 2, #86) `still_watching_cleared` failed: the prompt shown at 5:46:11 PM was cleared at 5:48:01, 110 s later.
- **Cause:** the grader, not the app. A `stick_drop` took that stick off adb 10 s after the prompt, for 87 s; no key reaches a dropped stick. The reload of that game cleared it 14 s after it came back. The sim also logged no `prompt_cleared` when a new link replaced the player, only on CENTER.
- **Fix:** the 60 s count from when the stick is reachable again (`clock_from` in the report), the clear must be on the same stick, and a relink logs `prompt_cleared (relink)`.
- **Signals:** still_watching_cleared, prompt, stick_drop, STK-04, seed 2

## The Linux test job hit its 20-minute limit
- **Symptom:** (#108, Nov 4) `test (3.12)` was cancelled at 54% after 20 min; 3.13 finished in 18 min.
- **Cause:** not one test. integration/next itself already took 19m50s on 3.12 (12:12 run): the e2e night, the playbook night (#86) and the SmartThings night take ~3 min each, and the drill night adds ~1 min. GitHub runners are ~1.6x slower than the cloud box (11.5 min there).
- **Fix:** timeout-minutes 30 on the test job. The next step is to run the replayed nights in their own job, in parallel.
- **Signals:** "The operation was canceled", test job cancelled, timeout-minutes
- 2026-11-04: **A sim with zero delay error hid a deterministic mid-play entry.** The backtest's screen ran exactly
  the delay the app believed, so a 2 s "urgent" landing always looked safe. With a real tune's 0-6 s late landing,
  one NHL entry was mid-play on every seed. Fix: thin-margin landings cover a one-sided landing lag, and "ended"
  switches never take the urgent path. Grade entry changes with `--tune-jitter 6` on several seeds.
- 2026-11-04, the Kaggle factory's first run failed every CBB season with KeyError <League.CBB>: the oracle (self-play labels) indexes sportsgrade.PLAY_LEN / RESUME_LEAD by the replay's league, and those were plain dicts of pro leagues; is_big / is_crunch also compared league == League.NBA and hard-coded period 4, so CBB would have had zero big or crunch plays even without the crash. Fix: ByLeague tables (college reads its pro row) and the last regulation period from models.REGULATION (CBB: the 2nd half). tests/test_kaggle_factory_jobs.py runs season_job for every league in PLAN offline.
- 2026-11-04: **"PLAYING" on Fire OS can be a stale report.** Live lab: with HDMI-CEC on, every flip paused the
  stick we left and it never resumed, and after backgrounding media_session kept a stale state=3 with
  active=false. Our readback grepped no `active=` line, so a frozen stick read as playing. Fix (STK-05): grep and
  parse `active=`, require active and speed>0 in playback_ready, and verify_standby re-tunes a paused or stale
  hidden stick at once. Night-lab fault `stick_paused` (mode stale / pause).
  - **Signals:** standby "playing" but the picture behind live after a flip, state=3 with speed but no new `updated`
- 2026-11-04: **The live-test collector read traces.jsonl in a format it never had.** data/traces.jsonl is one
  TraceEvent per line (switch_id, stage, at, detail), but collect_live_test looked for flat decided_at /
  picture_at rows, so press-to-TV and picture-within-3s would have come back empty from every real night. Its test
  had used hand-made flat rows. Fix: fold the events per switch_id; the test now uses the real event shape.
  - **Signals:** press_samples 0 with presses in the log; check collectors against a real data dir, not fixtures

## FastCast never logged a row on the Mac (Nov 4)
- **Symptom:** no `espn_fastcast` rows in any recording (Nov 2-4), and data/fastcast_check.json said "stopped: only 0 plays over 0 games in 4 h". The service log has no FastCast line at all: no connect, no warning.
- **Cause:** (confirmed) the live check's 4 h budget ran on wall time from boot. The service restarted at 00:03 ET with no live games, so the check spent its 4 h on an empty slate and switched FastCast off for good. **Not yet explained:** the Nov 3 19:25-23:45 run had live games, yet the client never logged a connect or an error.
- **Fix:** the budget counts only time with live games subscribed (`FastcastCheck.watching`). Every minute, the file carries the client's own status (task state, wanted topics, connected, updates, last error), the engine logs when FastCast starts and how many games it watches, and a client task that dies is logged at ERROR. The next live night's file says where it stops.
- **Signals:** fastcast_check.json `result: stopped` with `games: 0`; no `espn_fastcast` rows in recordings/*/plays.jsonl.

## A rewind landed in a data gap (NBA GS @ DEN, Nov 4)
- **Symptom:** the candidate's one remaining mid-play entry (dev seed 2) was a 70 s rewind, the same with or without tune jitter.
- **Cause:** a substitution at 377 s left was the last event for 238 s of wall time (the play that stopped the game is missing). The tracker opened a dead window on the substitution and closed it 24 s of game clock before the next event, so it looked like a 3.5-minute dead ball. The grader, rightly, can't call that a dead ball.
- **Fix:** `decider.entry.confirmed_dead_only` (candidate on): a lone substitution opens no window, so a rewind falls back to an earlier logged stoppage or the entry waits.
- **Signals:** a rewind of 50 s or more into NBA; a window of kind `substitution` with no stronger kind.

- 2026-11-04 **Replay window measured from the wrong clock.** `Missed.t` is when the feed had the play; Nick's "within 3 minutes" runs from when it aired on the TV, a whole stream delay later (~2 min with the time-shift buffer). The window filter and its urgency ramp used `t`, so they allowed ~1 minute. Now `Missed.aired` (feed time + screen delay) drives both. Check which clock a deadline means before tuning it.
- 2026-11-04 **NBA technicals without "foul" in the type were live play.** ESPN types like "Defensive 3-Seconds Technical" or "Coach Technical" mapped to Stoppage.NONE, so the grader called a leave 72 s into a timeout a mid-play cut (DET @ NY, Oct 27, found by lane 1). `espn.stoppage_for` now maps technical/flagrant to WHISTLE; the app's screen model already treated them as dead.

- 2026-11-04 **The sims trusted the rewind to land where the plan said.** `backtest` set an entry's offset to `rewind_s` exactly, so 0 mid-play entries looked proven while the stick stepped 15 s (not 10), swallowed the first press and stalled 2-4 s. With the real seek, today's code made 32-37 mid-play entries per dev seed in NFL and NHL. Model the hardware's actual response (`--real-seek`, `contracts.switching.YTTV_SEEK`) and grade from the commands the app really sends, not from what it meant.
- 2026-11-04: **Two sticks are not one tune speed.** Live lab: hidden-stick tune to PLAYING p50 2.33 s / p95 3.06 s on
  the 4K Plus vs 3.74 / 5.50 s on the 4K 2023, but every tune went into one shared median. The roles swap on every
  flip, so there is no free "use the fast stick" choice; what we can do is price the stick that will tune. Tunes now
  also book under `<app>@<model>`; tune_estimate uses it after 3, deadline_cost its p90 after 8. The decider's
  long-run switch_cost stays the shared figure, so the config doesn't flap with whichever stick is hidden.
- 2026-11-04: **A tuner's hard-coded holdout went stale when the splits were re-pinned.** #16's auto-tuner held
  out only the old section-7 slice (NFL `eval.py --n 2`, NBA/NHL from Oct 29), while eval-data-v1's LOCKED split
  has 19 NFL nights. Salvaging it as-is would have tuned on locked. Fix: hold out `splits.nights_for(..., "test")`
  as well. Any tool that excludes locked nights reads them from redzone/lab/splits.py, never from its own list.
- 2026-11-04: **The night's first switch was always a cold tune.** Before any game is live the standby planner
  has nothing to pre-position, and the pre-game checks leave the hidden stick on whichever game they checked
  last. Warm-up (warmup.pregame) puts the likeliest first pick there from T-10 min. Listed start times run
  early, so the window lasts until a game goes live (night lab: games went live 15 min after the listing).
  Also: Tonight rebuilds its plan every minute, so any state kept in the plan dict must be carried over.
- 2026-11-04: **YouTube TV's playback position is a per-tune clock, not the channel's timeline.** Every fresh tune
  starts near 46,790 s. The sync filter kept the "channel origin" across tunes, so a re-tune's reads were
  rejected and the channel flagged uncertain for 5 min; the sims read positions off a channel origin and so
  knew each tune's unknown landing for free (NFL big live ~5 points too good). Check what a device field
  means on two tunes before modelling it.
- 2026-11-04: **A hold's pause overshoots when a switch holds the lock.** `_hold` sleeps B s without the lock,
  then waits for it to press play: a switch on the other stick in between stretched a 60 s hold to 81.7 s and a
  90 s one to 111.6 s (night lab, seed 1). The recorded `held` was right, the target wasn't. The STK-06 watchdog
  now catches and rebuilds it; resuming without waiting behind a whole switch would fix the cause.
  - **Signals:** "buffer: ... held N s behind live" with N well over timing.behind_live_s
- 2026-11-04 (lane 3, #136 review by lane 4): calibration.flip_prior took the measured flip max as the landing deadline's upper bound, so one quiet night could LOWER it below the shipped 2.58 s and let a later slow flip land mid-play. The bound is now max(shipped prior, tonight, every accepted night): calibration may raise it, never lower it.
- 2026-11-04: **A time-shift hold's resume queued behind a switch's lock and overshot.** `_hold` paused,
  slept B s, then waited for the control lock to press PLAY; a switch waiting on a safe moment held it,
  so a 60 s hold came out 81.7 s (90 -> 111.6 s) in the night lab. Fix: the resume waits at most
  HOLD_RESUME_LOCK_S (1 s), then plays a stick nobody touched since the pause (a token that any _tune on
  that stick clears); `held` is only written if the token survived. Timed waits must not sit behind locks.
- 2026-11-04: `_held_for` added `self.held` and the caller added `base_s` too, so a watchdog top-up (STK-06)
  booked the earlier hold twice. `_held_for` now returns only this pause; the caller adds what the stream
  already ran behind.
- 2026-11-04: `hold_overreport_s` 1.5 (book a hold as the wall span less 1.5 s) fought `hold_cal_s` -1.5 (a
  rebuild pauses 1.5 s short): the STK-06 watchdog booked every short rebuild as ~0 s and rebuilt the same
  2.9 s gap forever on the night lab. The fallback now books the span as is (0); the stick-clock path measures.
- 2026-11-04 (lane 1, factory): a season with no data wrote its `done` marker into a folder that only a
  season WITH rows had created, so the first no-data season of a league crashed the job (found by the NHL
  api-web test). Create the folder before writing any marker.
- 2026-11-04 (lane 2, #132): **A standby that a failed switch tuned was never held.** A switch that tunes the
  hidden stick itself (path "tuned") and then finds no safe moment leaves it at the live edge with held=0;
  only preloads hold, and the STK-06 watchdog skipped held=0 by design. Now buffer_error reads an unheld
  standby whose buffer is wanted, with no hold or preload under way, as 0 s behind, so the watchdog holds it.
- 2026-11-04 (lane 3, #167, found by lane 2): **A fault at a fixed time can land inside the recovery it tests.** The
  STK-06 drift fault at t=4700 fell inside the rebuild hold that the unheld-standby detection starts (with #132), so no
  drift row was ever written. buffer_lost now takes `wait_s` and hits the first held, playing standby after `at`; the
  test reads the drift error relative to the hold's last booked value (a rebuild books 88.6 of 90 s).
- 2026-11-04: **A screen we forgot is a screen nobody watches.** Night lab seed 2: the stick on screen came back
  from an adb drop, `_forget` (hardening.sticks off) cleared on_target, and the during-a-switch screen check
  returned on "no target", so a "Still watching?" sat 110 s (limit 60). Now a forgotten screen still gets a
  prompt-only check (CENTER only on a prompt read from a screenshot): cleared at 46 s, 2/2 reruns pass.
  Seed 1 alone (CI) never showed it; run a second seed before calling a night clean.
- 2026-11-04 (one brain step 2, #168): `from redzone import crunch` in demo/engine.py was shadowed by a local `def crunch(r)` in the same function scope, so the demo card crashed with "'function' object has no attribute 'row'". Unit tests never reach the demo summary; only CI's scorecard job did. Fix: import the module under a name no local uses (`crunch as crunch_table`), and run `python -m redzone.demo 20261101 --summary --json` before pushing anything that touches demo/.
- **Nov 4, crunch block lifted under an OT winner (lane 2).** A game the feed ends in OT drops out of the candidates while its last plays are still a stream delay from the screen, so `replay.crunch_block` saw no crunch and a catch-up reel ran over the OT goal (NHL dev night 20261027: 6 crunch plays on replay). Fix: `replay.crunch_block_tail` keeps the block until mu + 2 sigma + a poll after the game was last seen in crunch.
- 2026-11-04 (RC6): the Mac deploy's test step failed on tests/test_citymap.py: docs/citymap_tags.json still tagged
  modules "Mid-play cuts + entries (per 100 landings)", but the nightly northstar table (700e4d4, `[skip ci]`)
  regenerated docs/northstar.md with northstar.py's renamed line "Mid-play entries (per 100 landings)". The rename
  had landed earlier, and the tags only broke once the table was regenerated. A `[skip ci]` commit can still break
  the suite, so run the full suite on the exact tree you cut, not on the PRs before the nightly merge.
- 2026-11-04 (RC6 cut): after merging a base commit that was only docs (the [skip ci] nightly table) I ran only the targeted tests. That base commit renamed a North Star line, and `test_citymap` failed on the Mac's deploy test step. The deploy stopped there and nothing switched. **Fix:** after any base merge, run the full suite again before handing an RC SHA to lane 4. A docs-only commit can still break a test that reads docs.
- 2026-11-05 (world model pilot, plan A leagues): the pilot cut train/valid/test on the pooled nights of all leagues. Once Retrosheet (ends 2024) and NBA cdn (ends 2025) sat side by side, every test night was NBA and NBA had no train rows: NBA's bigram baseline scored 0.0. **Fix:** split each league on its own nights (`split()` per league) and grade `test_by_league`. A time split must be per league when the leagues' date ranges differ.
- 2026-11-04 (RC6.1): the deploy health watch rolled RC6.1 back twice on "N sports feeds failing vs 0 before",
  though nothing was down. merge.py benches a source for a game when its copy regresses against a faster feed,
  and RC6.1's NBA/NHL feeds run ahead of ESPN, so ESPN got benched. engine._source_health reported a bench as
  ok:false, and deploy.py's Health.of counted every ok:false as a failing feed. Fix (RC7): ok = reachable and
  fresh, benched reported on its own, and deploy.py's source_failing() ignores a bench on a fresh source in both
  the old and the new shape. A health watch must compare like with like across versions.
- **2026-11-05: a phone pick's way back lost the hidden stick to a guess.** The standby planner ranked the game you just
  left as "best" (+10), so any "moment" guess outranked it by class and took the hidden stick; Prime then cost a ~30 s
  re-tune on the tap back (11 of 16 live presses, Nov 4). Fix: `two_stick.hand_back_s`. Drill: `redzone.lab.night.presses:pingpong`.
- 2026-11-05 (integrator): RC6.2's NBA league set passed on the honest harness but lost your-team crunch live 31/31 → 17/31
  on the live-sync harness (`eval_sports --resync`, which models the Kalman sync filter that live runs). The cause was
  the time-shift buffer key, `timing.behind_live_s.enabled`. The rule now: grade every league set on BOTH harnesses
  before it ships (Test Factory F4), and a set that is worse on either one doesn't promote.
- **2026-11-05: a forced "Go now" has to be told apart from a mid-play entry.** Nick allows the second tap to cut at
  once; without a mark, every forced press counted against never-join-mid-play. Only the switch whose `may_cut` went
  through on `Engine._forced_go` (held game, `hold.force`) carries `trace.forced`; a single tap that waits for the
  dead ball, and every autopilot switch, never does.
- 2026-11-05 (lane 2, EXP-208): the low-leverage leave gate (`decider.leave.low_leverage`) promised "never your
  team's game" but checked `personal >= hold_personal`, and garbage time zeroes your team's personal boost, so a
  decided WSH game could be left mid-play. It also keyed on `league.base`, so "nba" switched it on for CBB. Fixed with
  explicit `Scored.mine` / `Scored.high_leverage` flags (the grader's CLOSE_LATE table, kept equal by a test) and
  the full league. Lesson: a floor must be checked by what it protects (team membership, leverage), never by a
  score that other features can lower.
- **2026-11-05: "Watch this" on a game with no channel retried every 0.5 s.** Live Nov 4 (RC6.1, 10:04 PM ET): 13
  failed "you locked this game" switches in 6 s ("no channel for this game") until another tap. The fail cooldown is
  keyed by tuner target, and there was none. `_hold` now refuses a game whose channel is known to be untunable, once.

- **Nov 5 2026 (Data A #259):** CI's test jobs had no pyarrow, so every play-table shard test failed with "Unable to find a usable engine". Fix: `requirements-ci.txt` (requirements.txt + pandas + pyarrow) for CI test jobs only; the Mac keeps requirements.txt. Data-crew tests that write parquet rely on it; don't importorskip them away.

### The lock label was our own rule (EXP-086 CBB, Nov 5)
- **Symptom:** the CBB fast head called 96.7% of blind lock moments; the flag said leak_suspect, and lane 3 first read it as a false alarm.
- **Cause:** `y` = a big or crunch play within H s, and crunch (plus NBA/CBB big = clutch buckets) is defined by period, clock and margin, which the model also sees. Clock-only AUC 0.996 on NBA test nights.
- **Fix:** grade lock calls on `y_out` (outcome_label.py: lead change/catch or a win-probability swing), checked by scripts/label_leak_check.py. A label is only a label if clock-only AUC < 0.95, read on train/valid nights, never on locked (lane 3's first read used locked nights by mistake).
- **Signals:** head ≈ clock-only AUC, lift < 0.01, near-perfect lock accuracy, CBB, NBA
- **Nov 5 2026 (lane 4, sync program):** the Mac deploy runs the test suite next to the live sticks, and nothing stopped a test from shelling out to the real adb (Homebrew's /opt/homebrew/bin/adb exists there, not on Linux CI), so a stray test could press keys on the TV. Fix: `tests/conftest.py _no_real_devices` makes the real adb fail as "missing" (what CI sees) and blocks real SmartThings hosts; `tests/fake_adb.py` scripts and httpx MockTransports still work (`tests/test_no_real_devices.py`).
- 2026-11-05 (integrator, #298 review): **a switch's timing lives in two places in the backtest.** `SwitchLog.leave_dt` /
  `arrive_dt` move the landing cutgrade sees, but `watching` (what every on-screen line reads through `_on_screen_game`)
  is appended at the decision time. Anything that delays the cut (the pause cue today, EXP-803's defer) must move
  both, or big live / crunch live / dead air credit the destination before the TV shows it. The pause cue's gap is
  pre-existing (seconds, not tens of seconds); fix it under a grader freeze with a before/after.

- **Nov 5 2026 (lane 4, live-log collection):** `collect_live_test.py` masked RFC1918 LAN addresses but not the sticks' Tailscale 100.64.0.0/10 addresses, so the shared copies leaked them (lane 2 masked them by hand). Fix: the redactor also masks 100.64/10 (`ts.<last octet>`), Tailscale's fd7a:115c:a1e0::/48 IPv6 and 169.254/16 link-local; public addresses stay.
- 2026-11-05 (lane 2, from lane 4's read of the Nov 4-5 probe logs): sync_probe.stall_s compared the stick clock with the position without checking that both reports were playing at normal speed. A pause or backgrounded app counted its whole pause as a stall (up to 11,000 s), and a seek forward or live-edge jump came out negative (-44,000 s summed), so stalls_per_min and the probe's link-quality scale were polluted. Now it returns None unless both reports are playing at ~1x, after a forward jump (< -1 s), or across more than 10 minutes.
- 2026-11-05 (integrator): **a pytest exit code piped through `| tail` is tail's, not pytest's.** Batch 2 (ced1dd03)
  went to integration/next with 4 failing tests because the suite wrapper read `exit 0`. Run the suite to a log and
  check `$?` (or grep "failed") before any push. Two of the four were stale expectations after NHL's EXP-303 package
  went ON; one was the warm-up e2e (the night opens on NHL, whose timed start already pre-tunes the first pick, so the
  test now holds NHL's start untimed in both arms via the night lab's new `--set league_overrides.<lg>.<key>`); the
  last, test_night_leaves_repo_data_alone, only fails under `pytest -n` (another worker writes the shared data/).

- **Nov 5 2026 (lane 1, windowed oracle #322):** the first buffer sweep solved every window with an 8 s feed lag
  (a knobs default) while the measured p90 is 62-75 s, so "window % of full" read 88-100% for a feed we don't
  have; NBA leave/land also peek 26 s past a cell (grading.RESUME_LEAD), which the window didn't reserve, and the
  window was floored at blk+1 so a too-slow feed still planned on unrevealed steps (T2/T3 testers, #328). Fix:
  per-league measured lag (p90 default, primary p50 as a second set), the look-ahead reserved in window_steps, no
  switches without a revealed window, first pick waits for a dead ball, night-bootstrap CIs, and the oracle moved
  out of the frozen grader file into redzone/lab/oracle_window.py. Lesson: an oracle's information set must come
  from measured numbers and from everything the grader itself reads, and a second tester should re-derive both.
- **2026-11-05, YTTV night with no switches (RC6.2):** for 70+ minutes the leave gate on an NBA game read "play
  is live", even at timeouts and the quarter break. With a wide delay (42 ± 15 s) and the measured feed lag,
  `covered` can never be true for basketball, and NBA had no break floor except halftime. Separately, the DB couldn't
  show the gate at halftime, because decisions were logged only when the reason text changed. Fix: a measured NBA
  end-of-quarter floor behind `decider.leave.break_floor_s` (EXP-212, off), and log every gate change. Lesson: a
  safety gate that is shut for a structural reason looks exactly like a game that's live, so log the gate itself.
  Autopsy: /mnt/project-files/lane2/yttv-no-switch-autopsy-2026-11-05.md.
- **2026-11-06 Last season's NHL penalties read as live hockey (v3 autopsy).** eval-data-v3 jumped NHL dev mid-play
  0 -> 344/3340, all on 2025-26 nights. ESPN typed every 2025-26 NHL penalty "Penalty", which the hardcoded list of
  infractions in `espn.stoppage_for` didn't hold, and 2026-27 spellings ("Cross checking", "Delaying Game - ...")
  slipped past it too. Fix: match any penalty type (344 -> 3; v1 stays 0). Lesson: when a new season or window jumps
  a metric, diff the feed's type vocabulary first. Report: /mnt/project-files/test-writers/v3-autopsy.md.
- 2026-11-06: NBA real-lag replays had drawn ESPN's feed lag (p50 45 s) since nba_cdn became NBA's primary, because
  `LagSampler` quietly falls back to ESPN when a source has no profile. That made every NBA buffer read pessimistic
  (B60 big seen 12.8 % instead of 66.7 %). Fix: the measured nba_cdn profile. Lesson: when a sim falls back to a
  default input, say so in the output; a silent fallback reads as a real result.
- 2026-11-06 (EXP-214): the catch-up return stack outlived its moment. A Q1 break visit onto WSH left LAL on the stack
  for two hours, and the generic "moment's over, back to" return fired from WSH's Q4 (up 13, so not "late and
  close"). Separately `_crunch_go` returned True whenever the current game wasn't in crunch, so your team late and
  close lost to any neutral crunch. Lesson: every path that leaves a game (return, crunch hop, chase) has to ask the
  same "is this your team's endgame?" question; guarding only the "better game" path left three doors open.
- 2026-11-07 (edge model): delay_doubt's reset popped the channel's Delay, and with it `behind` (what the tune is held
  behind live). On a stick scrubbed 60 s back that turns the believed delay 60 s too short: every cut would plan the
  dead ball a minute early. Lesson: a "reset to safe defaults" must keep what was MEASURED about this tune (its
  behind_live), and only throw away what was guessed.
- 2026-11-07 (jump mode, overseer 20:01:50): a jump decided on a READY standby still went out "tuned" (30 s): at the
  flip `_execute_two` needs the hidden stick steady-PLAYING (FLIP_STEADY_WAIT_S 3 s) and otherwise re-tunes it on the
  spot. The readiness check in the decider and the one at the flip were different. Fix: a jump submits with
  `preloaded_only`, which refuses ("standby not ready", screen unchanged) instead of re-tuning.
- 2026-11-08 (jump mode): a long timeout blocked jumps because the leave gate only trusts a break with no resume in
  the data while the data "covers" the screen or the break is under its usual length. A slow feed fails "covered",
  so a timeout past its floor read as "play on screen". The feed is ~35 s ahead of the screen: if the fresh feed is
  still in the break, the screen is too (once the play before it has aired). The jump gate uses that now.
- 2026-11-07 (buffered jumps, overseer 22:30): 0 buffered cuts for an hour because `Engine._game_on(target, False)`
  returned None when several games map to one channel (FOX: IOWA@NU live + Sunday's NFL). behind_of/scrub_of then
  said 0/off and the hidden stick was never scrubbed; only single-game SEC Network worked. `_game_on_target` had the
  same bug fixed at 19:10. Lesson: every target -> game lookup must prefer the one LIVE game; grep for siblings when
  fixing one.
- 2026-11-07 (Mac, 22:00-22:09): 7.7 GB swapped on the 8 GB Mac froze the API (3 misses) and rolled back a good
  deploy (e5876ea0); the rollback itself was logged "rollback_failed" because the app was still frozen. Lesson: check
  swap/load before blaming a build; `deploy.py confirm` clears an interrupted watch.
- 2026-11-07 (overseer tooling): macOS has no `timeout`; `perl -e 'alarm N; exec @ARGV' cmd` instead.
- 2026-11-07 (session guard, sync hardening item 4): **"no evidence" read as "fine" in every session check we had.**
  An adb drop, an unread playback state or an unreadable screen left a scrubbed stick's buffer looking valid, the
  post-tune LIVE banner (Nov 6) passed as the overlay, and a reboot hidden inside an adb drop was never noticed because
  the drop wiped the last uptime. Fix: `session_guard.classify` gives every unknown the buffer state "unknown" (never
  "valid"), keeps the last read uptime / process id across a drop, and holds on any dialog-shaped text it has no rule
  for. Runbook: runbooks/stick-session.md.
- 2026-11-07 (Mac health): on Nov 5-6 the app went 66-145 min without a single switch row while up to 10 games were live; the supervisor logged stuck command locks and stale feeds every few minutes but nothing reached the phone, because each fault on its own was 'only a warning'. Lesson: alert on the OUTCOME (no switch in 20 min with games on autopilot), not only on each cause. Signals: no_switches, stuck_lock, stale_feed.

## A join that "worked" was not where we said (Nov 5-7)
- **Symptom:** six different buffer entries reported success while the stick was somewhere else (2026-11-07 join watchdog).
- **Cause:** media_session read flat after a seek (75 s "hold" VOID), a plain pause snapped back to live 20-30 s after resume, the overlay held open for 37.6 s turned an asked 90 into 130.8, a same-channel deep link left a stick behind (asked 90, read 23), a blind CENTER committed an uncounted step, and the label reset at a program boundary.
- **Fix:** a position is only what the label / Prime timecode READ after the entry; anything else is "unknown", never "buffered", and a re-read is rare, budgeted and only on the hidden stick (each YouTube TV read adds 7.5 s). Runbook: [join-watchdog.md](runbooks/join-watchdog.md).
- **Signals:** watchdog off_target, drift_reenter, drift_snapped, drift_unknown

## The watchdog counted its own read as a miss (Nick's stick test, Nov 7 11:35Z)
- **Symptom:** hidden stick (HDMI2), NBA TV, target 75: state unknown, fallback live_failed, planned 71.7, read 79.52; the stick kept
  playing NBA TV (79 s behind); data/joins.jsonl had no rows.
- **Cause:** the label probe's reading includes its own ~7.5 s frozen overlay, so a good join read 7.8 s "off"; the test
  script never identify()'d the stick, so every relink tune failed at once ("youtubetv app not found"); the run was
  from a scratch worktree, so its rows went to that worktree's data/joins.jsonl.
- **Fix:** join.yttv_read_s in join.plan_join (predicted includes the read; old rows corrected in join_calib), identify() and printed relink
  steps in scripts/watchdog_try.py, RZ_JOINS_LOG for the log path. Runbook: [join-watchdog.md](runbooks/join-watchdog.md).
- **Signals:** live_failed, youtubetv app not found, off_pred_s near 7.5
- 2026-11-07: Live mode with every stick offline (the Mac asleep) returned a dry-run style success from
  control._execute ("no stick connected", ok=True, 0 s) and set on_target: the switch log filled with phantom
  "start ALA@LSU ok=True took 0 s" rows that inflated switch counts. Only mode.dry_run is a simulated success now;
  no stick is a refused switch (ok=False, screen_unchanged, not the target's fault).
- 2026-11-08: A snap-parked standby was "ready" (standby_ready) but "steady playing" refused it, and the PLAY that fixes
  that sat after the check: every jump to a parked standby was refused. **A paused-on-purpose standby is a preloaded path
  of its own**, and the wait after PLAY must be short: buffered.park_lead_s plans the picture rolling from PLAY to the flip.
- 2026-11-08: A closure that polls for 25 s must not keep the tick's snapshot (jump may_cut read cands from decision time):
  rebuild from live state on each poll, as _entry_valid does.
- 2026-11-08: A check at decision time isn't a check at the flip: the flip can come 25 s later, so `_entry_valid` re-plans every timed league, not only football (P1-REVIEW-01). And a wait with no end is a freeze: every "wait for data" needs a timer that hands the pick to the next choice, not a go-live cap (P1-REVIEW-05).
