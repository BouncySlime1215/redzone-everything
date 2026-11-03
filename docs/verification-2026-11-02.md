# Verification — 2026-11-02

These are software and archived-event simulations. They are not measurements of a Fire Stick, monitor, broadcast pixels, actual tune latency, or exact DVR seek position.

## Tests and preview

Final full suite: **507 passed**, one dependency deprecation warning, in 26.20 seconds. `git diff --check` passed. Focused suites include the four parallel reviews and all recovered provider/discovery tests.

## Current scorecards

No arbitrary rate, dwell, gap or switch-count freeze is active. Each baseline and combined-fault run below had **zero modeled mid-play cuts**. Stricter live/replay credit and conservative timing reduced some coverage compared with earlier reports. The scorer was not relaxed to preserve old percentages.

| League | Sample | Baseline big plays seen | Baseline dead time | Fault big plays seen | Fault dead time |
|---|---:|---:|---:|---:|---:|
| NFL | 16 slates | 93.7% | 7.4% | 31.0% | 38.3% |
| NBA | 92 games | 89.7% | 19.2% | 66.7% | 23.6% |
| NHL | 95 games | 99.5% | 23.1% | 58.6% | 30.5% |
| MLB | 40 games | 67.2% | 25.7% | 60.1% | 28.0% |

Full results, including crunch coverage and separate modeled tuning blackout, are in [scorecards-2026-11-02.json](scorecards-2026-11-02.json). NFL baseline modeled tuning blackout is 4.8%; this is separate from 7.4% modeled stoppage/dead time and uses a different denominator.

Baseline commands: `scripts/eval.py` and `scripts/eval_sports.py SPORT --nights 999`, run with `.venv/bin/python`. Combined-fault flags: `--true-delay 41 --sigma 5 --lag-jitter --drop --late-plays --no-wallclock` (seed 0). This uses 3–25 second delivery jitter, intermittent 60–120 second feed gaps, 15% late/out-of-order events and 50% missing wallclocks. One seed is coverage, not an exhaustive stress proof.

## Evidence boundaries

- Timing comes from public sports feeds and estimated broadcast delay. HTTP receipt freshness is not a completeness watermark; buffering, editorial replays and unreported action can violate model assumptions.
- NFL/MLB live credit requires the whole modeled play interval. NBA/NHL credit covers logged events, not entire possessions. Replay credit waits for modeled uninterrupted completion; it does not prove which footage a real provider sought to.
- The archive loader estimates missing timestamps and makes time monotonic. An audit found 66 NFL entries without original wallclocks, including some real actions. Archives are not exact video ground truth.
- The offline model does not exercise the full asynchronous device transaction, wake/CEC behavior, provider prompts or real display readback. Those have synthetic regression tests and still need hardware testing.
- Cached old optimizer/Lab reports may belong to an earlier build and different settings; this report supersedes those claims.

## Recovery and regression coverage

Recovered persistent ADB, standby planning, optional Fastcast, provider discovery and autonomy from the original Claude project/worktrees in an isolated checkout. Added checks for partial-write ambiguity, command cancellation, stale stream success, failed seek, verified input changes, unknown power, payment/account screens, channel-map verification, deferred commits, precise calibration, timestamp uncertainty, event ordering, whole exit windows, actual target latency, replay completion and service shutdown.

At the time of this recovery baseline, physical setup was pending and no device operations or real playback benchmark had been performed. Later November 2 device observations are documented separately in [the initial device trial](device-trial-2026-11-02.md); they do not retroactively validate these modeled results. A monitor without supported input readback uses single-stick mode. Vega is unsupported by the current ADB controller.

The default dry-run preview was exercised through HTTP and the phone UI. Live provider feeds returned data; that does not validate device playback or account access. Fastcast remains disabled by default pending current major-league validation.
