---
id: D-OWN
title: Own-data inventory of RedZone data/ (what the system recorded itself, 2 to 8 Nov 2026)
date: 2026-11-09
stream: data
author: data job 16 (Claude)
status: draft
supersedes: []
sources: 30
verified: 29
affects: []
gate: []
---

# D-OWN: what RedZone has recorded about itself

Method: read-only, `nice -n 15`, Python `-I` and `sqlite3 -readonly`. Field names, counts and date ranges only. No device list, token, credential or secret-named file was opened. No image was opened. No row, address or account detail is quoted. `data/` is 1.4 GB, almost all of it public-feed archives, images and parquet. Nights run 2 to 8 Nov 2026 (7 calendar days, but the full instrumented set is 4 to 7 Nov).

Two corrections to the brief:
- **`data/transitions.jsonl` does not exist.** `data/buffer.jsonl` exists but is a stick-buffer event log (37 rows), not a play-transition log.
- The planner note `docs/agents/planner/2026-11-09-brain-vs-code.md` is not in the main checkout. I read a snapshot copy in the scratchpad. It says the brain "never sees his taps, thumbs or overrides" (self-taught rule, decision 2026-11-04). Re-check the snapshot is current before relying on it.

## 1. Bottom line

- **Unique and real:** (a) what the stick showed at each moment, as a dead-ball or live-play call from the real picture (`supervisor_cuts`, 64 rows); (b) the measured delay between the live feed and the TV picture (`sync`, `sync_anchors`, `sync_probe`, `calibrations`); (c) timing of every switch command to confirmed picture (`switches` table, 2,317 rows); (d) a per-second shadow log of stay/leave decisions with competing-game values (`buffer_shadow`, 2,469 rows, one night).
- **Zero rows:** votes (`feedback`, 0), labels (`labels`, 0), bets (0). There is no thumbs, rating or "that was wrong" label anywhere.
- **Not recorded at all:** what Nick saw on a US broadcast as an ad break, on a real channel. Ad breaks are only inferable from the feed ("break" state, `break_visit` cuts, 2 rows).
- **Volume is small.** 4 fully instrumented nights. 64 picture-graded cuts. 52 sync anchors. Every figure is too small for training and good for evaluation.

## 2. Source table

Row counts at the time of reading. "Nights" = distinct local dates.

