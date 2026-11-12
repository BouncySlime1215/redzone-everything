# 03 — Switch timing: cut only in dead time, enter at the restart

Research + model design for RedZone Everything, Nov 3 2026 (11:30 PM ET). Read-only pass over
`redzone/decider/*`, `redzone/scoring/*`, `redzone/screen/*`, `redzone/sources/fastcast.py`,
`redzone/lab/{backtest,cutgrade,sportsgrade}.py`, `scripts/eval*.py`, plus two read-only analysis
scripts run against `data/archive` and `data/recordings` (paths in the appendix). Every number
marked **[archive]** or **[recordings]** was computed tonight through the repo's own `DeadTracker`.

## The one number that decides everything: data lead

```
W  = real time of an event (ESPN play wallclock)
A  = W + feed_lag        when the app learns of it      polled ESPN summary: NBA 38 s, CBB 35 s,
                                                         NFL 45 s, NHL 49 s median  [recordings]
S  = W + delay           when it reaches the screen     delay book default 45 s (taps); Super Bowl LX
                                                         YouTube TV measured 53 s behind the field
lead = delay − feed_lag  how far the data runs AHEAD of the screen
```

With polling, **lead ≈ −5…+10 s**: the data is level with the screen. The backtest assumes
`feed_lag=8, delay=35` → lead ≈ +35 s (`redzone/lab/backtest.py:216-236`). Every scorecard "0
mid-play cuts" was earned with ~30 s more warning than the live app ever gets. FastCast was
measured at wallclock→receive ≈ 20 s and publish→receive 0.01–0.21 s (`fastcast.py` docstring),
so push adds **+15–25 s of lead**. Everything below is designed for lead ≈ 0 with polling and
lead ≈ +20 s with push; the entry problem is therefore a *prediction* problem (hazard models),
not a detection problem.

---

## Top 10 recommendations (in order)

1. **Make `lead` a measured, per-game, first-class quantity and gate timed cuts on it.**
   Compute `lead = delay.mu − timeline.lag_est()` per game, show it on the phone, log it to
   `data/recordings`. Turn FastCast on as a source (it already runs next to polling and the
   Merger takes whoever is ahead) and keep the NBA/NHL league feeds in the race: in tonight's
   race log the `nba` CDN feed was first 133 times with no other source catching it inside the
   window [recordings]. Fix the backtest default (`feed_lag`) to the measured per-league lag or
   replay the recorded lags; re-run the scorecards before trusting any other change (Experiment E1).

2. **Replace the fixed `FLOOR_S` / `MEDIAN_S` tables with per-(league, kind) survival curves
   conditioned on elapsed time.** The archive shows strong, exploitable hazard structure: an NFL TV
   timeout that has run 150 s has a median 32 s left (p10 14 s); an NBA long timeout at 150 s has
   35 s left (p10 24 s); an NFL two-minute warning at 180 s has 14 s left (p10 3 s) [archive].
   A floor (2nd percentile at e=0) cannot tighten as the break ages; `S(t | e) = S(e+t)/S(e)`
   does, for free. Ship Kaplan–Meier tables first (zero parameters, E2), then a discrete-time
   boosted hazard (E3).

3. **Enter at a calibrated lower bound of the restart, not at a median.** Commit the flip at
   `t = onset + q_lo(R | kind, e, x) − LEAD_S[league] − L_switch(p95) − settle`, where `q_lo` is a
   conformalized 2–5 % quantile (CQR / conformalized survival; refs below) that tightens as `e`
   grows. The loss is asymmetric (landing 10 s early costs 10 s of huddle; landing 1 s late is a
   mid-play entry), so the lower quantile is the right target, and `entry_wait_s` is the price.

4. **Hunt the unlabeled breaks with elapsed time.** ESPN names only ~⅓ of NHL TV timeouts: 349
   "TV timeout" entries in 109 games vs ~9 per game expected (3 per period); the rest are plain
   "Stoppage" entries. Their length gives them away: for an NHL whistle, P(≥20 s remaining) is
   0.35 at e=30 s but 0.92 at e=60 s, with a median 83 s still to go [archive]. Same pattern in
   NFL `team_timeout` (median remaining 10 s at e=90, 53 s at e=120) and NFL `score` [archive].
   A hazard model with `e` and "mark owed" as features captures this; a floor never will.

