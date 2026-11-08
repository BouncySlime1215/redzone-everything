# How to learn a RedZone switching policy safely

Research note, 2026-11-08. No code changed. Every source below was checked on arXiv (API record: title,
authors, date) or Crossref. Where I describe a paper's finding I say so; where I am inferring, I say
"inference". Papers before 2024 are marked **[old]**.

## 0. Bottom line (read this first)

1. **Do not start with RL.** RedZone already has what offline RL has to approximate: an exact, full-information
   replay of every night (`redzone/lab/oracle.py`) and a grader for it (`sportsgrade`, `outcome_label`). A game
   does not react to which game Nick watches, so a counterfactual "what if we had switched at t" is
   answered by replay, not estimated. That makes the hard part of offline RL and OPE (unseen actions, unknown
   propensities) mostly disappear. Inference, but a strong one.
2. **Smallest viable approach = what `noggin/pick.py` + `scripts/train_noggin_pick_dagger.py` already are:**
   imitate the hindsight oracle (supervised ranking/regression on per-game features), fix compounding error
   with DAgger, and wrap the result in the existing hard-rule shield (dead-ball-only entry, leverage floor,
   L1 team rules). Add a **conformal/PAC risk-control layer** on the learned "is it safe to leave / land" heads,
   and judge it in **shadow mode** against the rule decider.
3. **Off-policy evaluation (IPS/DR/FQE) is for the human-signal part only** (did Nick like it) and for live
   logs. `redzone/lab/offpolicy.py` already has DR with night-bootstrap CIs, but the live decider logs
   p = 1.0 on the chosen option (deterministic), so IPS/DR collapse to the direct method on any switch that differs
   from the log. Fix that with a small epsilon-explore **only among already-safe, near-tied options** (never a
   mid-play entry) if, and only if, Nick wants a live learning loop.
4. **LLM judge -> small model distillation** is already the `docs/distill.md` design. Keep it, but treat the
   judge as a noisy labeller and gate on gold labels (Huang et al. 2024 report fine-tuned judges behave like
   task-specific classifiers that do not transfer).

## 1. Ranked approaches (evidence, cost, fit)

Cost = Mac RAM / Kaggle hours / engineering days, at $0. Fit = how well it respects the hard constraints.

