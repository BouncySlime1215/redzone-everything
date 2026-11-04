# 05 — Evaluation, anti-overfitting, and the self-improvement loop

Research note for RedZone Everything, Nov 3 2026. Read-only review of the eval and gate code on
`origin/claude/distill-teacher-student` (agent/distill, agent/kaggle, scripts/eval*, scripts/release_gate.py,
redzone/lab/night, .github/workflows/ci.yml) and the BRAIN workflow on `origin/claude/platform-auto-fix`
(.github/workflows/auto-fix.yml, scripts/autofix_guard.py). Line numbers refer to those branches.

The one-sentence verdict: the repo already has the *shape* Nick asked for (time splits by whole night,
a ≥25 % locked set, dev-test per round, hindsight gates, an e2e night with faults, a guarded auto-fix),
but every gate is a pooled point estimate over a handful of nights, so it flickers (NBA "50 % (10/20)"),
passes things that would not survive a confidence interval (NHL 133/136 = 97.8 % has a 95 % lower bound
of 93.7 %), and the reasoner gate asks for 95 % exact agreement with a label that depends on the future.
The fixes below are mostly statistics and plumbing, not new models.

---

## 1. Top 10 recommendations (in order of impact)

1. **Make the night the unit of evidence.** Every rate gate (entries at a dead ball, leaves in dead time,
   reels in dead time, unsafe leaves, big plays seen) gets a 95 % interval from a cluster bootstrap over
   nights, and PASS means the interval's bound clears the target, not the point estimate. A gate with
   fewer than 8 nights or fewer than 60 decisions in its slice is PENDING, never PASS or FAIL. This
   removes the NBA 10/20 flicker and the false comfort of 97.8 % on 136 entries. (§2.2)
2. **Grow the locked set to what the arithmetic needs, and rotate it.** Keep "≥25 % of nights by count"
   but add "≥10 nights per league and ≥60 decisions per gate slice". Every night after the pin date is
   locked by construction (`split.plan`: `n > HELD_OUT_TO → locked`), so the fresh nights of each week
   are the supply. After a locked set has judged 3 releases, retire it into train and lock the newest
   nights instead (the reusable-holdout problem is real: Dwork et al. 2015; Blum & Hardt 2015). (§2.5)
3. **Split the reasoner gate into what is checkable and what is a forecast.** Rule conformance
   (garbage → none, crunch → must_see, your team late → must_see, NFL drive → must_see, NHL PP → ≥ watch)
   is deterministic from the brief and must be ≥99 %. The `y`-driven part (a big play within 2–3 min) is
   irreducibly uncertain; grade it by calibration (Brier, ECE) and lift over the head, and by the
   downstream scorecard with the reasoner ON vs OFF (`scripts/eval_reasoner.py` already cross-fits by
   night). Set the abstain threshold for "unsafe leaves ≤ 0.5 %" with conformal risk control on valid
   nights so the bound is a guarantee, not a hope. (§2.4)
4. **Make the realistic stress profile the default for every gate run.** `scripts/eval.py` and the night
   lab grade with a constant 8 s feed lag and a constant 45/40 s stream delay; the knobs that model the
   real world (`--lag-jitter 3..25`, `--late-plays 0.15`, `--no-wallclock 0.5`, `--drop 0.1`,
   `--channel-spread 8`, `--resync`) are all off. Flip them on for gates, keep the clean run as a
   diagnostic, and replace the constants with distributions measured from the pbp-tap and switch traces.
   (§5)
5. **One `scripts/leak_check.py` in CI, plus two concrete leaks to fix now:** the e2e night replays two
   locked-set games as fixtures (NHL 401892626 and NBA 401909906 are in `agent/kaggle/assets/locked_v1.json`),
   and `espn.parse_summary` fills `g.leaders` from the *final* summary's `leaders` block even when `upto`
   is set (lines 520–522), so a replayed state carries end-of-game leaders. Also stamp every model file
   with `trained_through` and assert it precedes the league's `test_start`; the brain-feature leak
   (`build_moments_kaggle.py` lines 57–62) happened because nothing enforced that. (§3)
6. **Log one decision record per decision, then label it at 5 AM.** The `decisions` table holds
   (ts, action, game, kind, state, gate, reason); the record needs the model version, an inputs hash,
   every candidate with its score and confidence, the abstain flag, which rule overrode the model, and
   what was on screen (target, `behind`, OCR clock moving?, luma). The nightly archive then labels it
   with hindsight (leave in dead time? big play followed? landed mid-play?). This is the substrate for
   feedback, active learning, shadow scoring and the BRAIN's evidence. (§4.1)
7. **Two buttons on the phone, and treat overrides as weak labels.** 👍/👎 "that switch" writes to the
   existing `feedback` table with the decision id; `/api/override` and the Skip tile become implicit
   labels with a confound filter (ignore overrides within 60 s of a fault or stale feed). Explicit votes
   weigh 2× like gold; implicit ones 0.5×. (§4.2–4.3)
8. **Shadow mode with a written promotion rule.** A challenger decides on the same `WorldState` and only
   logs. Each morning the night lab replays the real night with the challenger acting, giving paired
   per-night scorecards. Promote when ≥7 paired nights are all non-inferior on every hard gate and the
   challenger is better on one primary metric with an interval excluding zero (sign test p = 0.008 at
   7/7; 10 of 12 gives p = 0.019). Auto-rollback: a running SPRT on unsafe leaves and mid-play entries
   flips the flag OFF the moment the reject boundary is crossed. (§4.6)
