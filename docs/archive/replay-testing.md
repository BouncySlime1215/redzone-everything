# Testing when there are no live games

Yesterday's full-game replays are useful. We use two separate tests: an isolated archived-feed
simulation for selection/timing logic, and supervised device observations for playback and
switching. Neither is a substitute for measuring a real live broadcast's delay.

## 1. Exact-date offline test (ready; no device connection)

November 1, 2026 means the Eastern sports date, including games starting after midnight UTC.
The public archive has 12 NFL, four NBA and five NHL games. ESPN lists no MLB games for that
date; the report records that rather than choosing another date.

```bash
# Public metadata only, when an exact archive date is missing:
.venv/bin/python scripts/download_recent.py nfl --date 20261101
.venv/bin/python scripts/download_recent.py mlb --date 20261101

# No network or device commands in this runner:
.venv/bin/python scripts/replay_check.py 20261101 --out data/lab/replay_20261101_final
```

The runner uses shipped settings, an isolated historical clock, a five-second observation grid,
a baseline plus three seeded combined-fault runs, and an eight-second required exit window.
It writes manifests with source hashes and timestamp anomalies, per-league scorecards, every
modeled switch, a counterfactual exit audit and manual event cues.

Clock mapping: `historical_time = historical_start + session_elapsed_seconds`. The runner never
changes the live engine clock, loads yesterday's game into today's live store, or contacts a TV.
Its existing delay model is explicit: eight seconds of nominal feed delay plus 35 seconds after
that, yielding 43 seconds estimated total screen delay; fault runs use 49 seconds actual total.
These are test inputs, not measured device delays.

The exit audit asks whether each game's model would permit an eight-second departure window
and compares it with the archive grader's action proxy. It reports unsafe permissions, unknown
intervals and clear opportunities that were held. An always-hold policy cannot pass as useful
detection merely because it never cuts. NBA/NHL resume boundaries are coarse event proxies;
timestamp repairs are marked unknown. This is not visual classification accuracy.

The selection scorecard still uses the existing offline director and modeled tune duration;
it does not execute the asynchronous hardware controller. Counterfactual permissions are a
separate diagnostic, not a claim that every simulated switch used the device callback.

## 2. Supervised provider replay observations (initial device reads completed)

Keep the app in **dry-run**. Both selected sticks were authorized and reached on November 2.
The initial provider selections and metadata captures are documented in
[the device trial](../device-trial-2026-11-02.md). Physical switching latency and visible play
boundaries remain unmeasured. Do not scan unrelated devices or assume an unverified monitor
can switch inputs.

Open a full-game replay manually through existing provider access. Note provider, matchup,
recording title and playback speed. Do not buy access or circumvent a restriction. Prefer a
recording preserving the original sequence; a condensed highlight package cannot share the
original event timing continuously.

Run a short read-only metadata capture only after the intended device is authorized:

```bash
.venv/bin/python scripts/screen_lab.py --host CONFIRMED_STICK_IP:5555 \
  --duration 120 --out data/lab/replay_device_trial.jsonl
.venv/bin/python scripts/screen_lab.py --report --out data/lab/replay_device_trial.jsonl
```

The tool records each metadata read's UTC and monotonic begin/end, not video. Use phone Lab
labels for action, commercial, replay, loading and errors. Labels expire after 30 seconds;
repeat a label while observing a long segment. Reports exclude out-of-session and ambiguous
labels and show descriptive counts only. A media `playing` report may accompany every content
category and must never be scored as “live play detected.”

Observe at least these distinct cases, marking what is actually visible:

1. A normal live-action segment in the recording, including its beginning and outcome.
2. A dead-ball break and a broadcast editorial replay.
3. A commercial or intermission, if the recording includes one.
4. Pause, then resume; a deliberate seek; and return to the desired position.
5. A loading or unavailable state if encountered naturally. Do not induce account/purchase flows.

## 3. Align a recording to archived events

Use `manual_replay_cues.json` to identify an exact visible outcome by matchup, play ID, period,
game clock and description. Its UTC timestamp is **not** a position in the provider's recording.
Record the session-relative observation time when that outcome finishes, plus the player position
if available. A second exact event confirms the elapsed-time relationship.

Within an uninterrupted segment, `archive_time = matched_event_time + elapsed_since_anchor` at
normal playback speed. If the recording cuts a break, changes speed, pauses, buffers or seeks,
invalidate that relationship and establish a new segment. Do not extrapolate across an edit.
A mismatch between the two anchors greater than the human annotation uncertainty means the
segment is not aligned. Retain that failed alignment; do not adjust timestamps to make it pass.

Do **not** tap the live “I just saw it” calibration against yesterday's game. Recording offsets
and a live broadcast's real-time delay are different quantities. The current live engine does
not automatically synchronize a provider VOD to the archived clock.

## 4. Measure actual switching separately

Once ADB and source identity work, use a supervised single-stick trial with the source and
requested replay already known. Each trial needs an explicit transition ID and human observations:

- Command request time and controller completion time.
- Last source picture, first destination picture, and when the correct matchup is recognizable.
- Whether departure cut a visible play; whether entry missed the desired play's beginning.
- Wrong target, blank/loading interval, timeout, duplicate action, or recovery.

Use the controller's local `switches` log for command timestamps and the observation sheet for
actual pixels. A command acknowledgement is not a first-picture measurement. Pair timestamps
by trial ID; do not subtract timestamps from different clocks without an explicit mapping.
Report each result plus sample count, median and slowest observed transition. Use percentiles
only with enough trials; do not advertise a reliable p95 from a handful of switches.

Dual-stick trials require verified control/readback for the actual display. The current adapter
supports a specifically selected Samsung; an ordinary monitor remains a manual-input/single-stick
setup until supported control is established. Do not target an unrelated Samsung as a workaround.

## 5. Report honestly

For manually labeled and aligned segments, report play-start and play-end error separately,
false play triggers during labeled non-play, missed observed plays, and unknown/unscorable time.
Keep denominators and excluded intervals visible. No annotations means detection remains
**unmeasured**, not zero errors. Likewise, no device trials means switching latency is unmeasured.

A later live-game test must independently measure real stream delay, feed lead, buffering drift,
actual exit/entry behavior and account/channel correctness. Only that test can validate the
end-to-end live experience.
