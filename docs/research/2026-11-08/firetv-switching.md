# Fire TV app and channel switching over adb: research (2026-11-08)

Scope: fastest and most reliable app/channel switching on Fire TV sticks driven by adb. Method: every source
below was opened (fetched) on 2026-11-08. "Verified" means I read the page; it does NOT mean the behavior was
re-tested on Nick's sticks. Forum posts are anecdotal and marked so. Nothing here changes code.

Already covered by the repo (not repeated as new advice): persistent `adb shell` per stick
(`AdbShell`, redzone/tuners/adb.py), 10 s reconnect keepalive (`Controller.keepalive`, `Controller._reconnect`),
plain `am start` deep links with `-W`/`-f 0x14000000` options (`Stick.view`, `AppTuner.command`,
`providers._fresh`), wake check with TTL (`Stick.ensure_awake`), sleep/screensaver settings
(`Controller._stick_watchdog`, redzone/control.py ~L979-995), `dumpsys media_session` parsing
(`parse_playback`, `PLAYBACK_CMD`), OSD scrub (redzone/tuners/yttv_seek.py).

## 1. Top 10 findings (ranked by impact on switch time and reliability)

1. **New Fire TV sticks run Vega OS, not Android, and cannot be controlled like the current ones.** The
   2025 Fire TV Stick 4K Select, the 2026 Fire TV Stick HD and the new 3rd-gen Fire TV Stick 4K run Vega OS: no
   APK sideloading, Linux-based, apps must be rewritten, some Android-only apps are cloud-streamed. Fire OS (Android)
   models that still sideload: Stick 4K Plus, 4K Max, Cube, 1st-gen Stick HD (2024), older sticks. Reporting says
   all future sticks will be Vega. The sources I read do not say whether Vega exposes `adb shell`/`am start`
   for third-party control; treat it as unusable until proven. Existential risk if a stick dies and is replaced
   with the wrong model. [S1, S2, S3]
2. **YouTube TV launch targets drifted in 2026; address the package, not the activity.** Community reports
   (anecdotal) show three activity names in use: `dev.cobalt.app.MainActivity` (Fire TV, package
   `com.amazon.firetv.youtube.tv`), and on Google TV `...tvunplugged.activity.MainActivity` changing to
   `...ChrobaltMainActivity` around 2026-05-31. Channel code format `https://tv.youtube.com/watch/<code>`. ADBTuner
   keeps an optional alternate package and activity per channel for the same reason. RedZone's YTTV tuner sends a
   package-only VIEW intent (no pinned activity), which survives such renames. Keep it that way. [S5, S6, S7]
3. **Display-on and warm app is the difference between ~3 s and ~11 s.** ADBTuner's author logged YouTube TV tunes of
   3.42 s with the display on vs 11.29 s with it off, 2-3 s best case; Hulu slightly slower, NBC app slower again;
   ADBTuner deliberately leaves apps running between tunes (anecdotal, 2023). The HA YTTV author sleeps 3 s after
   wake before sending the link. Supports the existing `ensure_awake` + keep-both-sticks-awake design. [S6, S5]
4. **Only one adb client at a time per stick, and external adb is still allowed on Fire OS.** Home Assistant docs:
   only one client can connect at a time; a second one (HA, scrcpy, phone remote app) blocks yours. Amazon blocked
   *on-device* apps from making local adb connections in Fire OS 7.6.6.9 / 8.1.0.3 (Feb 2024); external connections
   from a computer still work. RedZone connects from the Mac, so it is unaffected, but any helper on the stick
   that shells out to adb is not. [S8, S9]
5. **Fire OS kills apps for memory; plan for cold starts on the hidden stick.** Amazon (Jul 2026): devices have
   1-4 GB RAM; out-of-memory termination is the most common unexpected closure on entry-level devices; guidelines
   are <400 MB foreground for 4K apps, <100 MB background, cold-start time to first frame <2 s and fully drawn
   <10 s. No kill thresholds are published. Implication: one heavy app per stick in the background; opening a
   second app (Prime search, ESPN) on the same stick can evict the warm one. [S10, S11]
