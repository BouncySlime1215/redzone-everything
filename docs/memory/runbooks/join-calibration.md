# Joins land off target (self-calibration)

`redzone/tuners/join_calib.py`, flag `join.calib.enabled` (OFF). Every join writes a row to `data/joins.jsonl` with
target, predicted, measured (label / timecode) and the lead or bias its plan used.

```sh
.venv/bin/python -m redzone.tuners.join_calib          # table per app / channel / stick, SHIFT alerts, D_edge
.venv/bin/python -m redzone.tuners.join_calib --json   # the same for the night report
```

| Symptom | Real case | What calibration does | Do this |
|---|---|---|---|
| Every join a few s early | Nov 7: 9 runs at T - 3.3 s | The shipped lead (21.0 s on the 16.8 s-per-LEFT model since Nick's depth sweep; 11.7 on the old 15 n model) already carries it; per channel the lead moves within shipped +- 5 s. | Nothing. |
| One wild landing (-18.7 s) | Nov 7: overlay held for an in-between target | Counted as gross (> 12 s off), never used. Never hold the overlay. | Nothing. |
| "Measured" 75 / 79.66 s hold that wasn't | Nov 6 21:45Z: a flat meter when the session stopped updating | Only label / timecode reads count; estimates and media_session never do. | Nothing. |
| Asked 90, read 23 | Nov 7 #391: same-channel deep link left the stick behind live | Gross: not used. | Tune another channel first (the controller does). |
| Program boundary label reset | Nov 7 ~1 AM: "moved 39 s, not 60 s" | The join failed closed (ok false): counted as failed. | Nothing; it retries from the live edge once. |
| SHIFT alert | (none yet) | Stays clamped; the canary goes WARN. | Measure 9 runs; change `join.yttv_lead_s` by PR. |
| Label read cost | Nov 7: each read adds ~7.5 s | Not calibrated here: the scrub plans around it. Read once after entry. | Nothing. |

Bounds: YouTube TV lead 6.7..16.7 s, Prime bias -3..+3 s, expected error 1..6 s, newest 20 rows, 21 days.
