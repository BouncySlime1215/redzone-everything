# Switch timing: what we measured (TIMING owner)

Owner: TIMING ("Dial in switch timing"), items 1-7 of the Nov 4 overnight plan. Numbers here are
measured, never assumed. Rerun them with `python scripts/timing_report.py` on the Mac (read-only).

## 1. Dead-ball detection lag (whistle -> in our data)

Source: the flight recorder, `data/recordings/2026-11-02` and `2026-11-03` (`plays.jsonl`: when we
first saw each play vs the feed's wallclock). Only plays while a game was tracked continuously
(the first batch and anything after a > 120 s silence are left out). 17,134 of 18,866 rows.

| League | Source | Dead balls | p10 | p50 | p90 | Within 3 s | Newest-in-batch p50 | Gap between batches p50 |
|---|---|---|---|---|---|---|---|---|
| NFL | ESPN summary | 50 (all plays 150) | 26.3 | 42.2 | 68.8 | 0 % | 44.2 | 41.9 |
| NBA | ESPN summary | 1,952 | 29.0 | 45.4 | 69.7 | 0 % | 37.8 | 20.9 |
| NHL | ESPN summary | 692 | 37.4 | 50.2 | 71.3 | 0 % | 48.7 | 29.8 |
| CBB | ESPN summary | 846 | 27.7 | 36.3 | 45.2 | 0 % | 33.9 | 13.8 |
| WCBB | ESPN summary | 216 | 28.3 | 35.3 | 45.0 | 0 % | 34.0 | 12.6 |

- **The 3 s target is not reachable with ESPN polling.** Even the newest play in a batch is ~26 s
  old at the p10: that is ESPN's own publish delay, before our poll cadence adds to it.
- The race log (`race.jsonl`) shows the ESPN scoreboard, ESPN summary and the NBA/NHL league feeds
  within ~1 s of each other, but they are polled together, so it can't tell which source is faster.
- **FastCast has no data yet.** The recorder has no `espn_fastcast` rows and there is no
  `data/fastcast_check.json` on the Mac, so the shadow check never graded a live game. FastCast is
  the only path toward a few-second dead ball; measuring it is the next step (SYNC, #33).
- NFL has only one night's worth of plays (150). Treat its row as a first look.

## 2. On-screen lead (stream delay minus detection lag)

Stream delays measured on the Mac (data/redzone.db calibrations, Nov 2): YouTube TV truTV 22 s,
ESPN 26.4 s, FS1 42 s, Peacock 48.4 s; Prime taps (32-88 s) set aside. Lead at the median detection lag:

| Channel delay | NFL | NBA | NHL | CBB |
|---|---|---|---|---|
| 22 s (YTTV truTV) | -20 | -23 | -28 | -14 |
| 26.4 s (YTTV ESPN) | -16 | -19 | -24 | -10 |
| 42 s (YTTV FS1) | 0 | -3 | -8 | +6 |
| 48.4 s (Peacock) | +6 | +3 | -2 | +12 |

Negative means the dead ball has already aired on your screen when the app hears of it. On the
YouTube TV channels we use most, **we learn of a whistle 15-30 s after you saw it.** A cut can
only land on a dead ball the app predicts or one that lasts long enough (a timeout, a review, a
TV break), never on a short whistle it reacts to. The scorecards assumed the feed arrives 8 s
after the play, which hid this.

Only 7 calibrations exist, all from one night and none per stick, so a per-stick offset can't be
measured yet. The live per-channel filter (`redzone/decider/sync.py`) learns the delay; the
missing piece is the detection side, which this report now measures.

## Scorecards under the measured feed lag

`--real-lag` on `scripts/eval.py` / `scripts/eval_sports.py` (and the `_reallag` cards in the
`--robust` CI run) delivers each play after a lag drawn from
`redzone/lab/feed_lag_profile.json` (quantiles of the table above, up to the p95; seeded).
Full scorecards, eval-data-v1:

| Card | Fixed 8 s feed (today's scorecard) | Measured feed, before the fix | Measured feed, after the fix | Fixed median lag (NFL 45, NBA 38, NHL 49 s), after the fix |
|---|---|---|---|---|
| NFL mid-play cuts | 0 | 0 | 0 | 0 |
| NFL dead air | 9.7 % | 21.6 % | 26.1 % | 21.2 % |
| NFL big plays seen / live | 96.4 / 33.6 % | 85.3 / 26.1 % | 84.3 / 23.7 % | 84.1 / 26.1 % |
| NFL crunch live | 73.5 % | 64.5 % | 60.1 % | 60.3 % |
| NFL entries mid-play / missed | 0 / 0 | 3 / 3 | 1 / 1 | 1 / 1 |
| NBA mid-play cuts | 0 | 0 | 0 | 0 |
| NBA big plays seen | 92.0 % | 70.1 % | 67.8 % | 79.3 % |
| NBA crunch live / your-team crunch live | 95.4 % / 31 of 31 | 78.5 % / 18 of 31 | 76.2 % / 18 of 31 | 79.8 % / 31 of 31 |
| NBA entries mid-play / missed | 7 / 10 | 26 / 11 | 26 / 12 | 21 / 17 |
| NHL mid-play cuts | 0 | **5** | **0** | 0 |
| NHL goals seen | 99.7 % | 95.1 % | 93.7 % | 88.4 % |
| NHL crunch live | 86.4 % | 84.3 % | 82.5 % | 73.9 % |
| NHL entries mid-play / missed | 0 / 0 | 10 / 3 | 11 / 2 | 13 / 2 |

`--feed-lag S` is the simple form: every play arrives S s after it happened, the screen stays 43 s
behind real life. `--real-lag` draws each play's lag from the measured distribution.

The fix: the gate's feed-lag estimate (`Timeline.lag_est`) capped each sample at 45 s, under
the NHL median of 50 s, so it believed its data covered your screen when it didn't. Samples now
cap at 180 s, and a game with fewer than 3 samples of its own uses the measured p90 for its league
(`FEED_LAG_PRIOR_S`) instead of a 10 s guess. On today's fixed-8 s scorecards it changes NFL dead
air by +0.1 and nothing else.

**What this means:** with ESPN polling, the real app is far from the scorecards on dead air and big
plays seen, and NBA entries land mid-play a quarter of the time. The fix is faster data, not more
tuning: FastCast (if it delivers in a few seconds), then predicting restarts (item 3) so entries
are timed from the stoppage's expected length instead of from a whistle we hear about late.

## A2. Faster feeds: the source race

The live app already polls NHL api-web (score + play-by-play, 8 s) and the NBA liveData mirror
(5 s) next to ESPN, and the merger notes which source reported each score first. From the Mac's
Nov 2-3 recordings (`python scripts/timing_report.py`), deduped per game and score:

| league | first: league feed | first: ESPN scoreboard | first: ESPN summary |
|---|---|---|---|
| NHL (94 scores) | **79.8 %** (api-web) | 19.1 % | 1.1 % |
| NBA (1665) | 2.0 % (liveData) | 95.2 % | 2.8 % |
| CBB / WCBB | none | 95 % / 96 % | 5 % / 4 % |

So NHL api-web is the faster source for NHL; the NBA mirror adds almost nothing over ESPN's
scoreboard. **How much faster is not known yet**: two recorder bugs hid it.

1. The recorder wrote each race row the moment the first source reported, before any other
   source had, so `others` (the followers' lags) was always empty.
2. It tracked rows by list index, but the merger trims its race list at 2000 events; after that
   the index froze and nothing new was written.

Fixed: the recorder writes a row 90 s after the first report with every follower's lag, tracked
by identity. The race also times **dead balls** now (label `stop p<period> <clock>`, keyed by the
stopped clock so feeds that name the stoppage differently still match; UNKNOWN doesn't count).
`timing_report.py` prints first % and the gain over the fastest ESPN copy per league, kind and
source. The status page's race summary stays score-only.

Next (needs a night of recordings on the Mac): if api-web whistles beat ESPN by more than a few
seconds, feed NHL stoppages to the decider timeline from api-web (today they only set the merged
state). Real (realapp.com) stays out: its terms forbid automated queries.