6. **ESPN has a native deep link scheme that works for some networks.** Verified-from-forum (Nov-Dec 2025):
   `am start -n com.espn.gtv/com.espn.startup.presentation.StartupActivity -d 'sportscenter://x-callback-url/showWatchStream?playID=<id>'`
   worked for ESPN+ events and the ESPN, ESPN Deportes and ESPNU lanes; ACCN, ACCNX, ESPN2, ESPNews, SEC lanes did not;
   Apple's `playChannel=` form failed on Fire TV. RedZone's `EspnTuner.url` uses `https://www.espn.com/watch/player/_/id/<id>`;
   no evidence in what I read about which is faster. [S12, S13]
7. **Prime Video: no authoritative live-event deep link documented; expect prompts.** A Dec 2025 community config
   for Prime on Fire TV needed three OK presses to clear the profile screen and the "Watch Live" prompt, and blind
   OK presses can subscribe a user to a premium service. Amazon's own docs only document the generic `am start -a -n -d`
   test pattern (Feb 2026). RedZone already guards this (`press_after`, `is_purchase_screen`). I found no verified
   Fox Sports or NBC-specific link formats beyond an ADBTuner example `nbctve://` link. [S13, S14, S15]
8. **media_session position is only trustworthy if the app updates it.** Android semantics: `getPosition()` plus
   `getLastPositionUpdateTime()` and `getPlaybackSpeed()` let you extrapolate; Amazon's media-session doc (Jun 2026)
   says rewind/forward/restart arrive via `onSeekTo(pos)`, relative only (no absolute clock times). RedZone's own
   measurement (yttv_seek.py header) is that YouTube TV's reported position is wrong after a seek, so the OSD scrub
   plus label OCR stays the ground truth. I found no source for `cmd media_session` seeking; unverified,
   test with `adb shell cmd media_session help` on-device before relying on it. [S16, S17, S18]
9. **Sleep/screensaver settings are real but version-dependent and may be reset by updates.** AFTVnews (2017) and
   a 2025 forum thread: `settings put secure sleep_timeout <ms>` and `settings put system screen_off_timeout <ms>`;
   success reported on Fire OS 7.6.8.8, failures reported on Fire OS 5/6 and (one commenter) Fire OS 8; one
   report warns updates reset values. Fire OS 7.6.7.9 (Aug 2024) added a "sleep mode" option that stops HDMI
   output when idle (a TV can then power off). RedZone writes both settings plus `screensaver_enabled 0`;
   it does not read them back. [S19, S20, S21]
10. **adb over Wi-Fi drops on idle, more on older sticks.** Home Assistant reports (anecdotal, 2022-2025):
    2nd-gen stick drops every couple of days, 4K/Max less often; a Wi-Fi stick went unavailable after a day idle while an
    Ethernet one stayed up; fix was a power cycle or toggling debugging. Amazon's docs: port 5555-5585, authorize
    once with "always allow", troubleshooting is `adb kill-server`, then `adb connect`. scrcpy works over the same
    connection but is one more adb client (see 4) and adds encode load, so it is a debugging tool, not a runtime path. [S22, S23, S8, S24, S25]

## 2. Recommendations mapped to RedZone code

Ranked by value per effort. All are suggestions for a later PR; each must show the before/after table from CLAUDE.md.

1. **Vega guard** (finding 1). In `Stick.identify` (redzone/tuners/adb.py) read the software line
   (`getprop ro.build.version.release`, plus the About page string "Fire OS" vs "OS 1.x") and set a visible
   `not_controllable` reason. Surface it in `Controller.two_stick_why`. Add a line to
   docs/memory/runbooks (TV setup) "replacement sticks: Fire OS only (4K Max / Plus / Cube)". Effort: ~1 hour.
2. **Resolve and log the YTTV activity at startup** (finding 2). `AppTuner.components` is empty for YTTV (good). Add
   a read-only `cmd package resolve-activity --brief -a android.intent.action.VIEW -d https://tv.youtube.com/watch/x <pkg>`
   (syntax unverified on Fire OS; check `cmd package help`) to `Stick.identify`, store it in the stick view, and
   alert if it changes. Gives an early warning before a rename breaks a night. Effort: ~1 hour.
