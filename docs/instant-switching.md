# Instant switching: the TV changes the input, the sticks keep playing

Today a switch presses HOME on the stick we flip to (HDMI-CEC Active Source). That shows the
Fire TV launcher, tears down the app's player (Prime reopens on its detail page: Watch -> Watch
Live, 5-50 s), and the stick we leave pauses. Real numbers from Nov 2-3 (data/redzone.db):
preloaded YouTube TV flips took 11-12 s from command to confirmed picture, Prime flips 57-73 s
or never confirmed.

The fix is to let the **TV** change its input. Nothing is pressed on either stick, so the game
we flip to is already playing, and with HDMI-CEC off on the sticks the one we leave never hears
that it lost the input, so it keeps playing too. Every switch becomes a ~1 s flip.

## Flip order (redzone/control.py `_flip_to`)

1. **TV input** (redzone/tv.py), when `scripts/tv_setup.py` proved a path:
   - `local`: Samsung's remote socket on port 8002 (`KEY_HDMI1` / `KEY_HDMI2`). Some models
     ignore these keys, so a local key counts only after tv_setup watched it switch this TV
     (`devices.json tv.local_input_verified`, and the TV's own Wi-Fi MAC matched).
   - `smartthings`: `mediaInputSource.setInputSource` through Samsung's cloud, then the input is
     READ BACK from the TV. OAuth with a refresh token in `data/smartthings_oauth.json`
     (owner-only, never in git), renewed automatically at least every 6 h.
   - Then the stick we flipped to must still be playing within 2.5 s; if it paused anyway it gets
     MEDIA_PLAY (it's on screen now, nothing can be cut), then one fresh link.
2. **HOME** on the destination stick, exactly as before, if the TV path isn't set up or fails.
   Two failures in a row back a TV method off for 5 minutes. A TV command whose delivery was
   ambiguous may already have switched, and HOME on the same destination can only push the same way.
   A stick with CEC off gets CEC turned on for this one HOME and off again 3 s later.
3. The old TV-remote attempt, only if no TV path was set up at all.

Each TV attempt is logged to `data/tv_input.jsonl` (requested input, method, how it was verified,
command and verify latency). `switch_times()` shows `tv_flip` and `home_flip` medians separately.

Also fixed: with only CEC to go on, the sticks' active-source flags alternated every 30 s on
Nov 2-3 with no switch (service.log, 98 "TV is showing" lines), and each one threw away both
standbys. CEC alone must now agree twice in a row before we believe the TV moved without us
(startup and an unknown input still take the first answer). With SmartThings readback, the
TV's own answer is used instead of CEC.

## HDMI-CEC off on the sticks

Why: a Fire TV with CEC on pauses its player when the TV switches away from it. Off, it never
pauses, never grabs the TV by itself, and the TV remote no longer controls it (we use adb).

    .venv/bin/python scripts/stick_cec.py status
    .venv/bin/python scripts/stick_cec.py off      # tv_setup.py offers this once a TV path works
    .venv/bin/python scripts/stick_cec.py on       # back to HOME-only switching

It sets `settings global hdmi_control_enabled` over adb and reads it back. If a stick refuses,
set it by hand on each stick: **Settings > Display & Sounds > HDMI CEC Device Control > Off**.
Only turn it off after a TV path works: HOME needs CEC to take the TV.

## At home tonight (under 10 minutes)

TV on, both sticks on, Mac on the home Wi-Fi (not school, not a guest network).

1. `cd` to the repo and run `.venv/bin/python scripts/tv_setup.py` (about 2 minutes).
   - It finds the TV by its MAC (the saved IP .151 was never verified), reads its model,
     and asks you to press **Allow** on the TV once.
   - It switches the TV between the sticks four times; answer **y/n** each time it asks whether
     the TV shows that stick.
   - If the TV's own keys don't work, it moves on to SmartThings: on your phone open
     https://account.smartthings.com/tokens , make a token with Devices + Apps, paste it, open the
     link it prints, press Authorize, and paste back the address you land on (about 3 minutes).
   - Say **y** when it offers to turn HDMI-CEC off on both sticks.
2. If it printed a cause instead (wrong subnet, isolation, port closed, IP Remote off, pairing
   denied), change the TV menu it names and run it again. The usual ones: Settings > General >
   External Device Manager > Device Connection Manager > Access Notification; Settings > General >
   Network > Expert Settings > IP Remote = On, Power On with Mobile = On.
3. Restart the service so it loads the new path: `launchctl kickstart -k gui/$(id -u)/com.redzone.everything`.
4. Check one switch from the phone: the new game appears in about a second, with no Fire TV home
   screen, and `tail -1 data/tv_input.jsonl` shows `"verified": true` or `"trusted": true`.

## Known limits

- Nothing here has touched the real TV yet; every path is proven with fakes only.
- A local key has no readback on this TV. It is trusted because tv_setup watched it work, plus the
  CEC view when CEC is on. With CEC off and no SmartThings, a wrong input would go unnoticed until
  the next check. Setting up SmartThings as well gives readback for both.
- The SmartThings app is created through `POST /v1/apps` with the token you paste (what the
  SmartThings CLI does for an OAuth-In app). If that call is refused, make the app with the CLI and
  pass `--client-id/--client-secret`. The default redirect is https://httpbin.org/get, which just
  echoes the code back to you.
