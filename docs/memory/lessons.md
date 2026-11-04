# Lessons

Bugs we hit and why, so nobody hits them twice. Newest first. **Add one after every fix**; the
auto-fix brain and the watchdog read this file before acting
([memory hook](runbooks/README.md#memory-hook)).

Each lesson is a `## ` heading plus four lines. `Signals:` holds the log words that point to it
(comma separated); the watchdog matches them against error signatures, so keep them literal.

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

## A rewind landed in a data gap (NBA GS @ DEN, Nov 4)
- **Symptom:** the candidate's one remaining mid-play entry (dev seed 2) was a 70 s rewind, the same with or without tune jitter.
- **Cause:** a substitution at 377 s left was the last event for 238 s of wall time (the play that stopped the game is missing). The tracker opened a dead window on the substitution and closed it 24 s of game clock before the next event, so it looked like a 3.5-minute dead ball. The grader, rightly, can't call that a dead ball.
- **Fix:** `decider.entry.confirmed_dead_only` (candidate on): a lone substitution opens no window, so a rewind falls back to an earlier logged stoppage or the entry waits.
- **Signals:** a rewind of 50 s or more into NBA; a window of kind `substitution` with no stronger kind.

- 2026-11-04 **Replay window measured from the wrong clock.** `Missed.t` is when the feed had the play; Nick's "within 3 minutes" runs from when it aired on the TV, a whole stream delay later (~2 min with the time-shift buffer). The window filter and its urgency ramp used `t`, so they allowed ~1 minute. Now `Missed.aired` (feed time + screen delay) drives both. Check which clock a deadline means before tuning it.
- 2026-11-04 **NBA technicals without "foul" in the type were live play.** ESPN types like "Defensive 3-Seconds Technical" or "Coach Technical" mapped to Stoppage.NONE, so the grader called a leave 72 s into a timeout a mid-play cut (DET @ NY, Oct 27, found by lane 1). `espn.stoppage_for` now maps technical/flagrant to WHISTLE; the app's screen model already treated them as dead.
