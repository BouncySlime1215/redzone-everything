---
id: P1-BOTTLENECKS
title: Switching bottlenecks, measured (Nov 5-8 2026 data)
date: 2026-11-08
stream: audit
author: P1 read-only analysis agent
status: draft
supersedes: []
sources: 0
verified: 0
affects: [redzone/control.py, redzone/engine.py, redzone/decider/decider.py, redzone/tuners/yttv_search.py, redzone/tuners/providers.py]
gate: []
---

## Summary

- Only one night has jump rows: Sat Nov 7 (jumps.jsonl and jump_status.jsonl start Nov 7 18:55; buffer.jsonl Nov 7 21:52). Nov 5, 6 and 8 have `switches` rows only (Nov 8 had no slate yet at 12:19). Everything below is Nov 7 unless it says otherwise.
- Throughput: 8 jumps landed in the 3.9 h slate (about 0.34 per 10 min); all live switches landed 0.64 per 10 min (median 1, p90 1, max 3, 36 ten-minute buckets). The measured RedZone need is median 5, p90 9 (docs/research/2026-11-08/redzone-numbers.md). That is about 8x short at the median.
- The decider held the TV for about 150 of 237 slate minutes on five hold codes; 10 of 18 jump attempts failed.

## Data and method

- `switches` table (redzone.db, read-only): Nov 5-8, dry_run=0, 972 rows. Of these 838 are YouTube TV replays (1 ok) and 134 are live switches (excluding 981 dry "no channel for this game" rows on Nov 4-6). The `detail` JSON carries a `trace.spans` per switch (decide, queued, tune, safe_wait, flip, resume, dead_air, to_picture, total), so stage times are measured, not inferred.
- HDMI: the detail text names HDMI only on success. Failed rows were mapped by the stick id in `trace.stick` (each stick appears with exactly one HDMI on its ok rows: 2 sticks, 18 ok rows, no conflicts). 45 rows have no stick (the stick was not responding or the switch failed before one was chosen), shown as "?".
- Holds: `jump_status.jsonl` holds are cumulative per-tick counters that reset at each landing. Summed as deltas (same as `scripts/why.py`), ticks converted at 0.52 s (measured 1.91 ticks/s from 695 consecutive row pairs). The status rows carry no app or HDMI, so holds cannot be split by app or HDMI.
- `scripts/why.py --night 2026-11-07` agrees: 8 ok, 19 failed (other 11, no_safe_moment 4, standby_not_ready 4), 1 landing veto. Nov 8 shows 0 jumps.

## Findings

### P1-BOTTLENECKS-01: per-stage time of a switch (ok switches only, seconds)

| Path / app / HDMI | n | tune p50/p90 | safe_wait p50/p90 | flip p50/p90 | to_picture p50/p90 | total p50/p90 |
|---|---|---|---|---|---|---|
| preloaded, YouTube TV (both HDMI) | 13 | none | 0 / 5.3 | 2.5 / 3.0 | 2.9 / 8.2 | 4.2 / 12.4 |
| preloaded, Prime (HDMI1 only) | 5 | none | 0 / 0 | 2.5 / 3.2 | 11.6 / 34.8 | 11.6 / 34.8 |
| tuned, YouTube TV | 12 | 4.4 / 12.2 | 0 / 3.3 | 2.2 / 2.7 | 10.4 / 18.8 | 14.9 / 20.9 |
| tuned, Prime | 6 | 43.1 / 49.4 | 0 / 0 | 2.5 / 2.6 | 47.4 / 55.8 | 47.4 / 55.8 |

By HDMI (ok switches, `took`): YouTube TV HDMI1 n=10 p50 3.2 / p90 14.8; YouTube TV HDMI2 n=16 p50 6.8 / p90 10.3; Prime HDMI1 n=7 p50 9.4 / p90 33.7; Prime HDMI2 n=4 p50 47.3 / p90 52.9. Prime on HDMI2 had only tuned paths (4 of 4); Prime on HDMI1 had 5 preloaded of 7. Decide and queue time is about 0 (p90 0.6 s, Prime HDMI1 p90 22 s from lock wait). The flip itself is a steady 2.5 s (p90 3.2 s); the picture wait after the flip is the variable part. Slowest stage per path: tune (Prime 43 s), to_picture (YouTube TV preloaded 3 s p50 but 8 s p90).

### P1-BOTTLENECKS-02: failures (Nov 5-8, live switches, "already" excluded: 127 attempts, 37 ok, 90 failed)

