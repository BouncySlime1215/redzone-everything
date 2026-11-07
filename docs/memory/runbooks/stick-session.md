# Stick session failures (session guard)

Code: `redzone/tuners/session_guard.py` (flags `session_guard.enabled`, `session_guard.act`, both off).
Tests: `tests/test_session_guard.py` (one per failure below). Nick's order, Nov 7 07:33 UTC, item 4.

**Rules for every row.** Fail closed: a stick whose verdict isn't `ok` is never shown as buffered and never flipped to
on an old reading. Keys go only to the HIDDEN stick, except the prompts that already cover the picture (YouTube TV's
profile picker, "Still watching?", the auto-play prompt). The auto-play prompt is answered only "Ask later", and only
when the screen read proves "Ask later" is the highlighted button. Nothing here changes account settings. At most 3
recoveries per stick per 10 min, then hold and alert. Screenshots are OCR'd for words and numbers, then deleted.

**Check a stick by hand (read-only, never presses):**

```sh
python -m redzone.tuners.session_guard <stick>:5555 --pkg youtubetv            # adb facts only
python -m redzone.tuners.session_guard <stick>:5555 --pkg youtubetv --screen   # + one screen read (deleted)
python -m redzone.tuners.session_guard <stick>:5555 --pkg prime --looks 3 --gap 10   # crash/reboot across looks
```

| Kind | What it looks like | Evidence (date) | Detected by | Action (hidden / on screen) | Buffer |
|---|---|---|---|---|---|
| `adb_drop` | stick stops answering adb | e2e night seed 2 (#86): stick_drop 87 s while a prompt was up | `cat /proc/uptime` fails, or the adb breaker is open | `reconnect` (no keys) / same | unknown |
| `reboot` | stick comes back on the Fire TV home | fault drill `reboot_visible` (lessons: "A rebooted stick on screen was never given its game back") | `/proc/uptime` fell by > 30 s between looks (kept across an adb drop) | `relink_live` / `restore_target` | lost |
| `app_crash` | app restarted or gone | fault drill `app_crash` | `pidof <app>` changed or vanished since the last look (our own tunes reset the anchor) | `relink_live` / `restore_target` | lost |
| `crash_dialog` | "isn't responding" / "has stopped" over the app | drill text; Fire OS standard dialog | OCR words | `hold` + alert, never pressed | lost |
| `fire_tv_home` | Fire OS launcher in front | Nov 2-3 device trial: HOME is the only input switch, Prime loses its player | foreground package | `relink_live` / `restore_target` | lost |
| `fire_os_profile` | Fire OS's own "Who's watching?" after a wake | screen_prompts docstring (Nov 5) | Fire OS package + picker words | `hold` + alert, never pressed | lost |
| `account_screen` | payment, sign-up, sign-in or UNAVAILABLE page | Nov 7 live test: standby kept picking a Prime game whose page said UNAVAILABLE | `providers.classify_screen` / `is_purchase_screen`, an "UNAVAILABLE" line | `hold` + alert, never pressed | lost |
| `yttv_profile_picker` | YouTube TV "Who's watching?" | YouTube TV's Cobalt picker after an app restart (screen_prompts.py) | picker words, YouTube TV in front | `clear_profile` (configured profile, lit-up proof; both sticks) | lost |
| `still_watching` | "Are you still watching?" | e2e night tests (#28 in-wait repair, #86 seed 2) | prompt words | `clear_still_watching` (yes-button lit-up proof; both sticks) | unknown (the picture paused behind it) |
| `autoplay_prompt` | YouTube TV asks about auto-play | Nick's rule, Nov 7: only "Ask later" | "auto-play" words | `ask_later` only when "Ask later" is THE highlighted line; otherwise `hold` | unknown |
| `player_error` | "Something went wrong" / playback error | general | OCR words | `relink_live` / `restore_target` | lost |
| `prime_detail_page` | Prime shows the title page, not the player | Nov 7: the first CENTER only wakes the nav (draft #390, DOWN vs RIGHT pending Nick) | Prime package, not playing, ≥ 2 detail words (Watch Live, Start Over, ...) | `prime_start` (the #390 tuner path) / `hold` | lost |
| `post_tune_banner` | LIVE/title banner sits up 20-25+ s after a tune, looks like the overlay, keys do nothing | Nov 6 live scrub runs | < `banner_s` (28 s) since the tune and no scrub label read | `wait` the rest of the window (max 30 s) before the scrub's first key | valid if playing |
| `label_reset` | overlay label restarts at a new program | Nov 7 ~1:00 AM: "knob moved 39s, not 60s" at a boundary | label < expected − 20 s, label > expected + 45 s, or the program window changed | `reanchor_label` (no keys; yttv_seek's boundary retry does the re-scrub) | unknown |
| `ui_unknown` | a prompt or layout we have no rule for | #356 (Nov 5-6): LEFT x9 with focus unknown seeked a stick 60 s | short button-like lines (Got it, Try it, Not now, ...) or expected overlay parts missing | `hold` + alert, never pressed; the log keeps a hash, not the words | unknown |
| `wrong_app` | another app in front | Nov 6: keys meant for YouTube TV landed in Prime | foreground package ≠ the session's app | `relink_live` / `restore_target` | lost |
| `not_playing` | paused, stopped, state unread | Nov 6 21:45Z: plain pause snaps back to live 20-30 s after resume | media_session state | buffering: `wait`; paused/stopped: `relink_live` / `restore_target`; unread: `wait` | unknown |

## What runs where today

- `session_guard.enabled`: before every scrub (`Controller._scrub_into`), the guard waits out the post-tune banner and
  takes one adb-only look. Any verdict other than `ok` refuses the scrub with no key pressed and logs
  `session_guard` to `data/buffer.jsonl`; a `lost` session is re-tuned at the live edge instead of reused.
- `session_guard.act`: allows `SessionGuard.recover()` to run the actions above through the caller's handlers.
  Nothing calls `recover()` yet; the fallback ladder (item 2) and join watchdog (item 1) are its callers.
- The phone and night report read `SessionGuard.summary()` (counts by kind, alerts, blocked, recoveries).

## When a new failure shows up

1. Run the probe above with `--screen` on the hidden stick while it shows the problem.
2. Add a row here, a test in `tests/test_session_guard.py`, and (if no rule matched) the words to the right list in
   `session_guard.py`. A new dialog is `ui_unknown` until it has a row: that is the point.