| Rank | Approach | Evidence | Cost | Fit to RedZone |
|---:|---|---|---|---|
| 1 | **Oracle imitation + DAgger** (supervised, per-game score, rollout in the app's own grader) | Ross et al. 2011 [old] prove plain behaviour cloning compounds error quadratically in horizon and DAgger removes that by labelling the learner's own visited states. Project evidence in `train_noggin_pick_dagger.py` header: plain imitation ~92 vs oracle ~98 "big live"; DAgger is the fix. | Gradient-boosted trees (`pick_nba.v1.txt.gz`, a few MB), CPU, minutes. Far under 2 GB. | Best. Labels come from play-by-play, not opinions. Shield stays outside the model. |
| 2 | **Learning-to-rank for "which game deserves the TV"** (pairwise/listwise, groups = decision step, items = live games) | Used as the core of broadcast direction systems: Smart Director (Pan et al. 2022 [old]) ranks views by highlight score then schedules; Disney's director assistance (see 4.5) shortlists and ranks cameras. Inference: the oracle's value-to-go gives graded relevance labels for free. | LightGBM `lambdarank` or XGBoost rank:pairwise, CPU, minutes. | Very good. Ranking output feeds the existing margin/hysteresis rule; no new action space. |
| 3 | **Conservative offline RL (IQL / TD3+BC / CQL) on logged + oracle trajectories** | Levine et al. 2020 [old] tutorial; Kostrikov et al. 2021 [old] IQL; Fujimoto & Gu 2021 [old] TD3+BC; Kumar et al. 2020 [old] CQL. Park et al. 2024: in many benchmarks policy extraction and test-time generalization limit performance more than value learning; high-coverage data and test-time extraction help. Park et al. 2025 FQL: one-step flow policy, simple and strong. | Small MLPs; Kaggle T4 is plenty; Mac inference trivial. Engineering is large: state/action/reward design, a simulator wrapper. | Medium. Only worth it if rank 1-2 plateau below the oracle on crunch and your-team slices. Rewards must be the grader's, not a proxy. |
| 4 | **Contextual bandit with logged feedback (counterfactual risk minimisation / DR learning)** | Li et al. 2010 [old] (news recommendation, the classic); Swaminathan & Joachims 2015 [old] CRM; Dudik et al. 2011 [old] DR policy evaluation and learning; Saito et al. 2020 [old] Open Bandit Pipeline for reproducible OPE. | Cheap compute; needs randomized logging to have any power. | Low today (deterministic logs, p = 1). Becomes useful only for the "did Nick prefer it" signal, which the project has deliberately excluded (decisions: Noggin is self-taught, feedback table never read). |
| 5 | **Safe-RL with hard constraints: shields and constrained MDPs** | Alshiekh et al. 2017 [old] shielding; Achiam et al. 2017 [old] CPO; Gu et al. 2022 [old] review; Konighofer et al. 2025 survey (CACM) says guarantees hold only relative to the shield's model; Hamel-De le Court et al. 2026 extend shields to robust MDPs with uncertain dynamics; Liu et al. 2023/2024 OSRL benchmark and Constrained Decision Transformer for offline safe RL. | The shield is a pure function; nearly free. CMDP training is expensive and unneeded. | Use the *shield* idea (already how `priority.py` and `cuts.py` work), skip learned constrained RL: "0 mid-play entries" needs a deterministic guarantee, not a Lagrangian that trades cost for return. |
| 6 | **LLM judge distilled to a small student** | Hinton et al. 2015 [old] distillation; Zheng et al. 2023 [old] MT-Bench judge agreement; Huang et al. 2024: fine-tuned judge models are task-specific classifiers and not a general substitute for GPT-4; surveys Gu et al. 2024 and Li et al. 2024; JudgeLRM 2025 (small RL-trained judges). | Already built (Qwen3-32B teacher, Qwen3-1.7B 4-bit student, LoRA; `docs/distill.md`). | Good for *narrative* judgments (lock-in, safe-to-leave). Not for timing: timing comes from the feed. |
| 7 | **Camera/broadcast selection research as method source** | Chen et al. 2018 and 2019 learn which camera is "on air" from human broadcasts and impute missing candidate views (random survival forest). Chen & Carr 2015 mimic camera operators by regression. Pan et al. 2022 Smart Director. | Reading only. | Useful framing, not code. These systems learn from *human* directors; RedZone's oracle is a better teacher than a human log. |

### What the evidence does and does not say

- **Offline RL surveys agree on the central risk**: the learned policy visits states the data never covered
  (Levine 2020; Park 2024). For RedZone this is the Ross-et-al. compounding error and the reason rank 1 uses DAgger
  and the grader's rollout, not a one-step accuracy.
- **OPE accuracy is task-dependent.** Voloshin et al. 2019 [old] find no estimator wins everywhere and
  model-based/FQE-type methods are competitive in many settings. Uehara et al. 2022 [old] is the survey to read.
  Recent work (2025) adds PAC-valid conformal intervals for OPE in contextual bandits (item 11 in the
  bibliography; abstract read only), which is the right tool for "is this lift real".
- **I found no 2024-2026 paper on learned game-switching for a multi-game viewer or RedZone-style
  whip-around.** The closest are broadcast camera selection (2018-2022) and one 2026 student thesis I could not
  verify (Lund University, VLM-chosen camera views). Treat "how broadcast systems do it" as design inspiration:
  rank sources by an event/highlight score, then schedule with smoothness constraints.

## 2. Why RedZone differs from standard offline RL (inference, from the repo)

| Standard offline RL | RedZone |
|---|---|
| Environment responds to actions | Games are exogenous: replay answers "what if" exactly (`oracle.py` `solve`, `value_to_go_cue`) |
| Reward must be estimated | Reward is computed from play-by-play (`sportsgrade`, `outcome_label`) |
| Behaviour policy unknown | Behaviour policy is our own rule decider, known but deterministic (p = 1) |
| Safety is a soft constraint | Mid-play entry = 0 is hard; enforce by construction (dead-ball table, `cuts.DeadTracker`, `leverage.py`) |
| Need many trajectories | 75 NFL nights, 13 NBA, 14 NHL in eval-data-v1; v2 plan ~5,000 games (architecture.md section 8) |
| Unknown: screen/feed delay | Delay is the real sim gap: `delaymodel.py`, `feed_lag_profile.json`, `ci_scorecard.py --robust` |

Consequence: the largest source of **train/live mismatch is the delay model and feed dropout**, not the
policy class. Every learned model must be graded under the robust slices (3 s early/late, 10% drop, 15% late
plays), as grading.md and architecture.md section 8.8 already require.

## 3. Staged plan mapped to real files

Each stage has an exit test. A stage that fails its test stops there and the previous stage stays live.
Time estimates are for one engineer-session each.

### Stage 0: freeze the question (about 1 hour)
- Register the experiment in `docs/experiments.md` (hypothesis, metric, success bar, nights) before running,
  per grading.md rule 2. Metric = North Star "% of hindsight-oracle value achieved" plus the hard counts
  entries_mid_play, high-leverage mid-play leaves, plays_missed_entry, dead_air_pct.
- Baseline to beat: today's rule decider (`redzone/decider/decider.py` + `playbooks.py` + `noggin/priority.py`),
  and the compute-matched context-only baseline (grading.md rule 12).

### Stage 1: data (about 1 day, mostly exists)
- Source of truth: `redzone/lab/datasets.py`, `evaldata.py`, `splits.py` (`split_nights`, `folds`, `fresh`,
  `leaks`). Build per-(game, 5 s step) rows via `noggin/features.py` and `pick.features_for_night`.
  Features only from plays with wall <= t - feed_lag(p90) (already the PICK rule).
- Labels: oracle value-to-go per option (`oracle.value_to_go_cue`, `cue_action_values`). Graded relevance for
  ranking = rank of the option by that value. Never teacher labels for grading (grading.md rule 3).
- Add the one piece that does not exist: **explicit action-validity masks** per step (can-leave, can-land)
  saved next to features, so the shield and the model train and run on the same masks.
- For the live log path: keep `decision_records.py` output as the human-readable audit trail, and do not
  read the feedback table (decisions.md Nov 4).

### Stage 2: model (about 2 days, then Kaggle for sweeps)
- 2a. Re-train PICK as a **pairwise/listwise ranker** (group = step; items = live games) next to the
  existing regressor, in `scripts/train_noggin_pick.py` (new `--objective lambdarank`); compare on
  forward-chaining folds. Keep LightGBM (CPU, MB-sized; matches the existing `models/pick_*.txt.gz`).
- 2b. Continue DAgger (`train_noggin_pick_dagger.py`), 2-3 rounds, labels from `value_to_go_cue` at states the
  learner visits. Report per-round rollout value and the worst fold.
- 2c. Only if 2a-2b stall below the oracle on the your-team and crunch slices: try a tiny IQL or TD3+BC
  head (2-layer MLP, under 1 MB) on Kaggle using the oracle's replay as the simulator. Do not start with CQL:
  its conservatism penalty is tuned for fixed logged data, while here we can roll out freely.
- 2d. LLM judge stays in `agent/distill/` for narrative heads only; its output is a feature or a veto input,
  never an entry decision.

### Stage 3: shield (about 1 day)
- The shield is **not learned**. It is the existing deterministic stack: dead-ball tables (`decider/cuts.py`,
  `break_tail.py`), leverage floor (`lab/leverage.py`), L1 team and crunch tier (`noggin/priority.py`),
  banned apps, and `decider.JUMP_KEEPS` kinds. The model only ranks inside the allowed set, so mid-play entry
  is impossible by construction (the shielding idea, Alshiekh 2017; Konighofer 2025 stresses the guarantee is
  relative to the model of dead balls, so test that model: `tests/` fault-injection with delayed/dropped feed).
- Add a **learned-head guard with finite-sample risk control**: for "safe to leave" and "will still be dead
  when the picture lands" probabilities, choose the threshold on the *valid* nights with conformal risk control
  (Angelopoulos et al. 2022 [old]) so that expected unsafe-leave loss is at most 0.5% (the reasoner gate in
  architecture.md section 7). Report the valid-night coverage and the test result separately.
- A policy veto path already exists in `core.py` ("name every jump veto" commit). Reuse it; log every veto with
  its reason into `decision_records`.

### Stage 4: shadow (about 1 week of real nights, no code risk)
- The repo already has shadow flags (`noggin.possession.shadow`, `pick_log`). Run the learned ranker in shadow:
  it proposes, the rule decider acts, and both are logged with the full candidate set (`candidates` in
  `decision_records.py`).
- Grade shadow nights with the same grader: oracle value achieved, mid-play entries the shadow would have
  made (must be 0 after the shield), high-leverage leaves, plays missed on entry.
- Optional: log a **propensity** for each decision (p = 1 for the acted option in a deterministic app). If
  Nick ever approves exploration, add epsilon <= 5% among options that already pass the shield AND are within a
  small oracle-value gap of the best. That gives `offpolicy.estimate` non-degenerate weights (its own
  `W_MAX` docstring assumes about a 5% explore rate, giving weights of about 20). Needs Nick's decision first;
  nothing here changes defaults.

### Stage 5: gate (existing machinery)
- Ship OFF behind a flag (architecture.md section 7). Promote only if `scripts/release_gate.py` passes on the
  **locked test split once** plus fresh nights, and shadow nights agree. Any slice that regresses (crunch,
  your-team, OT) blocks promotion even if the mean improves (grading.md rule 4; architecture.md section 8.7).
- A PR that changes a default adds a line to `docs/memory/decisions.md` (CI enforces).

## 4. Evaluation and leakage protocol

### 4.1 What to measure (all from real play-by-play)
Primary (hard): entries_mid_play = 0, high-leverage mid-play leaves = 0, plays_missed_entry, dead_air_pct,
oracle value achieved, crunch live %, your-team crunch live = 100%. Secondary: teacher agreement
(labelled secondary, grading.md rule 3), calibration (ECE) of each probability head (rule 6).

### 4.2 Splits (extends `lab/splits.py`)
1. Whole nights, time-ordered, locked newest >= 25%, scored once per release (already rules 1-2).
2. Model selection with **5 forward-chaining folds** over train+valid; report mean and worst fold.
3. **Two-level separation for DAgger**: DAgger's relabelled states count as training data, so the learner's
   rollouts must be produced only on train nights. A DAgger iteration touching valid/test nights is leakage.
4. Choose the shield thresholds (conformal) on **valid**, report on **test**. Never reuse the same nights for
   both the threshold and the claim.
5. Whole-**game** disjointness (a game appears on one night only; `archive_nights` rule) and, for double
   counts across leagues, per-league CIs, not pooled.

### 4.3 Estimators and CIs
- Primary estimator: **direct replay value** from the oracle grader. It is exact up to the delay model, so
  vary the delay (`--true-delay`) and feed drop/late settings as robustness slices.
- For logged-decision claims (live nights), use `offpolicy.estimate` (DR) and **cluster-bootstrap over
  nights**, not rows (already `night_mean_ci`). Report IPS, DM and DR next to each other; a large spread means the
  claim is not identified (Voloshin 2019; Dudik 2011). Cap weights (`W_MAX`) and report the clipped share.
- With deterministic logs the correct statement is "DR equals the direct method here"; do not present it as
  an unbiased off-policy estimate.
- For small samples use **PAC/conformal intervals** on policy value rather than a normal approximation
  (2025 contextual-bandit PAC prediction paper).

### 4.4 Leakage checks (turn the grading.md "todo" items on)
1. `causal(feature_times, decision_t)` empty for every feature builder (exists).
2. **Adversarial validation**: train a classifier to tell train steps from test steps; AUC must be near 0.5.
3. **Single-feature AUC scan**: any feature with AUC > .98 against the label is a red flag (the NBA .999 case).
4. **End-of-game fields**: no final score, final period, or "game over" flags in features (`leak_check.py`).
5. **Oracle leakage**: model inputs must not contain oracle tables; the oracle appears only in labels and grading.
6. **Judge leakage**: gold labels' game ids are excluded from distillation training (distill.md).
7. **Shadow/live parity**: features computed live vs recomputed offline from the archive must match within
   tolerance for the same step (catches train/serve skew).
8. Duplicate share <= 1% (rule 14), per-slice counts reported (crunch, OT, blowout, your team).

### 4.4a Slice and robustness report required in every PR
Per league, per slice (crunch, close-late, OT, early, blowout, your team), under three delay settings, with
mean, worst fold, and n nights. Underpowered cells show PENDING (rule 4). The locked set is 130 games in v1
(architecture.md section 8), so most slices are underpowered today; gate-power work (`scripts/gate_power.py`)
and eval-data-v2 are prerequisites for a trustworthy "95%" claim.

## 5. Open risks
- **Overfitting to one season of rules.** Sports differ; the per-league model + shared features design is
  already the right split. Do not pool leagues until the per-league worst fold is good.
- **Reward = oracle objective.** If the oracle's objective is wrong (it ignores Nick's taste), an imitation
  learner copies the error. Mitigate with the `docs/grading.md` final judges: locked nights, shadow, hardware.
