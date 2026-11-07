# A join landed off target, or a buffer drifted

Code: [redzone/tuners/join_watchdog.py](../../../redzone/tuners/join_watchdog.py) (flags `join.watchdog.enabled`,
`join.drift.enabled`, both off). Lesson: [lessons.md](../lessons.md) "A join that worked was not where we said".

**The rule:** a stick is "buffered" only at a position READ after the entry (YouTube TV overlay label, Prime timecode).
media_session is never a position. Anything not read is "unknown"; the phone shows `control.held`, which only a
verified reading writes.

## What the app does by itself (flags on)

| Seen | Where it shows | What happens |
|---|---|---|
| read within 6 s (YTTV) / 4 s (Prime) of the plan and 10.5 / 5 s of the target | buffer.jsonl `watchdog` status verified | buffered at the READ value |
| read off target (Nov 7: asked 90 -> 130.8; #391 asked 90 -> 23) | `watchdog` status off_target, action reseek | re-tune through another channel, scrub once more |
| still off, or no reading, or <= 5 s, or > max_s + 15 | `watchdog` ... action fallback; `scrub_failed` | fresh live tune; ready only with scrub_fallback live |
| drift read in band | joins.jsonl kind drift, action ok | held = the reading; next read later (x2, max 20 min) |
| drift read past the band (soak: +7.5 s per read) | action reenter, buffer.jsonl `drift_reenter` | not buffered; re-entered off screen (label_stale) |
| drift read at the live edge (plain-pause snap, Nov 6) | action snapped | same |
| no drift read (program boundary label reset, banner) | action unknown | same |

The stick on screen is never read or pressed for any of this. A read on YouTube TV freezes the picture ~7.5 s, so at
most 30 s of reads per stick per hour (`join.drift.budget_s_per_h`).

## By hand

1. `grep -E '"kind": "(watchdog|drift)"' data/joins.jsonl | tail` and `grep -E 'watchdog|drift_' data/buffer.jsonl | tail`.
2. Many off_target on one channel: its landing has a bias. Check `pred_error_s` in `kind: join` rows for that
   channel; self-calibration (item 5 thread) owns the bias. Until then raise nothing: the fallback is safe.
3. Many `unknown` drift reads: the label reader is failing (banner, OCR). Run
   `python scripts/yttv_scrub_try.py <hidden stick> 60 --label --again=300` on the HIDDEN stick.
4. A stick stuck "behind" on a channel (every re-seek reads short): tune another channel, then back (#391).
5. To test on hardware: `RZ_JOINS_LOG=$PWD/data/joins.jsonl python scripts/watchdog_try.py <hidden stick> youtubetv 75 nbatv --reset=espn --drift-min=20`
   (hidden stick only, no game you're watching on it).
6. **Retune before EVERY run** (another channel, then the target; `--reset=<other>` does it). A loop of
   `watchdog_try` runs without it measures from wherever the last run left the stick: LabelProbe anchors at the
   probe, so those numbers are relative to the start, not true behind-live (Nick's rig correction, Nov 7 12:36Z;
   the canary's numbers were always true live-edge).

## Tests

`tests/test_join_watchdog.py`: one case per real failure above (the 9 overseer runs verify; 130.8, 23 and the -18.7
overlay hold are caught; flat media_session is unmeasured; snap, soak and program-boundary drift reads).
