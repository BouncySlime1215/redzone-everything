# EXP-1404: EXP-1402 Teacher importance, re-graded on real outcomes

Data planner E, Nov 5 2026. Labels: `agent/kaggle/commentary_regrade.py label` at c6a28b4, Colab CPU (overseer, finished 3:54 PM ET), 66,048 plays, the same plays EXP-1402 answered. This file holds aggregate grades only. The labels and Teacher answers are ESPN-derived and private (`/mnt/project-files/dataE/`). Seed 1404, 1000 night-bootstrap draws.

**Join check: PASS.** The rebuilt `big_next` AUC reproduces EXP-1402 exactly in every league (NBA 0.9553, CBB 0.9569, CFB 0.5876, NFL 0.5808, MLB 0.5845, NHL 0.5386).

| league | n (nights) | y_out rate | importance AUC on y_out [95% CI] | within-context AUC [95% CI] | context-only AUC (y_out) | verdict |
|---|---|---:|---|---|---:|---|
| NBA | 11,008 (176) | 14.9% | 0.631 [0.611, 0.650] | **0.481** [0.459, 0.502] | 0.836 | NO SIGNAL beyond clock/score |
| NFL | 11,008 (102) | 13.9% | 0.725 [0.695, 0.757] | **0.574** [0.547, 0.601] | 0.698 | REAL SIGNAL |
| CBB | 11,008 (71) | 14.7% | 0.644 [0.608, 0.676] | 0.502 [0.472, 0.529] | 0.811 | NO SIGNAL |
| CFB | 11,008 (116) | 15.8% | 0.735 [0.708, 0.763] | 0.517 [0.487, 0.543] | 0.735 | NO SIGNAL |
| NHL | 10,996 (184) | 9.0% | 0.598 [0.575, 0.621] | 0.535 [0.510, 0.557] | 0.762 | REAL SIGNAL (small) |
| MLB | 11,008 (404) | 6.1% | 0.603 [0.578, 0.627] | 0.578 [0.554, 0.601] | 0.820 | REAL SIGNAL |

**What it means**
- **The basketball 0.955 / 0.957 is not evidence.** On real outcomes, NBA importance ranks a lead change or win-probability swing at 0.63. That is all clock and score: within the same period, 2-minute clock bin and margin bin it is 0.48, which is chance. CBB reads the same way. The old `big_next` is a field goal in the last 2:00 within 5 by definition. Its context-only AUC with coarse 1-minute bins is 0.88 for NBA and 0.92 for CBB, under the pre-registered 0.95 flag, so the automatic "our rule" flag reads False. That flag is weaker than the definition, which already says it is our rule.
- **Where the Teacher adds something beyond clock and score: NFL (0.574), MLB (0.578), NHL (0.535).** All three CI lower bounds are above 0.50. The effects are modest.
- No Holm correction was pre-registered across the six leagues. Under Bonferroni (6), NFL and MLB stay clearly above 0.5 and NHL is borderline.
- The sample is the priority queue (mine > crunch > late), not a random sample of plays.

**Decisions (pre-registered bar)**
- EXP-1711 (Teacher-feature ablation in Watch Value v2) may run for **NFL, MLB and NHL**. It is closed for NBA, CBB and CFB: their Teacher importance adds nothing beyond period, clock and margin, which Watch Value already has.
- For NBA, the priority league, the Teacher's importance does **not** help the Watch Value reward. Don't spend 32B GPU time on more NBA commentary for this purpose.
- The EXP-1402 tokens stay landed as causal context (mode 2). Use them as a world-model input only in NFL, MLB and NHL until a new EXP shows otherwise.
