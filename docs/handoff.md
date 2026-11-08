# RedZone Everything: handoff

The living handoff for whoever (human or AI) picks this up. The director updates it every
10 minutes while it runs. Read this first, then `docs/status.md` (the project board) and
`docs/architecture.md` (contracts and owners).

## 1. What this is

A self-hosted Mac app (Python/FastAPI/asyncio, launchd service `com.redzone.everything`, port 8778)
that runs a Samsung TV like NFL RedZone across every sport. Two Fire TV sticks over ADB play the
games, and the app switches between them at dead balls. The owner is Nick Matta (UNC student).

## 2. What Nick wants (north star; full list in `docs/context/claude-memory/redzone-north-star.md`)

- **Switching:** instant game-to-game switches, with no Fire TV home screen and no clicking. Cut only
  at dead time (commercials, timeouts, free throws, challenges, injuries) and enter at the restart.
  **0 mid-play cuts or entries.**
- **League order:** NFL > CFB > NBA > NCAA basketball > NHL > MLB. His teams are WSH (Commanders,
  Wizards, Capitals, Nationals) and UNC; teams and bets have their own priorities. Crunch time comes
  first. NHL: power plays, goals and overtime.
- **Catch-up reel:** 5+ clips back to back in 30–60 s, played only in dead time, with timing dialed.
- **Fully automatic:** sync is automatic (no taps, no mic or camera). Never use Peacock (NBC/NBCSN
  come via YouTube TV).
- **AI, "low and insane":** about 1.5–2 GB of RAM total on an 8 GB M2, but very smart.
  - REFLEX: a local distilled student (Qwen3-1.7B, 4B if needed).
  - BRAIN: Claude auto-repair through GitHub `claude-code-action` on his subscription token.
  - A live game reasoner.
- **Release gate:** 95% on locked, unseen data, with no overfitting (time splits, locked test,
  fresh nights, per-slice gates). Anything that misses ships OFF behind a flag.
- **Added Nov 6-7, 2026 (Nick, live; newest wins):**
  - **Many jumps, real autopilot.** "4-5 switches every minute" (Nov 7 12:55), raised to "several jumps across a
    minute (5-10)", "play by play, moment to moment" (20:45). "I don't want forces, I want real RedZone autopilot."
    Sitting through commercials on his team's game is the failure he hates most.
  - **Mechanics first** (21:05): "idc about the decisions rn, I just care about the code". Reach a reliable
    5-10 jumps/min; WHEN 1/min vs 10/min is right comes later from a heavily trained model.
  - **PASS BAR** (22:15, clarified 22:17): **75 jumps in 10 minutes** (7.5/min), and in that window: **0 commercials
    on screen, every shown play in full, never join mid-play, never leave mid-play**. The last is stricter than
    Rule #1's low-leverage leave allowance: leaves only at dead balls.
  - **Replays on the hidden stick:** he loved a catch-up that landed on a big play but hated seeing the rewind. Scrub
    on the HIDDEN stick, flip so it starts ~1-2 s before the snap.
  - **NEXT feature after the pass bar: the drive catch-up story** (21:46, refined 22:05). If we haven't shown a game
    for ~a minute and a big moment is coming (e.g. 2nd & goal at the 5), first tell what happened while we were gone,
    so anyone on the couch can follow: each missed possession by its deciding play (the TD, or the failed 3rd/4th
    down or turnover that ended it, "even if it's just a big third down miss"); skip punts unless the return is big;
    then the current drive's highlights (the 30-yd bomb, the 15-yd run, the missed TD throw); then live for the
    next snap. Alternate the two sticks to skip the dead plays between clips (each parked 1.5 s before its snap),
    and leave the other stick free for the next game when the moment passes. "That's how RedZone does it."
  - **Deploys:** Nick, Nov 7 12:08: "You can deploy whatever" / "You can do whatever you want". 22:35: "the threads are
    good, but just do the work yourself if you can... you're the watcher, peep the code and figure it out".
- **Constraints:** $0 budget (ask before spending). Never enter passwords or tokens. Keep the Mac
  light (8 GB). No disruptive TV tests while he watches. **Stop all Claude work at 88% weekly
  usage** (he keeps 10% for school). Keep ADHD-style replies: lead with the action, short.

