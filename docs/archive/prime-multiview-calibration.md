# Prime NBA multiview: live calibration plan

For the next Prime NBA night (PR #40, flag `tuners.prime_multiview`, off). About 15 minutes of
Nick's time, at home, before tip-off plus a few checks during the games. Not tonight: no live TV or
stick tests until Nick says so.

## Rehearsal (any time, no stick, ~10 s)

`.venv/bin/python scripts/prime_multiview_probe.py --fake --gap 0` runs the whole probe against a
simulated Prime player (redzone/lab/prime_mv_fake.py) and prints the same result block as the real
run: it should end `error: null, can_switch: true, can_build: true` with 20/20 bench switches. It never
writes data/prime_multiview_keys.json. If it fails, the probe is broken, not the stick: don't spend
Nick's 15 minutes on it. tests/test_prime_multiview_rehearsal.py runs the same in CI (swap and rotate
layouts, an unreadable DRM player, a dropped tile switch) and grades a synthetic night with
scripts/prime_multiview_grade.py.

## Before (app paused, ~10 min, no game on screen yet)

1. Pick the Prime stick. Sticks: .150 = HDMI1, .181 = HDMI2 (see data/devices.json). The other
   stick keeps YouTube TV and any excluded game.
2. In data/config.yaml, under `score.personal`, add `multiview_exclude: ["nba:WSH"]` (and
   `multiview_pin` if a game should always get a tile). Defaults stay empty in git.
3. Find the first game's gti (the phone's game card shows its target `prime:<gti>`).
4. Run the probe:
   `.venv/bin/python scripts/prime_multiview_probe.py --stick <host>:5555 --gti <gti> --rounds 20`
   It answers three questions, in this order. Stop at the first "no":
   - **Can uiautomator read the player?** (`player_readable`, `focus_check`). No: the feature can't
     verify a tile, so it stays off. Send probe.json; the follow-up is an accessibility reader.
   - **Did it find the multiview entry and the picker by itself?** If not, it asks you to do it with
     the remote and type the keys. That's fine; they're saved.
   - **Bench gate** (`gate_bench`): >= 95% of 20 timed switches on the right tile in under 2 s.
5. Output: data/prime_multiview_keys.json (written only when it can switch) and
   data/lab/prime_multiview/<time>/probe.json. Send probe.json to the coordinator.

## During the games (flag on for this night only)

6. Only if step 4 passed: set `tuners: {prime_multiview: true}` in data/config.yaml and restart the
   app. Leave everything else as tonight's release.
7. Watch for 2 things: a tile switch that lands mid-play, and a wrong game or audio after a switch.
   Either one: set the flag back to false and restart. Nothing else needs your hands.

## After (next morning, cloud or Mac, ~1 min)

8. `.venv/bin/python scripts/prime_multiview_grade.py --json data/lab/prime_multiview/night.json`
   It reads the `prime_tile` switches from data/redzone.db and the supervisor's graded cuts
   (data/supervisor_cuts.jsonl). **Gate:** >= 20 tile switches, >= 95% on the right tile in under
   2 s, 0 mid-play entries, 0 cuts that left a play. Ungraded switches block a pass.
9. Pass: the flag can go on in config for Prime NBA nights (a decisions.md line, then default stays
   off in git until a second night passes). Fail: flag back off; the PR notes what missed.

## Known ways it can miss

- The verification read (uiautomator, 1-3 s) is inside the 2 s: if the picture changes in ~1 s
  but the read is slow, note it and we time the picture separately next night.
- The standby planner can deep-link a non-tile Prime game onto the multiview stick, which closes
  the multiview (it rebuilds on the next Prime preload). Count rebuilds in the app log.
- Prime may not offer every game in the picker. Unoffered games go on the other stick.