- **Distribution shift in live feeds** (lag, dropouts) is the main sim gap; the robust scorecards are the guard.
- **Shield completeness.** The shield is only as good as the dead-ball tracker; Konighofer et al. 2025 say the
  guarantee is relative to the model. Fault-injection tests on the tracker are not optional.

## 6. Bibliography (all verified)

Verification: arXiv API (export.arxiv.org) returned the title, first author and date for each arXiv id on
2026-11-08; the CACM item was checked on Crossref (DOI resolves, issued 2025-10-29). **[old]** = before 2024.

### 2024-2026
1. Park, S., Frans, K., Levine, S., Kumar, A. "Is Value Learning Really the Main Bottleneck in Offline RL?"
   arXiv:2406.09329, 2024-06-13 (NeurIPS 2024). Policy extraction and generalization often limit performance.
2. Park, S. et al. "Flow Q-Learning." arXiv:2502.02538, 2025-02-04. Simple one-step flow policy
   for offline RL.
3. Huang, H. et al. "An Empirical Study of LLM-as-a-Judge for LLM Evaluation: Fine-tuned Judge Model is not a
   General Substitute for GPT-4." arXiv:2403.02839, 2024-03-05 (title changed across versions; cite v4).
4. Gu, J. et al. "A Survey on LLM-as-a-Judge." arXiv:2411.15594, 2024-11-23.
5. Li, H. et al. "LLMs-as-Judges: A Comprehensive Survey on LLM-based Evaluation Methods."
   arXiv:2412.05579, 2024-12-07.
