# YouTube TV live-edge tests on the real sticks (Nov 5, 2026, ~2:00–2:30 AM)

Overseer, with the TV on and autopilot paused (Nick: "test hundreds of ideas all night").
Tests ran on the hidden stick (.181) only; nothing Nick was watching changed. Read-only except
deep links and media PLAY/PAUSE keys on the hidden stick. Scripts: overseer scratchpad `sync/`.

**Nick's rule for this program: no number is truth until we measure it. Starting from scratch.**

## The signal: media_session position, extrapolated

`dumpsys media_session` gives YouTube TV's `position` and `updated` (stick uptime, ms). YTTV only
refreshes them on play/pause/seek/tune, so **position must be extrapolated**:

    pos_now = position + (uptime_ms_now - updated) * speed        # uptime from /proc/uptime, same adb call
    E       = mac_wall_now - pos_now / 1000                        # "origin"

The existing `sync_probe.jsonl` rows store raw `position` only, so they drift 1 s per second. **Bug:
the probe must also log `/proc/uptime` (or the extrapolated value).**

## Findings

| # | Test | Result |
|---|---|---|
| 1 | E stability while playing (no tune) | Constant to ±0.01 s over minutes; stick .150 on ESPN held E fixed for 3+ hours. |
| 2 | Position right after a deep-link tune ("L") | **ESPN 46,803.0 s ± 0.3** (n=7), ESPN2 46,802.9, **TNT 46,792.7 ± 0.7** (n=5), FS1 46,792.9. YouTube TV puts the live edge ≈ 13.0 h into its window, with a fixed per-channel offset (ESPN family vs TNT/FS1 differ by ~10 s). **Joins are repeatable to ~0.5 s.** |
| 3 | Behind-live meter: pause 30 s, resume | E rose by **+30.01 s**: exact. (A 10 s pause resumed AT live: YTTV auto-rejoins after a short pause.) |
| 4 | Re-link after being behind | Back at the live edge (L = 46,804 s) in **2.5 s**. |
| 5 | Time from deep link to PLAYING (media_session) | **0.9–4.1 s**, median ~2.4 s (n=14). |
| 6 | Stalls | Any rise in E while state = playing is buffering = seconds fallen behind live, exactly. |
| 7 | `adb screencap` of YTTV video | **Black** (DRM). Not bypassed. Overlays only (ad UI may be readable: test during a real ad). |

## What this gives us, free and exact
- **Are we at the live edge? By how much are we behind?** `behind = E - E_at_last_tune_landing`, or
  `behind = L_channel - (pos_now - seconds since tune)`. Exact to ~0.5 s.
- **Relative delay between the two sticks** on the same channel: from their landing L values.
- **Force live**: a deep link always lands at live (±0.5 s). Use it after any pause > ~10 s or any stall.

## What it does NOT give (still unknown, measure, don't assume)
- **Absolute latency**: live edge vs the real world (and vs any score feed). One constant per
  channel/provider/stick path. Needs ground truth: the scorebug on screen (camera at the TV, needs
  Nick's OK), TV audio, or YTTV ad-overlay text read via screencap during real ad breaks.
- Prime Video: untested tonight (no live Prime event at 2 AM). TNF tonight (JAX @ BAL) is the first chance.
- The ~44 s "ESPN feed lag" number is NOT trusted until re-measured against on-screen truth.

## Next tests
1. Probe fix: log uptime + extrapolated pos; derive `behind_live_s` per stick every tick.
2. Ad overlays: screencap the hidden stick during a live ad break; does YTTV draw "Ad 1 of 3 · 0:45"?
   If yes, that's an exact on-screen anchor every break.
3. Prime: the same position/E tests on TNF tonight (hidden stick, before kickoff and during).
4. Absolute latency: once one anchor exists per channel, compare the feed's play wall-clock times.

## Update ~3:00 AM: absolute delay from commercials (code only)

- **Ground truth already exists in our own recordings.** `data/recordings/<day>/plays.jsonl` stores each play's real-world
  time (`wall`, from the NBA cdn / ESPN feeds) and when we received it. Measured arrival lag: **nba_cdn ~5-20 s,
  espn_summary ~25-50 s, espn_fastcast ~25-40 s** (not the "44 s" constant).
- **YouTube TV commercials are visible to code.** During ad pods, media_session `updated` fires once per spot (every
  ~10-16 s, position advancing exactly by the spot length). 32 bursts found on Nov 4-5.
- **First absolute estimate (one break):** PHI timeout 19:32:07 real; play resumed with a made 3 at 19:35:44 real.
  Stick .150 on ESPN showed 3 ad spots 19:35:16 / :31 / :46, pod end ~19:36:01, and the controller cut there at
  19:36:13. **pod_end - first_play = +17.6 s → the YTTV ESPN live edge ≈ 20-30 s behind the real game** (±~8 s, one break).
- Limits: DAI pods are per-device (the two sticks' pods differ), and a stick must be parked on the game's channel
  during the break. **Plan for the next live NBA night:** park the hidden stick on a live game when it's free, run
  `burst_align.py`, and average over many breaks per channel/stick.
- Background loggers now running: decoder bitrate every 5 s per stick (`codec_logger.sh`) and an overnight
  live-edge/pause/twin harness on the hidden stick (`overnight.py`).

## Update ~3:15 AM: PRIME REPORTS REAL CLOCK TIME (the big one)

- On stick .150 during the Nov 4 Prime NBA window (19:12-21:56), Prime's media_session `position` is a **Unix
  epoch in ms**: the wall-clock time stamped on the frame on screen (encoder/packager time).
- `behind = wall_at_update - position/1000` = **6.3-7.0 s, median 6.4 s, across 19 readings over 3 h.** Exact, continuous,
  no ads or guessing. (Extrapolate between updates with /proc/uptime exactly like YTTV.)
- Remaining unknown for Prime: the constant gap between Amazon's frame clock and the arena (contribution delay).
  Measure it from NBA cdn `wall` times of breaks/period starts vs Prime frame times; expected a few seconds.
- Stick .181 on Prime reported a non-epoch position (~86,368 s and rising in real time, a 24 h counter). Likely a
  different content type (Prime Video Channels / linear) or player path. Test on TNF tonight on both sticks.
- **Code change (lane 2):** in the probe/controller, if the Prime position > 1e12 ms, treat it as an epoch and expose
  `prime_behind_s = now - pos_now` per stick. This alone gives Prime sync to well under a second vs Amazon's clock.