3. **Read back the sleep settings** (finding 9). In `Controller._stick_watchdog` after the `settings put` batch,
   run `settings get secure sleep_timeout` / `settings get system screen_off_timeout` and log when a Fire OS
   update reset them; re-apply on every reconnect (`Controller._reconnect`). Fire OS 8 sticks may ignore them, so
   also count `mWakefulness` flips in `Stick.ensure_awake` as a metric (it already asks every 60 s via `AWAKE_TTL_S`). ~1 hour.
4. **Single-client discipline** (finding 4). Document in the runbook and the dashboard: stop HA/scrcpy/phone
   remotes before a night. In `Stick.connect` the `unauthorized`/`offline` states already exist; add a hint to
   `connect_error` ("another adb client may hold the stick"). ~30 minutes.
5. **Cold-start budget per app** (finding 5). `LatencyBook` already keeps per-app medians (`record`, `median`,
   `note_preloaded`, `miss_rate`). Add a "was the app warm" flag (foreground/`dumpsys activity processes` check via
   `Stick.foreground`) to the recorded latency so slow tunes are attributable to evictions, then use `miss_rate`
   to decide whether the hidden stick may open a second app. ~2 hours.
6. **A/B ESPN deep link forms** (finding 6) behind a flag, only through the existing lab: compare `EspnTuner.url`
   (web link) against the `sportscenter://...showWatchStream?playID=` form with the `StartupActivity` component via
   `AppTuner.components`, measuring tune-to-PLAYING with `NewStream` (redzone/tuners/tuners.py). Per network,
   because the forum shows only some lanes work. Needs the playID source (ESPN Watch Graph, already in `redzone/sources/eventlinks.py` per the 2026-11-03 research notes). ~half a day.
7. **Seek:** keep `yttv_seek.enter_buffer`. If a cheap probe of `cmd media_session` works on a stick, use it only
   as an extra read (never to seek) and verify by the OSD label. Low priority.
8. **Ethernet for the older stick** (finding 10): costs money (adapter), so ask Nick first ($0 rule). Otherwise
   keep the 10 s keepalive; consider a Wi-Fi power-save/idle check by comparing `Stick.session_status` drops per night.

## 3. Risks

- Vega OS: a failed or replaced stick may be uncontrollable (finding 1). No mitigation except buying Fire OS models while they exist.
- Forum-derived formats (ESPN, YTTV, Prime) are anecdotal and change without notice (YTTV changed in 2026). Any new link form ships OFF behind a flag.
- Sleep settings: may be ignored on Fire OS 8 and reset by updates; unverified on Nick's exact firmware.
- Pressing OK blindly can start a paid subscription (Prime thread). Keep `is_purchase_screen`.
- Memory: a second app on the hidden stick can evict the warm one; no published kill threshold.
- I could not verify: Prime live-event deep link format, Fox Sports and NBC app links/activities, `cmd media_session` seek, Vega adb support, and the `-W`/`-S`/`-f` flag semantics from an official page (the repo's own measurements in adb.py are the only evidence; Amazon's doc lists only `-a -n -d`, and mentions `-f`/`-e` in the encoding step).
- ADBTuner's own page is "all rights reserved, open-source pending", so no code to borrow.

## 4. Bibliography (all opened 2026-11-08)

