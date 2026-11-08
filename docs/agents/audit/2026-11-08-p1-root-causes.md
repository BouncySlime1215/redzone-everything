---
id: P1-ROOTCAUSE
title: Root causes of the P1 switching bottlenecks (Nov 7 slate)
date: 2026-11-08
stream: audit
author: P1 read-only root-cause agent
status: draft
supersedes: []
sources: 0
verified: 0
affects: [redzone/decider/decider.py, redzone/control.py, redzone/engine.py, redzone/tuners/yttv_seek.py, redzone/tuners/tuners.py, redzone/tuners/yttv_search.py, redzone/tuners/adb.py]
gate: [P1-ROOTCAUSE-01, P1-ROOTCAUSE-02, P1-ROOTCAUSE-03, P1-ROOTCAUSE-04, P1-ROOTCAUSE-05, P1-ROOTCAUSE-07, P1-ROOTCAUSE-08, P1-ROOTCAUSE-09]
---

## Summary

- The 126 slate minutes "lost" to `bj_screen_not_buffered` + `play_on_screen` is really 74 min: the two counters fire on the same ticks. `bj_screen_not_buffered` is a label on a fall-through path, not its own hold.
- The screen was not buffered because every landing except a jump comes in at the live edge, and 15 app restarts in 3.3 h kept sending it back there. At the live edge the play-by-play gate opens 0.4-1 times per 10 min; buffered, 2-7 times.
- Prime sat at delay doubt +-13.3 s (a 56 s window needed). The score bug that would fix it is blocked whenever a preload runs.
- The 25 s wait was never useful past 10.7 s; 0 of 23 timeouts cut. Scrub failures are 10 real ones, not 20 (10 are backoff log rows), and the retry for them is dead code on this path.
- Prime's 43 s is unmeasured by step (not logged). Adb hangs hit both sticks equally: not a bad stick.

## Data and method

Read-only: `data/redzone.db` (`switches`, `decisions`, `snapshots`, `calibrations`), `data/jump_status.jsonl`, `data/jumps.jsonl`, `data/buffer.jsonl`, `data/sync.jsonl`, `data/service.log`, `data/recordings/2026-11-07/plays.jsonl`. Code is read at W (907efdae); live ran about 42e2ee8f (deployed Nov 8 00:06) and earlier commits during the slate, so a line number here can differ from what ran. Hold minutes convert ticks at 0.52 s as in P1-BOTTLENECKS. Stick addresses are masked: "stick A/B" in hang counts are aliases, not HDMI ports.

**Gate replay (the one number that is a simulation).** `DeadTracker` (real code) fed online from the recorded plays, 1 Hz, game on screen at mu seconds behind, opening = `dead_until(t-mu-m) >= t+3.2-mu+m` with `m = 2*sigma` (the `_jump_gate` play-by-play test). Excludes the break-on-screen and normal-gate branches; recorded plays have no game clock, so NBA window ends are conservative. Read it as relative, not absolute.

| Game (recorded source) | Picture | sigma 13.3 open% / openings per 10 min | sigma 4 | sigma 1.5 |
|---|---|---|---|---|
| NBA 401909944 (nba_cdn) | live, mu 17 | 1.5 / 0.2 | 2.6 / 0.4 | 6.3 / 3.7 |
| NBA 401909944 | buffered +78 | 9.7 / 0.9 | 18.2 / 2.3 | 22.5 / 3.1 |
| NBA 401909942 | live | 0.9 / 0.1 | 1.9 / 0.6 | 4.6 / 2.7 |
| NBA 401909942 | buffered +78 | 15.5 / 1.3 | 24.1 / 1.8 | 27.4 / 2.2 |
| CFB 401856856 (espn) | live, mu 35 | 10.1 / 0.1 | 11.4 / 0.6 | 16.3 / 6.1 |
| CFB 401856856 | buffered +78 | 46.1 / 2.0 | 62.1 / 4.3 | 71.0 / 6.6 |
| CFB 401856739 | live | 7.7 / 0.2 | 10.5 / 1.1 | 15.2 / 5.6 |
| CFB 401856739 | buffered +78 | 40.5 / 1.7 | 60.0 / 6.6 | 71.9 / 7.3 |

