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

## Time-shift buffer, live control (flag `timing.behind_live_s`, OFF)

How a stream gets B s behind live (`Controller._hold`, `control.py`):

1. The hidden stick tunes the next game at the live edge (as today).
2. If the buffer is on for that app (YouTube TV, Prime; not a Prime multiview tile) and league, it
   sends MEDIA_PAUSE and checks the player says "paused". If not, it plays on at the live edge.
3. It waits B s **without the control lock**, so switches go on. The stick isn't "ready", so nothing
   flips to it meanwhile.
4. MEDIA_PLAY, then a check that it's playing. If it won't resume, it's reloaded at the live edge.
5. The held seconds (measured, not assumed) go into `Controller.held` and the delay book
   (`DelayBook.set_behind` via `Engine._on_behind`). The sync filter re-syncs on that, as for an
   entry rewind. `Decider._land_behind` keeps held offsets instead of zeroing them.

Any fresh tune of a target clears its hold (`_tune` → `_set_held(target, 0)`). A replay parked on a
held stream rewinds that much less. Drift back to live is a reload, which is a fresh tune.

Verification on the TV: the per-tune delay estimate (sync filter: boundary, score bug, taps)
should read about B s more than the same channel without the hold. Live landings are graded in
`data/supervisor_cuts.jsonl` (leave / enter: dead, play or unknown) from each switch's trace.

## RC8: the buffer ON per league (flag `timing.behind_live_s.rc8_on`, OFF everywhere; Nick flips it)

Nick, Nov 5 9:10 AM: the time-shift buffer is the main architecture. RC8 is that release, built so Nick can turn
it on one league at a time the moment the evidence is in, and back off the same way (`redzone/buffer_rc8.py`).

