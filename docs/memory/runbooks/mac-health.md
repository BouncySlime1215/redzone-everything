# The phone says the Mac is the problem

Alerts from `redzone/platform/mac_health.py` (supervisor.mac_health, **OFF** until Nick turns it on). They only
see and say: nothing here changes the Mac by itself. Each alert below names the real night it comes from.
Turn on: `supervisor: {mac_health: {enabled: true}}` in data/config.yaml (and `ntfy_topic` for pushes;
`python -m redzone.supervisor push-test` checks the push). Tests: `tests/test_mac_health.py`.

| Alert | Real case (Mac logs) | Do this |
|---|---|---|
| **battery low** (<= 20% high, <= 10% urgent) | Nov 4: 19% at 11:00 PM, 13% at 12:44 AM ET with games on; Nov 5: 19% at 7:46 PM ET | Plug the Mac in. On battery with the lid closed macOS sleeps even with caffeinate. No deploy until it is on wall power ([deploy.md](deploy.md)). |
| **on battery** (2 min with live games) | Nov 4 evening and Nov 5 (RC6.3 deploy skipped: Mac on battery) | Plug it in before the battery alert comes. |
| **slept** (asleep or frozen 60 s+ between ticks) | the app reconnects everything after a wake (`redzone/platform/wake.py`) | Lid open, on power. Check the TV shows a game; the supervisor's `screen_not_game` repairs the visible stick. |
| **keep-awake** (caffeinate not running 2 min with live games) | preventive | Restart the app from the phone or `launchctl kickstart -k gui/$(id -u)/<service label>` (needs Nick's OK: a Mac service change). |
| **memory** (pressure critical 1 min, or swap >= 8 GB 5 min with live games) | Nov 5 8:13 PM ET: 10.0 GB swapped; 5.8-9.5 GB on every eco step Nov 3-7 | Close Chrome tabs, Xcode, other apps. 3-6 GB swapped is this Mac's normal evening and never alerts. |
| **paused** (auto-switching paused 10 min with live games) | preventive | Resume from the phone, or ignore if the pause is on purpose (repeats every 30 min). |
| **no switches** (no switch row 20 min, 2+ live games, autopilot on) | Nov 5 10:12 PM -> 12:37 AM ET (145 min); Nov 6 7:20 -> 8:26 PM (66), 8:26 -> 9:35 PM (69), 10:19 -> 11:41 PM ET (82), up to 10 live games, command locks stuck at 151-158 s, stale feeds | Look at the phone's status: stuck_lock / stick_hung / stale_feed faults say which part. A stick hung: [stick-offline.md](stick-offline.md). Nothing obvious: restart the app (Nick's OK). |

## Morning: the night ops section

`supervisor.night_ops.enabled` (OFF) adds "Sync and health" to data/reports/YYYY-MM-DD.md and one line to the
morning push: joins per app (how many were read, how many landed within `join_margin_s` = 10 s, |error|
p50 / p90 / max, bias, vs predicted), fallbacks per step, drift reads and their cost, invariant faults, fixes
that didn't work, and the Mac alerts. By hand, read-only, any night:
`.venv/bin/python -m redzone.platform.night_ops 2026-11-06` (`--json` for the numbers).

Inputs it reads (numbers only, no addresses): `data/joins.jsonl` (PR #393), `data/fallbacks.jsonl` (ladder
steps buffered / live / other_stick / hold), `data/drift.jsonl` (drift_s, read_cost_s, action ok / reenter /
live), `data/supervisor.jsonl`. A file that isn't written yet shows as "none".
