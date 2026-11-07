# Canary failed (daily hidden-stick self-test)

`scripts/canary.py` (code: `redzone/canary.py`) runs before games on the **hidden** stick only. It never runs with
a game live or starting within 90 min, never touches the stick on screen, and always puts the hidden stick back on
live TV. Every run is one row in `data/canary.jsonl`; FAIL and WARN reach the phone with the first failed check.

```sh
.venv/bin/python scripts/canary.py --dry-run     # preflight only: API, no game soon, which stick is hidden
.venv/bin/python scripts/canary.py               # the test (0 PASS, 1 FAIL, 2 deferred, 4 WARN)
.venv/bin/python -m redzone.tuners.join_calib    # per-channel/stick join stats, SHIFT alerts, D_edge
```

| Failed check | What it means | Do this |
|---|---|---|
| `api` | The app's API did not answer: nothing guards tonight. | `launchctl print gui/$(id -u)/com.redzone.everything`; see [deploy.md](deploy.md) / [rollback.md](rollback.md). |
| `standby` | No single connected "standby" stick: two-stick mode is off or a stick is offline. | [stick-offline.md](stick-offline.md). Sticks on wall power (Nov 5: TV USB cut one). |
| `adb` | adb connect / shell failed: adb drop, the stick asleep or rebooting. | `adb disconnect` + `adb connect` that stick (stick-offline.md step 4); rerun the canary. |
| `yttv_app` | YouTube TV not installed / not found on the hidden stick. | Check the stick's apps; an app update can rename the package. |
| `tune_away` / `tune` | The deep link did not confirm playing. "Who's watching" or "Are you still watching" the tuner could not clear, a crash, or no watch code for the channel in `data/channels.json`. | Look at the hidden stick (HDMI input of the standby stick); clear the prompt by hand; rerun. A missing code: paste the channel link in the app's channel settings. |
| `playing` | Tuned, but not playing (paused, a prompt, or the app crashed). | As above. |
| `join` "not measured" | The overlay label was not read after the scrub: the label reader, the overlay or OCR changed (a UI change), or the banner never cleared. Never treat the stick as buffered. | Run `scripts/yttv_scrub_try.py <stick> 60 --label` on the hidden stick and read the row. If the overlay looks different, file it for the label reader thread. |
| `join` "off ... limit 6" | It landed more than 6 s from the predicted 56.7 s. Seen Nov 7 (#391): asked 90, read 23, because a same-channel deep link did not bring a behind-live stick back to live. The canary tunes away first to avoid exactly this. | Rerun once. Twice in a row: the scrub step or lead changed; read `python -m redzone.tuners.join_calib`. |
| `join` "knob moved 39s, not 60s" | A program boundary reset the label mid-scrub (Nov 7 ~1 AM). The join failed closed. | Rerun after the top of the hour. |
| `relink` | The stick could not be put back on live TV after the test. **The hidden stick may be sitting behind live.** | Tune it by hand (any channel) before games; the app's next preload also re-links it. |
| `calib` (WARN) | The newest 3 joins on one channel/stick all landed > 5 s off the calibrated value on the same side: an app update, a new overlay or a stick swap. Calibration stays inside its bounds and will not chase it. | Read the table; if real, measure 9 runs like Nov 7 and update `join.yttv_lead_s` by PR. |
| `crash` | The canary itself crashed. | The row's detail has the exception; file it. |

Deferred (exit 2) is not a fault: a game was live or close. `--once-per-day` reruns it at the next slot.

Daily schedule (Nick's call, a Mac service change): `scripts/canary.py --plist > ~/Library/LaunchAgents/com.redzone.canary.plist`
then `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.redzone.canary.plist` (10:00 and 16:30, skips once
it passed that day). Remove: `launchctl bootout gui/$(id -u)/com.redzone.canary`.
