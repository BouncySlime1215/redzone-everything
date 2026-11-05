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