Median feed lag in the recording: CFB 41.9 s, NBA 10-12 s (nba_cdn). At the live edge the screen (mu 17-35 s) is at or ahead of the feed, so the feed almost never covers what is on screen. That is why buffering matters, not a tuning choice.

## Findings

### P1-ROOTCAUSE-01: `bj_screen_not_buffered` is a fall-through counter; the two holds are one thing

- Evidence: `decider.py:2821` increments it, then `return None` falls through to the jump-mode gate at `decider.py:2951-2953`, which counts `play_on_screen` on the same tick. Slate: 61.9 min not-buffered, 64.4 min play_on_screen, 52.0 min the same ticks. Union 74.3 min of 237, not 126. Of the 9.9 min not-buffered without play_on_screen, 5.9 were `standby_not_ready` and 3.8 `ready_not_dead`: the gate was open, the standby was the blocker.
- Root cause: high confidence (code and counters agree).
- Fix: `Decider._buffered_jump` (decider.py:2820-2821): count a separate `bj_bootstrap_wanted` only when a ready buffered standby exists, not every tick. Owner: p1/a-entry (decider).
- Test without TV: unit test on `_buffered_jump` with a live-edge screen, assert the old code path bumps exactly one counter per tick.
- 1-min live check: `scripts/why.py` no longer shows both codes for the same ticks.
- WORTH FIXING: yes.

### P1-ROOTCAUSE-02: Why the screen was not buffered

Chain (from `data/sync.jsonl` `behind` and the `switches` table):

1. `_bj_pic` needs `d.behind >= 45` (`decider.py:2751`). Only a landing on a scrubbed or held standby sets that.
2. 'start' and 'ended' are tuned at the live edge. Every screen in the not-buffered stretches got there that way: `start` 21:40 espn tuned, 22:51 espn tuned, 23:59 espn tuned, 00:09 and 00:11 Prime tuned. The one truly buffered stretch (secn, 22:21-22:33) shows zero not-buffered ticks and instead `bj_standby_not_ready` / `bj_standby_not_buffered` (next blocker).
3. Restarts undo it. 15 service restarts 20:53-00:11 (20:53, 21:11, 21:35, 21:57, 22:03, 22:11, 22:32, 22:50, 23:03, 23:17, 23:35, 23:47, 23:59, 00:09, 00:11). Example: at 23:47:41 a restart, 23:47:48 'start' onto the held Prime; `held` was not restored yet (fix came at 23:50), so sync says behind 0 from 23:49:24 and the whole 23:49-00:09 stretch is not buffered. Restores after 23:59 double-count: 23:59:04 mu 173.8 = 95.5 (kept) + 78.3 (restored) until the next seek re-anchors it.
4. Bootstrap cut needs the strict gate at a live-edge screen, which replay shows opens 0.4-1 times per 10 min. Bootstrap and screen-gone jump rows all night: 5 (2 at 23:41 and 1 at 00:15 timed out as no-safe-moment; 2 at 00:36 refused standby-not-ready). Zero landed (P1-ROOTCAUSE-04).

- Root cause: high confidence on 1-3 (rows and code); medium on how much each costs.
- Could not determine: how many no-buffer ticks are "no dead marks" instead of `behind < 45` (both return None from `_bj_pic`; the status row does not say). Prime and CFB plays exist in the recording, so behind is the likely one.
- Minimal fix: (a) no deploys during a slate (process, overlaps P-01/P-04; the single biggest lever and free). (b) `Decider._bj_screen_gone` and 'start' routing: when a buffered ready standby exists, prefer it over a fresh tune of a different game (`_bj_keep_standby` already holds it 120 s; extend to `kind == "start"` at session start, not only after a gone screen). (c) `Controller.restore_held` (control.py:2088): do not `_on_behind` on a target whose sync prior already carries `behind` (double count), or reset the sync filter first.
- Test without TV: fixture DelayBook with sync prior 95.5 and restored 78.3, assert mu stays 95.5.
- 1-min live check: after a restart mid-slate, `sync.jsonl` shows no behind-jump > 80 s on any target and `bj_screen_not_buffered` falls within two ticks of a landing on the standby.
- Owner: decider is p1/a-entry; control.py is p1/b-switch; apply after both merge.
- WORTH FIXING: yes.

