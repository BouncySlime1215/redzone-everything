# Brain v3: report (Nov 3 2026)

One compact model per league (NFL, NBA, NHL): LightGBM at training time, exported to flat numpy
arrays and walked as one fused forest at runtime (numpy only), with isotonic calibration on every
probability head. MLB v3 lost to v2 on our own archive, so MLB stays on v2 (`mlb.v3.npz` is not shipped).

**What each model predicts, for every game state:**

| head | output |
|---|---|
| `stop15/30/60` | P(a stoppage starts within 15 / 30 / 60 s), nested so 15 ≤ 30 ≤ 60 |
| `types` | which stoppage: TV timeout, team timeout, review/challenge, free throws, injury, score break, period end |
| `length` | stoppage length p10 / p50 / p90 (replaces the fixed `breaks.LEN` table for NFL and NBA) |
| `entry` | seconds until the next live snap / faceoff / inbound, p10 / p50 / p90 |
| `big60/180` | P(a big play within 1 / 3 min) |
| `hot` | game-flow score |

**Budget:** 0.54 to 0.61 ms per game per tick through the live path (v2 is about 0.4 ms), against a
2 ms target. The models are 0.86 MB together (NFL 299 KB, NBA 329 KB, NHL 234 KB), against a 10 MB
target. The numpy forest matches LightGBM plus isotonic to within 3e-8 (`tests/test_brain3.py`).

**Data and splits:** split by night (5 AM ET boundary, crc32 hash), so a night never leaks across
splits. NFL trains on ESPN's public NFL play-by-play for 2023–24 (sportsdataverse), with period-end
rows synthesized because that feed lacks them. NBA and NHL train on public play-by-play. Our own
ESPN archive (eval-data-v1, the scorecard nights) is held out, and the head-to-head below runs on it
one event at a time through the same `Tracker` the app uses live. The NHL length and entry heads are
the exception: they train on archive nights and are graded on the other archive nights only.

## Held-out nights, through the live path (our archive)

AUC is higher-better. Brier and ECE are lower-better. "Rules" is today's hand-written break forecast
(`breaks.forecast`), and "v2" is today's brain.

| league | target | rules AUC | v2 AUC | **v3 AUC** | rules Brier | v2 Brier | **v3 Brier** | v3 ECE |
|---|---|---|---|---|---|---|---|---|
| NFL | stoppage in 60 s | 0.678 | 0.708 | **0.762** | 0.173 | 0.192 | **0.140** | 0.038 |
| NFL | stoppage in 30 s | 0.715 | 0.768 | **0.868** | 0.110 | 0.192 | **0.048** | 0.014 |
| NFL | big play in 60 s | – | 0.627 | **0.718** | – | 0.286 | **0.063** | 0.009 |
| NFL | big play in 3 min | – | 0.629 | **0.709** | – | 0.259 | **0.154** | 0.021 |
| NBA | stoppage in 60 s | 0.558 | 0.643 | **0.677** | 0.403 | 0.275 | **0.233** | 0.051 |
| NBA | stoppage in 30 s | 0.565 | 0.648 | **0.696** | 0.244 | 0.213 | **0.194** | 0.068 |
| NBA | big play in 60 s | – | 0.931 | **0.971** | – | 0.079 | **0.025** | 0.017 |
| NBA | big play in 3 min | – | 0.948 | **0.969** | – | 0.066 | **0.037** | 0.039 |
| NHL | stoppage in 60 s | 0.615 | 0.675 | **0.701** | 0.250 | 0.110 | **0.109** | 0.023 |
| NHL | stoppage in 30 s | 0.623 | 0.664 | **0.710** | 0.240 | 0.088 | **0.068** | 0.014 |
| NHL | big play in 60 s | – | 0.555 | **0.589** | – | 0.106 | **0.060** | 0.038 |
| NHL | big play in 3 min | – | 0.560 | 0.562 | – | 0.146 | 0.147 | 0.112 |

**Lengths and entry point** (mean absolute error in seconds, held-out live path):

| league | stoppage length, v3 p50 | today's `LEN` table | entry point, v3 p50 | constant median | p10 / p90 coverage (entry) |
|---|---|---|---|---|---|
| NFL | **34.5** | 44.0 | **26.8** | 47.9 | 8.9% below p10, 11.3% above p90 |
| NBA | **35.2** | 58.0 | **35.1** | 56.1 | 11.1% / 18.5% |
| NHL | 18.2 | **13.9** | **78.9** | 107.0 | 10.8% / 11.4% |

NHL's table wins on length because it was measured on this same archive, so NHL keeps the table.

**Public test nights** (in-distribution, same source as training): NFL stop15 AUC 0.97 and stop60
0.84; stoppage type accuracy 77% (NFL), 74% (NBA) and 81% (NHL); NFL length MAE 36 s vs 79 s for a
constant. Calibration plots are in `docs/brain3/calibration_<league>.png`, and every number is in
`redzone/scoring/brain_models/metrics_v3.json`.