| Reason | Count | Time burned (s) | Where |
|---|---|---|---|
| no safe moment to cut (25 s wait expires) | 23 (7 Prime, 16 YTTV; 4 of them jumps, 11 'start') | 557 | Nov 7-8 |
| stick not responding (adb hung, fail fast) | 18 | about 0 | Nov 6 user taps only |
| video never started | 9 | 307 | Nov 6-7, both HDMI |
| search tune: screen text unreadable (OCR) | 7 (+3 other search-tune errors) | 498 | Nov 7 'start', HDMI2 mostly |
| switch timed out, playback not confirmed | 7 (4 Prime) | 645 | Nov 7 |
| safe exit window closed during preparation | 5 | 288 | Nov 7 |
| ESPN could not open the link | 5 | about 40 | Nov 5, 7 |
| buffer entry failed | 3 | 179 | Nov 7 |
| standby not ready | 4 (2 "stopped playing") | about 0 | Nov 7-8 jumps |

Success rate by app: YouTube TV 33 of 105 (31%), Prime 11 of 24 (46%), ESPN 0 of 5. The 31% is dragged down by Nov 6 user taps (hung stick, 17 rows) and 'start' attempts at tune time.
Jump attempts: 18 rows with an outcome: 8 ok, 4 no_safe_moment, 4 standby_not_ready, 2 other (a 0.17 s preloaded refusal and a 29 s tune of an un-preloaded game). Both un-preloaded jumps failed.

### P1-BOTTLENECKS-03: was the standby ready when a dead ball came

- jump_status rows with a game on screen during the slate: the standby was ready in 151 of 362 (42%). Rows with a standby present but not ready: 240 of 917 overall.
- Decider tick codes in the slate (minutes): `ready_not_dead` 19 (standby ready, no dead ball), `standby_not_ready` 13, `bj_standby_not_ready` 6, `bj_standby_not_buffered` 5. So about 24 min the editor had a dead-ball opening or a want but nothing ready, versus 19 min ready with no opening.
- Buffering the standby (buffer.jsonl, YouTube TV): 7 scrubbed, 20 scrub_failed (74% failed; 17 of 20 on HDMI1: "knob moved 0s, not 60s", "focus guard: no scrub label after the LEFT", backoff storms of 23 s each, 11 in 7 minutes on one ESPN game 23:24-23:32). Successful scrubs took 50-62 s. Prime: 6 held, 4 hold_failed ("pause-hold did not hold", all on HDMI2, each 75 s).

### P1-BOTTLENECKS-04: time lost to each hold code (slate Nov 7 20:43-00:40, 237 min, and the whole file)

| Hold code | Slate min | Nov 7 20:43-24:00 | Nov 8 00-06 | Nov 8 06-12 |
|---|---|---|---|---|
| play_on_screen | 64 | 36 | 28 | 6 |
| bj_screen_not_buffered | 62 | 31 | 31 | 14 |
| screen_game_unknown | 34 | 28 | 133 | 150 |
| crunch | 24 | 24 | 11 | 11 |
| bj_screen_gone_no_landing | 22 | 16 | 133 | 150 |
| ready_not_dead | 19 | 17 | 2 | 0 |
| standby_not_ready | 13 | 12 | 1 | 1 |
| screen_not_in_rotation | 10 | 10 | 0 | 0 |

Nov 8 00:40-12:19 numbers for `screen_game_unknown` and `bj_screen_gone_no_landing` are idle (no live slate, screen holds a finished or unknown game), not lost jumps; they count the same ticks twice (both codes fire together). `play_on_screen` and `bj_screen_not_buffered` are the two big real holds: the picture was mid-play, or the screen stick was tuned at the live edge so a buffered landing did not exist.

### P1-BOTTLENECKS-05: switches per 10 minutes vs need

- Landed switches per 10-minute bucket, slate Nov 7 18:50-00:40 (36 buckets): mean 0.64, median 1, p90 1, max 3. Jumps alone: 8 in 3.9 h.
- Need (redzone-numbers.md): median 5, p90 9, busiest 14. Gap at the median 4.4 per 10 min; at p90 8.4.

## Top 5 bottlenecks by jumps lost