## 3. Where everything lives

| Thing | Where |
|---|---|
| Repo | github.com/BouncySlime1215/redzone-everything (private). Base `codex/resume-redzone`, integration `integration/next`, release `integration/release` |
| Mac checkout | `/Users/nick_matta/Documents/Codex/2026-11-02/task-2/redzone` (the service runs from here) |
| Cloud build project | claude.ai Code Projects: "Training model for redzone" (`chan_011rPfhndEhhnAPuwfcdBQK8`). A coordinator runs about 5 owner threads |
| GitHub on claude.ai | connected as **BouncySlime1215** (switched from nmatta1215-svg on Nov 3) |
| Kaggle | account `nickmatta` (nmatta1215@gmail.com), phone verified, 2x T4, 30 GPU-h/week. Datasets: `nickmatta/redzone-distill` (round-1 pack, split .bin parts), `nickmatta/redzone-code` (round-2 code, auto-extracted to `code/`). Notebook: `nickmatta/notebooka466d7f817` |
| Watchdog | launchd `com.redzone.agent` (rules only, `--no-model`); plist in `agent/` |
| Memory snapshot | `docs/context/claude-memory/` (the director's memory files) |

## 4. Hardware facts

- **Fire TV sticks:** 100.110.165.150 on **HDMI1** (CEC physical address 0x1000) and .181 on **HDMI2**
  (0x2000), confirmed by Nick on 2026-11-03 (older notes had the ports the other way round).
- **TV:** Samsung 6 Series 55" that came with the apartment (The Union Chapel Hill, WhiteSky
  Wi-Fi). MAC 8c:79:f5:8e:e2:5f. The IP .151 was never verified.
- **Why network TV control failed:** almost certainly the Tailscale 100.64/10 route overlap or a
  wrong IP. Check `.venv/bin/python -m redzone.network`.
- **Today's switch:** HOME + CEC. It shows the launcher and makes Prime reload (5–50 s). The fix is
  TV-side input switching (`scripts/tv_setup.py`), plus a YouTube TV Multiview probe.
- **The Mac travels to school** (campus network 10.23.x). Then the sticks look offline; they are not dead.

## 5. Current state (updated by the director)

<!-- STATUS:START -->
- **2026-11-07 ~22:45 ET (overseer, newest; older entries below are kept as history).**
  - **Live build:** `claude/buffered-jump` **0b0f8534**, launchd `com.redzone.everything`, port 8778. Deployed
    22:32:45 with the wrapper in §9; its health watch was still open at 22:45, so check `data/deploy/deploy.jsonl`
    for the verdict. Last confirmed-good build: 9f3412d1 (healthy 22:22), and before that 3778a401 (marked good by
    hand 22:11).
  - **Flags live (set via /api/settings, persisted in data/config.yaml):**
    - jump_mode.enabled true; **jump_mode.leagues narrowed to [cfb, nfl] at 22:25 for the buffered test.
      RESTORE [cfb, nfl, nba, nhl, cbb] when the test ends.**
    - buffered_jump enabled (park false, eager true, behind_s 75, min_behind_s 45; leagues nfl, cfb).
    - yttv_seek.enabled; timing.behind_live_s enabled + scrub_entry + label_reader.
    - replay.enabled; delay.edge_prior.enabled (34.9 s); tuners.yttv_local_search.
    - decider.leave.dead_windows [cfb, nfl, nhl, nba, cbb]; quiet_break near_end_clock_s 30; switch_margin 1;
      return_tolerance 0.
  - **Autopilot:** ON, not paused, focus_mode autopilot. Nick is watching live CFB/NBA.
  - **Jumps:**
    - **0 buffered cuts so far.** The 9f3412d1 window 22:22-22:32 counted 0 cuts, held by bj_standby_not_ready
      1292/min and bj_standby_not_buffered 1071/min.
    - Root cause 1, fixed in 0b0f8534: `Engine._game_on` returned None for a channel shared with tomorrow's games
      (FOX, ABC, NBC, ESPN), so the hidden stick was never scrubbed.
    - After the fix, ESPN scrubbed **77.7 s behind with a 2.7 s error** (22:36).
    - **Root cause 2, OPEN:** after every restart `decider.current` is None (`now` None), so jump mode holds
      `screen_game_unknown` (514 ticks by 22:40). The fix belongs in `Decider._jump`
      (redzone/decider/decider.py:2557-2577) or the engine restore. Re-learn the screen game from
      control.on_target / TV readback, or let a jump onto a ready buffered standby run when the screen game is
      unknown and the screen is at a dead ball or a break.
  - **Mac:** 8 GB, on AC power since about 19:50. Memory starvation at 22:00-22:09 (7.7 GB swapped, load 18) froze
    the API and rolled back the e5876ea0 deploy. That was not the code. Chrome, ChatGPT/Codex and Claude are the big
    users of memory.