6. Chen, N. et al. "JudgeLRM: Large Reasoning Models as a Judge." arXiv:2504.00050, 2025-03-31.
7. Konighofer, B., Bloem, R., Jansen, N., Junges, S., Pranger, S. "Shields for Safe Reinforcement Learning."
   Communications of the ACM, 2025 (DOI 10.1145/3715958; Crossref issued 2025-10-29).
8. Hamel-De le Court, E. et al. "Robust Shielding for Safe Reinforcement Learning." arXiv:2606.00270, 2026-05-29.
   Shielding when the safety-relevant dynamics are uncertain (robust MDPs). I read only the abstract.
9. Liu, Z. et al. "Datasets and Benchmarks for Offline Safe Reinforcement Learning." arXiv:2306.09303,
   2023-06-15 (a search result says the journal version is 2024; I did not open it, so treat as **[old]**).
10. Liu, Z. et al. "Constrained Decision Transformer for Offline Safe Reinforcement Learning."
    arXiv:2302.07351, 2023-02-14 (ICML 2023) **[old]**.
11. "PAC Off-Policy Prediction of Contextual Bandits." arXiv:2507.16236, 2025-07-22. PAC-valid conformal
    intervals for OPE in contextual bandits. (Author list not recorded; abstract read.)
12. "Uncertainty Quantification and Causal Considerations for Off-Policy Decision Making." arXiv:2502.06011,
    2025-02-09 (thesis on OPE variance and uncertainty). Abstract read.

