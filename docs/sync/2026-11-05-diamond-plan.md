# Sync diamond harness output (6 planners, 36 skeptic reviews, 1 synthesizer; Nov 5 ~3 AM)

# Plan: place each stick on the live game clock (Prime first, then YouTube TV)

**Needs Nick's call first: Prime scorebug OCR.** The fastest absolute anchor is reading the clock off Prime's scorebug. On 2026-11-04 Prime video was not blanked, and scorebug OCR read 35 of 40 frames. But the hard constraint says "no screen capture of protected video", and the skeptic killed an idea for exactly this. Every OCR step below is marked **[NEEDS NICK'S YES]** and is off by default. The plan still reaches about 2 s without OCR, through steps that read only app UI or media_session.

## 1. Core architecture (5 lines)

1. **One clock.** The Mac is NTP-corrected (`sntp time.apple.com`). Each stick's uptime maps to UTC by taking the lowest-round-trip sample of 20 adb reads, with a bound of about 20-50 ms. Feeds, `updated` anchors and frames all go on this one UTC axis.
2. **Screen position, relative and exact.** `pos_now = position + (uptime_now - updated) * speed`. On Prime the position is epoch ms, i.e. a content UTC from the encoder or packager. On YouTube TV it is DVR stream time. Re-anchor on every media_session event, and fit the drift slope instead of trusting the reported speed.
3. **Truth line.** Build `wall(period, clock)` from NBA timeActual, using only clock-stopping actions inside running segments. Other leagues get a measured jitter band from the ESPN/nflverse rigidity tests and are never treated as exact.
4. **Bridge: one constant per (app, channel).** `K_c = real_time_on_screen - pos_now`, with a per-tune term `o_tune` on YouTube TV. Anchors, in order of strength:
   - Prime scorebug tick edges [NEEDS NICK'S YES]
   - YouTube TV Scores & stats panel clock (app UI only, allowed)
   - Prime Key Moments and YouTube TV Key Plays seeks (about 2 s, coarse prior)
5. **Actuator.** Hold a common delay D*, re-tuning at dead balls. Use pause, then flip input, then play, so switch jitter drops out. Target `pos* = R_event + D* - K_c - preroll`. Mid-air joins stay behind a flag that is OFF until the shadow p95 is 1.5 s or less.

## 2. Ranked test queue

Rules for every test: hidden stick only, results written to `data/reports/<test>_<ts>.json`, and raw logcat kept in the scratchpad (redacted, never committed).

### A. Tonight, no game needed (about 75 min total)

| # | Test (time) | Exact steps | Pass / fail |
|---|---|---|---|
| A1 | Stick clock (5 min) | 20 times per stick: `t0=time.time(); adb -s H shell 'echo $EPOCHREALTIME; cat /proc/uptime; dumpsys alarm \| grep -m2 -E "nowRTC\|nowELAPSED"'; t1=time.time()`. Keep the 3 samples with the lowest round trip; offset = stick_epoch - (t0+t1)/2. | Round trip under 80 ms and offset sd under 30 ms. Fail: use the `dumpsys alarm` nowRTC/nowELAPSED pair only. |
| A2 | Seek capability (2 min) | `adb -s H shell dumpsys media_session` on YouTube TV and on Prime (com.amazon.firebat). Read the `actions=` bitmask: 256 = SEEK_TO, 64 = FF, 8 = REW. | If bit 256 is set, build the seekTo helper APK later. If not, drop the APK and actuate by re-tune or pause only. |
| A3 | Is the secure flag on the whole window or only the video layer? (10 min) | 1. `dumpsys SurfaceFlinger --list` and `dumpsys window \| grep -iE "secure\|FLAG_SECURE"` on both apps.<br>2. YouTube TV: `input keyevent KEYCODE_DPAD_CENTER`, then `screencap -p` and run Apple Vision OCR.<br>3. Restore the UI afterwards. | Controls or progress text readable means UI routes are open (B3/B4). All black means the YouTube TV pixel route is dead; use Key Plays only. |
| A4 | Prime position semantics (20 min, any Prime live or FAST channel; Monumental if it is live) | 1. Both sticks on the same channel.<br>2. Every 1 s for 15 min, run `dumpsys media_session` and parse state, position, speed and updated for firebat.<br>3. Compute `E_prime = stick_utc_now - pos_now/1000`.<br>4. Then pause 20 s and play.<br>5. Then 7 deep-link re-tunes. | All of: position over 1e12; E_prime flat within 0.1 s between anchors after fitting drift; the pause adds +20 ± 0.1 s; the two sticks agree within 0.3 s; landing sd under 0.5 s. Record the drift slope (the "1% fast"). |
| A5 | Prime trick-play and hold (15 min, **only when Nick is not watching**) | 1. Pause for 5, 10, 30 and 60 s and check whether Prime auto-rejoins live.<br>2. MEDIA_REWIND / MEDIA_FAST_FORWARD, 1 press and 3 presses: content shift per press and settle time.<br>3. Flip the TV input away for 60 s and check whether E_prime holds.<br>4. Same pause-limit test on YouTube TV at 8-12 s, at live and after a seek-back. | Write a key → (ΔE, settle_s, sd) table. If the position fails to hold while the input is away, D*-holding dies on that stick. |
| A6 | YouTube TV broadcast-delay setting (5 min) | Navigate Settings with keyevents, then `uiautomator dump` (fall back to OCR of a settings-page screencap). Read Default/Decreased on both sticks. | If the sticks differ, that explains the 13 s ESPN gap between them. Ask Nick before changing it. |
| A7 | Truth line, offline (no stick, 45 min) | 1. Build `redzone/lab/nba_wallclock.py` from scratchpad `ta2.py`. Stopping actions only (foul, turnover, timeout, violation, period end); rebounds left out of the statistics.<br>2. Fixture test from 0022600157.<br>3. Rigidity test on ESPN wallclock per league.<br>4. ESPN vs timeActual, matched per play.<br>5. Start logging timeActual and `edited` into plays.jsonl. | timeActual running-pair residual p95 at or under 1.5 s. A league whose ESPN wallclock fails rigidity is used as an upper bound only. |

### B. TNF on Prime tonight (whole game, hidden stick, passive)

- **B1. E_prime through the full game.** Log media_session every 1 s, plus `logcat -v epoch` grepped for `MediaCodecLogger|AmazonHdmi|firebat`.
  - Pass: E_prime is piecewise flat within 0.1 s.
  - Count every jump at ad pods or reconnects. More than 2 jumps per hour that no media_session event explains means re-anchoring is required.
- **B2. Key Moments seeks**, 5 times at commercial breaks only:
  1. Press UP and select a moment.
  2. Read the position, which should be the epoch of the clip start.
  3. BACK, then re-link to live.
  4. Pair each clip start with the nflverse `time_of_day` / ESPN wallclock for that play.
  - Output the spread of clip epoch minus feed time. If the spread is 2 s or less, Key Moments is usable as a prior; if more, kill it.
- **B3. [NEEDS NICK'S YES] Scorebug tick edges.**
  1. Run `screenrecord --output-format=h264 --size 640x360 --bit-rate 1000000 -` piped to ffmpeg, stamping each frame with its Mac arrival time.
  2. OCR the cropped scorebug only when it changes.
  3. Use only running-clock segments of 20 s or more, and stop if gfxinfo shows dropped frames.
  - NFL truth is only ±2-3 s, so tonight's goal is the stability of K across ad pods, not its absolute value.

### C. Next live NBA game (Prime NBA or Monumental first, then YouTube TV)

- **C1. Prime K_c.**
  - Truth line: 1 s ETag poll of cdn.nba.com (also log first-seen times).
  - Bridge: B3 tick edges if Nick says yes, otherwise Key Moments. Fit `K = wall(period, clock) - pos_now` over 20 or more running-clock ticks.
  - Pass: K agrees within 1.0 s across 3 tunes and across 2 nights. p95 of the per-tune estimate at or under 1.5 s.
- **C2. YouTube TV, if A3 passed.**
  1. Open the Scores & stats panel and OCR its clock every 2 s.
  2. Panel clock change at stick time T minus wall(clock) gives L, and pos_now carries it forward.
  - Pass: K agrees within 1.5 s across 2 tunes 10 min apart.
- **C3. YouTube TV Key Plays gate** (one press):
  1. Select one key play.
  2. Check that the package and duration did not change, that the position jumped back inside the 13 h DVR window, and that BACK returns to live.
  - If it passes, repeat on 6 or more plays and fit by gap fingerprint. Treat the result as a ±2 s prior, never as truth.
- **C4. Shadow join.** Log the planned `pos*` against the actual position at the planned flip time without flipping, over 20 restarts.
  - Pass: |error| p95 at or under 1.5 s. That is the gate for turning the mid-air flag ON.

## 3. Estimator and self-validation (no human truth)

**State and measurements**
- One state per (app, channel): `x = [K_c, o_tune]`.
  - K_c is a random walk (q about 1e-4 s²/h).
  - o_tune resets on every tune, seek or ad event, which media_session reports.
- Measurements:
  - Tick edges: σ 0.3 s, Gaussian.
  - Stats-panel changes: σ 0.7 s.
  - Key Moments / Key Plays: Student-t, σ 2 s.
  - Cross-stick same-content burst: constrains the difference between the two sticks' o_tune, σ 0.3 s.
- Gate innovations at 4σ. A discontinuity re-opens o_tune.
- File: `redzone/decider/kc_filter.py`, replayed against the livelab anchors (must converge within 0.3 s).

**Self-validation, all automatic**
1. **Repeat agreement.** Two tunes 10 min apart, and two nights, must give the same K_c within 1 s.
2. **Agreement between anchor types with independent biases.** Tick edges, the stats panel and Key Moments come from separate systems. A shared bias has to pass all three to go unnoticed.
3. **Held-out prediction.** Use a foul's timeActual to predict when the clock freezes on screen. Score it against the next tick-edge or panel read that was not used in the fit.
4. **Causality bounds.** The feed's first-seen time minus timeActual must be ≥ 0 (catches a fast stats clock). K must be stable across ad pods.
5. **Release discipline.** Forward-chaining over whole nights, a locked ≥25% night set scored once, and the 95% gate from architecture.md §7.

## 4. What would prove it impossible, and the fallback

**Impossible at 1-2 s, code only, if all of these hold:**
- A4 fails: Prime's position is not epoch and does not follow content, so there is no exact relative clock.
- Nick declines B3, or the frames are black.
- A3 shows the YouTube TV window fully secure, so no readable UI.
- Key Moments and Key Plays spread more than 2 s (B2/C3).

In that case no anchor ties the screen to real time any tighter than about 3-5 s (the floor for captions, ad bursts and bitrate). Write that down as a measured negative result.

**Fallback (still useful):**
- Keep the exact relative layer: pos_now, the fixed landing positions per channel and stick, and the exact pause meter.
- Fix K_c to the best prior, with the interval stated honestly.
- Run the D* rail with dead-ball-only joins: pause, flip input, play, with a ±3 s margin. The mid-air-join flag stays OFF.
- NFL always uses a 3 s preroll with no precision claim until snap truth improves.
- Rule #1 holds in every case: never join mid-play.