### P1-ROOTCAUSE-03: Prime delay doubt +-13.3 s blocks the gate, and the score bug is off while a preload runs

- Evidence: sync rows for Prime `a0382680` at `sd 13.31` the whole 23:22-00:16 stretch; the same night `5ac1a238` was at 1.1-4 after clock and score samples. Decisions on 401909944 (Prime NBA) 23:50-00:30: 155 of 164 "play is live", 4 "dead ball". `_jump_gate` needs a dead window `flip + 2m` = 3.2 + 2*26.6 = 56 s. Replay: buffered NBA opens 9.7% at sigma 13.3 vs 18.2% at sigma 4 (twice the openings); live opens 1.5-2.6%.
- Why doubt stays high: `Controller.scorebug_sticks` (control.py:523-528) returns no frames whenever a switch, preload, lock or flip is running on any stick, so the bug is unread. service.log counted `preload_running` 12, `decision_pending_or_replay` 8, `lock_held` 6 between "reading" states; the bug file (`scorebug_reads.jsonl`) has no rows after 23:21.
- Root cause: high confidence on 13.3 s being what blocks the gate. Medium on `preload_running` being the main reason for no reads (the reasons were only logged from 00:05 on, and 10 `no_bug` reads also appear after that).
- Minimal fix: `scorebug_sticks`: only skip the stick the preload is running on. Add a read on the hidden Prime standby at its first 20 s even when the visible stick is a non-Prime game (the code path exists, `delay.scorebug_hidden` is already true). Do not lower the 2-sigma margin (Rule #1). Owner: control.py, p1/b-switch.
- Test without TV: fake `_preload_task` on the other stick, assert frames come back for the Prime one.
- 1-min live check: `scorebug_reads.jsonl` gets rows while a preload runs; a Prime standby's `sd` drops below 5 within two score changes.
- WORTH FIXING: yes.

### P1-ROOTCAUSE-04: The 25 s wait is not useful past about 11 s

- Evidence, `switches.detail.trace.spans.safe_wait` for ok switches (all of Nov 5-8): 1.2, 1.5, 3.0, 3.3, 5.3, 10.7, 10.7 s. Maximum useful wait 10.7 s. All 23 no-safe-moment fails ran the full 25 s. Zero of them cut. A replay of the CFB buffered pictures shows 47-69% of closed seconds open within 25 s at sigma 4 (19-28% within 10 s); live edge 2-5% within 25 s. The wait also holds the control lock (Bottlenecks: "Prime HDMI1 p90 22 s from lock wait", `queued` 22-25 s in the slate).
- Decision vs flip mismatch: `Decider._bj_lands` (decider.py:2755) passes `flip_s` = 3.2. The engine's `may_cut` (engine.py:1894-1907) calls `jump_lands_dead(..., cost=deadline_cost(target))`, which uses `max(flip_s, cost)` = 4.1 s. The 00:15:41 row is the only veto logged: bootstrap decided at 3.2, vetoed "landing (cost 4.1 s)" one second later, then burned 25 s. Same class as the 23:35 ESPN 28 s cost bug.
- 'start' fails (11 of 23): `before` is None so `safe_to_leave` is True; the veto can only be the entry side (`_entry_valid`, `_tap_lands_dead`, `_flip_lands_whistle`) which is not logged for non-jump kinds. Which one: could not determine.
- Root cause: high confidence the wait is futile past about 11 s, and on the cost mismatch. Medium on how many of 23 would have cut with a shorter one (none cut in 25).
- Minimal fix: (a) `control.MAX_SAFE_WAIT_S` -> 12.0. (b) make `may_cut` expose a reason (closure attribute or `(ok, why)`); in `_wait_safe_loop` return `None` at once on hopeless vetoes (`not_live`, `feed_stale`, `target_moved`). (c) log `veto` for every kind, not only jumps (engine.py:1894-1907). (d) `_bj_lands` uses the same `max(flip_s, deadline_cost)` as `jump_lands_dead`. Owners: control.py and engine.py p1/b-switch, decider.py p1/a-entry.
- What should happen instead: on a failed wait the tuned standby stays (it does today); the next tick re-plans. Buffered pictures know the next snap, so a fixed `d.defer_s` (already in `may_cut`) should be set from `next_snap_in` instead of polling; that is a separate change after this one.
- Test without TV: fake `may_cut` always False, assert `_wait_safe` returns None at 12 s; `not_live` returns at once; `_bj_lands` and `jump_lands_dead` agree for the same cost.
- 1-min live check: `why.py` shows `waited_s` <= 12 and every no-safe row has a `veto`.
- WORTH FIXING: yes.

### P1-ROOTCAUSE-05: Scrub failures

- Exact reasons in `buffer.jsonl` (Nov 7-8): 7 scrubbed, 20 scrub_failed = 10 real fails + 10 "backoff after a failed scrub" rows. Real: knob moved 0 s x4 (22:44 nbc, 23:24, 23:29, 23:32 espn, all labels 1.7-3.3 h into the program), focus guard x4, label lost after LEFTs x1, label read 233 s x1. Real fail rate 10 of 17.
- By HDMI, real only: HDMI1 5 ok / 7 failed; HDMI2 2 ok / 3 failed. The "17 of 20 on HDMI1" in the bottlenecks table is 9 backoff rows in one ESPN run. No HDMI signal in the real fails.
- Backoff rows stopped after 23:30 (`buffer_standby` guard and the 23:35 cost check at control.py:3942). Closed.
- Code path: `enter_buffer` retries a "knob moved" fail once from the live edge only when `res.relinked is True` (yttv_seek.py:211-213). `Controller._scrub_into` (control.py:4003) calls `enter_buffer(...)` with no `relink` argument, so `_relink` sets `relinked False` and the retry never runs on this path. Then `_scrub_into` re-tunes and backs off 120 s (control.py:4065-4068).
- Root cause: high confidence the retry is dead code. The knob-0 cause itself: not determined (low confidence). `res.steps` is not logged, so the before/after presses are invisible. Suspects: LEFTs dropped while the app is busy, or a screencap returning an unchanged frame. All four took 38.0-38.6 s, a fixed length; ok scrubs take 50-62 s (they include the commit and the label read).
- Minimal fix: (a) `_scrub_into`: pass `relink` wired to `self._tune(stick, target)` (the existing `behind_on` path resets it). (b) log `res.steps` to `buffer.jsonl` on failure. Owner: control.py/yttv_seek, p1/b-switch.
- Test without TV: fake overlay reader returning "knob moved 0" once then a moved label, assert `enter_buffer` retries and `res.retries == 1` with `relink` passed; assert no retry when relink returns False.
- 1-min live check: one forced scrub; `buffer.jsonl` carries `steps`, and a knob-0 is followed by a retry not a 120 s backoff.
- Expected: 3-5 of the 10 real fails recovered on the same tick; standby ready rises from 7/17.
- WORTH FIXING: yes.

### P1-ROOTCAUSE-06: Prime pause-hold

- Evidence: 4 hold_failed, all HDMI2, 23:06-23:19. Log: `didn't resume (stopped); reloading at the live edge` at 23:06:21 and 23:10:09. Fix eb463897 (23:17:20, chained 25 s pauses + resume settle). After it: HDMI1 6 of 6 held; the 23:19 HDMI2 hold failed (started after the deploy, reason unknown: `buffer.jsonl` writes a fixed "pause-hold did not hold", not `_last_hold["end"]`).
- Root cause: high confidence the single 75 s pause reads `stopped` on resume. HDMI2 under chunks: unknown (n=1 failed, no successes).
- Fix: write `end` in the `hold_failed` row (control.py:4182). Hold chunks stay.
- 1-min live check: one held standby on HDMI2 shows `held` or a named `end`.
- WORTH FIXING: no (log line only, cheap; do it with -05).

### P1-ROOTCAUSE-07: Where Prime's 43 s goes

- Tune times (seconds): 25.3, 43.1, 49.4, 25.0, 25.7, 28.7, 43.8 (ok), 29.5, 20.3 (no_safe after). Bimodal: about 25 and about 45. Stick does not separate them (A: 25, 43, 25, 26, 44; B: 49, 29). Cold vs warm does not separate them either (8 of 9 follow a YouTube TV tune on that stick, one follows Prime, both modes appear). `TuneResult.steps` is built but not logged anywhere, so step times are unknown.
- Code path `PrimeTuner.tune` (tuners.py:506) -> `Tuner._tune` -> `stick.view(-W)` then `PrimeTuner._confirm` (tuners.py:463). `_confirm` runs 3 rounds of `page_wait_s` 3 s + `screen_text()`; each `screen_text` is uiautomator (12 s timeout) and falls back to `ocr_text` (8 s screencap + 8 s OCR) on Prime's own UI. That is up to 28 s per round. 53 + 23 "screenshot ... timed out" lines, both sticks.
- Avoidable (hypotheses, unmeasured): the OCR fallback when the player is already loading; `-W` when the app is warm; a direct deep link to Prime's watch URL instead of the detail page (no `autoplay` link is in the repo; not tested). Warm app alone: the data does not support it.
- Root cause: low confidence on the split. The structure is high confidence.
- Minimal fix now: log `res.steps` with timestamps for every Prime tune (`Controller._tune`), and in `PrimeTuner._confirm` skip the OCR fallback once `state in LOADING`. Do not change the link format without a stick test. Owner: tuners.py is free; `_tune` p1/b-switch. Note a hidden Prime standby avoids the 43 s entirely, which is what buffering wants anyway.
- Test without TV: fake stick, assert confirm does not call `ocr_text` after LOADING; steps logged with `at_s`.
- 1-min live check: one Prime preload; `steps` shows how long each stage took.
- WORTH FIXING: yes (logging plus the small gate).

### P1-ROOTCAUSE-08: OCR read failures

- Evidence: "search tune: screen text could not be read (screencap/OCR)" x10 on Nov 7 (24-31 s each, 2 at 160-200 s). Raised at `yttv_search.py:760` when `frame.read_ok` is false. `adb.py:371` notes a 1080p `screencap -p` takes about 6.5 s on the 2023 stick; `adb.py:343` default timeout 8 s; `tuners.py:410` already uses 15 s. 76 screenshot timeouts in the log across both sticks. Mac under memory pressure (`governor -> eco` with 6-11 GB swapped, every restart).
- Root cause: medium confidence (PNG encode near the timeout plus a swapping Mac). Which of screencap and OCR timed out per failure: not logged.
- Minimal fix: `stickio`/`observe` used by the search tune: use `screencap(timeout=15)` as `tuners.py:410` does, or `screencap_band` for the lines the search needs (raw, no PNG encode). Log which step failed. Owner: `yttv_search.py` (p1/c-tv touches it, apply after).
- Test without TV: fake `observe` that fails the first read; assert the search retries within the budget and the error names the step.
- 1-min live check: `why.py` shows no OCR tune failures across a search; `service.log` no "screenshot timed out".
- WORTH FIXING: yes.

### P1-ROOTCAUSE-09: Why the adb sticks hang

- Evidence: 140 "adb commands hung in a row" lines. Stick A 70, stick B 70, not stick-specific. Clusters (90 s) 69; 31 of 140 hit the other stick within 30 s. Hour histogram: long Mac sessions (Nov 5 13-19, Nov 7 13-19, Nov 8 10-12). Not after a restart (0 of 140 within 60 s of a restart). `supervisor adb: drop ... reconnect` 155 times; it preceded a hang 2 of 140 times, so the reconnect is an effect, not a cause (P-09's two reconnectors stay a real risk at flip time, but they do not explain these).
- Pattern: `always-open shell not usable (no reply in 3s)` -> breaker opens at 2 hangs -> `stick not responding` until re-handshake succeeds, usually within 10-20 s. Often nothing else happened on that stick for minutes (no command to blame in the log). Nov 5 also has an authorization episode (`ADB_VENDOR_KEYS is not set`, 70 rows, stick A only), a separate thing not traced here.
- Hypotheses ranked: (1) medium: the 3 s handshake is too tight and a one-off Mac stall counts as a hang (Mac 6-11 GB swap; `HANDSHAKE_S=3.0`, adb.py:43). Evidence: symmetric sticks, hours align with busy Mac sessions, not tied to commands. (2) low-medium: brief network or Wi-Fi stalls (33% of hang clusters follow a fetch failure within 30 s vs 20% random baseline; a weak signal). (3) low: sticks freezing (would be asymmetric; they are not).
- Could not determine: the exact command that hung (`Stick.shell` logs no command name), and whether the stick or the Mac was the slow side.
- Minimal fix: (a) log the first word of `cmd` and elapsed on each hang (`adb.py:178`). (b) `Stick.shell`: count handshake fails as non-hang (`NOT_SENT`), breaker threshold 3 not 2. (c) `HANDSHAKE_S` 3 -> 8. (d) memory is the real fix: PLAN item, not this PR. Owner: adb.py is p1/c-tv: after merge.
- Test without TV: a fake that times out once then succeeds; assert the breaker stays closed and the next command runs.
- 1-min live check: `service.log` hang lines name the command; a single late handshake no longer shows `stick not responding` within 10 s.
- WORTH FIXING: yes (cheap, and required before the memory work can be judged).

## What to change in this repo

Order, after p1/a-entry, p1/b-switch and p1/c-tv merge. Expected jumps are estimates on the 4 h Nov 7 slate (8 landed, 18 tried); they overlap.

| # | Finding | Fix | Files | Owner | Jumps/slate (estimate) |
|---|---|---|---|---|---|
| 1 | 02 | No deploys during a slate; prefer the buffered standby on 'start' | process, `_bj_keep_standby` | coordinator, p1/a | +5-10 |
| 2 | 05 | Wire `relink` to `enter_buffer`; log steps | control.py | p1/b | +3-5 |
| 3 | 03 | Score bug on the non-preloading stick | control.py | p1/b | +2-4 |
| 4 | 04 | Wait cap 12 s; veto reasons; decide = flip cost | control.py, engine.py, decider.py | p1/b, p1/a | +1-3 |
| 5 | 01 | Honest counters | decider.py | p1/a | 0, prevents misreads |
| 6 | 07, 08, 09 | Step logs; OCR timeout; handshake/breaker | tuners, yttv_search, adb | free, p1/c | 0-2 |

Rule #1 is untouched by all of these: no margin is lowered. Each PR still needs the before/after table (`entries_mid_play`, `plays_missed_entry`, `dead_air_pct`) per project rules.

## Risks / unverified

- One night of jump data; n is small. Jump gains are judgment, not measured; the replay's openings ignore whether a standby is ready at that moment, so a 2x-6x gate gain is an upper bound.
- W is newer than what ran on the slate; the fixes after the 23:17, 23:35 and 23:50 commits are partly already in.
- The log only shows reasons after they were added (veto after 23:43, score-bug reasons after 00:05, held offsets after 23:50), so early-evening causes are inferred.
- Could not determine: the knob-0 cause, Prime step times, per-failure OCR vs screencap, the adb command that hung, and which 'start' veto fired. Each has a logging fix above because of that.
- The deep-link alternative for Prime is an idea, not a tested path; do not try it without a stick test.

## Bibliography

None (local data only: data/redzone.db, data/jump_status.jsonl, data/jumps.jsonl, data/buffer.jsonl, data/sync.jsonl, data/service.log, data/recordings/2026-11-07/plays.jsonl, all opened and read read-only; docs/agents/audit/2026-11-08-p1-bottlenecks.md; docs/audits/2026-11-08-devices.md and -platform.md headers).