### Older foundations **[old]**
13. Levine, S., Kumar, A., Tucker, G., Fu, J. "Offline Reinforcement Learning: Tutorial, Review, and
    Perspectives on Open Problems." arXiv:2005.01643, 2020.
14. Kostrikov, I., Nair, A., Levine, S. "Offline Reinforcement Learning with Implicit Q-Learning."
    arXiv:2110.06169, 2021.
15. Fujimoto, S., Gu, S. "A Minimalist Approach to Offline Reinforcement Learning." arXiv:2106.06860, 2021.
16. Kumar, A., Zhou, A., Tucker, G., Levine, S. "Conservative Q-Learning for Offline Reinforcement Learning."
    arXiv:2006.04779, 2020.
17. Ross, S., Gordon, G., Bagnell, J. A. "A Reduction of Imitation Learning and Structured Prediction to
    No-Regret Online Learning." arXiv:1011.0686, 2010 (DAgger).
18. Dudik, M., Langford, J., Li, L. "Doubly Robust Policy Evaluation and Learning." arXiv:1103.4601, 2011.
19. Jiang, N., Li, L. "Doubly Robust Off-policy Value Evaluation for Reinforcement Learning."
    arXiv:1511.03722, 2015.
20. Swaminathan, A., Joachims, T. "Counterfactual Risk Minimization: Learning from Logged Bandit Feedback."
    arXiv:1502.02362, 2015.