- **Flag.** `timing.behind_live_s.rc8_on`, set only under `league_overrides.<league>` (a global true is reported by
  `validate_scope` and ignored by `is_on`). For a flipped league the stream is held B s behind live by the existing
  hold above (the controller's gate opens for it even where `enabled` is off), with B from the formula below or a
  pin (`rc8_fixed_s`). It is separate from `enabled` (RC6.2's NBA/NFL hold), which RC8 leaves exactly as it is.
- **Adaptive B** (Nick 9:35 AM; gate v28): feed-lag p95 + sync error + tune/seek + margin
  (`timing.behind_live_s.adaptive`), minus a credited stream delay (0: Nick's formula as written). A league that
  already holds its stream (RC6.2: NBA 90, NFL 60) never goes below that live B until a LOCKED read held
  (`approved_min_s`), and then steps down at most `ratchet_step` (15 s) per release. Hard floor 15 s in RC8 (settings
  refuse outside 10-15; 10 s is Nick's later call), cap 90 s (`max_s` limited to 90). The B that ships is
  `ship_b` = max(sweep point, formula/ratchet, floor), and straight back to the cap after any mid-play night. Today:
  NBA 83 -> 90 (live hold), NFL 75, NHL 88. The feed-lag terms are P90 PLACEHOLDERS (`noggin.pick.FEED_LAG_P90`)
  until T6's per-league table gives real p95s.
- **Gate report.** `python scripts/rc8_gate_report.py --shadow <buffer_shadow_grade.py --json>` reads lane 3's sweep
  verdict (`/mnt/project-files/lane3/buffer-sweep/verdict.json`) and shows the five pre-registered items per league
  (docs/plans/2026-11-05-rc8-buffer-gate.md) as PASS / FAIL / PENDING, Rule #1 at the B that would ship and across the
  whole grid, the formula B with its parts, and the flip command for `ship_b` (plus `start_timed` only when the verdict's
  arm needs it). It never loosens a bar: one shadow night can only veto.
- **Flip.** `python scripts/rc8_flip.py <league> on [--seconds B] [--set key=value]` posts only that league's keys to
  the running app (`/api/settings`, effective on the next fresh hidden-stick tune); `off` writes explicit
  false / 0 (settings deep-merge, so a deleted key would stay on). `--dry-run` shows the change and the B; `--status`
  lists every league. Rule #1 never changes, and only Nick runs it.

## A2. NBA liveData play-by-play (flag `sources.nba_primary`, OFF; shadow ON)

`playbyplay_<gameId>.json` on the same S3 mirror as the scoreboard. The path is assumed from the
scoreboard mirror and can't be reached from the cloud; the shadow log is the check. Every action
has `timeActual` (when it happened, ms) and `edited` (last publish). In the fixture game
(0022600157), edited - timeActual: p10 3.8 s, **p50 11.6 s**, timeouts ~10 s, fouls ~11 s. That's
an upper bound, since `edited` is the last edit, not the first. ESPN's NBA dead balls: p50 45 s.

- Shadow (default): polls every `polling.nba_pbp_s` (3 s) for live games and writes new actions to
  `plays.jsonl` as source `nba_cdn`, with wall and lag. `python scripts/timing_report.py` puts its
  dead-ball lag next to ESPN's.
- Primary (off): its actions reach the timeline with real times (exact bounds and lag samples),
  and its last action sets the stoppage, which it owns while fresh (`Merger.stoppage_owner`).
  Kinds use ESPN's spelling, so the dead-ball rules (fouls, subs, non-final free throws, timeouts)
  match.

## SYNC 1: steady before the flip, buffering into the delay (Nov 4)

- **Steady before the flip.** `Controller._steady_playing`: the hidden stick must be still on the stream we tuned
  (`Stick.still_on`) and show the same `updated` stamp (the app's last state change) on two reads at least
  `FLIP_STEADY_S` = 1 s apart. A warm cache (`_kept_playing` and earlier reads) makes this free on most flips.
  Buffering or connecting gets `FLIP_STEADY_WAIT_S` = 3 s, after which the stream is re-tuned. A pause or stop is
  believed at once.
- **Buffering into the delay.** Each report carries position p at stick time u. Against the tune anchor,
  lag = (u - u0) - (p - p0). Between 0.5 s and 20 s it is a stall: it goes into `held[target]`, the engine's
  `on_behind` moves the delay book by the same amount, and the stream is re-anchored so the stall is booked once.
  It is checked before the flip and right after a TV-input flip. Negative lag (a player catching up) is never booked.
- **Settle.** `settle_book` counts flips with a picture check and those PLAYING within `FLIP_SETTLE_S` = 3 s. Every
  switch trace carries `settled` and `stall_s`. North Star row: flips PLAYING within 3 s >= 99%.
- **#61 hold fix.** After the hold's PLAY, the stream is re-anchored. Before, the pause broke `still_on`, so the
  next flip re-tuned the held stream at the live edge and threw the hold away.

## SYNC probe: what the sticks expose (Nov 4)

`redzone/sync_probe.py` collects the data for auto-anchors and the network features of the delay model.
Nick's 1:45 and 1:50 AM revisions: no tap-sync, and delay is not stationary. It runs on its own whenever the
app is live (`sync.probe.enabled`, default on). Each tick (5 s) writes one row per stick to
`recordings/<day>/sync_probe.jsonl`:

| field | from | for |
|---|---|---|
| `ms` | `dumpsys media_session` (state, position, updated, speed, title/subtitle/description) | state transitions, ad metadata |
| `stall_s`, `state_changed` | stick clock vs position since the last tick | buffering per minute and its length |
| `wifi` (every 15 s) | `dumpsys wifi`: RSSI, link/tx/rx speed, frequency (numbers only) | network condition features |
| `ui` (hidden stick, every 20 s) | `uiautomator dump`: text, hints `ad`/`countdown`/`clock`/`score`/`captions` | break start/end, clock anchors |
| `log` (every 15 s) | `logcat` lines on ads, cues, SCTE, buffering, bitrate, resolution | break anchors, bitrate |

Expect YouTube TV (Cobalt) and Prime's player to draw their own UI, so `ui` may come back empty. That is
one of the things Wednesday will settle. The next step builds break anchors (sample_kind `break_start` /
`break_end` plus the feed event wallclock) into `redzone/decider/sync.py`. delay_mu and delay_sigma are
exposed through DelayBook for the Noggin.

## Sync stack (Nov 4, overnight)

- **Known moves keep the sync.** `DelayBook.set_behind(..., known_sd=0.5)` is used for the controller's holds
  and stalls (`Engine._on_behind`), and it calls `SyncBook.shift`. The estimate moves by the measured amount and
  gains 0.5 s of doubt. Before this, every booked stall re-synced the channel from its prior.
- **Network condition → error bar.** `SyncProbe._condition` turns stalls per minute (last 5 min), RSSI below
  -65 dBm and an RSSI drop of 10 dB or more within 2 min into `q_scale` (1 to 25). `SyncBook.condition`
  multiplies the delay's random-walk noise by it, so `d.current_sigma()` grows faster on a bad link. That is
  the variance the Reflex reads.
- **Break anchors.** An ad label appearing or going in the hidden stick's UI tree is placed midway between
  two reads (sd = gap/√12). It is matched to the game's last dead ball 10 to 240 s earlier:
  z = t − wallclock − `start_bias_s` (15 ± 10 s, a prior). Every edge goes to
  `recordings/<day>/sync_anchors.jsonl` (ts, target, stick, sample_kind, feed_wall, z) as the Noggin's labels.
  It reaches the filter only with `sync.break_anchor.enabled`.
- **Drift alarm.** `SyncBook.alarms` flags "measured" when the delay moved more than 5 s since it synced (a
  restart keeps the plan, so a jump counts), and "predicted" when q_scale ≥ 9. Alarms are logged once per
  5 min. `sync.drift_alarm.resync` re-tunes the hidden standby when no switch is pending (off by default).
## Sims score the feed actually in use (planner 2:45 AM item 3, Nov 4)

`--real-lag` used to draw every league's feed lag from ESPN's summary profile, even when the config decides
NHL from the api-web play-by-play (#58). Now `timing.sources_in_use(cfg)` picks the profile per league:
`sources.nhl_primary` → `sources.nhl_wsc.nhl` in `feed_lag_profile.json`, `sources.nba_primary` → `nba_cdn`.
A source with no profile keeps ESPN's.

- **nhl_wsc is derived, not measured row by row.** Nick's live check gave p50 16.7 s (ESPN 43.1 s). Each
  quantile is the larger of ESPN's NHL dead-ball quantile scaled to a 16.7 s median and the same quantile
  shifted to it (floor 2 s), giving p0 8.0, p50 16.7, p90 37.8 and p95 57.3 s. The tail is kept as late as
  ESPN's on purpose. `scripts/timing_report.py --write-profile` replaces it with measured rows once the
  `nhl` source has a live night in the recordings.
- **nba_cdn has no profile yet.** The liveData pbp's own lag hasn't been measured, so NBA stays on ESPN's.

NHL dev split (`fit`, 10 nights, `--real-lag`), ESPN profile vs `--set sources.nhl_primary=true`:

| seed | entries mid-play | mid-play cuts | plays missed on entry | dead air % | big seen % | crunch live % |
|---|---|---|---|---|---|---|
| 0 | 8 → 3 | 0 → 1 | 1 → 2 | 22.7 → 22.5 | 95.3 → 99.3 | 79.7 → 84.3 |
| 1 | 9 → 1 | 1 → 0 | 1 → 1 | 23.3 → 22.5 | 98.3 → 99.5 | 81.2 → 82.8 |
| 2 | 9 → 0 | 1 → 0 | 2 → 0 | 21.7 → 22.0 | 96.0 → 99.5 | 84.5 → 85.8 |
| total | 26 → 4 | 2 → 1 | 4 → 3 | | | |

The single mid-play cut moves from seed to seed and shows up with either feed (base: seeds 1 and 2;
wsc: seed 0, night 20261024).

## Entry policy in the candidate: no blind cap, crunch rewinds (Nov 4, cycle 01)

The remaining NBA mid-play entries in the candidate were entry policy, not timing: two were a finished game
leaving after the 180 s `decider.entry.max_wait_s.nba` cap ran out, and the rest were crunch-time entries
going in live (`decider.entry.crunch_now: live`). NBA dev nights (fit split), candidate profile, B=90:

| variant | mid-play entries | missed on entry | big seen | big live | crunch live |
|---|---|---|---|---|---|
| candidate before (cap 180, crunch live) | 6/61 | 4 | 84.5 | 81.7 | 90.9 |
| no cap, crunch live | 4/60 | 2 | 84.5 | 81.7 | 90.9 |
| **no cap, crunch rewind (shipped in the candidate)** | **3/60** | **1** | 84.5 | 81.7 | 90.9 |
| no cap, crunch wait capped at 15 s | 2/60 | 0 | 83.1 | 80.3 | 90.6 |
| no cap, crunch wait capped at 60 s | 2/60 | 1 | 80.3 | 77.5 | 90.1 |
| no cap, crunch wait | 0/60 | 0 | 80.3 | 77.5 | 90.0 |

A crunch entry with no rewind in reach has to choose between landing mid-play and missing what happens
while it waits; any wait costs plays. Full North Star, candidate column: dev mid-play 0.35 -> 0.23 per 100,
locked 0.33 -> 0.17, every other line unchanged (dead air dev 18.90 -> 18.95, locked 20.35 -> 20.32).

## Clairvoyance: the lookahead view and GAME-04 (cycle 02, flag `noggin.lookahead.enabled`)

With B=90 the screen runs about 100 s behind real life, and the feed is about 45 s behind, so the app sees roughly
the next minute of every game before it airs. Big plays show up a median of 36 s ahead (4 NFL dev nights).
`redzone/noggin/lookahead.py` gives that window per game: the plays and dead windows between screen-now
(`now - mu`) and the newest data. It returns nothing when sigma is above 10 s (SAFE-02). GAME-04 in the decider:
- On a stay, if another game has a big play ahead, it pre-tunes that game (`want`). It flips only when the
  picture would land on a dead ball before the play, at most 45 s early.
- On a break visit, it steers the visit to the game whose big play will air during the break.

What limits it: on 4 NFL dev nights, 790 of 866 attempts were blocked by the game on screen's own leave gate. The
current channel's delay doubt was above `decider.uncalibrated_sigma_s` (8 s), so rule #1 lets it leave only at
official breaks. Tighter sync (SYNC-01..03, Wednesday's probe data) is what unlocks pre-emptive flips.

| split | league | candidate before (rewind, no lookahead) → after (wait + lookahead) |
|---|---|---|
| dev | NFL | mid 0 → 0; big live 50.3 → 54.3; crunch 68.0 → 68.7; big seen 98.7 → 98.9; dead air 29.8 → 30.2 |
| dev | NBA | mid 3/60 → 0/60; big live 81.7 → 77.5; crunch 90.9 → 90.0; big seen 84.5 → 80.3; dead air 19.4 |
| dev | NHL | mid 1/242 → 0/244; big live 44.7 → 44.9; crunch 80.6 → 80.2; dead air 22.4 → 22.5 |
| locked | NFL | mid 0 → 0; big live 49.0 → 52.9; crunch 68.1 → 68.1; dead air 29.2 → 29.7 |
| locked | NBA | mid 2/24 → 0/24; big live 100 → 68.8; crunch 93.3 → 84.9 |
| locked | NHL | mid 0 → 0; big live 48.1 → 48.6; crunch 84.7 → 84.7 |

The NBA cost is all crunch_now=wait (the lookahead doesn't fire on NBA). `sources.nhl_primary` and its
api-web lag (`nhl_wsc`, #75) were already in the candidate and its sims.

### What-if: tighter sync (cycle 02, sizing SYNC-01..03)

This is the candidate from #100 on dev nights, with every channel's stated delay doubt capped. It is a what-if, not a
setting, and it's an upper bound: the true delay error is unchanged, and mid-play staying at about 0 is the only
safety signal.

| league | candidate (big live / crunch / mid) | cap 5 s | cap 3 s |
|---|---|---|---|
| NFL | 54.3 / 68.7 / 0 | 61.6 / 71.9 / 0 | 64.2 / 72.1 / 0 |
| NBA | 77.5 / 90.0 / 0 of 60 | 80.3 / 91.6 / 1 of 66 | 81.7 / 91.9 / 0 of 67 |
| NHL | 44.9 / 80.2 / 0 | 44.7 / 71.6 / 0 | 48.9 / 77.6 / 0 |

Reproduce with `SIGCAP=3 python sigcap.py <league> ...`, which wraps scripts/eval_sports.py and patches
`Delay.current_sigma`.

**Crunch guard** (with `noggin.lookahead.enabled`): a catch-up replay of another game never takes the screen off a game
inside its crunch window. The replay waits; it stays catchable for 10 minutes. Under the 3 s what-if those replays
were the NHL crunch loss. Results:

| | guard off | guard on |
|---|---|---|
| NHL crunch, 3 s cap | 77.6 | 78.7 |
| NFL crunch, 3 s cap | 72.1 | 74.3 |
| NHL crunch, candidate | 80.2 | 80.6 |

Big plays live held under the cap: NFL 64.1, NBA 81.7, NHL 49.2. Dead air under the cap rose for NFL (29.8 → 30.6).
The rest of the NHL gap is two nights (Oct 19 and Oct 27) where two crunches overlap and it picks the other one.
That's GAME-05 (crunch overlaps), not a switch away from crunch time.
## Safety playbooks SAFE-01/02/03/05 (flag `playbooks.safe.enabled`, OFF)

`redzone/decider/playbooks.py`, consulted after cut timing on every switch (and on replays for SAFE-05). Each
one turns a switch into a hold and writes `playbook=<id>` into the decision's reason. Nick's buttons and the
first pick are never held.

| id | trigger | hold until |
|---|---|---|
| SAFE-01 | the target's feed is more than its poll cadence + 10 s old (or flagged dark) | the feed is fresh; if the game on screen is stale too, stay put |
| SAFE-02 | the target channel's delay doubt is above 10 s | the entry lands on a dead ball at the full margin (no centred/urgent landing, no going in live) |
| SAFE-03 | the sync probe's network condition is 9 or more (2+ buffering events a minute, or an RSSI drop) on a stick, in the last 5 min | the landing is a long break (TV timeout, period end, halftime, intermission, two-minute warning) |
| SAFE-05 | the wall clock moved more than 5 s against the monotonic clock | every live game has had a fresh poll since, or 90 s |

The SAFE-02 threshold sweep on NHL dev nights (candidate profile) is below. Flag on vs off at 10 s: dev and locked scorecards are identical
for NBA, NHL and NFL.

| sigma_s | mid-play entries | crunch live | big live |
|---|---|---|---|
| off | 1/242 | 80.6 | 44.7 |
| 3 | 7/197 (live config) | 66.0 | 37.8 |
| 6 | 0/217 | 72.4 | 42.3 |
| 8 | 0/220 | 74.3 | 42.1 |
| 10 | 1/242 | 80.6 | 44.7 |

Night lab: `python -m redzone.lab.night --no-default-faults --faults redzone.lab.night.faults:safety --set
playbooks.safe.enabled=true` injects `feed_stale` (every game but the one on screen, 15 min), `sync_wide` (12 s
doubt, 10 min), `wifi_degrade` (15 min) and `clock_skew` (+30 s for 30 min, then back). Seeds 7, 11 and 13:
every check passes with 0 mid-play cuts. The existing guards already hold most of these situations. The gate
looked at 149 switches on seed 7 and held 6, all SAFE-05 at the backward clock jump.

## Scenario-tree lookup (flag `playbooks.game.enabled`, OFF)

`redzone/decider/scenarios.py` (idea #19, Mac half). Each tick turns the slate into one state word per live game:
`my_crunch`, `long_break`, `after_score`, `break`, `redzone`, `pp`, `crunch` or `live`. A table of branches then says
what to do. First match wins; a file can replace the seeds (`playbooks.game.table`, the cloud tree's format). A live
plan is dropped as soon as B leaves the state that justified it, and the slate is looked up again. A branch never
acts while the leave gate says A is mid-play, and it never visits away from a game in crunch time. Seeds:
GAME-03 (your team's crunch: go there, then hold), GAME-01 (NFL red zone during a long break on screen) and GAME-02
(NHL power play after a score on screen).

| split | league | flag | mid-play | crunch live | big live | big seen | dead air | your-team crunch |
|---|---|---|---|---|---|---|---|---|
| dev | NFL | off → on | 0 → 0 | 68.0 → 67.6 | 50.3 → 50.5 | 98.7 → 98.1 | 29.8 → 29.4 | 51/56 → 51/56 |
| dev | NHL | off → on | 1/242 → 1/240 | 80.6 → 80.6 | 44.7 → 44.9 | 98.6 → 98.6 | 22.4 → 22.6 | 15/15 |
| dev | NBA | off → on | 3/60 → 3/60 | 90.9 → 90.9 | 81.7 → 81.7 | 84.5 → 84.5 | 19.4 → 19.4 | 31/31 |
| locked | NFL | off → on | 0 → 0 | 68.1 → 69.0 | 49.0 → 49.5 | 100 → 100 | 29.2 → 29.0 | 26/27 → 27/27 |
| locked | NHL | off → on | 0 → 0 | 84.7 → 84.7 | 48.1 → 48.6 | 98.1 → 98.1 | 25.4 → 25.5 | 13/13 |

On dev, GAME-01 fired 213 times and agreed with the generic break visit another 42. Its visits trade some
crunch and big-play coverage for dead air, so it stays off until the cloud tree replaces the hand-written seed.

## Honest sync doubt in the sims (cycle 03, Nov 4)

`eval_sports.py --tune-jitter 6 [--break-anchors --set sync.break_anchor.enabled=true]` (backtest.run
`tune_jitter_s`, `anchors`). Every on-screen tick is graded for honesty: the true error of the delay the app
uses against its stated sigma (`per_night[].sync_honesty`: within 2 sigma, sigma <= 8 s, sigma <= 3 s).

- **Until now the sims had no true delay error at all** (the app's delay = the screen's), so their ~9 s doubt
  was pure pessimism. `--tune-jitter 6` adds what a YouTube TV tune really does (redzone/lab/syncsim.py): each
  tune lands U(0, 6) s behind the live edge, unknown to the app, graded as a real offset of that visit.
- `--break-anchors` feeds the filter only what live can feed it today: the ad break on screen of the game
  you watch, matched to the feed's dead ball, weighted like `engine._break_event`. The true whistle -> ad gap
  is drawn N(15, 10) by default (the config prior: an assumption until the Wednesday probe data).

Candidate, dev, all with `--tune-jitter 6` (truth error p95 ~5 s everywhere):

| run | NFL big live | NBA big live | NHL big live | NHL crunch | mid-play NFL/NBA/NHL | within 2 sigma | sigma <= 8 s (NFL/NBA/NHL) | sigma <= 3 s |
|---|---|---|---|---|---|---|---|---|
| no anchors (today) | 54.4 | 77.5 | 44.9 | 79.9 | 0 / 0 / 1 | 100% | 9 / 19 / 21% | 0% |
| anchors, prior sd 10 | 54.4 | 78.9 | 44.0 | 74.4 | 0 / 0 / 1 | 100% | 12 / 31 / 34% | 0% |
| anchors, sd 2 (what-if) | 54.4 | 76.1 | 43.7 | 75.7 | 0 / 1 / 0 | 100% | 45 / 91 / 51% | 0% |
| doubt calibrated to 3 s (what-if cap) | **64.1** | **81.7** | **49.2** | 78.2 | 0 / 0 / 0 | 100% | 100% | 100% |
| same, crunch_now=rewind | 64.1 | 90.1 | 49.2 | 78.2 | 0 / **5** / 0 | 100% | 100% | 100% |
| no anchors, crunch_now=rewind | 54.4 | 81.7 | 44.9 | 80.2 | 0 / **4** / **2** | 100% | 9 / 19 / 21% | 0% |

Locked (read once), wait: no anchors NFL 53.3 / NBA 68.8 / NHL 49.0, 0 mid-play; doubt at 3 s NFL 64.8 / NBA
68.8 / NHL 57.2, 0 mid-play, within 2 sigma 100%.

Reading: the doubt is honest (no misses) but about 3x too wide: the true error is ~5 s at p95, the stated 2
sigma ~18 s. Break anchors at today's prior (sd 10 s) hardly narrow it, and the 4 s sync floor makes sigma <=
3 s impossible by construction. A doubt calibrated to the real error reaches the cycle targets (NFL >= 60, NBA
>= 77.5, NHL ~50) at 0 mid-play, but only if the real per-tune error is as small as syncsim assumes, which is
what the Wednesday probe logs measure. Anchors also cost NHL crunch (79.9 -> 74.4) through re-syncs on tunes:
not understood yet, keep `sync.break_anchor.enabled` off. With honest per-tune error, crunch_now=rewind
makes mid-play entries (NBA 4-5, NHL 2) where wait makes 0-1: one more reason wait is the candidate default.

### Self-calibrating doubt (NoiseCal, `delay.self_calibrate.enabled`, OFF)

The doubt learns from the filter's own innovations, so it works live without ground truth. The first break
anchor after each tune is compared with the delay that tune started from: r = z - mu = (true error) + (anchor
noise, sd R). E[r^2] - R^2 is the real squared error, bias included. The stated doubt is the 95 % upper bound
of that (chi-square on n residuals, n >= 20), floored at 2 s (the 4 s floor stays for the filter itself), and
it never widens the aging model. The pool persists across nights (in the sims: across the nights of one run, in
date order, so a night only learns from earlier ones).

Candidate, `--tune-jitter 6 --break-anchors` + self-calibration, wait:

| split | league | big live | crunch | mid-play | dead air | within 2 sigma | sigma <= 8 s | sigma <= 3 s | sigma p50 |
|---|---|---|---|---|---|---|---|---|---|
| dev | NFL | 54.4 -> **57.6** | 70.5 -> 71.2 | 0 | 30.0 -> 30.3 | 100% | 9 -> 66% | 0% | 9.0 -> 6.7 |
| dev | NBA | 77.5 -> 78.9 | 90.0 -> 91.1 | 0 | 19.4 -> 19.5 | 100% | 19 -> 36% | 0% | 8.6 |
| dev | NHL | 44.9 -> 44.2 | 79.9 -> 79.3 | 1 -> 1 | 22.5 -> 22.6 | 100% | 21 -> 77% | 0% | 7.3 |
| locked | NFL | 53.3 -> **60.4** | 69.0 -> 71.6 | 0 | 29.4 -> 30.3 | 98.4% | 7 -> 70% | 29% | 5.3 |
| locked | NBA | 68.8 | 84.9 | 0 | 24.1 | 100% | 19 -> 25% | 0% | 9.0 |
| locked | NHL | 49.0 -> 48.6 | 76.2 -> 80.0 | 0 | 25.5 | 100% | 16 -> 33% | 0% | 9.0 |

(before = no anchors with the same per-tune error.) Rewind, dev: NBA big live 83.1 with 3 mid-play entries,
NHL 2: wait stays the default. With R = 10 s the bound tightens slowly (about 1/sqrt(n)): NFL, with the most
tunes and breaks, gets there first. A tighter anchor (Thursday's probe refit of the 15 +- 10 s prior) is the
lever for NBA/NHL.

**NHL crunch drop with anchors (79.9 -> 74.4, seed 0)** is not systematic: seeds 1 and 2 give 78.0 -> 77.8 and
82.1 -> 82.3, and the seed-to-seed spread is about 4 points. At seed 0, two nights (Oct 22 -10, Oct 27 -24) lose it
and Oct 20 gains 5. The decision logs first diverge at a catch-up replay that starts 6 min earlier (the anchor
lowered the doubt, so the gate opened sooner). From there the crunch picks differ, ending with a crunch wait
stuck behind a 110 s rewound tune. A lab lesson: judge an NHL crunch delta under ~4 points over several seeds.

### Landing lag in the entry margin (cycle 03, rule #1)

With an honest per-tune landing error (`--tune-jitter 6`) the candidate made one NHL mid-play entry on every
seed: an "ended" switch (the game on screen had finished) into BUF @ WSH at 18:57 of P1. It took the urgent
2 s landing because WSH is your team, and the tune came up 4-6 s late. Candidate only:
`decider.entry.ended_strict=true` (a switch off a finished game never takes the urgent landing: nothing on screen
is lost by waiting) and `decider.entry.landing_lag_s=6` (syncsim's upper end; Thursday's probe refits it). The lag
is one-sided, since a tune only ever lands late. It widens each pass's early side to cover at least 6 s, so a full
2-sigma margin (which already covers it) is unchanged and only the thin centred/urgent passes move. Adding 6 s on
top of every margin also gave 0 but cost NFL big live 54.4 -> 49.7 and NBA 77.5 -> 67.6.

| dev, --tune-jitter 6 | seed 0 before -> after | seed 1 | seed 2 |
|---|---|---|---|
| NHL mid-play | 1 -> **0** | 1 -> **0** | 1 -> **0** |
| NHL big live / crunch | 44.9 -> 44.7 / 79.9 -> 81.3 | 44.0 -> 44.2 / 78.0 = | 45.4 = / 82.1 = |
| NFL big live / dead air | 54.4 = / 30.0 = | 54.9 -> 55.0 / 29.6 = | 54.3 = / 30.0 = |
| NBA big live / mid-play | 77.5 = / 0 | 77.5 = / 0 | 84.5 = / 1 = 1 |

Locked (`--tune-jitter 6`): every line equal (NHL seen within 10 min 78.8 -> 79.3), 0 mid-play. Dev without jitter:
NHL crunch 80.6 -> 81.9, big live 44.9 -> 44.7, the rest equal.

The NBA seed-2 entry is not the landing lag: it is mid-play with no jitter too. It is a 70 s rewind into GS @ DEN
where the replayed feed has only two plays in the 200 s around the landing, so the grader can't call it dead.
Open, a data-gap case.

## Possession-start entry (Nick, Nov 4 10:45 AM)

`decider.entry.possession_start: off | crunch | all` (default off). With the time-shift buffer, the
hidden stick cues to the target's last dead ball before the current possession (a DVR rewind, whole
seek steps, up to `max_rewind_s`) instead of waiting for the next one. It is never mid-play: after the
strict, centred and urgent passes, a last pass cues with no doubt beyond the one-sided landing lag, and
with no dead ball in reach the entry still waits (it never goes live, and `max_wait_s` / `no_wait_leagues`
are ignored). `Decider.possession_policy` is the hook for the learned jump / stay policy (None = jump);
it is self-taught only (oracle, simulation, play-by-play outcomes), never Nick's buttons.

The grade is `possession_start_entry_pct` (cutgrade): entries that came up on a dead ball with no play
missed. Candidate, dev, `--tune-jitter 6`, 3 seeds: the candidate's crunch `wait` already lands on
the last dead ball by rewind whenever one is in reach, so `crunch` changes nothing and the share is
already 96.9-100%. `all` costs NBA on seed 0 (big live 77.5 -> 73.2, crunch 90.0 -> 85.3), so it
stays off. Locked: equal for all three.

## YouTube TV seek, as measured (live lab, Nov 4 12:10 PM)

The lab found three things about YouTube TV's DVR rewind: a press steps **15 s** (the app assumed 10),
the **first press of each batch is swallowed**, and each seek **stalls the picture 2-4 s**, which leaves
it that much further behind. A "60 s" rewind landed 75-80 s back and a "20 s" one 15-19 s back. Every
entry rewind (possession start, crunch, rewind-only leagues) therefore landed somewhere the planner
never checked.

- `contracts.switching.SeekModel` holds it: `YTTV_SEEK` (15 s, 1 swallowed, 2-4 s) is the stick as
  measured; `seek_model(cfg)` is what control.py assumes (`replay.seek_step_s: 15`,
  `seek_wake_presses: 1`, `seek_stall_min_s: 2`, `seek_stall_max_s: 4`). control.py sends one extra
  press, and the planner (`cuts._rewind`) lands a rewind of k at k + 2..4 s and checks the whole range.
- `decider.entry.cue`: `seek` (default) rewinds the visible stick after the flip, so the live edge is
  on screen for the presses first; `hidden` (candidate) rewinds the hidden stick before the flip. The stall
  passes off screen and puts the stream exactly as far behind as it takes, so the content that comes up
  doesn't depend on it; the flip waits ~1.5 s + the stall. `pause` (seek past it on the hidden stick, play
  up to the dead ball, pause, flip) is graded and loses: the flip waits up to ~19 s and the old game gets
  left mid-play.
- Sims: `--real-seek` re-lands every entry rewind from the presses control.py sends
  (`backtest.seek_cue`). New grades per night: `rewinds`, `flash_mid_play` (an on-screen rewind whose
  live edge showed play first) and `plays_missed_entry`.
- Hold calibration: a time-shift hold is measured on the stick's own clock against its position since the
  tune anchor (`Controller._held_for`, error under 0.5 s). The wall span from pause to play over-reports by
  ~1.4 s, so it is only the fallback, less `timing.behind_live_s.hold_overreport_s` (1.5).

Candidate, honest dev config (`--tune-jitter 6 --flip-dist --break-anchors --positions --real-lag`),
seeds 0/1/2:

| | today's code (10 s assumed) | calibrated seek | hidden-stick cue |
|---|---|---|---|
| NFL mid-play entries | 33/32/34 | 0/0/0 | 0/0/0 |
| NFL mid-play cuts | 44/44/63 | 0/0/0 | 0/0/0 |
| NFL live-edge glimpses mid-play | 345/312/341 | 332/270/304 | 0/0/0 |
| NFL big live % | 57.6/57.0/56.5 | 60.2/60.3/59.7 | 61.4/61.1/61.4 |
| NFL crunch live % | 74.4/73.7/72.9 | 73.4/71.8/72.3 | 71.5/70.8/72.7 |
| NHL mid-play entries | 37/35/32 | 0/0/0 | 0/0/0 |
| NHL mid-play cuts | 86/76/76 | 0/0/0 | 0/0/0 |
| NHL landings in dead time % | 83.7/85.6/86.2 | 100/100/100 | 100/100/100 |
| NHL big live % | 44.7/42.8/44.4 | 46.1/47.8/46.1 | 45.2/47.0/46.3 |
| NHL crunch live % | 77.6/77.4/80.8 | 77.8/82.6/79.7 | 76.5/83.4/81.2 |
| NBA mid-play entries / cuts | 1/1/1, 2/3/1 | 0, 0 | 0, 0 |

What the planner believed before (rewinds landing exactly where planned) was 0 mid-play with NFL big live
62.5/62.3/61.7 and NHL 49.4/47.0/49.4: those numbers were never real on the TV.

Locked, read once (seed 0):

| | believed before | today's code | calibrated seek | hidden-stick cue |
|---|---|---|---|---|
| NFL mid-play entries / cuts | 0 / 0 | 12 / 24 | 0 / 0 | 0 / 0 |
| NFL live-edge glimpses mid-play | 0 | 127 | 126 | 0 |
| NFL big live % | 59.4 | 53.8 | 57.6 | 59.5 |
| NFL crunch live % | 74.6 | 73.1 | 71.3 | **67.2** |
| NHL mid-play entries / cuts | 0 / 0 | 14 / 38 | 0 / 0 | 0 / 0 |
| NHL landings in dead time % | 100 | 86.6 | 100 | 100 |
| NHL big live % / seen within 3 min % | 51.0 / 72.1 | 46.6 / 68.3 | 49.5 / 72.6 | 49.0 / 75.5 |
| NHL crunch live % | 81.7 | 78.7 | 80.9 | **76.2** |
| NBA | equal everywhere | | | |

Locked seeds 1/2 (same configs, to size the noise): hidden cue vs calibrated seek, NFL crunch 69.9/71.6
vs 73.7/73.7 and big live 61.4/60.0 vs 58.3/58.5; NHL crunch 78.7/82.1 vs 70.6/82.1, seen within 3 min
76.0/76.0 vs 71.6/73.6. So the hidden cue costs NFL crunch 2-4 points (each rewind flip waits ~4-5 s for
the seek) and the NHL crunch swing is noise. Pre-cueing during a wait can't win it back: on NFL dev seed
0 none of 1188 cued entries followed a wait of 6 s or more (the plan rewinds at once). Whether a ~1 s
live glimpse before an on-screen rewind counts as mid-play is Nick's call; the candidate assumes it does.

## Self-calibration, sync half (flag `calib.auto`, off; overseer item c)

Every night, with no one's input, the app re-measures what lane 2's planning assumes and writes it to the
night's `data/calib/YYYY-MM-DD.json`, `sync` section (lane 3's `redzone/calibration.py` format; their
`timing` section holds flip time and press-to-TV). The outlier guard refuses a value more than 3 MADs
from the last 5 accepted nights; a refused value is never read back.

- **Seek** (`yttv_seek_step_s`, `yttv_seek_wake_presses`, `yttv_seek_stall_s`): every entry rewind logs
  one row to `data/seeks.jsonl` (`Controller._seek_sample`): the presses sent, and how far behind the
  picture really went, from the stick's own clock against its position since the stream's anchor (the same
  measurement the live lab did by hand). `calibration.seek_fit` reads the wake presses (the most presses that
  moved < 1 s), the step (slope) and the stall (intercept).
- **Per-channel delay** (`delay_s:<target>`, with its sd): `data/sync.jsonl` rows now carry `behind` (what
  that tune was held or rewound behind live). `calibration.delay_fit` takes the median of the night's
  used anchors less `behind`, needing at least 5.
- At start with `calib.auto` on, `calibration.sync_prior` sets the seek model in memory
  (`contracts.switching.set_calibrated_seek`) and gives each calibrated channel's sync filter last nights'
  delay as its prior (`DelayBook.calibrated`), never surer than 2 s (a fresh tune's edge still varies ~4 s
  between sticks). Nothing in the config file changes.

## YouTube TV position is a per-tune clock; MediaSession bursts (live lab, Nov 4)

**Found in the lab's logs (adwatch.jsonl).** Every fresh YouTube TV tune starts near the same playback
position, ~46,790 s (seen at 7 tunes over 4 h, both sticks), whatever the hour. So a position read is a clock
for ONE tune: it times a seek, a pause or a stall on that tune to ~0.3 s, but it says nothing about how far
behind live a new tune landed. The filter (decider/sync.py) used to keep the channel's origin across tunes,
so a re-tune's first reads disagreed by hours, were rejected, and flagged the channel uncertain for 5 min
before it restarted. The origin is now per tune: a new tune forgets it (`SyncBook.tuned(new_stream=True)`,
BoundaryWatcher tells a new tune from a seek or pause), a seek or pause keeps it.

The sims had the same mistake: `--positions` read `wall - channel origin - true delay`, which handed the
filter each tune's unknown landing (tune_jitter 0-6 s) for free. Now it is a per-tune clock that moves only
when the stream moves under the same tune (backtest._position_tick). Honest candidate config, seeds 0-2,
sync honesty pooled over on-screen ticks (cov = true error within 2 sigma):

| split | league | cov 2 sigma | sigma <= 3 s | sigma p50 | err p50 | big live | crunch | mid-play entries |
|---|---|---|---|---|---|---|---|---|
| dev | NFL | 99.8-100 -> 98.5-100 | 93.5 -> 47.6 | 3.0 -> 9.0 | 2.7-4.0 -> 2.8-3.2 | 61.1-61.4 -> 54.7-57.7 | 70.8-72.7 -> 69.2-71.0 | 0 -> 0 |
| dev | NHL | 97.1-100 -> 94.9-99.6 | 90 -> 40.5 | 3.0 -> 8.8-8.9 | 2.8-3.8 -> 3.0-3.9 | 45.2-47.0 -> 46.3-47.0 | 76.5-83.4 -> 76.7-81.7 | 0 -> 0/0/1 |
| locked | NFL | 99.6-100 -> 99.7-100 | 94.6 -> 34-50 | 3.0 -> 9.0 | 2.6-4.1 -> 2.5-3.2 | 59.5-61.4 -> 55.8-56.7 | 67.2-71.6 -> 70.1-72.5 | 0 -> 0 |
| locked | NHL | 96.6-100 -> 96.7-100 | 91.8 -> 35.5-39.5 | 3.0 -> 9.0 | 2.9-4.0 -> 3.0-3.6 | 48.1-49.5 -> 50.5-52.9 | 76.2-82.1 -> 77.0-80.4 | 0 -> 0/1/0 |

(NBA reads no YTTV positions in the sim: unchanged.) The old NFL big-live figures were ~5 points too
good. The new mid-play entries (one NHL night on dev, one on locked) are the sim being honest about a
re-tune's unknown landing, not a code change on the Mac.

**MediaSession bursts are the content anchor.** `onPlaybackStateChanged` fires in bursts (4-8 updates over
1-2 min, 15-30 min apart) at the same stream position on both sticks and exactly one hold later on a held
stick. `sync_probe.BurstWatch` finds each onset: a playing update after a 300 s quiet gap, confirmed by a
second within 60 s, with the 30 s after any tune, seek, pause or PLAY ignored. On the lab's 5 h of logs it
finds the 5 real bursts on both sticks and nothing else (the lone updates and the pause/resume lines drop
out). The onset goes to the engine as a `burst` screen event and is logged in sync_anchors.jsonl with the
feed's last dead ball, its play type and the delay the app believed (`delay_mu`, `delay_sd`).

What it can't do yet: a burst only becomes a delay anchor if it follows something on the feed by a steady
gap. If bursts are TV breaks, every YTTV channel, on screen or hidden, gets an anchor it has no other way
to get (YTTV has no UI text and logs no ad lines). `scripts/burst_align.py` answers that from one night
(bursts vs the feed's dead balls, against random moments as the null); `sync.burst_anchor.enabled` stays
off until it does, with `gap_s` / `gap_sd_s` fitted from it. Comparing two tunes by a shared burst only
works while both sticks play the same channel, which the app never does by design, so that use stays
in the lab (it is how the hold was measured to 0.3 s).

## Replay and resume (flag `replay.resume`, off)

A catch-up replay costs buffer instead of live play. At the screen game's dead ball (the leave gate),
the app pauses that stick, flips to the replay parked on the hidden stick, then flips back and
unpauses on the same frame, so we leave and land on one dead ball (rule #1). The screen now runs that
much further behind its buffer. It wins the time back by skipping ahead inside its next commercial or
TV timeout (`replay.resume_min_skip_s`), or by any switch away, which comes back to a fresh tune.

Limits:
- `replay.resume_window_s` (180): only plays that aired within 3 minutes.
- `replay.resume_cap_s`: never run further behind the buffer than this, counting entry rewinds.
- `replay.resume_crunch_share` (0): crunch time is never paused.

The sims report `behind_extra_p95_s` and `behind_extra_max_s` (per night and pooled) and `resumes`.

**Hardware gate (night-lab step for lane 3's next probe).** On each stick model (4K Plus, 4K 2023), on
YouTube TV live with DVR:
1. Note the playback position, then send a media PAUSE over adb.
2. Flip the TV input away for 20 s and then 60 s, and flip back.
3. Send PLAY and read the position again.

Pass: playback resumes within ~1 s of PLAY, on the same frame (position delta ≤ 0.5 s plus the time
before PLAY), with no ad insertion, no "Are you still watching" prompt and no jump to live, in 20 of
20 trials per model. Log `pause_ack_s`, `resume_ack_s`, the position deltas and any UI dump. Until
this passes, the sims' zero-cost pause is unproven and the flag stays off.