- 2026-11-03 ~4:15 PM ET. Live service: this MORNING's code (not deployed yet).
- **integration/next:** green. Merged so far:
  - trip budget (#1), NHL (#3/#22/#27), NFL drive priority (#2), sync (#8/#25), CI (#7)
  - pinned eval data (#14), traces (#10), network awareness (#12), platform (#15/#20/#23/#24)
  - instant switching (#5, off until TV setup), deploy (#9/#29), governor (#6)
  - Brain v3 (#26, acting parts off), reasoner plumbing (#17, off), e2e night (#19/#28)
  - tonight checklist (#31), multiview path (#32, off), fastcast check (#33)
  - Decision core #21 merging.
- **Kaggle round 1, Version 2:** running since ~2:47 PM. The teacher (Qwen3-32B-AWQ) was at 4,352 of
  81,372 labels (~45–65/min), then the students, grading, and `out.zip`. ETA ~1 AM.
  - Version 1 failed: `run_teacher.py` read the `.gz` as text. Fixed in the notebook and in the repo.
- **Kaggle round 2:** ready on branch `kaggle-inputs` (millions of moments built from ESPN inside
  Kaggle). It starts after V2 finishes and needs the notebook fix for the auto-extracted `code/`.
- **Usage:** 44% weekly (Nov 3, 4 PM). The cap pauses the project at 88%.
- **Waiting on Nick tonight (about 30 min):** `docs/tonight-checklist.md`: wall power for the
  sticks, quit Tailscale, TV settings (Anynet+, IP Remote), the deploy command, `tv_setup.py`
  (press Allow), the Multiview probe.
<!-- STATUS:END -->

## 6. Next steps, in order

**Nov 7 ~22:45 ET (overseer), newest first. The older list below is kept as history.**

1. **Hit the pass bar: 75 jumps in 10 min**, with 0 commercials, every play in full, no mid-play join or leave.
   1. Fix `screen_game_unknown` after a restart (§5, root cause 2), test it, deploy it, then confirm in
      `data/jump_status.jsonl` that the holds move to bj_* codes.
   2. Measure with `.venv/bin/python scripts/buffered_jump_try.py watch --port 8778`. Stop it with
      `perl -e 'alarm 610; exec @ARGV' ...`, because macOS has no `timeout`.
   3. Add NBA and CBB to buffered mode, using made baskets, fouls and timeouts as dead balls. Restore
      jump_mode.leagues.
   4. When the screen game drops out of the feed, take a free cut at the next safe point.
   5. Stop a queued replay from starving the standby (standby_plan "yields to a queued replay").
   6. Fix the stuck command lock (supervisor `stuck_lock`: lock held 150-156 s).
   7. Make `buffered_jump_try.py watch` print PASS/FAIL per 10-min window against Nick's bar (asked of the thread).
2. **Replays only for fresh plays** (under about 120 s old, at most 8 LEFTs): catch-ups of older plays are all
   refused. Test PLAY-on-flip of a parked hidden stick when Nick isn't watching.
3. **Then the drive catch-up story** (§2, Nov 7 additions).
4. Leftovers: control.execute's no-stick path logs a phantom success (`Outcome(True, ..., dry_run=True)`);
   ESPN/NBC/ABC showed `tunable False` after "espn couldn't open the link" x2 (22:19); Prime start failure and Prime
   seek (#390, #383); an ntfy topic for Mac alerts and a daily canary install (Nick).

**Older list (Nov 3):**

1. Cut `integration/release` (95% gate report attached, flags off for misses) → Nick deploys tonight.
2. Kaggle: when V2 finishes, download `out.zip`, grade it against the gates, then start round 2.
3. Decision core → highlight jump + auto-tune on top. Next come data sources tier A (1–11), the
   reasoning add-ons (game memory, handbook, scouting), and the auto-fix BRAIN (Nick adds
   `CLAUDE_CODE_OAUTH_TOKEN` himself).
4. Data sources tier B (12–31).

## 7. How the director works (so a new session can take over)

- **Every 10 minutes, in one pass:** usage guard → PRs/CI → Kaggle log → project threads → one
  concise directive if something's off → update §5 of this file. Only message Nick when he must act.
- **Project rules:**
  - max 5 working threads; paused threads stay paused until their dependencies land
  - every PR targets `integration/next`; merge small, CI green, scorecards no worse
  - 95% gates on held-out data
- **Kaggle uploads:** the Chrome upload tool caps files at 10 MB (split into .bin parts), and
  Kaggle auto-extracts .zip/.tar.gz.

## 8. What got done Nov 5-7, 2026 (overseer; sources in §11)

The overseer is the Claude Code session on the Mac. It runs the stick tests, deploys, and reports through the
coordinator chat in the claude.ai project "Training model for redzone". The project's threads write most of the code.

**Nov 5 (TNF night)**
- No switching all night. Cause 1: the overseer's overnight pause was never resumed. Cause 2, replay-proven: the
  leave gate blocked every switch (NBA TV delay prior 42±15, no NBA break floors, stale ESPN corrections). Autopsy:
  lane2/yttv-no-switch-autopsy-2026-11-05.md; fix PRs #357/#358, test fixes #356.
- RC6.3 was not deployed: the Mac was on battery and sleeping, so the tests timed out.

**Nov 6: the YouTube TV seek breakthrough**
- A pause does NOT hold on YouTube TV: it snaps back near live within ~20-30 s. The overlay scrub DOES hold.
  - The player overlay is opened with CENTER; each LEFT moves the knob 15 s; CENTER commits.
  - The overlay's time label is the exact playhead.
  - Holds measured: 20 min, 31 min, then 3 h; each label read adds ~7.5-8 s.
  - 6/6 seeks held across ESPNU, ACCN and TNT; the HDMI1 stick works too.
- Prime CAN be buffered by a pause (60 s pause → 67.7 s behind, real-time timecode). A Prime seek is still untested.
  The POR@LAL Prime start failed ("Shared via Amazon Family" detail page).
- The Scores & stats panel and Key plays exist, but are too coarse for a 1-2 s delay (score changes arrive in ~30 s
  lumps).

**Nov 7 morning: seek mechanics locked down** (all on the real sticks, retuning to the live edge before every join)
- Lost press: at the live edge neither the LEFT nor the RIGHT probe moves the knob. #403 made the RIGHT probe the
  default and sends n-1 LEFTs (12f405ab): RIGHT 8/8 landed mean 63.0 vs predicted 64.2; LEFT 6/6.
- Across channels: 7 channels, 26 joins, knob moved 30 s on 26/26; landed 61.2-67.5. #404 reprobe: 12/12.
- Depth fit: **landed = 16.8·k + 28.5 s** for k real LEFTs (k=1 45 s, k=2 62, k=4 95, k=6 129). The minimum join is
  ~45 s behind live.
- Drift: 52bd3a73 PASS (max drift 0.8 s, 0 re-enters).
- **YouTube TV live-edge delay = 34.9 s ±0.4** on 11 channels, including the FOX 50, CBS 17, ABC 11 and WRAL locals
  (tool: edge_delay.py). The app's old prior (42 s) was too high, so the edge prior flag now uses 34.9.
- **Locals:** the fox/cbs codes in channels.json are Sunday Ticket mirrors. Locals now tune by search ("FOX 50",
  "CBS 17", "ABC 11", WRAL) in ~11-15 s. During a live game the channel page goes Top picks → On now → a "How would
  you like to begin watching?" sheet → Join live. A false positive in the purchase guard on a "Rent or buy" tile is
  fixed (4b6a88e9 / df7c560d / 592016bb).
- Deployed: build B 8841ebd0 (12:11), then the hotfix with FOX working (12:50).

**Nov 7 afternoon (live CFB, Nick watching)**
- "4 min until the next switch? ... watching commercials" led to API knob changes: switch_margin 1,
  return_tolerance 0, my_team 10, and others.
- A watcher agent ran 12:48-14:16. Findings:
  - The Mac was in **clamshell sleep on battery** from 13:09; ~56 of 88 min were dead.
  - 22/24 auto switches failed at the cut ("no safe moment to cut").
  - A replay retry loop ran (BYU 8x, UNC 7x).
  - Summary: logs/watcher-summary in §11.
- The FOX deploy rolled back because benign warnings counted as "worse"; the deploy wrapper now ignores them (§9).
- A YAML bug: `decider.leave` was defined twice, so dead_windows/tune_s were silently dropped. Fixed in
  claude/leave-dead-windows 322b71b7, with a test.
- The full test suite plus a lab job pushed the load to 18-21 on the battery Mac and starved the app.
- **Jump mode** shipped (rotation at dead balls, standby re-tunes):
  - First successful jump 18:59:44, ALA@LSU → BOIS@CSU, preloaded, 4.0 s.
  - On power at the live edge it peaked at **~0.5 jumps/min**. Limits: standby readiness, and the play-by-play feed
    lags the TV (feed lag 14-109 s vs a 35 s TV delay), so the leave gate can't see dead balls in time.
- **Hidden-stick replay park:** passed at ddcead10 (ok true, to_snap_s 1.5, paused, 49.5 s). The old MEDIA_REWIND
  replay path looped at 2 Hz and was removed (203eacb4).

**Nov 7 evening: buffered jumps** (the plan to get to 5-10/min)
- Idea: run BOTH sticks ~60-90 s behind live, so the play-by-play already holds every snap and whistle about to air
  and the editor can cut play by play.
- Deployed 3778a401 (21:36) → 0 cuts: no stick was ever scrubbed (scrubs only on a fresh tune).
- e5876ea0 bootstrap fix → rolled back by Mac memory starvation, not by the code.
- 9f3412d1 (wait out the post-tune banner, 22 s): healthy, **first app scrub 95.2 s behind**; still 0 cuts.
- **0b0f8534 (overseer's own fix):** a channel shared with tomorrow's games maps to its one live game (4 tests, 186
  targeted pass). ESPN then scrubbed 77.7 s behind (error 2.7 s).
- Remaining blocker: `screen_game_unknown` after a restart (§5).

## 9. How to run things (the overseer's toolkit)

- **State:** `curl -s localhost:8778/api/state` (now, mode.decision, health.sticks[].role / standby_for / ready).
  The stick roles swap; never assume HDMI1 is on screen.
- **Settings:** `POST /api/settings {"changes": {nested dict}}`. Dotted keys are rejected. Undo with
  `/api/settings/rollback`.
- **Actions:** `POST /api/action` with pause | resume | watch (+ game_id; value "force" = a manual force, which Nick
  doesn't want) | release | focus | peek.
- **Jump logs:**
  - data/jumps.jsonl: jump rows, plus event=standby rows.
  - data/jump_status.jsonl: a 30 s heartbeat with hold codes.
  - data/buffer.jsonl: scrubbed / scrub_failed rows.
  - data/service.log.
- **Try scripts:** scripts/jump_try.py, scripts/buffered_jump_try.py (on [--eager] | watch | report | off), and
  scripts/replay_park_try.py `<stick> <play_ago_s> [--no-play]`. Pass `--port 8778` unless the branch defaults it.
- **Deploy:** scripts/deploy.py (busy() live-game gate, health watch, auto-rollback). During live games the overseer
  runs the wrapper at `reports/overseer-handoff-2026-11-07/logs/deploy_wrapper.py`: it bypasses busy(), adds ignore
  regexes for benign warnings, and sets switch_fail_slack 80. Run it from the repo root as
  `.venv/bin/python - < wrapper`, in the background.
  - Never restart the app during a health watch: a restart counts as a crash.
  - After an interrupted watch, run `deploy.py confirm` (marks HEAD good) or `deploy.py rollback`.
- **Before a deploy:** run targeted tests only, in a worktree, one process, `nice -n 15`.
- **Stick tests:** the overseer's tools are copied to `reports/overseer-handoff-2026-11-07/logs/stick-test-tools/`:
  edge_delay.py, locals_check.py, local_join.py, live_rounds.sh, chan_rounds.sh, depth_rounds.sh, wd_steps.py.
  Always retune to the live edge before a join (LabelProbe anchors at the probe).

## 10. Lessons from Nov 5-7 (also see docs/memory/lessons.md)

- Check that autopilot is resumed after every test (a forgotten pause cost the whole Nov 5 night).
- **The Mac:**
  - It must be on AC with the lid open. Clamshell sleep on battery kills polling and drops both sticks.
  - Use `pmset -g batt`, never `pmset -g log`: it hangs and hogs the CPU.
  - 8 GB: no full test suite during games. Watch swap: past ~7 GB swapped, the API freezes and deploys roll back.
- **Deploys:** benign warnings ("no safe moment to cut", fetch failed, Fastcast) are not regressions: pass ignore
  regexes. A manual restart during the watch triggers a rollback.
- **YouTube TV:**
  - The post-tune LIVE/title banner sits up to ~30 s and swallows keys: wait ~22 s before the first overlay key.
  - Neither probe moves the knob at the live edge.
- **Channels:** a network maps to several games on the board (today's live game plus tomorrow's). Any target → game
  lookup must prefer the LIVE game (fixed in `_game_on_target` at 19:10 and in `_game_on` at 0b0f8534).
- **Jump mode:** the rotation's standby must be a game the editor can actually cut to (the same leagues as buffered
  mode), or the standby is never usable.
- I wrongly blamed Tailscale once; the real cause was sleep and memory. Prove a cause before reporting it.

## 11. Where the logs are (sources for this file)

| What | Where |
|---|---|
| Overseer running log, Nov 5 → Nov 7 22:25 | `reports/overseer-handoff-2026-11-07/logs/overseer-log-nov5-nov7.md` (local only, not committed: has partial stick IPs) |
| Watcher agent, Nov 7 12:48-14:16 | `reports/overseer-handoff-2026-11-07/logs/watcher-summary-nov7-1248-1416.md` |
| Tails of buffer / jumps / jump_status / deploy journal at handoff | `reports/overseer-handoff-2026-11-07/logs/*.jsonl` |
| Live app data | `data/service.log`, `data/jumps.jsonl`, `data/jump_status.jsonl`, `data/buffer.jsonl`, `data/deploy/deploy.jsonl`, `data/supervisor.jsonl` |
| Full session transcripts (Claude Code) | `~/.claude/projects/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/*.jsonl` (db4845a1 is Nov 5-7, ~200 MB; 2d6d4265 is Nov 4) |
| Overseer memory | `~/.claude/projects/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/memory/` (nick-wants-many-switches.md holds every Nov 7 goal) |
| Night reports | `docs/nights/2026-11-0{4,5,6}.md`, `docs/livelab-2026-11-04.md` |
| Decisions / lessons | `docs/memory/decisions.md`, `docs/memory/lessons.md` |
| Coordinator chat | claude.ai Code project "Training model for redzone"; the buffered-jumps thread is "Build buffered jumps from play-by-play" |
| Open PRs touching this | #422 buffered-jump-next, #420 jump-break-refill, #419 replay-park-to-snap, #399 espnplus-via-yttv, #395 join-watchdog, #392 exp610, #390 prime-start, #383 prime-seek-probe, #382 yttv-keyplays |

## 12. Prompt for the next agent

The full prompt is in `reports/overseer-handoff-2026-11-07/NEXT_AGENT_PROMPT.md`. In short: you are the RedZone
overseer on Nick's Mac. Read CLAUDE.md, this file (§2 Nov 7 goals, §5 newest status, §6 newest list) and the
memory dir. Then fix `screen_game_unknown`, deploy, and measure jumps against the 75-in-10-min bar, fixing code
yourself rather than waiting on threads.