21. Swaminathan, A. et al. "Off-policy evaluation for slate recommendation." arXiv:1605.04812, 2016 (for
    ranking-style actions).
22. Li, L., Chu, W., Langford, J., Schapire, R. "A Contextual-Bandit Approach to Personalized News Article
    Recommendation." arXiv:1003.0146, 2010.
23. Saito, Y. et al. "Open Bandit Dataset and Pipeline: Towards Realistic and Reproducible Off-Policy
    Evaluation." arXiv:2008.07146, 2020.
24. Voloshin, C., Le, H. M., Jiang, N., Yue, Y. "Empirical Study of Off-Policy Policy Evaluation for
    Reinforcement Learning." arXiv:1911.06854, 2019.
25. Le, H. M., Voloshin, C., Yue, Y. "Batch Policy Learning under Constraints." arXiv:1903.08738, 2019
    (constrained batch RL with fitted-Q evaluation).
26. Uehara, M., Shi, C., Kallus, N. "A Review of Off-Policy Evaluation in Reinforcement Learning."
    arXiv:2212.06355, 2022.
27. Alshiekh, M. et al. "Safe Reinforcement Learning via Shielding." arXiv:1708.08611, 2017.
28. Achiam, J., Held, D., Tamar, A., Abbeel, P. "Constrained Policy Optimization." arXiv:1705.10528, 2017.
29. Gu, S. et al. "A Review of Safe Reinforcement Learning: Methods, Theory and Applications."
    arXiv:2205.10330, 2022.
30. Jin, Y., Yang, Z., Wang, Z. "Is Pessimism Provably Efficient for Offline RL?" arXiv:2012.15085, 2020.
31. Angelopoulos, A. N. et al. "Conformal Risk Control." arXiv:2208.02814, 2022.
32. Hinton, G., Vinyals, O., Dean, J. "Distilling the Knowledge in a Neural Network." arXiv:1503.02531, 2015.
33. Zheng, L. et al. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena." arXiv:2306.05685, 2023.
34. Zhu, L. et al. "JudgeLM: Fine-tuned Large Language Models are Scalable Judges." arXiv:2310.17631, 2023.
35. Pan, Y. et al. "Smart Director: An Event-Driven Directing System for Live Broadcasting."
    arXiv:2201.04024, 2022 (multi-camera soccer; event localization, view ranking, scheduler).
36. Chen, J. et al. "Learning Sports Camera Selection from Internet Videos." arXiv:1809.02854, 2018 (also WACV
    2019). Predicts which camera is "on air" for soccer.
37. Chen, J., Carr, P. "Mimicking Human Camera Operators." WACV 2015 (found via MLAnthology and cited in
    Disney patents US10003722 / US10812686; no arXiv id; page listing seen, full text not read).
38. "Camera Selection for Broadcasting Soccer Games." WACV 2018 (listing on MLAnthology seen in search;
    authors and text not opened, so cite as found-but-unverified).

### Not verified, do not cite
- A 2026 Lund University thesis on multimodal-LLM best-camera selection (student paper, year unconfirmed).
- 2026-10 arXiv items 2610.22930 (conformal policy selection) and 2609.36740 (offline ranking-policy learning
  via top-k decomposition): titles confirmed in the arXiv listing, abstracts only skimmed or unread; use as
  leads, not evidence.

## 7. One thing to do next (under 2 minutes)
Open `docs/experiments.md` and add one row for "PICK lambdarank + DAgger vs rule decider, success bar = oracle value +X with 0 mid-play entries,
nights = fold 1-5 + locked once".
