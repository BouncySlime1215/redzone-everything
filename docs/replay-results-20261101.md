# November 1 replay results

Ran the exact Eastern sports date in an isolated historical clock. No device commands, provider playback, visual detector evaluation or live-delay calibration were performed.

| League | Games | Baseline big plays seen | Combined-fault seeds 0 / 1 / 2 | Modeled cuts in all four runs |
|---|---:|---:|---|---:|
| NFL | 12 | 136/137 | 52 / 62 / 49 out of 137 | 0 |
| NBA | 4 | 1/1 | 1 / 1 / 1 out of 1 | 0 |
| NHL | 5 | 36/36 | 29 / 25 / 31 out of 36 | 0 |

MLB: ESPN returned no events for November 1. No substitute date was used.

The NBA denominator is only **one** clutch-scoring event. Its 1/1 is not broad highlight or play-detection validation. In fault seed 0, the eight-second exit auditor allowed **zero** departures and held all 3,477 archive-clear grid samples. Safety alone does not establish a useful switching experience.

The counterfactual eight-second exit audit found zero unsafe-permission episodes and zero permissions in unknown archive intervals across these runs. It evaluates every game, not just the one selected. The five-second grid does not certify behavior between samples. A separate selection scorecard uses the existing offline director; neither executes the actual asynchronous controller.

Raw timestamp quality: NFL 2,212 entries, zero missing/backward and 48 repeated timestamps; NBA 1,999 entries, zero missing but 36 backward transitions (largest 253 seconds) and 821 repeats; NHL 1,570 entries, zero missing/backward and 68 repeats. Repaired timing intervals are excluded as unknown. Broadcast editorial timing and true on-screen boundaries are not present in these feeds.

Baseline plus combined delay/feed faults used seeds 0, 1 and 2. Clock assumptions, source hashes, anomaly counts, all denominators and missed clear opportunities are preserved in [the machine-readable result](replay-results-20261101.json). Detailed modeled switch timelines and human cue sheets remain local in `data/lab/replay_20261101_final/`.

Validation: 30 focused replay/measurement/capture tests passed; after exact-date downloader regressions, the final full suite, including connection diagnostics, passed **532 tests** in 24.40 seconds with one existing dependency warning.

Next: follow [the supervised replay protocol](replay-testing.md) once the exact Fire Stick is reachable and authorized. Measure actual display departure, first picture, correct matchup and visible play boundaries; do not use yesterday's VOD to calibrate today's live stream.