| # | Source (under `data/`) | Records | Dates | Rows | Nights | Schema (field names) |
|---|---|---|---|---|---|---|
| 1 | `redzone.db` table `switches` | every switch command and its outcome | 2-8 Nov | 2,317 (1,179 dry run; 890 replay) | 7 | ts, frm, to_game, target, kind, reason, event_ts, cmd_ts, confirmed_ts, ok, dry_run, detail |
| 2 | `redzone.db` `decisions` | stay/switch/replay choice per tick | 2-8 Nov | 8,896 | 7 | ts, action, game, kind, state, gate, reason |
| 3 | `redzone.db` `snapshots` | game state sampled | 2-8 Nov | 18,303 (154 games) | 7 | ts, game, league, score, moment, reason, state |
| 4 | `redzone.db` `calibrations` | stick delay, boundary, screen and tap timing | 2-7 Nov | 11 | 4 | ts, target, sample, mu, sigma, kind |
| 5 | `redzone.db` `feedback`, `labels`, `bets` | votes, labels, bets | none | 0, 0, 0 | 0 | feedback: ts, game, vote, note, context. labels: ts, label, game, signals |
| 6 | `live_nights/<night>/switches.jsonl` | night-scoped copy of 1 | 4-7 Nov | 61 / 288 / 906 / 931 | 4 | as table 1, plus frm, detail dict |
| 7 | `supervisor_cuts.jsonl` (+ per-night copies) | each cut: what was on screen when leaving and entering | 4-8 Nov | 64 | 5 | ts, id, from, from_label, to, to_label, kind, path, leave, leave_why, enter, enter_why, enter_after_dead_s, enter_before_play_s, forced |
| 8 | `supervisor_moments.jsonl` | big moments the system tracked (goal, td, fg, turnover, three) and whether seen | 3-8 Nov | 624 (42 seen) | 6 | ts, game, matchup, league, label, kind, seen, on_screen, dry_run |
| 9 | `supervisor.jsonl` | health invariants, fixes, limits | 3-8 Nov | 1,233 | 6 | ts, kind, detail, rule, key, code, component, action, ok, severity, what, reason, path, rows, foreground, state |
| 10 | `traces.jsonl` (+ per-night) | per-switch stage timeline | 4-8 Nov | 4,529 | 5 | switch_id, stage, at, component, detail |
| 11 | `sync.jsonl` (+ per-night) | delay estimator output | 4-8 Nov | 3,588 (3,240 position, 278 tune, 35 seek, 27 shift) | 5 | t, target, kind, mu, sd, synced, uncertain, behind, used, position, pos_sd, prior, prior_sd, delta, shift_sd, z, err, stick |
| 12 | `recordings/<night>/sync_anchors.jsonl` | feed event vs picture event pairs (the stick delay ground truth) | 5-8 Nov | 52 (all `burst` type) | 4 | ts, target, stick, sample_kind, game, edge_sd, feed_wall, z, used, delay_mu, delay_sd, feed_type |
| 13 | `recordings/<night>/sync_probe.jsonl` and `live_nights/*/sync_probe.jsonl` | stick player position, stall and UI state probes | 4-8 Nov | about 87,000 recordings + 47,000 live_nights | 5 | t, host, role, target, app, ms, stall_s, state_changed, cond, uptime_ms, pos_now_s, wifi, log, ui, bursts |
| 14 | `recordings/<night>/plays.jsonl` | feed play events with arrival lag | 2-8 Nov | 79,000 (11.7k / 7.3k / 15.7k / 10.2k / 15.4k / 17.2k / 1.1k) | 7 | t, league, game, play, type, wall, lag, source, text |
| 15 | `recordings/<night>/race.jsonl` | which feed saw each play first | 2-8 Nov | 21,000 (4.4k / 1.3k / 3.8k / 2.6k / 3.8k / 5.0k / 0.3k) | 7 | t, league, game, label, first, others |
| 16 | `recordings/<night>/nhl_seen.jsonl` | NHL events as they were observed | 4-7 Nov | about 9,000 | 4 | t, league, game, event, type, kind, period, clock, text |
| 17 | `tap_bug.jsonl` | score-bug visible-or-not reads off the picture (not Nick's taps despite the name) | 7-8 Nov | 679 (69 bug present, 403 visible) | 2 | kind, t, target, clock, period, bug, visible, why |
| 18 | `scorebug_reads.jsonl` | stored score-bug reads (NBA) | 7 Nov | 69 (19 visible, 2 games) | 1 | t0, t1, league, game, target, visible, home, away, period, clock, down |
| 19 | `tv_input.jsonl` | TV input switch requests and read-back | 4-8 Nov | 71 (68 verified) | 5 | at, requested, source, accepted, verified, trusted, observed, detail, how, cmd_s, verify_s, total_s |
| 20 | `buffer.jsonl` | stick pause-and-scrub events | 7-8 Nov | 37 (20 scrub_failed, 7 scrubbed, 6 held) | 2 | ts, kind, target, stick, behind_s, err_s, took, why, asked_s |
| 21 | `buffer_shadow.jsonl` | shadow-mode decision log with stay value vs best alternative | 7 Nov | 2,469 (1,044 stay_leave, 68 entry) | 1 | ts, mode, kind, league, gid, stick, role, behind_s, action, reason, decision_kind, source, feed_lag_p95_s, sync_err_s, tune_s, margin_s, value_stay, value_best_alt, best_alt, value_gap, screen_lag_s, alts, context, alt_context, picture_at, mu, sd, arena_at |
| 22 | `jumps.jsonl`, `jump_status.jsonl` | mid-game jump and standby-stick state | 7-8 Nov | 27; 917 | 2 | jumps: from, to, app, ok, why, gate, held_s, score, value, plays_seen, veto, fail. status: standby_plan, pending, replaying, holds, bj_park, want |
| 23 | `live_nights/<night>/summary.json` | per-night rollup (landings, mid-play entries, press spans, misses) | 3-7 Nov | 5 files | 5 | landings{n, after_dead_p50_s, before_play_*}, entries_mid_play, leaves_mid_play, press_spans, sync_anchor_err, misses[], big_plays, faults_fixed |
| 24 | `calib/<night>.json`, `switch_times.json` | per-night delay and flip timing | 4-7 Nov | 4 + 1 | 4 | night, sync.delay_s, timing.press_to_tv, tv_flip |
| 25 | `plan_20261102..08.json` | day plan of games and windows | 2-8 Nov | 7 | 7 | day, built, games, windows, gaps, note |
| 26 | `agent.jsonl`, `autonomy.jsonl` | watchdog and deploy notes | 2-8 Nov | 61; 73 | 7 | agent: ts, kind, action, source, why, result, target, obs. autonomy: t, job, outcome, ok, message, slot |
| 27 | `archive/<league>/` | public ESPN scoreboards and summaries | NFL from Sep 2025; others Sep-Nov 2026 | 659 files, 369 MB | n/a | public data, not own data |
| 28 | `training/*.parquet` (+ manifest) | built training tables | n/a | 12 files | n/a | public-derived; manifest keys leagues, total_mb, labels |
| 29 | `imgcache/`, `lab/navigation_frames/` | cached images and frames | n/a | 655 + 24 images (not opened) | n/a | never open; frames are read then deleted by rule |
| 30 | `user_state.json`, `held.json` | current lock, focus, pause | now | 2 small files | 1 | paused, locked_game, focus_game, focus_mode, hold, muted |

Not opened by design: `devices.json` (does not appear in the listing), `smartthings_oauth.json`, any token or key-named file. `providers.json` and `channels.json` (static config) were not read.

## 3. What each source can label

| Brain task | Best source | Real labels available | Verdict |
|---|---|---|---|
| End-of-play timing on the real picture | 7 (`leave_why`, `enter_why`, `enter_after_dead_s`, `enter_before_play_s`); 17-18 (score-bug visibility); 23 (landings) | 58 cuts carry an after-dead-ball time; 21 carry a before-play time. Landings graded 27 / 0 / 5 / 25 on the four nights. Mid-play entries 17, 1, 1, 11. | Truly new, but evaluation scale only (about 60 rows) |
| Ad breaks on real channels | none directly. 21 has `state=break` (25 rows); 7 has `break_visit` (2 rows); 8 | Feed-inferred, not picture-confirmed. | Gap. Not recorded. Needs the hand log (section 6) |
| Stick delay | 12, 11, 13, 24, 4 | 52 burst anchors, 3,240 position estimates, 4 nightly calibrations. In anchors `used` is false in every row and `edge_sd` is a constant 0.3, so the anchors look like a fixed-width default, not a measured spread. | Real and unique; sparse per stick/app. Best-supported label |
| Switch latency (press to picture) | 1, 10, 19, 24 | 294 switches with a confirmed time; 68 TV input read-backs verified. | Good for sim fidelity, not for the brain |
| What Nick chose or overrode | 1, 2, 6 (`kind=user`, reasons "you locked this game", "you selected this game"); 5 (votes) | about 1,072 `user` switch rows, but see quality below. Votes 0. | Evaluation only (see 4) |
| Moment seen or missed | 8 (`seen`, 42 of 624) | System-side flag, not what Nick saw | Weak |

Unique compared with public data: public sources (ESPN summaries, NBA CDN, FastCast) already cover play events and timestamps; the lag of those feeds is public-measurable and 14-15 record it. None of the public data records (i) the picture on a real stick, (ii) the broadcast delay behind the feed, (iii) which game was actually on screen at any second, (iv) the dead-ball or live-play call made from that picture. Those four are only here.

## 4. Nick's taps and overrides: evaluation only

Per the planner note, these must never be training input to the brain. List for evaluation and replay scoring only:
- `switches` rows with `kind=user` (table 1 and per-night copies in 6), `decisions` rows with `kind=user` (1,016). Reasons are of the form "you locked / you selected this game".
- `supervisor_cuts` rows with `kind=user` (30 of 64), including their `leave_why` and `enter_why`.
- `user_state.json` (current lock, focus, pause) and `held.json` (empty).
- `calibrations` kinds `tap` and `tap_set_aside` (5 rows, 2 Nov).
- `feedback` (0 rows) and `labels` (0 rows): empty, so no opt-in rating exists yet.
- `tap_bug.jsonl` is NOT taps. It is score-bug detection, so it is not covered by the rule, but check with the planner before using it as a label because it comes from the stick picture.

## 5. Quality problems

1. **Dry-run contamination.** 1,179 of 2,317 `switches` rows are `dry_run=1`. On 6 Nov, 866 of 892 `user` rows are dry run. They look like a harness replay, not Nick. Count `kind=user` only where `dry_run=0`: about 4 + 26 + 36 + 24 = 90 real rows.
2. **Repeated lock rows.** On 7 Nov, 101 of 104 `user` rows say "you locked this game": re-asserted lock, not 101 choices.
3. **Replay rows** (890) are system actions, not choices.
4. **Small cut set.** 64 graded cuts, 24 with leave reason "play may be live" (an unsure call), 20 blank. Night 5 Nov has 1 cut and 6 Nov has 6.
5. **Single-night sources.** `buffer_shadow` and `scorebug_reads` cover one night; `tap_bug` two. About 72% (50 of 69) of `scorebug_reads` have `visible=false`, and the `down` field is empty in all rows (NFL not covered).
6. **Anchors:** all 52 unused, constant `edge_sd`; 26 have no feed type.
7. **Time units and keys are inconsistent** across files (`ts`, `t`, `at`, `t0/t1`; `target` vs `stick`). Join keys differ.
8. **Gaps:** NHL feed `nhl_seen` stops at 7 Nov; recordings for 8 Nov are partial (1,116 plays); 3 Nov has no instrumented files besides plays/race.
9. **Mid-play entries are not zero** (17 / 1 / 1 / 11 on the four graded nights), so the data also shows Rule 1 breaches, useful as failure cases.
10. **Sensitive content.** `sync_probe` rows carry `host`, `wifi` and `log` fields (addresses, network details). `traces` and `switches` carry `target` and `stick` identifiers. `calib` keys include a provider content id. `agent.jsonl` text includes build hashes. Do not commit any of these. Aggregates only.

## 6. Five labels to add cheaply on normal nights (no visible stick)

All four of the first are logged by code the system already runs; none needs Nick to act or any stick to appear on the TV.

1. **Picture-confirmed dead/live at every entry and leave** (extend `supervisor_cuts`): record the score-bug clock-running flag and the read-back time from the already-running screen read. Fields to add: `bug_clock_running`, `bug_read_age_s`. Gives end-of-play labels without Nick.
2. **Ad-break flag on the real channel:** when the screen read shows no score bug for over 20 s on a game the feed says is live, log `break_on_picture` with start and end. Reuse `tap_bug` (`bug=false`) and add `game`, `channel`, `league`. Gives the first real ad-break labels.
3. **Anchor fix:** make `sync_anchors` write `used`, real `edge_sd` and `feed_type` on every row, and add an anchor at every period end and timeout, not just bursts. Delay ground truth per stick and app.
4. **Per-switch outcome joined in one row:** `switch_id`, `cmd_ts`, `confirmed_ts`, `first_picture_ts`, `landed_dead` (bool), `landed_play_s` (seconds to next play). Join `switches`, `traces` and `supervisor_cuts` so no one joins on mismatched keys.
5. **Override reason tag with one tap, after the fact:** log `user_override` with `prior_pick`, `chosen`, and a 1-field `why_code` (lock, better_game, my_team, bored, bug) filled in by Nick on the phone later. Evaluation only, never training.

Also fix: set `dry_run` filtering in nightly summaries so user counts are real.

## 7. One-hour hand log of real RedZone

Goal: truth for cut time, game and reason on the real broadcast, without code. One plain note, one line per cut, written while watching the real RedZone channel for 60 minutes (a Sunday afternoon NFL window is the best hour). No stick on the TV.

Format, one line each:

```
HH:MM:SS | game | reason
```

Rules:
- Time is wall-clock seconds taken from the phone clock, when the picture changes to a new game. Add "~" if you are unsure by more than 5 s.
- Game is `AWAY@HOME` using the three-letter team codes, for example `WSH@DAL`.
- Reason is one code: `TD`, `FG`, `TO` (turnover), `RZ` (red zone), `END` (period or half end), `TOUT` (timeout), `AD` (ad break), `PEN` (penalty), `Q4` (close and late), `OTHER`.
- Add `LIVE` or `DEAD` at the end when you can tell whether the new game was in a play or between plays when the cut landed (the key label).
- Mark the start and end: `START hh:mm:ss` and `END hh:mm:ss`. Note any ad break shown on the channel as `AD start` and `AD end` lines.
- Write the date and channel at the top. Do not write account or device details.

Example shape (not real data):

```
Sun 15 Nov, RedZone channel
START 13:02:10
13:04:41 | AAA@BBB | TD | LIVE
13:07:15 | CCC@DDD | RZ | DEAD
13:09:02 | AD start
13:10:40 | AD end
END 14:02:10
```

Expected size: about 40 to 80 lines. Save the note in the repo's `docs/` folder as plain text or give it to the data thread, and run the code's cut timing for the same hour so the two can be compared.

## 8. Next action

Fix the sync anchor writer (item 3) and join key (item 4): both are small changes and unlock the best-supported label.