9. **Weekly Kaggle round, nightly CPU drift report, active-learning teacher queue.** 30 GPU-h/week buys
   about 6 h of 32B teacher (~18k answers at Round 1's rate), 6 h of students, 2 h of grading. The
   teacher queue is picked by uncertainty ∪ student≠head ∪ student≠rules ∪ Nick's overrides ∪
   hindsight-wrong, de-duplicated per game and 5-minute window. Drift: PSI/KS on features, ADWIN on the
   per-night unsafe-leave rate, a classifier two-sample test on the brief features (Rabanser et al. 2019).
   (§4.4–4.5)
10. **The BRAIN sees failures on dev-test, never on locked nights, and every fix ships with a replay
    regression and a golden-trace diff.** Extend the existing auto-fix trigger from "watchdog issue" to
    "eval-regression issue" built from the CI comment (gate rows, PR-vs-base deltas with intervals, the
    exact `--slates` lines and trace ids). Keep the guard's PROTECTED list, add `scripts/download*`, run
    the locked gate only in a GitHub environment that needs Nick's approval, and require two keys to
    flip a flag: dev-test green and the next week's fresh nights green. Stop-loss: two failed BRAIN PRs
    on the same gate in a week escalates to Nick and stops filing. (§6)

---

## 2. The gate suite

### 2.1 Ground truth: what the repo grades today

| Where | What it measures | Unit | Statistic |
|---|---|---|---|
| `scripts/eval.py` (NFL, 16 slates), `scripts/eval_sports.py` (NBA/NHL nights) | mid_play_cuts, dead_air_pct, big_seen/live, crunch_live, your_team_*, switches/h, blackout, drive_live, behind_live, big_missed_on_replay, plus `cutgrade.CutCard` (entries_mid_play, entries_in_break, entry_wait_s, plays_missed_entry, leaves_in_dead_pct, rewinds) | pooled plays/switches over the slice | point estimate |
| `scripts/release_gate.py` | PASS/FAIL/PENDING per card: cuts = 0, entries at dead ball ≥95 %, entries mid-play ≤5 % and "never above today" (`docs/gate_baseline.json`), big seen ≥95 %, your-team big = 100 %, your-team crunch = 100 % (0/0 → PENDING), crunch ≥95 % or feasible ceiling (ceiling not built → PENDING); six system gates hard-wired PENDING | pooled over the held-out slice (NFL 2 slates, NBA/NHL 4 nights, demo day) | point estimate; count-vs-count for "never above today" |
| `agent/distill/evaluate.py` + `agent/kaggle/grade.py` | reasoner: usable_json 100 %, lock_acc ≥95 % vs gold, unsafe_leave ≤0.5 %; must_see precision/recall/lift, leave_allowed, over_cautious, eta_mae; gap train/valid/test; slices (crunch, early, late, garbage, your team); calibrated abstain (cut picked on valid, 5-point grid, ≥20 rows); jump: reels_in_dead_time, big_missed_on_reel, agree, jump_recall; watchdog: accuracy, by kind | pooled rows (moments) | point estimate |
| `redzone/lab/night/run.py` (e2e night) | no_crash, no_stuck_state, safe_mode_clears, two_stick_recovers, zero_midplay_cuts, still_watching_cleared, no_center_in_player, wedged_stick_reconnected, feed_outage_handled, watchdog_fixes_what_it_should, tv_shows_games (≥75 % of live time) | one night, 5 games, seed 7 | boolean |
| `.github/workflows/ci.yml` | smoke scorecard PR vs base on pinned `eval-data-v1`; held-out slice + release gate (fails only PRs into `codex/resume-redzone`); kaggle-smoke e2e on CPU; e2e night when present | — | — |

What is missing relative to Nick's list: intervals over nights, a minimum-evidence rule, a reel line in
the scorecards (PENDING today), a reasoner line in the release gate (PENDING: "no gold labels yet"), the
feasible ceiling for crunch, and any sequential or non-inferiority logic.

### 2.2 Statistics: intervals over nights, not plays

**Why nights.** Consecutive moments in one game share their label window (`HORIZON_S` is 120–300 s and
plays land every 30–40 s, so 4–8 rows share each big play), and one night's games share the stream
delay, the slate, and the decider's state. 5,542 test rows are not 5,542 trials. Block/cluster
structure like this is exactly where random-split intervals understate error (Roberts et al. 2017;
Bergmeir & Benítez 2012; Cerqueira et al. 2020). The repo splits by night, which is right; it must also
*estimate uncertainty* by night.

**The estimator.** For a rate gate on decisions (entries at a dead ball, leaves in dead time, reels in
dead time, unsafe leaves): resample nights with replacement (2,000 draws), recompute the pooled rate
each draw, report the 2.5/97.5 percentiles. With fewer than 8 nights the cluster bootstrap is unstable;
fall back to a Wilson interval on pooled decisions (Brown, Cai & DasGupta 2001 recommend Wilson over
Wald for exactly these small-n, near-boundary cases) inflated by a design effect 1 + (m̄ − 1)·ICC, and
mark the gate PENDING unless the bound still clears.

**What the current numbers look like with an interval.** Wilson 95 % lower bounds:

| Gate row today | point | lower bound | verdict with intervals |
|---|---|---|---|
| NBA entries at a dead ball 10/20 | 50.0 % | 29.9 % | FAIL (and too few decisions to say more) |
| NFL entries at a dead ball 93/93 | 100 % | 96.0 % | PASS |
| NHL entries at a dead ball 133/136 | 97.8 % | 93.7 % | PENDING: cannot claim ≥95 % yet |
| demo entries at a dead ball 197/201 | 98.0 % | 95.0 % | borderline PASS |

**How many decisions, how many nights.** For a 95 % one-sided claim that a rate is ≥95 %:

| failures observed | decisions needed |
|---|---|
| 0 | 59 |
| 1 | 93 |
| 2 | 124 |
| 3 | 153 |

For "unsafe leaves ≤0.5 %": 0 events in 598 leaves, 1 in 947, 2 in 1,258. For a hard zero (mid-play
cuts), the honest statement is the rule of three (Hanley & Lippman-Hand 1983): 0 cuts in N cuts means
the true rate is ≤3/N with 95 % confidence; the gate report should print that bound next to the zero
("0 of 1,240 → ≤0.24 %"). To claim ≤1 % you need 299 cuts with none mid-play.

Nights: if a league's per-night rate averages m with night-to-night sd s, the one-sided 95 % bound of
the mean clears 95 % once n ≥ (1.645·s/(m − 0.95))²:

| per-night mean | sd | nights needed |
|---|---|---|
| 99 % | 2 pt | 1 |
| 98 % | 3 pt | 3 |
| 98 % | 5 pt | 8 |
| 97 % | 4 pt | 11 |
| 96 % | 4 pt | 44 |

So the minimum is not a constant: a system comfortably above the bar passes on 3–8 nights, a system
at 96 % needs a season. Today's NBA/NHL locked set is 4 nights each; `locked_v1.json` has 19 NFL nights.
The rule to adopt: **PASS needs ≥8 nights and ≥60 decisions in the slice, and the interval bound must
clear; otherwise PENDING.** Fresh nights (every night after the pin is locked by `split.plan`) supply
the count within two to three weeks for NBA/NHL.

**Non-inferiority vs today.** "Entries mid-play never above today" compares raw counts (3 vs 2 on
different n). Replace it with a difference of proportions and a margin: FAIL only when the 95 % lower
bound of (PR rate − baseline rate) exceeds +1 percentage point; otherwise print "not worse (interval)".
Newcombe's hybrid-score interval is the standard choice for the difference.

**Multiplicity.** With ~30 gate rows, a release with every row exactly at its threshold would show at
least one false FAIL 78 % of the time. Do **not** correct for this: a correction makes shipping easier,
and a false FAIL costs a day while a false PASS costs Nick a night. The remedy for flicker is more
nights (recommendation 2), plus the headline number below so the report has one verdict rather than 30.

**One headline number.** Define a *good decision* as: the leave was in dead time, the entry was at a
dead ball, no live big play on a must-see game was missed because of it, and any reel fit its break.
The round's headline is the pooled good-decision rate over locked nights with its night-bootstrap
interval; "95 % good minimum" is the lower bound ≥ 95 %. The hard gates (0 mid-play cuts, your-team
100 %) stay separate and binary, each with its rule-of-three bound.

**Sequential testing.** Two uses.

*Early acceptance within a round (SPRT, Wald 1945).* Testing H0: good-rate = 0.95 against H1: 0.90 with
α = β = 0.05: each good decision adds ln(0.90/0.95) = −0.054 to the log-likelihood ratio, each bad one
adds ln(0.10/0.05) = +0.693; accept "good" at −2.94, reject at +2.94. That is 55 consecutive good
decisions to accept, and each failure costs 12.8 further successes. For unsafe leaves (0.5 % vs 2 %):
194 safe leaves to accept, and one unsafe leave costs 91. The SPRT is the right tool for shadow-mode
promotion (evidence accumulates night by night) and for the live rollback trigger (§4.6). For a
continuously watched live rate, use always-valid p-values (Johari et al. 2017/2022) so peeking does not
inflate the error.

*Across rounds (the locked set wears out).* Every time the release gate runs on the same locked nights
and someone changes code in response, the locked set leaks a little; after enough rounds it is a
training set (Dwork et al. 2015). Mitigations in order of cheapness: (1) run the locked gate only on
release PRs (already the rule); (2) print locked metrics rounded to their interval half-width, so a
0.3-point "gain" is invisible (the Ladder idea, Blum & Hardt 2015); (3) retire a locked set after 3
releases into train and lock the newest nights (recommendation 2).

### 2.3 Per-slice gates and the 0/0 rule

Slices Nick cares about: league; your team (WSH ×4, UNC); crunch vs not; early vs late; decided
(garbage); must-see games; NHL power play / goals / OT; NFL red zone and two-minute drives; weekday vs
Sunday slate; one-stick degraded mode. `grade.py` already slices the reasoner by crunch/early/late/
garbage/your team; the scorecards do not slice at all except per league.

Rule: a slice with no decisions is PENDING (the `your_team_crunch_live_pct == 0.0` TODO in
`release_gate.py` line 95 generalizes: emit numerators and denominators for every rate so 0/0 cannot
print as 0 %). A slice with 1–59 decisions prints its interval and is PENDING. Your-team slices will be
thin for weeks; that is fine, the gate says so instead of passing.

### 2.4 The reasoner and reel gates: measure what is measurable

`evaluate.GATE` wants `lock_acc ≥ 95` against `gold_target`, and gold is built from the export's
hindsight target: `y` (a scorecard big or crunch play within the horizon) raises lock_in and forces
`safe_to_leave = "no"` (`gold.py` lines 55–59). `y` is the future. No blind model, including the 32B
teacher, can match a future-dependent label 95 % of the time unless big plays are nearly predictable,
and the Round 1 numbers will show that. An unreachable gate either blocks forever or gets gamed.

Decompose it:

1. **Rule conformance (deterministic, from the brief alone):** garbage → none; crunch → must_see; your
   team late → must_see; NFL red zone/two-minute drive that resumes at the snap → must_see; NHL PP →
   ≥ watch; must_see → stay_s ≥ 60 and safe_to_leave = no. Gate: ≥99 % on every rule, every league, and
   100 % valid JSON. This is what "Nick's rules applied exactly" can be held to.
2. **Forecast quality (the `y` part):** Brier score and ECE of the implied P(big soon) against hindsight
   (Gneiting & Raftery 2007 for why a proper score; Guo et al. 2017 for ECE and the fact that model
   confidences are usually miscalibrated and can be fixed post hoc with temperature scaling), must_see
   lift over the head baseline, and `unsafe_leave_pct` as a *risk* to be controlled, not an accuracy.
3. **Downstream:** the scorecard with the reasoner ON vs OFF on the same nights, cross-fitted
   (`scripts/eval_reasoner.py` with `FoldHead`): non-inferior on every hard gate, better on big_live_pct
   or leaves_in_dead_pct with an interval excluding zero. This is the gate that decides the flag.

**Calibrated abstain with a guarantee.** `_abstain` in `grade.py` picks the lowest confidence cut on
valid rows whose answered subset meets the gates, then applies it to test. Make the bound a guarantee:
conformal risk control (Angelopoulos, Bates, Fisch, Lei & Schuster, ICLR 2024) picks a threshold λ on a
calibration set so that E[loss] ≤ α for any monotone loss, with a finite-sample bound tight to O(1/n);
with loss = 1[said safe, big play followed] and α = 0.005 it gives the abstain cut directly. When several
constraints must hold at once (unsafe ≤ 0.5 % and over-cautious ≤ x %), Learn-then-Test (Angelopoulos,
Bates, Candès, Jordan & Lei 2021) treats the threshold search as multiple testing with family-wise error
control. Both need the calibration nights to be exchangeable with the test nights, which holds within a
season and not across a rule change; adaptive conformal inference (Gibbs & Candès 2021) updates the
level online as nights arrive and keeps long-run coverage without that assumption. Report the
risk–coverage curve (Geifman & El-Yaniv 2017) each round so the trade is visible: at 0.5 % risk, how
much does the student answer? Require coverage ≥ 70 % or the "student" is just the rules.

**Reel gate.** `jump.grade` already defines the lines (reels_in_dead_time_pct, big_missed_on_reel,
agree_pct, jump_recall_pct). Move them into the scorecards (the release gate's "reel scorecard line not
built yet") by running the decider with the reel enabled in the backtest and grading each jump against
the real break length on the archive, exactly as `jump.label` does with hindsight. Target: reels in dead
time ≥ 95 % (interval), big_missed_on_reel = 0 with its rule-of-three bound.

### 2.5 How a round is judged

| When | Data | Who sees it | Purpose |
|---|---|---|---|
| every Kaggle round | dev-test nights (`split` = test) | everyone, the BRAIN | fit nothing, compare students, decide what to try |
| every round | train vs valid vs dev-test gap (`grade.py` `gap`) | everyone | overfit check: gap over a few points = overfit |
| every round | fresh nights since the last round (locked by construction) | report only, nobody tunes on them | the honest trend line |
| release PR only | the locked set (+ fresh nights) | the gate job under an approval environment | the verdict |
| after 3 releases | locked nights retire to train; newest nights lock | — | stops holdout reuse |

The "two-key" rule for any flag: dev-test green at the round, and the next week's fresh nights green
before the flag flips ON. Anything that misses ships OFF behind its flag, as the architecture already
says.

---

## 3. Leakage traps specific to sports time series

For each: what it is, where it bites this repo, and the CI check. The checks together are
`scripts/leak_check.py`, run in the `test` job and in the Kaggle notebook before packing.

### 3.1 Fields written after the fact (ESPN summaries)

A replay is built from the *final* summary JSON (`Replay.load`), then `parse_summary(upto=i)` rebuilds
the state at play i. Anything read from outside `plays[:i+1]` is end-of-game information.

| Field | Status in the code | Risk |
|---|---|---|
| header final score | `NightFeed` zeroes it (`pre.home.score = 0`); `parse_summary` takes scores from the play itself | handled |
| team records (`wins/losses/ties`) | `Replay.state_at` nulls them ("Final records would leak the future") | handled |
| `leaders` (game leaders) | `parse_summary` lines 520–522 read `data["leaders"]` regardless of `upto` → `g.leaders` is the final leader board at every replayed moment | **leak**: `brief.py` does not read it (grep), but the Scorer, the phone cards and any future feature may. Null it in `state_at` like records. |
| `winprobability[].homeWinPercentage` | taken only for plays ≤ cut (`wp_by_play`, lines 510–515) | fine per play, two caveats: the live array can lag the latest play while the replay always has it, and ESPN can revise the curve. Measure with the pbp-tap: diff live polls vs the final summary for the same game and report the per-field revision rate. |
| `drives.previous[].result`, `drives.current` | `flatten_plays` only takes the plays and sorts by `sequenceNumber`; drive-level results are not read | fine today; add a test that no feature reads `drives[*].result`/`displayResult` |
| `end` state of a play (`_nfl_situation`) | the play's own end state | legitimate once the play has *completed and arrived*; see timing below |
| `pickcenter` spread/odds | pre-game | fine |
| `status.type.state == post` | used only when `upto ≥ len(plays)−1` | fine |
| `boxscore`, `gameInfo`, `predictor` | not read for features | keep it that way (test) |

**The timing leak.** A play's `wallclock` is when it started; its text, `scoringPlay`, yardage and end
state exist only after it ends and the stat crew logs it. The backtest and the night lab deliver the
complete play at `wallclock + 8 s` (`FEED_LAG_S = 8.0`, `feed_lag=8.0` default), so the app "knows" the
outcome of a 7-second play about one second after the whistle, every time. Real lag is a distribution
with a tail (`--lag-jitter 3..25` models it, off by default; `--late-plays` and `--no-wallclock 0.5` model
ESPN's out-of-order and clockless plays, off by default). Fix: log `(received_at, play_id, wallclock)`
per poll in the live service (the pbp-tap thread), fit the empirical lag per league and source, and make
that the default in `_feed` and `NightFeed`. Until measured, gate runs use the stress profile (§5).

**CI check:** a test that builds `state_at(i)` for fixture games and asserts, field by field, that the
GameState equals the one built from a summary truncated to plays ≤ i with `leaders`, `boxscore`,
`winprobability[i+1:]`, `drives.current`, header scores and records removed. Any field that differs is a
leak by definition.

### 3.2 The same game in train and test

`split.check` asserts a game id maps to one split and splits are ordered in time; `pack_data.py` drops
locked rows and re-checks; `build_moments_kaggle.py` asserts per game that locked ids are never
downloaded; jump rows are re-split by the build's plan and locked ones dropped. Good. Gaps:

- **Fixtures.** The e2e night (`redzone/lab/night/run.py` `GAMES`) replays NHL 401892626 and NBA 401909906,
  both in `locked_v1.json`. The night lab trains nothing, but it is a CI gate that the BRAIN and every
  thread iterate against, so decider thresholds get tuned on two locked games. Swap them for train-night
  games (or drop those two games from the locked set in `eval-data-v2` and say so).
- **Cross-artifact.** Teacher answers are keyed by `id = league:gid:i`; gold rows carry `split`; the
  Kaggle dataset is versioned. Check: the set of `(league, gid)` in any training artifact (student
  train/valid chats, teacher grounded answers, gold used for up-weighting, `p10_by_type` samples) is
  disjoint from `(league, gid)` in dev-test and locked, computed over the *files*, not the plan.
- **Night identity.** Nights are ET event dates (`download_recent.event_date`) and `nights_of` lets the
  first scoreboard win, so a West-coast game ending after midnight ET stays on its night. NFL slates
  (Thu/Sun/Mon of one week) are grouped only in `split.plan`, not in `BIG_CUTS`; a Monday game can land
  on the other side of a cutoff from its Sunday siblings. Harmless for independence, but keep the slate
  rule in both.

### 3.3 Teacher prompts that saw outcomes

`teacher.reasoner_requests` asks the grounded question ("WHAT ACTUALLY HAPPENED NEXT (hidden from it)")
on train nights only in the pack (`grounded_splits=("train",)`, asserted at `pack_data.py` lines 183–184),
and the blind question on test; `student.build` swaps only the words and keeps the graded fields, with a
test that rejects leaked hindsight. The remaining leak is subtle: the student learns to write
"forecasts" composed by a model that knew the answer, so its text is over-specific and over-confident,
and its `next`/ETA field is trained on hindsight-shaped prose.

Checks:
- **Text-leak scanner** on every grounded answer before it enters training: reject if it contains a
  token of `next_text` (the actual next big play), a player surname that appears in the next 3 minutes
  of plays but not in the brief, or a future score. Count and report rejections per round.
- **Hindsight gap.** `blind_train_every` exists; use it: on the same train moments, compare the
  teacher's blind answers to its grounded ones. If grounded "forecasts" are far more accurate than blind,
  the words carry the answer; shrink the grounded share or drop the free-text fields from training.
- **Never grounded on valid.** `reasoner_requests` defaults to `grounded_splits=("train","valid")`; the
  pack overrides it. Make the default `("train",)` so a new caller cannot regress it.

### 3.4 Shared seasons

`BIG_CUTS` puts NBA/NHL valid, test and locked in the same 2025–26 season and NFL test/locked in 2025;
the current (2026–27) season is entirely locked. Forward chaining means a season is always shared between
train and the near future in production, so that is not a flaw; the trap is **team identity**: a model
that learns "this roster scores late" from valid/test-season games is learning the test set's season.
Rules: no team-id, player-id or venue features in the head or the student (the brief's `COMMON`
features are state features; keep it so, with a test), and report a **season-disjoint slice** every
round (nights from a season with zero train nights; this autumn, that is every fresh night).

### 3.5 Model artifacts trained on overlapping windows

The brain leak: shipped brain models were trained on seasons overlapping dev-test and locked, so
`build_moments_kaggle.py` strips `brain_key/brain_break/brain_hot` from features, briefs and targets
(`NO_BRAIN`), and the live brief must drop its "brain:" line before the student ships (`brief.py` line
210 still writes it). Make this structural: every `.npz` under `redzone/reasoner/models`, `brain_models`
and the watchdog adapter carries `trained_through: YYYYMMDD` and `leagues` in its meta; `leak_check.py`
fails if `trained_through ≥ BIG_CUTS[league].test_start` for anything the eval path loads, and the
release gate marks the reasoner/brain PENDING if the stamp is missing.

### 3.6 Thresholds and config fitted on the wrong nights

- `evaluate.fit_thresholds` fits must_see/watch cuts on valid, scores test. Good.
- `Head.cuts` reads `watch`/`must_see` from the model meta, "picked on held-out nights" (`head.py`
  docstring). Which nights? It must be valid, never test or locked; stamp the nights into the meta and
  check them.
- `jump.p10_by_type` uses train rows only. Good.
- `data/eval_config.yaml` pins switch costs measured on live nights, not fitted to the archive. Fine.
- `redzone/lab/optimize.py` (decider auto-tune) must read nights < `valid_start` only; assert the window
  in `leak_check.py`.

### 3.7 Random folds inside train

`reasoner_data.fold_of(night, k) = crc32(night) % k` gives random-by-night folds for the head's
out-of-fold odds inside train (`train_head` lines 271–278). That is leak-free for test, but it makes
`head_p` on train rows come from models that saw *later* train nights, while test rows get `head_p` from
a past-only model: a train/test distribution shift in a feature the student reads. Use expanding-window
(forward-chaining) folds inside train so every `head_p` is from a past-only model, as in deployment.
`sklearn.model_selection.TimeSeriesSplit` is the plain version; López de Prado's purged/embargoed folds
are the version to use if any within-night CV is ever added, because label windows overlap.

### 3.8 Selection bias in "gold"

`gold.hardness` adds +1.5 when `y` is true and `pick_hard` ranks by it, so the gold set over-represents
moments where a big play followed. Fine for teaching; not a population estimate. Always print the
population dev-test line next to the "gold hard" line (grade.py does) and never gate on gold-hard alone.

### 3.9 Effective sample size

See §2.2: 4–8 consecutive rows share a label. Any row-level interval is ~2.5× too narrow. Nights are
the cluster; within-game autocorrelation is why.

### 3.10 Gate erosion that is not leakage but acts like it

`run.py` `KNOWN_ERRORS` allowlists a real bug so the night still passes. Each entry needs an issue link
and an expiry date, and `leak_check.py` fails the build when an entry is past its date.

**`scripts/leak_check.py` (the list):**
1. `(league, gid)` disjoint across train artifacts vs dev-test vs locked, over files.
2. No locked `(league, gid)` in `tests/fixtures` used by the e2e night or any gate.
3. `state_at(i)` field-by-field equals the truncated-summary state (§3.1).
4. No feature reads `leaders`, `boxscore`, `drives[*].result`, `winprobability` beyond the cut.
5. Every loaded model file has `trained_through < test_start` for its league.
6. Head `cuts` nights ⊆ valid nights.
7. Grounded teacher answers only for train ids; text-leak scanner rejection count printed.
8. No team/player/venue identity in `brief.features_for`.
9. Auto-tune/optimize window ends before `valid_start`.
10. `KNOWN_ERRORS` entries not expired.

Background: Kaufman, Rosset, Perlich & Stitelman (2012) define leakage and the learn–predict separation
these checks implement; Kapoor & Narayanan (2023) found leakage in 294 papers across 17 fields and
propose "model info sheets", which is what the round report's provenance block should be.

---

## 4. The learning loop from Nick's own nights

### 4.1 Log every decision plus what was on screen

Today: `store.decisions(ts, action, game, kind, state, gate, reason)`, `store.switches`, `traces.jsonl`
(DECIDED → GUARDED → RECHECKED → COMMAND → PICTURE per switch_id), `store.feedback(ts, game, vote, note,
context)`, `store.calibrations` (the "I just saw it" taps), `store.snapshots`. The auto-fix issue attaches
the last 20 decisions and 15 traces.

Add one JSONL line per decision (`data/decisions/<night>.jsonl`), joinable to traces by `switch_id`:

```
decision_id, night, t, model_versions{decider, head, student, brain}, flags{...},
inputs_hash (the WorldState the decider saw), candidates[{game, league, score, head_p, student{lock_in,
safe_to_leave, confidence, abstained}, rule_overrides[]}], chosen, action, reason, gate,
screen{target, behind_s, clock_moving (OCR), luma, media_title}, delay_belief{mu, sigma},
health{feed_age_s, sticks, two_stick}
```

At 5 AM, when the night is archived, label each record from hindsight with the scorecard's own rules
(`cutgrade._dead_at`, `sportsgrade.is_big`): `leave_in_dead`, `entry_at_dead_ball`, `big_followed_on_left_game`,
`reel_fit`, `good_decision`. The screen-side truth (did we land mid-play *on the TV*) needs the real
delay: the taps and the sync filter give it; where neither exists the label is `unknown`, not `good`.

### 4.2 Implicit feedback

| Signal | Source today | Label | Weight | Confound filter |
|---|---|---|---|---|
| manual override to another game | `/api/override`, `engine.channels.set_override` | negative for the current decision, positive for the chosen game at that moment | 0.5 | ignore if a fault/stale feed/`input_unknown` was active in the last 60 s; ignore if the override is to a game not in the candidate set (he wanted something the app cannot know about) |
| Skip / "Leave now" tile | `/api/action` `skip` | "leaving is fine here" | 0.5 | ignore during a prompt or reload |
| stays put when the app wanted to switch (he vetoed via settings or kept overriding back) | decisions vs overrides | stay preference for that game/situation | 0.3 | require ≥2 consecutive vetoes |
| "I just saw it" taps | `store.calibrations` | delay label (already used) | — | — |
| phone opened within 30 s of a switch | web server access log | "something looked wrong" (weak) | 0.1 | none; it is only a sampling hint for the teacher queue |

Weak labels from several noisy sources combine best with explicit weights and a denoising step rather
than as truth; Snorkel-style weak supervision (Ratner et al. 2017) is the reference pattern, and the
simple version (weights above, conflicts resolved by the explicit vote) is enough here.

### 4.3 Explicit thumbs up/down on the phone page

One row of two tiles on the Now card after each switch, for 20 s: 👍 "good switch" / 👎 "bad switch", a
second tap on 👎 opens three reasons (mid-play, boring, missed something). Writes `store.feedback(game,
vote, note, context={decision_id, switch_id})`. Weight 2× like gold. Expect 5–15 votes a night; that is
enough to anchor the active-learning queue and the weekly "what Nick thought" line in the report, not
enough to train on alone.

### 4.4 Active learning: send the teacher the most uncertain moments

Round 1 answered 22,272 requests in 7 h (~3,200/h on the 32B). A weekly budget of ~6 teacher GPU-hours
is ~18k answers; the queue must be chosen. Score each new moment:

```
priority = 1.0·(1 − student_confidence)
         + 1.0·[student lock_in ≠ head level]        (query-by-committee: Seung, Opper & Sompolinsky 1992)
         + 1.5·[student ≠ deterministic rule]        (a rule violation is a bug, not uncertainty)
         + 2.0·[Nick overrode or voted 👎]
         + 1.5·[hindsight says the decision was bad]
         + 0.5·slice_rarity (your team, NHL PP/OT, NFL drive, crunch)
```

De-duplicate to one moment per game per 5-minute window (consecutive rows are near-copies), cap any
league at 40 % of the queue, and keep 10 % uniformly random so the queue does not collapse onto one
failure mode (Settles 2009 on uncertainty sampling and its known biases). Grounded (hindsight) questions
only for moments on nights that will be train nights in the next round; blind questions for everything
graded.

### 4.5 Nightly build, weekly retrain, drift detection

Kaggle gives 30 GPU-h/week and 12 h sessions, so "nightly retraining" means nightly *data and eval*,
weekly *teacher and students*:

| When | Where | GPU | What |
|---|---|---|---|
| 5 AM daily | Mac | — | archive the night, label decisions (§4.1), upload night pack (polls, decisions, feedback) to the Kaggle dataset |
| 6 AM daily | Kaggle (scheduler if the account has it, else a GitHub Actions cron pushing the notebook with `kaggle kernels push`) | ~20 min T4 | build moments for the new night (dev-test/fresh), score the shipped student and the shadow challenger on it, drift report, append to the trend line |
| weekly (Tue, after MNF) | Kaggle | ~6 h teacher + ~6 h students + 1–2 h grade | teacher on the active-learning queue, retrain students, grade on dev-test, gate report as a PR artifact |
| release | CI | — | locked gate under the approval environment |

**Drift detection.** Three layers, each a line in the daily report:
1. *Inputs:* PSI and KS per brief feature per league vs the training reference; PSI > 0.2 on any
   feature → "retrain this week" flag. A domain classifier (train vs this week's nights) with AUC > 0.6
   is the most sensitive single test and tells you *which* features moved (Rabanser, Günnemann & Lipton
   2019).
2. *Model:* histogram of student confidence and abstain rate; a jump in abstains is drift before any
   label arrives.
3. *Outcomes (labels arrive at 5 AM):* ADWIN (Bifet & Gavaldà 2007) on the per-night unsafe-leave rate
   and good-decision rate, which sizes its own window and bounds false alarms; a detected change raises
   the abstain cut one notch immediately and files an issue.
Known drift sources to tag by season: rule changes (kickoff, timeout counts, review scope), the
All-Star break, playoffs (longer breaks, more reviews), bowl season, March.

### 4.6 Shadow mode and promotion

`flags.reasoner_shadow`: the challenger (new student adapter, new brain, new thresholds) receives the
same `WorldState`, produces a `Decision`, and only logs it with `shadow=true`. The live decision acts.

Scoring is counterfactual but unusually exact here, because the world (every game's plays and wall
clocks) is exogenous to the policy: the next morning the night lab replays the real night with the
challenger as the acting policy and the measured delays, and the scorecard of that replay is paired
with the live night's scorecard (champion). The one thing the replay cannot know is the TV-side
stochasticity, which is why the sim-to-real gap (§5.5) is printed next to it.

Promotion rule (all of):
- ≥7 paired nights (or 10 of 12) where the challenger is non-inferior on every hard gate (0 mid-play
  cuts, your-team 100 %, unsafe leaves) — sign test p = 0.008 at 7/7, p = 0.019 at 10/12; the Wilcoxon
  signed-rank test (Demšar 2006) for the continuous lines (dead air, big live);
- better on one primary metric (good-decision rate or big_live_pct) with a night-bootstrap interval
  excluding zero;
- dev-test gate green at the round and fresh nights green the following week (two keys);
- the flag flips ON for one league first (NHL or NBA, where entries mid-play are the gap), then the rest.

Auto-rollback: a running SPRT per league on unsafe leaves (H0 0.5 % vs H1 2 %) and on entries mid-play
(H0 5 % vs H1 10 %) over the live night; crossing the reject boundary flips the flag OFF, files an
issue with the decision ids, and the phone says so. Breck et al. (2017) list exactly these as production
tests ("model quality validated before serving", "canary/shadow", "rollback"); Sculley et al. (2015) is
the reason to keep the flag and the config in one place rather than scattered.

---

## 5. Simulation: the e2e night as the main offline benchmark

### 5.1 What the night lab does today

`redzone/lab/night`: a virtual clock replaces `time.time` process-wide (hours in minutes), the real
Engine/Decider/Scorer/Controller/Tuners/Supervisor/watchdog run unmodified against a fake house (two
sticks, a TV, YouTube TV and Prime with Nov 2 timings), the archived play-by-play is delivered through
the real merger, seven fault kinds are injected at seeded times (stick drop, wedge, "still watching",
feed outage, flip ignored, API down, clock skew), and eleven boolean checks grade the night, including
`zero_midplay_cuts` from the TV's own record at each channel's true delay. This is the right design;
the realism gaps are all parameters.

### 5.2 Realism gaps and what replaces them

| Today | Gap | Replace with |
|---|---|---|
| `FEED_LAG_S = 8.0` constant | the real feed lag is a distribution with a tail; outcomes are known too early | empirical lag per league from the pbp-tap (`received_at − wallclock`); until then `lag_jitter=(3,25)`, `late_plays=0.15`, `no_wallclock=0.5` (ESPN NBA often has none), `drop=0.1` |
| `DELAY_S = {youtubetv 45, prime 40}` constant, calibrated every 30 min by a perfect tap | per-channel delay varies (22–42 s measured on one app, Nov 2) and drifts within a night; taps have ~2 s error | `channel_spread=8`, clipped 15–75 (already in `backtest.channel_scenario`), tap error N(0, 2), within-night drift; `--resync` on |
| `house.Timing` constants (yttv_tune 4.5, prime_tune 30, home_flip 0.5, tv_settle 1.0, ...) | no tail, no failures; the SmartThings path measured median 1.98 s but gates need p95/p99 | lognormal per command fitted from `traces.jsonl` (COMMAND → PICTURE), a per-command failure probability, a "still watching" rate per hour from logs, Prime reload probability on resume |
| one night, 5 fixed games, seed 7 | one sample; two of the games are locked-set games | generate nights from the archive's scoreboards (every archived night, real schedule), ≥3 seeds each; locked nights only in the release run |
| `reel: enabled False`, `autonomy: False` | the reel is never exercised | enable once the reel scorecard line exists (§2.4) |
| `tv_shows_games ≥ 0.75` | lenient; coverage is not a cut-timing grade | add `cutgrade.grade` on the TV record (entries_mid_play, leaves_in_dead), dead_air_pct, big_live_pct, reel lines, with intervals across nights and seeds |
| 7 fault kinds | missing: one-league ESPN outage, stale scoreboard, clock regression after review (the merger handles it; inject it), duplicate/out-of-order plays, adb latency spikes, SmartThings cloud 5xx, TV off/on, Mac sleep | add as `faults.KINDS`; the platform fault module plugs in via `--faults` |
| `KNOWN_ERRORS` allowlist | silently keeps the night green | issue link + expiry per entry (§3.10) |

Make the **realistic profile the default** for anything called a gate (`scripts/eval.py`,
`eval_sports.py`, the night lab, `ci_scorecard.py`), and keep `--clean` as the diagnostic run. Report
both so a regression that only shows under stress is visible.

### 5.3 Many nights, with intervals

`ci_scorecard.py --full` is NFL 16 slates and NBA/NHL 14 nights; the night lab is one night. Run the
night lab over every archived night with 3 seeds (the virtual clock makes an 8-hour night ~3 minutes of
real time, so 40 nights × 3 seeds is a 6-hour CI job, or 2 hours on 3 runners). Stratify the report:
quiet weeknight, Sunday slate, your-team crunch night, one-stick degraded, Prime-heavy night.

### 5.4 Golden nights (metamorphic tests)

Store the `screen.jsonl` and decision trace of 5 golden nights. A PR's night must reproduce the same
decisions except where the PR intends a change; the CI comment prints the decision diff count and the
first 10 differences. This catches "fixed the NBA entry, broke the NFL leave" without waiting for a
scorecard, and it is the cheapest guard against the BRAIN's unintended edits.

### 5.5 Sim-to-real calibration

Every live night produces the same lines the simulator does (from `traces.jsonl`, taps, decision
records). Print the sim-vs-live gap per metric monthly (switch seconds p50/p95, preload hit rate — 48.7 %
on demo day, entries mid-play, dead air) and refit `Timing` and the lag distributions when a gap exceeds
its interval. A simulator that is not checked against live nights becomes a second training set.

---

## 6. The BRAIN: using eval failures to open fixes safely

### 6.1 What exists (`origin/claude/platform-auto-fix`)

The watchdog files a redacted GitHub issue labeled `auto-fix` for a crash loop or an unknown error
that persists (max 3/day, once per signature per week, kill switches on both sides). `auto-fix.yml`
runs `anthropics/claude-code-action@v1` with the user's OAuth token, `--max-turns 60`, allowed tools
limited to read/edit/pytest/ruff/git-read, disallowed web, push, commit, `gh`, `curl`, `env`; the prompt
treats the issue as data, demands a root-cause fix and a failing-then-passing test, and forbids
touching gates. `scripts/autofix_guard.py` refuses diffs that touch `.github`, CI/gate/eval scripts,
`docs/gate_baseline.json`, `data/`, deps, conftest, secrets; that delete tests, remove asserts, add
skips; that lack a test; or exceed 12 files / 600 lines. Then the e2e night, the full suite and ruff
run, and a **draft** PR opens into `integration/next`. Nothing merges or deploys from the workflow.

That is a sound base. The gap is that it only reacts to *crashes*; eval regressions and gate failures
never reach it, and when they do the risk profile changes: a fix that "makes the gate pass" is the
definition of overfitting to the benchmark.

### 6.2 Rules for eval-driven fixes

1. **Trigger from dev-test, never from locked.** The CI `report` job files an `eval-regression` issue
   when a PR into `integration/next` worsens a gate row on the dev-test slice (PR vs base, interval
   excluding zero) or when the weekly Kaggle gate report shows a slice regression. The issue body holds
   metrics, intervals, the exact repro (`scripts/eval_sports.py nba --nights N --slates` output with its
   `CUT`/`MID-PLAY ENTRY` lines), the decision ids and switch_ids around the failures, and the night's
   `screen.jsonl` excerpt. It never holds hindsight labels for test or locked moments.
2. **The locked gate runs where the BRAIN cannot reach.** Put the release-gate step behind a GitHub
   environment (`release-gate`) with Nick as required reviewer; the auto-fix workflow has no access to
   it, `data/` stays PROTECTED, and `scripts/download*.py`, `scripts/build_eval_data.py`,
   `agent/kaggle/assets/*` join the PROTECTED list so a fix cannot pull or re-split nights.
3. **A fix must carry a replay regression, not only a unit test.** The guard requires a test under
   `tests/` that asserts the specific failure on the specific night ("on 20261031 NHL, the switch at
   21:14:07 is no longer an entry mid-play") using the pinned data, plus the golden-trace diff (§5.4)
   with a cap on unintended decision changes (default ≤ 3 across the golden nights).
4. **Graded with intervals, on the realistic profile.** The CI comment on a BRAIN PR shows PR vs base
   with night-bootstrap intervals; an improvement smaller than the interval half-width is reported as
   "no measurable change", and the PR stays draft.
5. **Two keys before any flag flips.** A BRAIN PR may fix code and tests; flag defaults change only in a
   human release PR after the dev-test round and the following week's fresh nights are green.
6. **Models never arrive via the BRAIN.** Weights come from the Kaggle round with its gate report as a PR
   artifact and `trained_through` stamps checked by `leak_check.py`; the BRAIN edits code only.
7. **Budget and stop-loss.** Keep 3/day. Add: at most 2 PRs per gate row per week; if both fail to
   clear, stop filing on that row and page Nick with the two diffs. A BRAIN PR that is not merged within
   7 days is closed with its branch kept.
8. **Ratchet only upward, by hand.** `docs/gate_baseline.json` moves only in a release PR; the BRAIN
   cannot edit it (already PROTECTED) and the "not worse than today" row uses the non-inferiority test,
   not counts.
9. **Keep the sandbox blind to the web.** `WebFetch/WebSearch/curl` are already disallowed; also set
   `--disallowedTools "Bash(python scripts/download*)"` and run with no network egress if the runner
   allows it, so no fix can fetch ESPN data for nights it should not see.
10. **Prompt hygiene for Goodhart.** The prompt should say what the number means and what it must not
    do: "do not change thresholds in `decider/cuts.py` or `screen.leave_gate` to move the metric; fix
    the mechanism the trace shows". The guard already blocks weakening `leave_gate`; add the cut
    tables and `data/eval_config.yaml` to PROTECTED for BRAIN PRs.

Framed by the ML Test Score rubric (Breck et al. 2017), items 1–10 cover its "model quality validated
before serving", "reproducible training", "canary", and "rollback" rows; the thing the rubric cannot
give you is the discipline that the locked set is never a loop variable.

---

## 7. What to build first (effort in real units)

1. `release_gate.py`: night-bootstrap intervals, Wilson fallback, min-evidence PENDING rule,
   numerators/denominators in every row, rule-of-three bound next to every zero, non-inferiority for
   "never above today". About 4 hours; the scorecards already expose per-slate/per-night output via
   `--slates`.
2. `scripts/leak_check.py` with the 10 assertions, wired into the `test` CI job and the Kaggle
   notebook. About 4 hours, plus 1 hour each to null `g.leaders` in `state_at`, swap the two locked
   fixtures, and add `trained_through` to model meta.
3. Realistic default profile in `eval.py`/`eval_sports.py`/`ci_scorecard.py`/night lab (`--clean` to
   opt out). About 2 hours; then one 1-hour live night of pbp-tap logging per league to fit the lag.
4. Decision records + 5 AM hindsight labelling (`redzone/decider` emit, a labeller in `redzone/lab`).
   About 1 day.
5. Phone 👍/👎 tiles + override-as-label with confound filter. About half a day.
6. Reasoner gate split (rule conformance vs forecast; conformal abstain in `grade.py`; ON/OFF
   non-inferiority via `eval_reasoner.py`). About 1 day.
7. Shadow flag + morning counterfactual replay + promotion/rollback SPRT. About 2 days.
8. Night lab over many nights with seeds and `cutgrade` lines; golden-trace diff in CI. About 2 days.
9. Active-learning queue in `pack_data.py`/`student_data.py`; drift report in the daily notebook.
   About 1 day.
10. `eval-regression` issue filer in the CI `report` job + guard additions. About half a day.

---

## References (verified by web search on Nov 3 2026)

- Kaufman, S., Rosset, S., Perlich, C., Stitelman, O. (2012). *Leakage in Data Mining: Formulation,
  Detection, and Avoidance.* ACM TKDD 6(4), art. 15. doi:10.1145/2382577.2382579. (KDD 2011 version
  without Stitelman.)
- Kapoor, S., Narayanan, A. (2023). *Leakage and the reproducibility crisis in machine-learning-based
  science.* Patterns 4(9):100804. doi:10.1016/j.patter.2023.100804.
- Bergmeir, C., Benítez, J. M. (2012). *On the use of cross-validation for time series predictor
  evaluation.* Information Sciences 191:192–213. doi:10.1016/j.ins.2011.12.028.
- Cerqueira, V., Torgo, L., Mozetič, I. (2020). *Evaluating time series forecasting models: an empirical
  study on performance estimation methods.* Machine Learning 109:1997–2028. doi:10.1007/s10994-020-05910-7.
- Roberts, D. R., et al. (2017). *Cross-validation strategies for data with temporal, spatial,
  hierarchical, or phylogenetic structure.* Ecography 40(8):913–929. doi:10.1111/ecog.02881.
- López de Prado, M. (2018). *Advances in Financial Machine Learning.* Wiley. Ch. 7 (purged k-fold,
  embargo).
- Dwork, C., Feldman, V., Hardt, M., Pitassi, T., Reingold, O., Roth, A. (2015). *The reusable holdout:
  Preserving validity in adaptive data analysis.* Science 349(6248):636–638. doi:10.1126/science.aaa9375.
- Blum, A., Hardt, M. (2015). *The Ladder: A Reliable Leaderboard for Machine Learning Competitions.*
  ICML, PMLR 37:1006–1014.
- Wald, A. (1945). *Sequential Tests of Statistical Hypotheses.* Annals of Mathematical Statistics
  16(2):117–186. doi:10.1214/aoms/1177731118.
- Johari, R., Koomen, P., Pekelis, L., Walsh, D. (2017). *Peeking at A/B Tests: Why it matters, and what
  to do about it.* KDD 2017:1517–1525; (2022) *Always Valid Inference: Continuous Monitoring of A/B
  Tests.* Operations Research 70(3):1806–1821.
- Hanley, J. A., Lippman-Hand, A. (1983). *If nothing goes wrong, is everything all right? Interpreting
  zero numerators.* JAMA 249(13):1743–1745.
- Brown, L. D., Cai, T. T., DasGupta, A. (2001). *Interval Estimation for a Binomial Proportion.*
  Statistical Science 16(2):101–133.
- Demšar, J. (2006). *Statistical Comparisons of Classifiers over Multiple Data Sets.* JMLR 7:1–30.
- Guo, C., Pleiss, G., Sun, Y., Weinberger, K. Q. (2017). *On Calibration of Modern Neural Networks.*
  ICML, PMLR 70:1321–1330.
- Geifman, Y., El-Yaniv, R. (2017). *Selective Classification for Deep Neural Networks.* NeurIPS 2017.
- Angelopoulos, A. N., Bates, S., Fisch, A., Lei, L., Schuster, T. (2024). *Conformal Risk Control.*
  ICLR 2024 (arXiv:2208.02814).
- Angelopoulos, A. N., Bates, S., Candès, E. J., Jordan, M. I., Lei, L. (2021). *Learn then Test:
  Calibrating Predictive Algorithms to Achieve Risk Control.* arXiv:2110.01052.
- Gibbs, I., Candès, E. (2021). *Adaptive Conformal Inference Under Distribution Shift.* NeurIPS 2021.
- Bifet, A., Gavaldà, R. (2007). *Learning from Time-Changing Data with Adaptive Windowing.* SDM 2007:
  443–448. doi:10.1137/1.9781611972771.42.
- Rabanser, S., Günnemann, S., Lipton, Z. C. (2019). *Failing Loudly: An Empirical Study of Methods for
  Detecting Dataset Shift.* NeurIPS 2019.
- Settles, B. (2009). *Active Learning Literature Survey.* UW–Madison CS Technical Report 1648.
- Seung, H. S., Opper, M., Sompolinsky, H. (1992). *Query by Committee.* COLT 1992:287–294.
  doi:10.1145/130385.130417.
- Dudík, M., Langford, J., Li, L. (2011). *Doubly Robust Policy Evaluation and Learning.* ICML 2011:
  1097–1104. (Off-policy evaluation from logs; the replay simulator makes it mostly unnecessary here.)
- Breck, E., Cai, S., Nielsen, E., Salib, M., Sculley, D. (2017). *The ML Test Score: A Rubric for ML
  Production Readiness and Technical Debt Reduction.* IEEE Big Data 2017. (Google's publication page
  lists only three of the five authors.)
- Sculley, D., et al. (2015). *Hidden Technical Debt in Machine Learning Systems.* NeurIPS 2015:2503–2511.
- Not separately verified today but standard: Gneiting & Raftery (2007) JASA on proper scoring rules;
  Ratner et al. (2017) VLDB on Snorkel; Newcombe (1998) Statistics in Medicine on difference-of-
  proportions intervals; `sklearn.model_selection.TimeSeriesSplit`.
