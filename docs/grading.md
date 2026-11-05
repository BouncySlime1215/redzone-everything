# Grading and training rules

**Source:** Nick, Nov 4 2026, 6:40 PM ET: "training needs to be SHARP, grading even SHARPER." These rules
bind lanes 1-2 and the integrator from that moment on.

**Owner:** the integrator. It writes and freezes the graders; the lanes that train never do.

**Related:**
- [architecture.md §7](architecture.md) (release gate);
- the promotion bar (B) and cut rules in [decisions.md](memory/decisions.md);
- the experiment queue [experiments.md](experiments.md).

The **Enforced** column says how each rule is held:
- **CI** means a red job blocks the merge;
- **review** means the integrator refuses the PR;
- **todo** names the check still to build.

## Grading

| # | Rule | Enforced |
|---:|---|---|
| 1 | **Separation of duties.** The integrator writes and freezes the graders, never the lane that trains. Grader code is versioned per release in `docs/grader_freeze.json`. | **CI** `grading_check.py` G1: a grader file whose sha256 differs from the freeze turns the `graders` job red. The integrator reviews the change and re-freezes (`--refreeze`) in the merge commit. Every RC records the freeze version it shipped with. |
| 2 | **Pre-register every experiment** in [experiments.md](experiments.md) BEFORE running it: hypothesis, metric (North Star line), success bar, nights needed. No moving goalposts. | **CI** G2: a `running`/`done` row with no success bar or nights is red; it shows PENDING until the table has those columns. G3 (`--base`): a row already running in the base branch may not change its hypothesis, line, bar or nights; change it by registering a new EXP id. |
| 3 | **Grade against REAL outcomes**: play-by-play plus the hindsight oracle (`redzone/lab/oracle.py`), not teacher labels. Teacher agreement is a secondary line only. | **review**: every North Star line is computed from the archive's play-by-play. **todo**: a teacher-agreement line labeled "secondary". |
| 4 | **Every number gets a cluster-bootstrap CI over nights**, a minimum sample size, and PENDING when underpowered. Report per league and per slice: crunch, down the stretch, OT, your team, early, blowout. | **CI (part)**: `northstar.py` and `release_gate.py` already show PENDING under the minimum nights, and the promotion bar is CI-aware. **todo**: a bootstrap CI column on every line, plus the OT / early / blowout slices. |
| 5 | **Automatic leakage checks**: a time-split audit; adversarial validation (a model telling train from test must be at ~chance); a feature-leak scan (any single-feature AUC > .98 is a red flag, like the NBA .999). | **CI (part)**: `leak_check.py` covers splits, time order, end-of-game fields and stamps. **todo**: adversarial validation and the AUC scan as leak_check checks that FAIL. |
| 6 | **Calibration**: ECE and reliability curves, not just accuracy. | **CI (part)**: `wv_calibration_err` is a North Star line. **todo**: ECE for every probability head (WP, crunch, break), plus reliability curves in the Teacher/Mind reports. |
| 7 | **A golden regression suite** of hand-picked nightmare nights (overlapping crunches, OT pileups, feed drops) that must never get worse. | **todo**: `tests/golden/`, each night scored against a pinned value that may only go up. Candidate nights come from the cycle reviews. |
| 8 | **Ablations**: every component shows its own contribution. | **review**: a PR adding a component shows the North Star with it off and on. **todo**: `northstar.py --ablate <flag>`. |
| 9 | **Final judges, in order**: locked nights, then shadow mode on live nights, then real hardware. | **review**: the promotion step in each cycle review. Shadow mode is EXP-004. |

## Training

| # | Rule | Enforced |
|---:|---|---|
| 10 | **Reproducible runs**: seed, data hash, recipe stamp and code pack id in grades.json. | **todo**: the grades.json schema check fails on a missing field; Kaggle and Colab notebooks write all four. |
| 11 | **Checkpoint and hyperparameter selection on VALID only**, with early stopping on valid. | **review** plus the locked set rule (scored once per release, never tuned on). **todo**: grades.json records the split used for selection, and CI refuses `test`/`locked`. |
| 12 | **Compute-matched baselines.** | **review**: every model report sets a baseline trained with the same GPU-h beside it. The head metric stays lift over the context-only baseline. |
| 13 | **Learning curves and the train/valid/test gap** in every report. | **review**. **todo**: the grades.json schema requires `curve` and `gap`. |
| 14 | **Dedupe the data and balance the slices** (crunch, OT, blowouts). | **todo**: the factory pack reports duplicate share and per-slice counts in the data ledger, and CI flags duplicates above 1%. |

## How a grader changes

1. The lane opens a PR. The `graders` job goes red on the changed file. That is expected, not a failure.
2. The integrator reviews the grading change on its own merits. Does it count something new? Does it
   re-base a number? Is the old/new table shown side by side?
3. If accepted, the integrator runs `python scripts/grading_check.py --refreeze` in the merge, adds a line to
   decisions.md, and re-scores live + candidate under the new grader. The cycle review shows both.
4. A grader change never lands in the same PR as a model or decider change that it would grade.