S1. AFTVnews, Vega OS tag index (Sep 25 to Oct 21, 2025): https://www.aftvnews.com/tag/vega-os/page/2/ (sideloading, cloud apps; Oct 16, 2025 "sideloading is truly dead")
S2. AFTVnews, models without sideloading, 2026-10-05: https://www.aftvnews.com/these-are-the-fire-tvs-that-dont-support-sideloading-or-downloader-due-to-vega-os-replacing-fire-os/
S3. 9to5Google, 2026-04-17: https://9to5google.com/2026/04/17/amazon-fire-tv-android-vega-switch/
S4. Android Central, Amazon on Fire OS vs Vega (opened; body not returned by the fetch, headline only, undated): https://www.androidcentral.com/streaming-tv/amazon-fire-tv/amazon-not-giving-up-android-fire-tv-os (not relied on)
S5. Home Assistant community, "YouTubeTV channel with ADB" (2021-02 to 2026-07-08; 2026-05-31 activity rename): https://community.home-assistant.io/t/youtubetv-channel-with-adb-channel-codes/284593?page=2 (anecdotal)
S6. Channels community, ADBTuner thread, 2023-07-06 (benchmarks 3.42 s vs 11.29 s): https://community.getchannels.com/t/adbtuner-a-channel-tuning-application-for-networked-google-tv-android-tv-devices/36822 (anecdotal)
S7. ADBTuner site, (c) 2024: https://adbtuner.github.io (features, compatibility mode, alternate package)
S8. Home Assistant Android TV integration docs (current): https://www.home-assistant.io/integrations/androidtv/ (one adb client, Wi-Fi drops, state detection)
S9. AFTVnews, Amazon blocks on-device adb, 2024-02-23: https://aftvnews.com/amazon-blocks-long-running-fire-tv-capability-breaking-popular-apps-with-no-warning-and-giving-developers-the-runaround
S10. Amazon, KPIs for Fire OS apps, updated 2026-07-23: https://developer.amazon.com/docs/app-testing/measure-kpis-fire-tv-apps.html
S11. Amazon developer blog, Blueprint pt. 4 (stability), 2026-07-24: https://developer.amazon.com/apps-and-games/blogs/2026/07/blueprint-series-pt4-stability
S12. Channels community, ESPN+ via ADBTuner, 2025-11-13: https://community.getchannels.com/t/espn-with-custom-channels-via-adbtuner/45008/51 (anecdotal)
S13. Channels community, FruitDeepLinks, 2025-12-26/27: https://community.getchannels.com/t/fruitdeeplinks-universal-sports-aggregator-for-channels-dvr/45197?page=10 (anecdotal)
S14. Amazon, deep-linking test pattern, updated 2026-02-02: https://developer.amazon.com/docs/fire-tv/deep-linking-featured-content.html
S15. Home Assistant community, Helix channel via `am start` on Fire TV, 2024-11: https://community.home-assistant.io/t/auto-selecting-a-specific-channel-in-my-cable-provider-s-app-helix-on-fire-tv/792200 (anecdotal; shows component-addressed `am start`)
S16. Android developers, PlaybackState reference (undated): https://developer.android.com/reference/android/media/session/PlaybackState
S17. Amazon, Media Session API on Fire TV, updated 2026-06-05: https://developer.amazon.com/docs/fire-tv/mediasession-api-integration.html
S18. python-androidtv on GitHub (maintainer inactive; reads state via adb): https://github.com/JeffLIrion/python-androidtv
S19. AFTVnews, custom sleep/screensaver via adb, 2017-05-08 (old): https://www.aftvnews.com/?p=21237
S20. AFTVnews, sleep mode that stops HDMI output, 2024-08-13: https://www.aftvnews.com/older-fire-tv-sticks-can-now-let-your-tv-turn-off-when-they-go-to-sleep/
S21. Home Assistant community, Fire TV screensaver via adb keyevents, 2022-03-19: https://community.home-assistant.io/t/control-of-androidtv-firetv-screensaver/403748
S22. Home Assistant community, "Fire TV frequently disconnecting", 2022-09: https://community.home-assistant.io/t/fire-tv-frequently-disconnecting/460667 (anecdotal)
S23. Amazon, connect adb to Fire TV, updated 2022-06-28: https://developer.amazon.com/docs/fire-tv/connecting-adb-to-device.html
S24. Android developers, adb overview (wireless debugging, reconnect, undated): https://developer.android.com/tools/adb
S25. i12bretro, scrcpy with Fire TV (undated): https://i12bretro.github.io/tutorials/0357.html
S26. AFTVnews, remote control via adb, keycodes, 2014-11-28 (old): https://www.aftvnews.com/?p=3249
