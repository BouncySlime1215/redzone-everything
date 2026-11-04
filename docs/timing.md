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

## Time-shift buffer (hold each stream ~1 min behind live)

Nick accepts being about a minute behind live. Holding every stream B s behind its live edge puts
the screen at 26 s (a YouTube TV channel) + B behind real life, so the feed's 35-50 s turns into a
lead: **lead = 26 + B - feed lag**. At B = 60 the median dead ball is known ~35-40 s before it airs.

Scored with `--real-lag --behind-live B` (full scorecards, eval-data-v1, after #47's gate fix):

| B (s behind live) | 0 | 30 | 45 | 60 | 75 | 90 |
|---|---|---|---|---|---|---|
| NFL dead air | 30.1 % | 24.4 | 21.0 | 13.3 | 12.0 | 13.0 |
| NFL big plays seen | 77.9 % | 85.7 | 91.0 | 97.3 | 97.2 | 97.1 |
| NFL big plays live | 24.9 % | 26.0 | 28.5 | 39.4 | 46.2 | 49.4 |
| NFL your-team crunch live | 19/21 | 19/21 | 20/21 | 16/21 | 20/21 | 21/21 |
| NFL entries mid-play / missed | 1/1 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| NBA big plays seen | 67.8 % | | | 81.6 | | 87.4 |
| NBA crunch live | 76.5 % | | | 82.8 | | 91.3 |
| NBA your-team crunch live | 18/31 | | | 13/31 | | 31/31 |
| NBA entries mid-play / missed | 28/11 | | | 6/3 | | 9/3 |
| NHL goals seen | 93.2 % | 93.5 | 98.1 | 99.0 | 99.5 | 99.2 |
| NHL entries mid-play / missed | 12/7 | 10/2 | 5/4 | 2/0 | 2/1 | 0/0 |
| Mid-play cuts (all) | 0 | 0 | 0 | 0 | 0 | 0 |

**It is the biggest timing lever found so far.** NFL dead air halves, big plays seen goes from 78
to 97 %, and NHL entries reach 0 mid-play at 90 s. NBA is mixed at 60 s (your-team crunch 13/31)
and good at 90 s. The rest of the NBA gap is the restart countdown (A3).

Sim note: with a buffer, every app's game is calibrated to the screen's delay (`calibrate_targets`).
Before, only YouTube TV games were: Prime/ESPN games were planned at the config's 40 s. The first
NBA buffer runs made 3-15 mid-play cuts from exactly that (planned at 40 s against an 86 s screen),
which wouldn't happen live, where each channel's delay is measured. Turning it on for today's 8 s NBA
card moves that card a lot (big seen 92.0 → 77.0, still 0 cuts) for a reason not yet understood, so
it stays off there. It's an open item.

### Design for the live app (not built yet; A5/A6)

- **Knob:** `delay.behind_live_s` (default 60, hard cap 90). 0 = today (live edge).
- **Set it after each tune:** the stick lands at the live edge. Then pause (YTTV) for B s, or seek back
  B s (Prime, which has a 10 s seek step; the e2e night already models seeks). Do this while the TV
  shows the other stick, so the viewer never sees it. The hidden stick's preload does it before a flip.
- **Verify:** the per-channel delay filter (`redzone/decider/sync.py`) reads the actual screen delay
  from YTTV show changes and the Prime score-bug clock (OCR). Target 26 + B ± 3 s. Re-seek when it is
  more than 5 s off.
- **Drift back to live:** apps jump to the live edge after an ad pod or an error. The filter sees the
  delay fall below target, and the gate treats that channel as uncalibrated (`sigma` up) until it's
  re-seeked at the next safe moment. Never seek the visible stick mid-play.
- **Every stick held to the same B,** so a flip between games doesn't jump in time (same-B rule).
  Crunch overlaps already use the second stick time-shifted. That stays, inside the cap.
- **Gate:** lead = screen delay - measured feed lag. The decider already uses both, so nothing new
  is needed. A dead ball known by the time it airs makes "cut only at P_safe ≥ 99.5 % over the
  ~3 s flip+settle window" (A6) reachable.
- **Cost:** you're about a minute behind your group chat and phone alerts. Nick accepted that.

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

## A3. Restart countdown, step 1: break-length floors

Break lengths on the **fit split only** (train + valid nights; locked nights unseen), from the
stoppage entry to the real restart (`cutgrade.resume_after`), with 5-fold forward-chaining CV:

| league, stoppage | n | p0.5 | p1 | p5 | p50 | share below candidate floor (all / CV mean / worst fold) |
|---|---|---|---|---|---|---|
| NHL TV timeout | 221 | 130 | 131 | 135 | 149 | 120 s: 0.45 % / 0 % / 0 % |
| NHL goal | 410 | 0 | 29 | 38 | 49 | 30 s: 1.2 / 1.9 / 4.9 %: no floor |
| NFL timeout | 4355 | 7 | 15 | 27 | 136 | 30 s: 6.2 %: no floor |
| NFL two-minute warning | 411 | -7 | 48 | 101 | 160 | today's 50 s floor: ~1.5 % below (data artifacts, to check) |
| NBA timeout | 747 | 0 | 0 | 0 | 182 | ~10 % read as 0 s: the timeout entry is often logged after the restart |

Why a floor matters now: with the measured 35-50 s feed lag the data never covers the screen
(`leave_gate`'s `covered` is false), so a break with no resume in the data is safe to leave only
while `start + floor + delay` hasn't passed. Without a floor, an NHL TV timeout couldn't be left.

Shipped: **NHL TV timeout floor 120 s** (`MIN_BREAK_S`). The one fit-split row under 120 s is a
mis-stamped faceoff (the next shot came 214 s later). NHL scorecard, last 14 nights:

| card | entries_mid_play | plays_missed_entry | dead_air_pct | big seen | switches/h | mid-play cuts |
|---|---|---|---|---|---|---|
| default (8 s feed) | 0 → 0 | 0 → 0 | 24.1 → 24.1 | 99.7 → 99.7 | 4.3 → 4.6 | 0 → 0 |
| real lag | 11 → 12 (3.8 → 3.6 % of entries) | 2 → 3 | 24.9 → 24.0 | 93.7 → 95.7 | 3.1 → 3.7 | 0 → 0 |

Why NBA gets no timeout floor: in ESPN's NBA feed a timeout's wallclock is often the moment play
**resumed**, not the whistle. Example: a "Full Timeout" at 6:09 is stamped 19 s before a jump shot at
5:50, with 19 s of game clock between them, so the restart works back to the timeout's own stamp.
A floor counted from that stamp would start the countdown at the end of the break, which is unsafe.
NBA breaks need the resume seen in data (or the stick's own score-bug OCR), not a floor. Only 2 of
92 archived NBA games have flat wallclocks, so this is ESPN's stamping, not missing data.

Next: a hazard model per league for the "how long until the restart" countdown (the floors are
its lower bound with no features), and fixing the NBA timeout stamps before NBA gets a floor.
