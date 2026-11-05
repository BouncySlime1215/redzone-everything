# rzseek: read and seek a Fire TV stick's media sessions over adb (sync diamond A2)

YouTube TV (actions=383) and Prime (1049455) both advertise `SEEK_TO` (overseer, Nov 5 3:20 AM). This tiny
dex runs as the adb shell user through `app_process`, so nothing is installed on the stick. It does two things:

- **`list`**: one JSON line per media session: package, state, position, updated, speed, actions, plus the
  position NOW extrapolated on the stick's own clock (`pos_now`) and the stick's wall clock (`wall_ms`). It reads
  the same data as `dumpsys media_session` with no text parsing and no adb round trip inside the extrapolation.
- **`seek <pkg> <ms>` / `seekrel <pkg> <delta_ms>`**: `MediaController.TransportControls.seekTo`. On Prime the
  position is an epoch in ms (the frame's wall clock), so `seek` places the picture at an exact real time.

It is not wired into the app. Rule #1 holds: only seek a stick that is not on screen, or one on a dead ball.

## Build (on the Mac, once)

    D8=~/Library/Android/sdk/build-tools/<ver>/d8 sh tools/rzseek/build.sh   # or DX_JAR=<dalvik-dx jar>

## Test A2 (hidden stick only while Nick watches; not while a game he watches is live)

    adb -s <hidden> push tools/rzseek/rzseek.dex /data/local/tmp/
    R="CLASSPATH=/data/local/tmp/rzseek.dex app_process / RzSeek"
    adb -s <hidden> shell "$R list"                                   # 1. sessions + pos_now
    adb -s <hidden> shell "$R seekrel com.amazon.firetv.youtube.tv -30000"   # 2. back 30 s
    adb -s <hidden> shell "$R list"                                   # 3. pos_now moved -30 s?
    adb -s <hidden> shell "$R seekrel com.amazon.firetv.youtube.tv 30000"    # 4. forward 30 s = live again?

Pass: the position moves by the asked amount within 0.5 s, the stream keeps playing, and the app does not
show its own UI. Record settle time (until state is PLAYING again) and the landing error, per app. Prime's
package is shown by `list` (com.amazon.firebat on Fire OS). If `list` fails with a security or hidden-API error,
paste the stack trace (it has no secrets): the reflection path needs a different entry point on that Fire OS.

## Tonight (B1, TNF on Prime): log both sticks once a second, then report

    python tools/rzseek/prime_night.py log <stick A> <stick B> --out data/reports/b1_2026-11-05.jsonl
    python tools/rzseek/prime_night.py report data/reports/b1_2026-11-05.jsonl

Read-only (no key press), sticks written as stickA/stickB. The report says per stick x content whether the
position is an epoch (> 1e12: the frame's wall clock, so E = seconds behind real life) or a counter, how flat E
stays between state updates, unexplained jumps per hour (B1 pass: <= 2), and each pause's rise in E against
its length.

## T7: the SEEK_TO helper and the rewind bench (Nov 5)

`list` now also prints each session's `title`, `subtitle` and `duration` (MediaMetadata), the ad evidence.
`redzone/seek_to.py` drives `list` + `seekrel` from the app: behind `replay.seek_to.enabled` (OFF),
Controller._rewind seeks by the exact amount, re-measures on the stick's clock, corrects once, and presses
keys only for what is still short (priced per app). It pushes `rzseek.dex` to a stick by itself the first time.

Hidden-stick run, before games, while Nick watches (about 15 minutes per app/channel):

    sh tools/rzseek/build.sh                                       # once, after pulling (D8=... or DX_JAR=...)
    python scripts/seek_bench.py preflight <hidden>                # read-only: SEEK_TO bit, title, hidden?
    # tune the hidden stick to the channel in the app as usual, then:
    python scripts/seek_bench.py run <hidden> --channel nbatv --reps 2    # 8 SEEK_TO + 8 key trials
    python scripts/seek_bench.py report data/reports/seek_bench_<date>.jsonl data/seeks.jsonl

`run` refuses unless the app's /api/state says the stick is hidden (`--tv-off` only when Nick says the TV is
off) and never runs on Peacock. Rows name the stick by `--alias` (stickA), never by address. Turn
`replay.seek_to.enabled` on for an app only when its rows PASS: p90 |err| <= 1 s, settle p50 <= 3 s, 0 ads.