Where v3 is worse: on v2's own "break soon" label, v2's head ranks long breaks better on NBA (AUC
0.82 vs v3's 0.77) and is better calibrated on all three leagues (NFL ECE 0.10 vs 0.26). So the
break forecast keeps v2's odds (`brain.v3_break_odds: []`) and takes only v3's lengths. v3's long-break
odds still reach the contract (`MomentPrediction.break_soon_p`).

## Scorecards (eval-data-v1 + Nov 1 mixed demo)

Before = `integration/next` f7f8d67 (with #2 and #8). After = this branch with its defaults.

| scorecard | line | before | after |
|---|---|---|---|
| NFL | dead air % | 9.7 | 9.7 |
| NFL | big plays seen / live % | 96.4 / 33.6 | 96.4 / 33.6 |
| NFL | crunch live % | 73.5 | 73.5 |
| NFL | your-team big seen / crunch live % | 100 / 100 | 100 / 100 |
| NFL | mid-play entries / plays missed on entry | 0 / 0 | 0 / 0 |
| NBA | dead air % | 19.4 | 19.4 |
| NBA | mid-play entries / plays missed on entry | 38 / 18 | 38 / 18 |
| NHL | dead air % | 24.1 | 24.1 |
| NHL | mid-play entries / plays missed on entry | 5 / 0 | 5 / 0 |
| Nov 1 demo | commercials % | 15.3 | 15.3 |
| Nov 1 demo | NFL big plays live / crunch | 67 / 105 of 137 | 67 / 105 of 137 |
| Nov 1 demo | mid-play entries / missed / mid-play cuts | 4 / 0 / 0 | 4 / 0 / 0 |

Every line is unchanged. The better predictions do not turn into fewer commercials yet: the parts that
would act on them (learned entry, v3 scoring outside NFL) each cost a protected line, so they ship off.
Turning them into fewer commercials is a policy question for the decision core.

## What is on by default, and why

The parts went on one at a time, each graded on every scorecard. Only the parts that cost nothing are on.

| part | setting | on for | result |
|---|---|---|---|
| v3 break lengths | `brain.v3_lengths` | NFL, NBA | no scorecard line moved; better lengths for the trip planner and the highlight jump |
| v3 break odds in the break forecast | `brain.v3_break_odds` | none | v2's head is better calibrated on held-out nights |
| v3 big-play / game flow in the score | `brain.v3_score` | NFL | no scorecard line moved. On NBA it cost +2 missed plays and on NHL +1 mid-play entry and +1 missed, so those stay on v2 |
| learned entry point in `resume_eta` | `brain.v3_entry` | none | uncapped p50: NFL your-team big plays 100 → 93.1, NBA mid-play entries 38 → 45. p10 capped: NFL dead air 8.6 → 8.8. p50 capped: changed nothing. Left off, for the decision core to use |

Ablation details (all against the d702565 baseline, before #2 and #8 merged):

| variant | NFL dead air | NFL big seen | NFL your-team big | NBA mid-play | NBA missed | NHL mid-play | NHL missed | demo commercials |
|---|---|---|---|---|---|---|---|---|
| baseline | 8.6 | 95.4 | 100 | 38 | 18 | 5 | 0 | 15.3 |
| everything on | 8.4 | 94.2 | 93.1 | 42 | 19 | 6 | 1 | 15.3 |
| v3 score all, no entry | 8.6 | 95.4 | 100 | 35 | 20 | 6 | 1 | 15.3 |
| v2 score, entry on | – | – | 93.1 | 45 | 17 | 5 | 0 | – |
| v3 score NFL, entry p50 uncapped | 8.4 | 94.2 | 93.1 | 45 | 17 | 5 | 0 | 15.2 |
| v3 score NFL, entry p10 | 8.8 | 95.4 | 100 | 38 | 18 | 5 | 0 | 15.3 |
| v3 score NFL, entry p50 capped | 8.6 | 95.4 | 100 | 38 | 18 | 5 | 0 | 15.3 |

## Gaps against the no-overfitting rule (Nov 3, 18:29)

This round predates the rule and falls short of it:
- Nights were split by a crc32 hash, not by time. The NFL model trains on 2023–24 and is graded on
  2025 (time-ordered), but NBA, NHL and the within-source test splits are not.
- The default settings above were chosen on the eval-data-v1 scorecard nights, which are also the
  nights reported here, so these scorecards are not an unseen test of those choices.
- There is no forward-chaining CV, no train/valid/test gap report, and no per-slice or robustness report.

Next round: a time-ordered split, 5 forward-chaining folds (mean and worst fold), the gap report,
slices, and the locked test set left untouched.

## For other owners

- `brain3.MomentV3(brain)` and `brain3.BreaksV3(book, brain)` implement the MomentModel and
  BreakModel contracts (`redzone/contracts`), with real `length_q10/50/90`, `next_restart_at` and
  `kind`. `LegacyMoment` and `LegacyBreaks` stay the defaults until the integration owner switches them.
- Every `Reading` carries `v3` (a `brain3.V3`): `stop15/30/60`, `types`, `length`, `entry`,
  `big60/180`, `resume_at(q)` and `as_dict()`. Highlight jump: `entry[0]` (p10) and `big60`/`big180`.
- A broken or mismatched `.v3.npz` falls back to v2 for that league, and so does a prediction error at runtime.

## Rebuild

```
python scripts/build_training3.py --raw <raw dir>    # tables -> data/training3
python scripts/train_brain3.py --live                # models + metrics_v3.json + plots
```
The raw public data is not in the repo (sportsdataverse / nflverse / NHL / MLB public feeds).