| # | Bottleneck | Jumps / switches lost | Code | Fix in audits |
|---|---|---|---|---|
| 1 | Screen game never reaches a safe/dead moment: `play_on_screen` and `bj_screen_not_buffered` holds (126 of 237 slate min), then the 25 s wait ends in "no safe moment" (23 fails, 557 s) | 23 fails + most of the 0.34/10 min shortfall | decider.py `_buffered_jump` / `_jump_hold` / `_jump_gate` (2509-2860); control.py `MAX_SAFE_WAIT_S` (76), `_wait_safe_loop` (2500); engine.py `_act` may_cut | D-05 (stale timeline), S-2 (stale gate), S-3 (leave margin), D-03 (crunch hold) tweak the gate but none shortens the 25 s futile wait or fixes why the screen is not buffered. UNCOVERED: no audit finding addresses `bj_screen_not_buffered` (62 min) or re-planning when the safe moment does not come (S-12 only notes the 25 s is paid when the answer is already no) |
| 2 | Tune failures and slow tunes: Prime tune 43-47 s p50, OCR/search tune fails, "video never started", timeouts | about 28 fails (7 OCR, 9 never started, 7 timed out, 5 ESPN), 1,400 s | control.py `_tune` (2011); tuners/yttv_search.py:760; prime_search.py:158; tuners/providers.py `DiagnosingTuner.tune` (377-394) | DEV-17 (diagnosis adds 28 s to every failed tune), DEV-08 (search tune has no cut window), DEV-06. Prime's 43 s tune and the OCR read failure itself: UNCOVERED |
| 3 | Standby not ready or not buffered: 42% ready, 20 of 27 scrubs failed, 4 of 10 Prime holds failed | 6 of 18 jump attempts, 24 min of slate holds | control.py `buffer_standby` (4083), `_scrub_into` (3951), `_execute_two` path choice, `standby_ready` (1659); engine.py `_preload_standby` (2117) | S-1 fixes 2 of the 4 "standby not ready" jumps (snap-parked standby refused). Scrub reliability (knob moved 0 s, focus guard, backoff storm) and Prime pause-hold failure: UNCOVERED |
| 4 | Screen game unknown or gone with no landing after restart, and the standby dropped | 34 + 22 min of slate holds (plus idle time Nov 8) | decider.py `_bj_screen_gone` (2761), `_bj_keep_standby` (2792), jump_holds counters (2690-2990) | P-04 (restart makes screen unknown) and platform P-01/P-03 (restarts); `_relearn_screen` commits already cited in P-04 |
| 5 | Adb stick not responding / flip-prep races: 18 "stick not responding" (Nov 6), 5 "safe exit window closed", 3 "Two-stick mode off (SmartThings HTTP)" | 26 fails | adb.py `Stick`; control.py `_flip_to`, `_execute_two`; supervisor adb reconnect | P-09 (two reconnectors), DEV-02, DEV-03, DEV-10, DEV-11, S-9. The log shows 136 "adb commands hung in a row" and 56 supervisor drops, no audit explains the root cause |

Not a jump bottleneck but noted: 838 replay attempts (1 ok), 736 of them "replay seek failed", about 1.8 h of stick time (median 0.09 s each) on Nov 6-7, YouTube TV only. Covered by S-11 in part (replay rewind paths), not by a finding about the failure rate.

## What to change in this repo

1. Bottleneck 1: measure why `bj_screen_not_buffered` happens (which stick, which app) and make a futile `_wait_safe_loop` fail fast (re-plan on the next dead ball instead of burning 25 s). Needs a finding of its own.
2. Bottleneck 2: skip the post-failure screen read on the live path (DEV-17), look at why the Prime tune is 43 s and whether a preloaded Prime path (HDMI1 works, HDMI2 never did) can be the default.
3. Bottleneck 3: land S-1, then log per-attempt why a scrub fails (the buffer.jsonl row carries a reason but nothing aggregates it).
4. Add `stick` (HDMI) and `app` to jump_status rows and jumps.jsonl rows so holds and jump failures split by HDMI and app. Today they cannot be.

## Risks / unverified

- Only one slate night (Nov 7, CFB Saturday) has jump rows; Nov 5-6 were tests and Nov 8 had no games by 12:19, so per-night comparison is not possible.
- n is small: 37 ok and 90 failed live switches, 8 ok jumps, 18 jump attempts. p90 on n=4-13 is a single sample; read the table as direction, not as gate numbers.
- HDMI attribution for failures depends on the stick->HDMI map from ok rows (no conflicts found) and covers 45 of 134 rows as "?".
- Hold seconds use 0.52 s per tick (median of consecutive status rows, 30 s apart); counters reset at landings, so ticks between the last status row and a reset are not counted (understates).
- Prime and YouTube TV ran on Nov 7 while Nick was likely watching; failed tunes may include his manual use.
- No fix was tested; the audit mapping is by reading the finding text, not by re-running anything.

## Bibliography

None (local data only: data/redzone.db switches table, data/jumps.jsonl, data/jump_status.jsonl, data/buffer.jsonl, data/service.log, all opened and read; docs/research/2026-11-08/redzone-numbers.md; docs/audits/2026-11-08-{switch-path,decider,devices,platform,rules-and-config}.md).