5. **Fuse three stoppage-onset detectors in log-odds, and use the fourth only to pre-position.**
   (i) feed event (`espn.stoppage_for`), (ii) a clock-stall change-point detector on the 3-s
   scoreboard poll (the scoreboard endpoint won 5222 of 5581 score races [recordings]; a stalled
   game clock is the earliest feed sign of a whistle in NBA/NHL), (iii) stick-side evidence on
   Prime (score bug disappears → ad; OCR'd clock stops → whistle; "TIMEOUT / END OF 1ST" text),
   (iv) the onset hazard (Brain v3 `break_soon`, re-labeled on wall-clock horizons) to pre-tune
   the hidden stick 20–30 s ahead. Bayesian online change-point detection (Adams & MacKay) or a
   CUSUM on clock increments is the right tool for (ii).

6. **Track stream delay with a per-(app, channel, tune) robust Kalman filter, with asymmetric
   use.** State = delay (+ slow drift); measurements with their own noise: taps σ≈4 s, quarter-hour
   title flips σ≈6 s (`screen/boundary.py`), score-bug score changes σ≈1 s, and a new one: **OCR'd
   game clock vs feed clock** on Prime, which yields a delay sample every 2 s while the clock runs
   (E7). Reject outliers with a Huber/Student-t innovation gate, inflate on buffering
   (`DelayBook.buffered`). Decisions use the *upper* quantile of delay to decide "is the play still
   on screen" and the *lower* quantile to decide "has the restart aired".

7. **Decide cuts as optimal stopping with a calibrated P(safe).** With the hidden stick pre-tuned,
   the switch is an input flip: SmartThings median 1.98 s (10/10) + ~1 s picture settle. Cut iff
   `P(no restart in [t, t + L_p95 + settle] | kind, e, x)` ≥ 1 − ε (ε ≤ 0.5 %, the north-star
   "unsafe leaves ≤ 0.5 %") **and** the expected value of cutting now beats the option value of
   waiting inside this window (a 1-s-grid Bellman recursion over the predicted remaining-time
   distribution; ≤300 states, microseconds). The gate interval shrinks from "tune cost 3–8 s +
   slack" to ~3.5 s, which is how switching gets *more frequent* without getting riskier.

8. **Measure what Nick asked for: a landing-offset histogram, not just a count.** Add to
   `cutgrade`: `entry_offset_s` (picture-up minus on-screen restart; must be ≤ 0, target −2…−15 s),
   `leave_offset_s` (cut minus last play's end on screen), `% landings in dead time`,
   `mid-play per 100 switches` with a Wilson interval (0/200 only proves < 1.5 %; the 0.5 % claim
   needs ~600 clean switches on locked nights), `missed big plays`, and two opportunity metrics:
   safe windows ≥ 15 s found per hour and the share taken.

9. **Build the restart-labeled dataset from what you already have, and label it on the screen,
   not only in the feed.** The archive yields 43k NFL, 6.4k NBA, 5.8k NHL, 14k MLB and 0.9k CBB
   dead windows with exact restarts [archive], plus rule state (timeouts used this quarter, marks
   owed, down/distance, outs, national vs local from ESPN's `broadcasts`). Then calibrate the
   feed→screen bias per league with Prime OCR (restart = first running clock on screen), because
   the ESPN wallclock means different things per league (`PLAY_S`: NFL ≈ whistle, NBA/NHL ≈ event).

10. **Roll out per league behind flags, NFL → NBA → NHL → MLB, each through the 95 % gate on
    locked nights with realistic lag.** NFL first (long windows: huddle median 28 s, p5 13 s;
    predictable kinds), NBA second (first-two-timeouts rule verified: 184 s, CV 0.12), NHL third
    (owed-mark + elapsed detector), MLB fourth (inning break 168 s, CV 0.23). Keep `hold_only: [nhl]`
    until E6 shows the hazard entry beats it.

---

## Per-league timing facts

Lengths are **[archive]** dead windows through `DeadTracker.windows()` (start = play wallclock
+3 s for action plays; end = next action's wallclock − `PLAY_S`, or the restart worked back from
the game clock / the faceoff). Median (p5–p95), CV = sd/mean. Lower CV = more predictable.

| League | Stoppage | Rule / source | Archive length, s | ESPN restart marker | Modeling note |
|---|---|---|---|---|---|
| NFL | TV commercial ("Official Timeout") | 4 breaks/quarter × ~2:20 since 2017 ([SI](https://www.si.com/nfl/2017/03/22/roger-goodell-commercial-breaks-pace-play-changes), [Awful Announcing/CBS](https://awfulannouncing.com/cbs/cbs-says-fewer-ad-breaks-nfl-games-will-stay-2018-season.html)); kickoff "double-ups" dropped ([theScore](https://www.thescore.com/nfl/news/1261225)) | 180 (157–240), CV 0.20, n 3964 | next scrimmage play wallclock − 10 s | Hazard tightens late: median remaining 32 s at e=150 (p10 14) |
| NFL | Two-minute warning | Rule 4-5-4 ([NFL Ops glossary](https://operations.nfl.com/learn-the-game/nfl-basics/terms-glossary/glossary-terms-list/two-minute-warning)) | 183 (150–222), CV 0.16, n 568 | same | At e=180: 14 s left (p10 3) |
| NFL | End of Q1/Q3 | — | 191 (170–233), CV 0.13 | same | Very predictable |
| NFL | Halftime | 12–13 min + ads | 844 (814–924), CV 0.07 | same | Plan a visit; return at 800 s |
| NFL | Score (TD/FG → PAT → kickoff → snap) | — | 112 (55–240), CV 0.49, n 678 | kickoff logged, then snap | Bimodal (break vs none); e disambiguates |
| NFL | Team timeout | 1 per call, no ad unless it becomes the TV break | 64 (41–117), CV 0.42 | next play | Bimodal: median remaining 10 s at e=90 but 53 s at e=120 |
| NFL | Replay review | 60-s review cap, longer if reversed ([Fox Sports](https://www.foxsports.com/stories/nfl/nfl-referees-can-defy-laws-of-time), [Bucs 2019](https://www.buccaneers.com/news/nfl-updates-instant-replay-rules-17180578)) | 203 (90–287), CV 0.33 | next play | Usually joined to a TV break |
| NFL | Huddle (every other play) | 40-s play clock | 28 (13–35), CV 0.32, n 27 112 | next play − 10 s | The everyday cut window; needs lead ≥ 0 or a 2-s flip |
| NFL | Injury / penalty / punt / kickoff | — | 38 (17–133) CV 0.76 / 44 (23–84) / 44 (33–79) / 35 (27–62) | next play | Injury is the least predictable kind |
| NBA | Mandatory (long) timeouts | 2 per period: first dead ball under 6:59 (home charged) and 2:59; 2:45 local / 3:15 national ([NBA Rule 5 §VI](https://official.nba.com/rule-no-5-scoring-and-timing/)); 7 team timeouts of 75 s ([NBA 2017-18 changes](https://pr.nba.com/nba-rules-changes-2017-18)) | `timeout_long` 184 (172–226), CV 0.12, n 652 | first clock-moving play, worked back by the clock that ran | Marks are a backstop: 72 % of a quarter's first timeouts are team-called *before* 6:59; only 25 % are forced at the mark [archive]. "First two of a quarter are long" (already in `cuts.py`) is the right feature |
| NBA | Later team timeouts | 75 s | `timeout_short` 102 (87–156), CV 0.32 | same | At e=60: 43 s left (p10 31) |
| NBA | Quarter breaks / halftime | 2:30 local, 3:30 national between Q1-2 and Q3-4; halftime 15:00 ([Rule 5 §II](https://official.nba.com/rule-no-5-scoring-and-timing/)) | end_period ≈171, halftime 933 (`breaks.py`) | same | |
| NBA | Shooting foul → free throws | 10 s per attempt ([Rule 9](https://official.nba.com/rule-no-9-free-throws-and-penalties)) | `shooting_foul` 50 (29–72), CV 0.31; `free_throw` 51 (28–77); `foul` 23 (14–45) | last FT ends the dead ball | Clean 20–50 s entry points; the inbound after the last FT is the restart |
| NBA | Coach's challenge / review | — | 186 (27–288), CV 0.43 | same | Wide; often merges with a timeout |
| NHL | TV timeouts | 3/period at the first stoppage after 6, 10, 14 min elapsed; not after a goal/icing or on a power play ([Wikipedia NHL rules](https://en.wikipedia.org/wiki/National_Hockey_League_rules), [Television timeout](https://en.wikipedia.org/wiki/Television_timeout); secondary) | 149 (135–191), CV 0.17, n 349 **labeled**; ~630 more hide as "Stoppage" windows > 80 s | "Face Off" play (exact) | ESPN's NHL play clock is **elapsed** (0:00 → 20:00) [archive]; ESPN labels ≈⅓ of TV timeouts |
| NHL | Ordinary whistle | — | 36 (22–151), CV 0.81, n 3382 | faceoff | Hazard flips at ~45 s: P(≥20 s left) 0.35 @30 s → 0.92 @60 s |
| NHL | Goal / icing / penalty / team timeout | team timeout 30 s (Rule 87) | 48 (39–69) / 30 (22–43) / 51 (38–133) / 102 (89–141) | faceoff | Goal and icing never carry a TV break |
| NHL | Intermission | 18 min (NHL rulebook; not re-verified online tonight) | 1129 median (`breaks.py`), p5 1120 (`screen.py`) | period start | |
| MLB | Between pitches | 15 s empty / 18 s runners on (2024; was 20) ([ESPN](https://www.espn.com/mlb/story/_/id/39161682/mlb-competition-committee-tinkering-pitch-runners-lanes)) | 11 (3–35), CV 0.93, n 10 219 | next pitch − 8 s | Too short to visit; cut-only |
| MLB | Between batters | 30 s | 19 (8–159), CV 1.25 | same | Long tail = pitching changes / mound visits |
| MLB | Inning break | 2:15 local / 2:40 national / longer postseason (MLBPA pace-of-game regs); pitching change resets to 2:00 (2024, ESPN above) | 168 (114–209), CV 0.23, n 855 | first pitch − 8 s | At e=150: 24 s left (p10 14) |
| CFB | Two-minute timeout | Automatic at 2:00 of Q2/Q4 since 2024; broadcaster holds a media timeout for it ([NCAA Apr 2024](https://www.ncaa.org/news/2024/4/19/media-center-technology-rules-approved-in-football.aspx), [rules report](https://ncaaorg.s3.amazonaws.com/championships/sports/football/rules/Feb2024PRMFB_AnnualMeetingReport.pdf), [Fox explainer](https://www.foxsports.com/stories/college-football/college-footballs-2-minute-timeout)) | no CFB archive yet | — | Treat like NFL two-minute |
| CFB | Media timeouts | Per TV agreement at scores / possession changes (no fixed marks) | — | — | NFL `score`/`punt` priors as a start |
| CBB (men) | Media timeouts | Rule 5-14.10: first dead ball **at or after** the 16/12/8/4 (or 15/10/5) marks; a team timeout or replay within 30 s before a mark becomes it; the first charged timeout of the 2nd half is media-length ([NCAA 2020-21 format](https://s3.amazonaws.com/ncaaorg/championships/sports/basketball/rules/men/2020-21PRMBB_D1RSMediaTimeoutFormat.pdf), text extracted) | 190 (143–252), CV 0.19, n 137 (17 games) | clock-moving play | Conference experiments (17/14/11/8/4 in 2nd half, [OVC](https://ovcsports.com/news/2023/12/4/mens-basketball-teams-to-use-experimental-rule-for-second-half-media-timeouts-in-conference-play.aspx)) → learn marks per conference |
| WCBB | Media timeouts | One per quarter at the first dead ball at or below 5:00; a team timeout before the mark becomes it ([NCAA 2021-23 guidelines](https://ncaaorg.s3.amazonaws.com/championships/sports/basketball/rules/women/2021-23PRWBB_MediaTimeoutGuidelines.pdf), text extracted; [NCAA 2015](https://www.ncaa.org/news/2015/6/25/media-timeout-format-approved-in-women-s-basketball.aspx)) | ~174 (`breaks.py`) | same | |

Stream/feed facts worth pinning: Super Bowl LX (Feb 2026, Stats Perform) measured OTA 19 s,
cable 38 s, Peacock 48 s, **YouTube TV 53 s**, Hulu 53 s, DirecTV Stream 60 s, NFL+ 62 s behind the
field ([TV Technology](https://www.tvtechnology.com/platform/study-ota-broadcasts-offered-lowest-latency-of-super-bowl-lx-feeds));
Phenix's 2025 numbers were YouTube TV 67 s, Fubo 78 s, Tubi 26 s ([Phenix](https://blog.phenixrts.com/once-again-super-bowl-latency-numbers-are-staggering)).
Prime Video has no independent study; Amazon's stated target is ≤ 10 s glass-to-glass and one
reviewer measured ~6–8 s behind OTA on Fire TV ([Streaming Media Blog](https://www.streamingmediablog.com/2025/01/prime-afc-wildcard.html)), so Prime's delay is likely
much smaller than YouTube TV's — meaning **lead is most negative on Prime**. Classic HLS clients
start ≥ 3 target durations from the live edge ([RFC 8216 §6.3.3](https://www.rfc-editor.org/rfc/rfc8216.txt)), which is why
app-level delay is a per-app constant plus a per-tune offset, exactly what `DelayBook` models.
The repo's own boundary measurements (truTV 22 s, ESPN 26 s behind the *schedule clock*,
`screen/boundary.py`) are relative to network air time, not the field; add the network's
production delay to compare with wallclock-based taps.

---

## Model design

### (a) Detect the stoppage as early as possible

Timebase per game: the event at `W`; the feed knows at `W + ℓ` (ℓ measured per game by
`Timeline._lags`, use the median, not only the max); the screen shows it at `W + D` (per tune).

Detectors (each emits a log-likelihood ratio for "a stoppage began at τ"):

1. **Feed event** — `espn.stoppage_for` / `cuts.after_kind` (what exists). Certain, but late by ℓ.
2. **Clock-stall change point** — on the 3-s "hot" scoreboard poll, model clock increments
   `Δclock/Δwall` as a two-regime process (running ≈ 1, stopped ≈ 0) and run Bayesian online
   change-point detection ([Adams & MacKay 2007](https://arxiv.org/abs/0710.3742)) or a CUSUM
   ([Page 1954](https://doi.org/10.1093/biomet/41.1-2.100)) on the increments; a run-length posterior
   gives P(stopped since τ). This is the fastest *feed* signal of a whistle in NBA/NHL (the play
   text is entered later) and it also pins the onset time to ±one poll. Offline, `ruptures`
   ([Truong, Oudre & Vayatis 2020](https://arxiv.org/abs/1801.00718)) segments archived nights to label onsets.
3. **Stick-side (Prime only; YouTube TV frames are DRM-black)** — extend `ScoreBugWatcher` to emit
   three binary signals at ~2 s cadence: bug present (ads have no bug), OCR'd clock advancing,
   period text (TIMEOUT, END OF 1ST, HALFTIME). These see the *screen* directly (delay 0), so they
   also catch the broadcast's replays and ad tails that no feed knows about. Run them on the hidden
   stick too (`screencap` works per stick; HDMI1 stick ~7 s/frame, so cadence is the limit). The
   broadcast-analysis literature uses the same cues: black frames + audio dips for ad boundaries
   ([Lienhart et al. 1997](https://link.springer.com/article/10.1023/A:1019996817159)) and logo
   transitions for replays ([Zhao et al., ICASSP 2012](https://unpaywall.org/10.1109%2FICASSP.2012.6288154)).
4. **Onset hazard (anticipation only)** — P(stoppage starts within the next 10/20/30 s wall) from
   rule state + Brain v3's `break_soon` re-trained on the existing `break_soon_wall` /
   `next_break_wall_s` labels (`lab/datasets.py`). Used to pre-tune the hidden stick and to arm
   the stopping rule, never to cut.

Fusion: sum the calibrated LLRs (naive Bayes over detectors, calibrated per league on archived
nights), declare "dead on screen now" at posterior ≥ 0.99, and hand the onset time τ and kind
to the duration model. Keep the existing `[mid-play guard]` interval logic as a hard floor.

### (b) Predict stoppage length per type and league

Target: remaining time to the on-screen restart, `R − e`, given kind `k`, elapsed `e`, covariates
`x`. Censoring: windows with no restart in the data (game end, feed drop, frozen NBA wallclock
`FROZEN_HOLD_S`) are right-censored at the observed length.

Covariates `x` by league (all already parseable from the feed):

- NFL: kind (TD vs FG vs safety; punt; turnover; team vs official timeout; review; injury),
  quarter, clock, two-minute/quarter-end proximity, down & distance before the stop, whether a
  kickoff follows, score margin, national/prime-time flag (`broadcasts`).
- NBA: timeouts already taken this quarter (1st/2nd → long), mandatory mark owed (clock ≤ 6:59 /
  2:59 with none taken), FT sequence position ("1 of 2"), review/challenge, period, clock,
  national vs local (2:45 vs 3:15 and 2:30 vs 3:30 are *rules*), crunch flag.
- NHL: mark owed (first eligible stoppage after 6/10/14 min elapsed), power play, goal/icing
  exclusion, penalty text, period, clock, team timeout.
- MLB: inning number, half, outs (2 outs → inning break), pitching change, mound visit,
  national flag.

Models, in the order to ship them:

- **M0 Kaplan–Meier per (league, kind)** — the tables in this report; conditional
  `S(t|e) = S(e+t)/S(e)`. Zero parameters, exact for the lookup use, trivially exportable as JSON.
  Cheap win over `FLOOR_S`/`MEDIAN_S` because it tightens with `e`.
- **M1 Discrete-time boosted hazard** — expand each window into 2-s person-period rows up to
  300 s (NFL ≈ 1.3M rows), label `y=1` in the bin containing the restart, fit LightGBM binary on
  `(k, e, x)`; `h(t|x)` is the per-bin restart probability and `S = Π(1−h)`. Formulation:
  [Singer & Willett 1993](https://journals.sagepub.com/doi/10.3102/10769986018002155). It handles
  the bimodal kinds (NHL whistle, NFL team timeout/score) because `e` is a feature. Export like
  `scoring/brain.py` (numpy trees, sub-ms per state).
- **M2 Direct quantiles or AFT** — LightGBM quantile objectives at α = 0.02/0.5/0.98 on `R − e`,
  or XGBoost AFT ([Barnwal, Cho & Hocking 2022](https://arxiv.org/abs/2006.04920); supports
  censoring natively). Random survival forests ([Ishwaran et al. 2008](https://projecteuclid.org/euclid.aoas/1223908043))
  and `scikit-survival` ([Pölsterl 2020](https://www.jmlr.org/papers/v21/20-729.html)) are the
  offline benchmarks; Cox (1972) is the interpretable baseline for hazard ratios of rule
  features (e.g., "national broadcast ×1.4 on NBA timeout length").
- Sports precedent: Kaplan–Meier + Cox with time-dependent covariates on NHL timeouts/goalie
  pulls ([Ferer, Hockey Graphs 2020](https://hockey-graphs.com/2020/05/28/quantifying-the-value-of-an-nhl-timeout-using-survival-analysis-part-1/)).

Calibration and guarantees:

- Evaluate the horizons the decision uses: reliability of `P(R − e ≤ L)` for L ∈ {3, 6, 10, 20} s
  (ECE; [Guo et al. 2017](https://proceedings.mlr.press/v70/guo17a.html) for the recipe), plus
  integrated Brier score and C-index for the curve.
- **Conformal lower bound on the restart**: split-conformal CQR on the lower quantile
  ([Romano, Patterson & Candès 2019](https://proceedings.neurips.cc/paper_files/paper/2019/file/5103c3584b063c431bd1268e9b5e76fb-Paper.pdf));
  with censored windows use conformalized survival analysis ([Candès, Lei & Ren 2023](https://arxiv.org/abs/2103.09763)),
  which gives a finite-sample lower predictive bound on the time-to-event. Calibrate per
  league×kind-group on held-out *nights*.
- **Night-to-night drift**: wrap with adaptive conformal inference
  ([Gibbs & Candès 2021](https://arxiv.org/abs/2106.00170)): `α_{t+1} = α_t + γ(α − err_t)` on the
  live night's own misses (γ ≈ 0.01), so a broadcaster with longer breaks widens the bound within
  the night without retraining.

### (c) Estimate each stream's delay vs the data feed

Per key `(app, channel, tune)`, a scalar Kalman filter ([Welch & Bishop, UNC TR 95-041](https://www.cs.unc.edu/~welch/kalman/kalmanIntro.html)):
state `D` (delay) with slow random-walk process noise (q ≈ 0.05 s²/min) and a jump term when the
media session reports a stall (`buffered()`: +3 s mean, +5 s σ, as today). Measurements and noise:

| Measurement | Where | σ | Cadence |
|---|---|---|---|
| "I just saw it" tap | `DelayBook.calibrate` | 4 s (reaction + rounding floor) | rare |
| Quarter-hour title flip | `screen/boundary.py` | 6 s | ~4/h per channel |
| Stream position vs channel origin | `boundary.py` origin model | ~1 s once the stick clock is pinned to 2 s | continuous |
| Score-bug score change | `screen/scorebug.py` (Prime) | gap/2 ≈ 1 s | per score |
| **OCR'd game clock vs feed clock (new)** | Prime, 2-s frames | ~1–2 s | **every frame while the clock runs** |
| Period text onset (HALFTIME, END OF 1ST) vs feed | Prime | 2 s | per period |

Robustness: Huber / Student-t innovation gating (a 10-s outlier moves the estimate little), and
an asymmetric prior — accept "more delay" readily, demand two agreeing samples for "less delay",
because a low delay estimate is what cuts plays mid-play (`MIN_TAP_SIGMA_S` comment). The OCR
clock alignment is a robust offset estimate: for each frame, `d_i = f_i − W(clock = c_i)` using
the feed's (wallclock, clock) pairs; take the median over the last N and reject periods where the
same clock value is ambiguous (use the period text). Keep two quantiles for decisions:
`D_hi` (is the play still on screen?) and `D_lo` (has the restart aired?). The tap floor stays as
a cap on how narrow σ may get until the OCR source has ≥ 3 agreeing samples (as `SCREEN_SIGMA_S`
does today).

Per-app priors from the public studies above: YouTube TV ≈ 50–55 s behind the field, Prime
≈ 10–30 s; both consistent with the repo's `delay.default` once the field→wallclock offset is
accounted for. Also log `lead` per game (recommendation 1): when `lead < 0` the screen leads the
data and the OCR detectors are the *only* early warning.

### (d) When to commit to a cut

Inputs at tick `t` (1 s): posterior that a dead window of kind `k` started at τ (from (a)),
elapsed `e = t − D̂ − τ` in screen time, survival `S(·|k, e, x)` from (b), delay quantiles from (c),
switch latency `L` (SmartThings: median 1.98 s, 10/10; measure p95/p99 — cloud tails matter) and
settle `s` (1 s).

Safety: `P_safe(t) = P(R − e > L_p95 + s + 2σ_D | k, e, x)`. Cut only if `P_safe ≥ 1 − ε`,
ε ≤ 0.005. Because a sent flip cannot be aborted, the interval is the whole `[t, t + L_p95 + s]`
(what `leave_window` already checks, with a fixed `required_s`).

Value: `EV_cut(t) = V_dest(t) − c_dead · E[wait at destination] − C_mid · (1 − P_safe)` where
`V_dest` includes the destination's own entry plan (land on its dead ball, rewind if needed) and
`C_mid` is large enough that ε binds first. `EV_stay(t)` is the current game's value when play
resumes plus the **option value of cutting later in this window**:
`U(t) = max{ EV_cut(t), E[U(t+1) | restart has not happened] }`, a backward recursion over the
predicted remaining-time distribution on a 1-s grid (optimal stopping,
[Chow, Robbins & Siegmund 1971](https://en.wikipedia.org/wiki/David_Siegmund)). Cut at the first
`t` where `EV_cut(t) ≥ E[U(t+1)|survive]` and `P_safe ≥ 1 − ε`. In practice this says: in a long
break, go now (waiting only burns ad time); in a short whistle with lead ≈ 0, wait for the hidden
stick to be ready and for `S` to confirm ≥ `L + s + 2σ_D` remain, else stay.

Entry (the destination side) uses the same machinery with the asymmetric target:
`t_commit = τ_dest + q_lo(R | k, e, x) − LEAD_S[league] − L_p95 − s` (lower conformal quantile, so
early-by-design), re-evaluated each tick as `e` grows; the expected early margin
(`median − q_lo`) is the `entry_wait_s` price and is typically 5–15 s for breaks, 2–5 s for FT
sequences and huddles. Pre-position the hidden stick ≥ 20 s before `t_commit` using (a)(iv).

### (e) Metrics

- **Landing-offset histogram** `Δ_land = picture-up − on-screen restart` (s): report p5/p50/p95
  and the share in [−15, 0]; any `Δ_land > 0` is a mid-play entry. Ground truth: the archive's
  restart (as `cutgrade.resume_after`), later the OCR'd clock start on Prime.
- **Leave-offset histogram** `Δ_leave = cut − last play's end on screen` (must be ≥ +3 s) and
  `restart − cut` (must be ≥ `L + s`).
- **% landings in dead time** (gate ≥ 95 %), **% leaves in dead time** (`leaves_in_dead_pct`).
- **Mid-play rate per 100 switches**, both ends, with a 95 % Wilson interval; the sample size
  needed for "< 0.5 %" is ~600 clean switches, so the gate must accumulate over locked nights.
- **Missed big plays**: `big_seen_pct`, `big_live_pct`, `crunch_live_pct`, your-team variants.
- **Opportunity**: safe windows ≥ 15 s per hour found vs taken; `switches_per_hour`;
  `dead_air_pct`; `entry_wait_s`.
- **Model health**: lead per game (s), delay σ per channel, horizon calibration (ECE at 3/6/10/20
  s), conformal coverage per night (should sit at 1 − α), FastCast vs poll lag by league.

---

## Experiments (concrete, using the archives and scorecards)

Each runs read-only on `data/archive` / `data/recordings` unless noted; times are wall-clock
effort for one person.

**E1 — Honest baseline: realistic feed lag in the scorecards (1 h).** Expose `feed_lag` (and a
per-league default of NFL 45 / NBA 38 / NHL 49 / MLB ~45 / CBB 35 s) in `scripts/eval.py` and
`eval_sports.py`; run both with and without. Record `mid_play_cuts`, `entries_mid_play`,
`switches_per_hour`, `dead_air_pct`. Expect fewer switches and more "waiting for an official
break" gates; this is the baseline everything else must beat. Success: CI gate uses the realistic
lag; the gap to the optimistic numbers is documented.

**E2 — Kaplan–Meier tables replace `FLOOR_S`/`MEDIAN_S` (half a day).** Generalize
`scratchpad/research/analysis/dead_windows.py` into `redzone/lab/survival.py` → `data/lab/survival_<league>.json`
(S(t) per kind at 1-s resolution, with n and censoring). In `cuts.DeadTracker._open_until` and
`resume_eta`, use `S(t|e)` quantiles (p02 for "surely dead until", p50 for ETA). Backtest with E1
settings. Success: `entries_mid_play` unchanged at 0, `entry_wait_s` down, `switches_per_hour` up.

**E3 — Boosted discrete-time hazard, forward-chained by night (1 day; Kaggle or local).** Build
person-period rows from the archive windows + rule-state features; split by whole nights
(train → valid → locked test, `brainfit.time_split` style); fit LightGBM; report IBS, C-index,
horizon calibration vs M0. Export numpy trees like `brain.py`. Success: calibration error at the
3/6/10 s horizons ≤ 2 points and a tighter p02 than M0 on ≥ 80 % of windows.

**E4 — Conformal coverage on locked nights (1 h after E3).** Calibrate CQR / conformalized
survival lower bounds per league×kind-group at α = 0.02; measure empirical coverage and median
bound width vs `FLOOR_S`. Add ACI with γ = 0.01 and show per-night coverage stays within ±1 point.

**E5 — NHL unlabeled TV timeouts (1 h).** Truth = whistle windows ≥ 120 s. Detector A (rule):
mark owed ∧ not goal/icing ∧ no power play ∧ `e > 45 s`. Detector B: the M1 hazard. Report
precision/recall and lead time; current baseline is "never, unless ESPN typed 'TV timeout'".

**E6 — NBA entry at the restart with a 2-s flip (2 h).** Simulate entries into `timeout_long`,
`timeout_short` and FT sequences with `entry_cost = 2.5 s`, M0/M1 lower quantiles and the
`LEAD_S[NBA] = 4 s` target; produce the landing-offset histogram and `entries_mid_play`. Compare
to today's `decider.entry.hold_only` behavior (NBA mid-play entries were 36–50 per 14 nights per
`docs/morning-report-2026-11-03.md`). Success: 0 mid-play entries with `entry_wait_s` ≤ 12 s.

**E7 — Prime OCR clock alignment (tonight, 1 h with a live Prime NBA game; needs Nick's OK to
screenshot).** Capture every 2 s, OCR the bug's clock, match to the feed's (wallclock, clock)
pairs, emit delay samples; report σ and drift over 30 min, and compare with taps. Also test
`screencap` on the *hidden* stick to detect the destination's on-screen restart. Success: σ ≤ 2 s
and ≥ 10 samples/min while the clock runs.

**E8 — FastCast head start (30 min + one night).** Enable `sources.fastcast: true`, let
`recorder.py` log per-source lag; compute lag by source and league from `plays.jsonl`. Expect
15–25 s less than polling; verify NFL/NHL/MLB documents parse (`fastcast.py` lists them as
unverified). Success: `lead` per game ≥ +10 s on push for NBA/NHL.

**E9 — Switch-latency tail (20 min, off-hours).** 50–100 SmartThings flips; record p50/p95/p99.
Use p95 in `P_safe`. If p95 > 4 s, keep the CEC flip (median 2.9 s in `data/switch_times.json`)
as the fast path and SmartThings as the reliable one.

**E10 — Scorecard instrumentation (1 h).** Add `entry_offsets`, `leave_offsets` and
`safe_windows_found/taken` to `lab/cutgrade.py` and `lab/sportsgrade.py`; print histogram bins
in `scripts/eval*.py --dead`; store in `docs/scorecards-*.json`. This is the measurement Nick
asked for and the gate for everything above.

Caveats to carry into every experiment: the archive's restart is feed-derived (NFL next play −
10 s; NBA worked back from the clock; NHL faceoff), ESPN's wallclock semantics differ per league,
and `wallclockAvailable` games only. E7 is what turns these proxies into screen truth.

---

## Appendix: what was measured tonight

- Analysis scripts (read-only on the repo):
  `/private/tmp/claude-501/-Users-nick-matta-Documents-Codex-2026-11-02-task-2-redzone/db4845a1-e6cf-4448-8874-21dc8f52883f/scratchpad/research/analysis/dead_windows.py`
  and `.../analysis/nhl_race_check.py`, outputs `dead_windows.out`, `nhl_race_check.out` next to them.
- Archive sizes used: NFL 285 games, NBA 107, NHL 109, MLB 54, CBB 17 (`data/archive/<league>/summary_*.json`).
- Recordings used: `data/recordings/2026-11-02` and `2026-11-03` (`plays.jsonl` 18.9k rows,
  `race.jsonl` 5.6k rows).
- Repo constants referenced: `cuts.FLOOR_S/MEDIAN_S/PLAY_S/LEAD_S`, `screen.MIN_TAP_SIGMA_S=4`,
  `screen.SCREEN_SIGMA_S=4`, `screen.BOUNDARY_SIGMA_S=6`, `decider.ENTRY_SLACK_S=2`,
  `two_stick.input_settle_s=1`, `polling.hot_s=3 / live_s=10`, `backtest.run(feed_lag=8, delay=35, sigma=3, tune_s=5)`.

## Sources

League rules: [NBA Rule 5 — Scoring and Timing](https://official.nba.com/rule-no-5-scoring-and-timing/) ·
[NBA 2017-18 rule changes (75-s timeouts, 7 per team, 15-min halftime)](https://pr.nba.com/nba-rules-changes-2017-18) ·
[NBA Rule 9 — Free Throws](https://official.nba.com/rule-no-9-free-throws-and-penalties) ·
[Wikipedia — NHL rules (TV timeouts)](https://en.wikipedia.org/wiki/National_Hockey_League_rules) ·
[Wikipedia — Television timeout](https://en.wikipedia.org/wiki/Television_timeout) ·
[ESPN — MLB 2024 rule changes (18-s timer, 2:00 pitching-change reset)](https://www.espn.com/mlb/story/_/id/39161682/mlb-competition-committee-tinkering-pitch-runners-lanes) ·
[MLB glossary — Pitch Timer](https://www.mlb.com/glossary/rules/pitch-timer) ·
[MLBPA — Pace of Game Regulations (PDF)](https://www.mlbplayers.com/_files/ugd/4d23dc_e2a4a105547247f598e81bc67dbea27b.pdf) ·
[NFL Ops glossary — Two-minute warning](https://operations.nfl.com/learn-the-game/nfl-basics/terms-glossary/glossary-terms-list/two-minute-warning) ·
[Fox Sports — 60-second review clock](https://www.foxsports.com/stories/nfl/nfl-referees-can-defy-laws-of-time) ·
[Buccaneers.com — 2019 replay rule updates](https://www.buccaneers.com/news/nfl-updates-instant-replay-rules-17180578) ·
[SI — Goodell on commercial breaks (2017)](https://www.si.com/nfl/2017/03/22/roger-goodell-commercial-breaks-pace-play-changes) ·
[Awful Announcing — CBS keeps fewer ad breaks in 2018](https://awfulannouncing.com/cbs/cbs-says-fewer-ad-breaks-nfl-games-will-stay-2018-season.html) ·
[theScore — NFL kills double-up commercials](https://www.thescore.com/nfl/news/1261225) ·
[NCAA — 2020-21 D-I men's media timeout format (PDF)](https://s3.amazonaws.com/ncaaorg/championships/sports/basketball/rules/men/2020-21PRMBB_D1RSMediaTimeoutFormat.pdf) ·
[OVC — second-half media timeout experiment](https://ovcsports.com/news/2023/12/4/mens-basketball-teams-to-use-experimental-rule-for-second-half-media-timeouts-in-conference-play.aspx) ·
[NCAA — 2021-23 women's media timeout guidelines (PDF)](https://ncaaorg.s3.amazonaws.com/championships/sports/basketball/rules/women/2021-23PRWBB_MediaTimeoutGuidelines.pdf) ·
[NCAA — women's media timeout format approved (2015)](https://www.ncaa.org/news/2015/6/25/media-timeout-format-approved-in-women-s-basketball.aspx) ·
[NCAA — football technology rules approved, two-minute timeout (2024)](https://www.ncaa.org/news/2024/4/19/media-center-technology-rules-approved-in-football.aspx) ·
[NCAA Football Rules Committee Feb 2024 report (PDF)](https://ncaaorg.s3.amazonaws.com/championships/sports/football/rules/Feb2024PRMFB_AnnualMeetingReport.pdf) ·
[Fox Sports — college football's 2-minute timeout](https://www.foxsports.com/stories/college-football/college-footballs-2-minute-timeout).

Streaming latency: [TV Technology — Super Bowl LX latency study](https://www.tvtechnology.com/platform/study-ota-broadcasts-offered-lowest-latency-of-super-bowl-lx-feeds) ·
[Phenix — Super Bowl LIX latency](https://blog.phenixrts.com/once-again-super-bowl-latency-numbers-are-staggering) ·
[Streaming Media Blog — Prime Video wild-card stream](https://www.streamingmediablog.com/2025/01/prime-afc-wildcard.html) ·
[RFC 8216 — HTTP Live Streaming](https://www.rfc-editor.org/rfc/rfc8216.txt) ·
[WNYC/WSJ — "11 minutes" of action](https://www.wnycstudios.org/story/game-time/).

Modeling: [Singer & Willett 1993 — discrete-time survival](https://journals.sagepub.com/doi/10.3102/10769986018002155) ·
[Ishwaran et al. 2008 — Random survival forests](https://projecteuclid.org/euclid.aoas/1223908043) ·
[Barnwal, Cho & Hocking 2022 — AFT in XGBoost](https://arxiv.org/abs/2006.04920) ·
[Pölsterl 2020 — scikit-survival](https://www.jmlr.org/papers/v21/20-729.html) ·
[Candès, Lei & Ren 2023 — Conformalized survival analysis](https://arxiv.org/abs/2103.09763) ·
[Romano, Patterson & Candès 2019 — Conformalized quantile regression](https://proceedings.neurips.cc/paper_files/paper/2019/file/5103c3584b063c431bd1268e9b5e76fb-Paper.pdf) ·
[Gibbs & Candès 2021 — Adaptive conformal inference](https://arxiv.org/abs/2106.00170) ·
[Guo et al. 2017 — On calibration of modern neural networks](https://proceedings.mlr.press/v70/guo17a.html) ·
[Adams & MacKay 2007 — Bayesian online changepoint detection](https://arxiv.org/abs/0710.3742) ·
[Truong, Oudre & Vayatis 2020 — Selective review of change point detection (`ruptures`)](https://arxiv.org/abs/1801.00718) ·
[Welch & Bishop — An Introduction to the Kalman Filter (UNC TR 95-041)](https://www.cs.unc.edu/~welch/kalman/kalmanIntro.html) ·
[Chow, Robbins & Siegmund 1971 — Great Expectations: The Theory of Optimal Stopping](https://en.wikipedia.org/wiki/David_Siegmund) ·
[Ferer 2020 — NHL timeouts via survival analysis (Hockey Graphs)](https://hockey-graphs.com/2020/05/28/quantifying-the-value-of-an-nhl-timeout-using-survival-analysis-part-1/) ·
[Lienhart, Kuhmünch & Effelsberg 1997 — detection of TV commercials (cited via Springer)](https://link.springer.com/article/10.1023/A:1019996817159) ·
[Zhao et al. 2012 — logo matching for replay detection (ICASSP)](https://unpaywall.org/10.1109%2FICASSP.2012.6288154).
Classics cited without a fresh lookup: Cox 1972 (JRSS B 34:187–220), Page 1954 (Biometrika 41:100–115), Kalman 1960, Brier 1950.
