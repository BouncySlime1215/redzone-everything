---
id: D-FRONTIER
title: "Frontier prediction methods for RedZone: what to try, what is hype, and one joint where-and-when design"
date: 2026-11-09
stream: training
author: research agent (Claude Opus 5.5)
status: draft
supersedes: []
sources: 48
verified: 44
affects: []
gate: []
---

# Frontier prediction for RedZone

**Do this first (about 2 hours):** add two columns to `scripts/kaggle_gamevalue_holdset.py`: FC-lag and FC-reach, defined below. Today's fair test reads the feed with no lag and ignores whether a game can be reached, so it cannot judge any timing experiment.

## The answer in 10 lines

1. **Nothing published replaces boosted trees for our numeric game state.** The frontier agrees with what the repo already built: a discrete-time boosted hazard for "when", a tree value model for "where".
2. **The best idea I found is cheap.** A January 2026 paper fits a whole future hazard surface with plain Poisson boosting and a log-exposure offset ([26](#s26)). That gives one model for both questions.
3. **"Where" and "when" should be two readings of the same surface.** Value is the integral of the event hazards. The restart bound is a low quantile of the dead-ball survival curve.
4. **The plus-or-minus should be set on the decision, not on the forecast.** Conformal risk control picks the safety margin that bounds mid-play entries directly ([13](#s13)). An online update keeps it honest as the feed drifts ([12](#s12)).
5. **Conformal gives an average miss rate, never zero.** It cannot deliver Rule #1 alone. The code vetoes stay.
6. **The one neural model worth a run is tiny.** A 7,203-parameter event transformer matched models up to 3.14 M parameters on order-book data and ran in 0.48 ms on one M2 thread ([31](#s31)). That is the right shape for live, and about 3,500x smaller than NFM-S.
7. **Our biggest uncertainty is probably not sports randomness.** It is the feed lag and the picture offset. The fair test sets lag to zero, so its scores are optimistic for timing.
8. **Hype for this product:** 1-3 B sports language models, time-series foundation models, diffusion and flow generators, TabPFN at 60 M rows, offline RL, and live bandits.
9. **Reported frontier gains are usually small once read closely.** S2P2's "33%" is a likelihood ratio, and its next-event time error moves about 1-3% ([3](#s3)). A simple baseline matched or beat the neural event models in 6 of 10 long-horizon tasks ([1](#s1)).
10. **Expect 1-4 points of fair capture in total, mostly NBA and mostly at K=2.** NFL is already at 81.0 of a 97.1 hindsight. Those gain figures are my judgment, not measurements.

## What "fair capture" means here

No repo file defines the phrase "fair capture score". I took it to mean the GameValue fair test in `scripts/kaggle_gamevalue_holdset.py`: train on public seasons, then rank games on ESPN-format dev test nights, on the same decisions as the real `standby.Planner`. **Please correct me if you meant something else.**

Current results from `gv_models/*.holdset.json`:

| League, K | GameValue v1 | Real Planner | Hindsight | Decisions / nights |
|---|---|---|---|---|
| NFL, K=4 | 81.0 | 72.7 | 97.1 | 360 / 6 |
| NFL, K=2 | 62.6 | 54.4 | 84.1 | 701 / 6 |
| NBA, K=4 | 85.8 | 77.9 | 97.7 | 118 / 3 |
| NBA, K=2 | 74.6 | 64.1 | 88.3 | 240 / 4 |

Three limits matter:

- **Sample.** NBA K=4 rests on 3 nights. A 1-point gain there is noise, so every test below uses K=2 as the main number, a night-level bootstrap, and the public-season folds (Ring 1 in P-MIND).
- **No lag.** The script reads the feed with no lag at each decision. Its own header says so.
- **No mechanics.** A held game counts even if the sticks could not have reached it.

So every test below reports three versions:

| Name | Definition | Status |
|---|---|---|
| **FC** | The fair test as it is today | Exists |
| **FC-lag** | Same, with features read at a feed lag sampled from the measured table | About 1 hour to add |
| **FC-reach** | FC-lag, but a held game only earns value after its first entry the timing gate allows | About 1 hour to add |

## How this builds on P-MIND

I agree with its stack: small trees live, no LLM during games, code owns facts. Three updates:

| P-MIND said | Now | Why |
|---|---|---|
| NFL "true rate known" simulation caps K=4 near 71.6 | **That simulation is not a ceiling** | GameValue v1 scored 81.0 on a very similar 360-decision NFL test (night definitions differ slightly). The R² ceiling (0.099) may still hold, because capture rewards ranking the tail, not explaining variance |
| NFM-S at 25 M parameters is the sequence challenger | **Try about 15 K parameters first** | Source [31](#s31), and P-MIND's own ceiling argument. It trains in hours and needs no fp16 trick |
| `stay_one_more` and `value_now` are code | **Agree**, and extend the idea | Everything inside the look-ahead is a fact. Models only start at the feed head |

## Verdict by method

"Kaggle" assumes 60 M rows and 30 GPU-hours a week. "Mac" means live on the 8 GB M2. Quota figures are reported by third parties, not by Kaggle ([48](#s48), snippet-only).

### When: time to event

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| **Landmark supermodel, boosted Poisson hazard** ([26](#s26)) | Whole future hazard from any landmark via Poisson loss plus log-exposure. Beat Cox by a wide margin at 100 K subjects on a nonlinear case; simulations and one 312-patient dataset only | CPU only. 60 M rows are about 2.4 GB binned | Trees, ms per game | Directly gives one surface for where and when. Exact fit for our features | **Build (E1)** |
| SurvivalBoost ([27](#s27), abstract) | Proper scoring rule for competing risks in boosted trees; author-reported wins over 12 models | CPU | Trees | Same family as E1. Useful comparator if heads compete badly | Keep as fallback |
| BoXHED 2.0 ([28](#s28), abstract) | Boosted hazard with time-varying covariates, GPU support | GPU | Trees | Older, adds a dependency for no clear gain over LightGBM | Skip |
| Conformal survival ([11](#s11), abstract) | Lower bounds under censoring | Trivial | Trivial | Built for censoring. Our restarts are almost always observed, so plain conformal is enough | Skip the censoring part |

### When and what: neural point processes

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| **Compact event transformer, MBOFormer** ([31](#s31)) | 7,203 params, last 128 events, 0.479 ms median on one M2 thread, 39 kB. Macro-F1 at or above models up to 3.14 M on three markets; three seeds, no significance tests | 2-4 T4-h a run (estimate) | Fits easily | Sequence texture such as runs and tempo, at live-safe size | **Try (E4)** |
| Hawkes attention ([2](#s2)) | Per-type time kernels. RMSE moves 0.370 to 0.367 on Taxi, 0.621 to 0.559 on Amazon | Cheap | Fits | Borrow the kernel inside E4. Small gains | Borrow |
| S2P2, NeurIPS 2025 ([3](#s3)) | Likelihood ratio 1.33; next-event RMSE about 1-3% better; next-mark accuracy rank slightly behind NHP | Cheap to moderate | Likely fits | Its "33%" does not mean 33% better timing | Skip unless E4 passes |
| MoTPP ([4](#s4)) | Zero-inflated LogNormal mixture for tied timestamps. Abstract overclaims: best calibration on 6 of 10 logs, sharpest on none | Cheap | 0.03 ms a prefix | Tied same-second plays are common in play-by-play. Borrow the decoder | Borrow |
| HoTPP benchmark ([1](#s1)) | Simple baselines matched or beat MTPP in 6 of 10 long-horizon tasks; most methods collapse to frequent labels | n/a | n/a | Our 3-minute target is long-horizon. Every sequence model must beat the state table and GameValue | Rule to adopt |
| FIM-PP in-context ([5](#s5)) | 14.4 M synthetic events. Zero-shot mark accuracy 0.47 vs 0.91 on Taxi, fixed only after fine-tuning | Moderate | Likely heavy | Sports transitions are rule-like, the exact weakness it reports | Skip |
| LLM point processes ([6](#s6), [7](#s7)) | 1.1 B backbone; compared only against another LLM method | Heavy | No | Fails the Mac limit and has no small baseline | **Hype** |

### Generative: diffusion, flow, world models

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| UFM-TPP flow matching ([8](#s8)) | 150 K params, under 10 steps, 7-137 s to sample a dataset on an M3 Max; marks mixed; assumes future events are independent given context | Cheap | Sampling is the cost | We need integrals of hazards, not sampled futures. That independence assumption also erases runs | Skip |
| EdiTPP ([9](#s9), abstract) | Edit-based flow; no numbers on the page | Unknown | Unknown | Same argument | Skip |
| Neural Sabermetrics, LLM world model ([35](#s35), abstract) | About 3 B tokens, 64% next pitch, baselines unnamed in the abstract | Not feasible on free quota | No | P-MIND's cost math stands. No decision-level result | **Hype at our scale** |
| EventGPT ([36](#s36), abstract) | Player-conditioned soccer transformer over five seasons; no numbers on the page | Moderate | Possible | Nothing to compare yet | Watch |

### Foundation models

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| Chronos-2, TimesFM ([21](#s21), [22](#s22)) | 28-120 M params, patch-based on regular grids. Strong against seasonal naive; authors of the benchmark warn near-flat forecasts can score well | GPU | Too large for live | Our data is irregular events. Binning them throws away the signal | **Hype for this job** |
| Break-even study ([23](#s23)) | XGBoost beat zero-shot foundation models on 6 of 30 sets from as little as 21-2,768 samples; LoRA can hurt short series | n/a | n/a | With millions of rows we sit on the classical side | Evidence against |
| Noise titration ([24](#s24), abstract) | Zero-shot models act like context parrots; fail with high noise and regime shifts | n/a | n/a | NFL's target is about 90% noise | Evidence against |
| TabPFN-2.5 ([18](#s18), abstract) | Built for up to 50 K points; license not on the page | Slice only | Distilled only | 0.08% of 60 M rows. Finetuned v2 was also less stable under temporal shift ([17](#s17), abstract) | **Hype at 60 M rows** |
| RealMLP, TabM, TabArena ([15](#s15), abstract) | Neural ensembles caught trees under long budgets; authors flag validation overfit | 2-4 T4-h | Teacher only | Reasonable teacher members, not live models | Use in E5 |
| TabReD ([16](#s16), abstract) | Under time splits, GBDT and simple MLP-like models lead | n/a | n/a | Matches our whole-night split | Adopt the split rule |

### Calibrated uncertainty

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| Split conformal, Mondrian | Already in `timing_fit.py`: NBA 0.56%, NFL 1.43%, NHL 0.36% of restarts before the bound | Done | Done | Works. Keep | **Keep** |
| **Conformal risk control** ([13](#s13), abstract) | Controls the expected value of any monotone loss | None | Trivial | Set the margin on the cost of a mid-play entry, not on interval width | **Build (E2)** |
| **ECI online update** ([12](#s12), abstract; ICLR 2025) | Long-run coverage under dependence and shift; tighter than binary-error updates | None | One line per night | Corrects night-to-night feed drift | **Build (E2)** |
| Conformal decision theory ([14](#s14), abstract) | Calibrates a decision directly with no world-model assumption | None | Trivial | Same purpose as E2 for the shadow logs, and gives a long-run bound only | Use for shadow nights |
| ACI ([10](#s10), abstract) | Long-run coverage without exchangeability | None | Trivial | Superseded by ECI for us | Fallback |
| RetroAdj ([34](#s34)) | Coverage stays close to target through shifts, but O(n²) memory and time per step | None | Wasteful | Faster recovery we do not need nightly | Skip |
| Joint regions ([33](#s33), abstract) | Sets covering time and mark together, marginal guarantee | None | Trivial | Elegant for one game; our decision compares games | Skip for now |
| Scoring rules benchmark ([25](#s25), abstract) | Model rankings change with the proper score used | n/a | n/a | Report CRPS and hold-set capture, not RMSE alone | Adopt |
| Conformal order-fulfilment times ([32](#s32), abstract) | Distributional forecast plus cost-sensitive rule cut late-delivery misses by up to 75% (author-reported) | Cheap | Trivial | Closest applied analogue: a calibrated time distribution feeding an asymmetric decision | Evidence for E2 |

### Ranking and decisions

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| LambdaRank head | Already a trial in `kaggle_gamevalue.py` | Done | Done | It is already the cheap form of decision-focused learning ([29](#s29), [37](#s37)) | Keep |
| Fenchel-Young top-k ([30](#s30)) | Differentiable top-k with lower knapsack regret in a preprint | Moderate | n/a | Our K is small and LambdaRank exists. No evidence it beats that | Skip |
| Full decision-focused learning ([29](#s29)) | Two-stage wins when the model is well specified; several leading methods cost about 100x its training time | Heavy | n/a | Two-stage is the cheap baseline, and it has not failed yet | Skip |
| Decision-focused Whittle index ([38](#s38)) | Beat two-stage on sparse data, gap narrows with abundant data; scaled to 639 arms | Moderate | Index is trivial | The honest shape of "2 sticks, N games". Fair capture cannot see holding costs, so it needs the sim | Next after top 5 |

### Offline RL and bandits

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| Offline RL, horizon reduction ([39](#s39), abstract) | Standard methods saturate even at 1000x data. Long horizon is the blocker | Heavy | n/a | Same pattern as our PICK plateau. We have an exact oracle and simulator, so RL solves a problem we lack | **Hype here** |
| Live bandits, inverse propensity ([40](#s40)) | IPS breaks under deterministic logging | n/a | n/a | No exploration is allowed on live nights, so logged data has no propensities. The replay simulator gives exact counterfactuals | Skip |

### Teacher, student, fine-tuning

| Method | What the source shows | Kaggle | Mac | For RedZone | Verdict |
|---|---|---|---|---|---|
| **Out-of-fold distillation to trees** ([19](#s19), abstract) | Teacher to XGBoost at 96.5% of teacher AUC, 1.9 ms CPU. Beat tuned CatBoost by +0.011 under 21 features, +0.001 above. OOF labels are required | 4-8 T4-h | Student only | Soft labels should denoise a target that is about 90% noise. We have about 40 features, so expect a small gain | **Try (E5)** |
| LoRA on foundation models ([20](#s20), [23](#s23)) | Mixed. LoRA won on HR, lost on MeanBP. It can hurt short series | Cheap | n/a | Do not assume LoRA is the safe choice | Sub-test in E4 only |
| **Trees continued on new data (the tree version of fine-tuning)** | Frozen-backbone-plus-residual is the exPreCast pattern ([41](#s41)) | Minutes on CPU | Trees | Corrects nflverse-to-ESPN skew using only train-side dev nights | **Try (E3)** |

### What other fields and production systems do

| Field | Finding | What to take |
|---|---|---|
| Order books | Predictability tracks market structure: MCC 0.26 for large-tick vs 0.01 for small-tick stocks at the long horizon ([42](#s42)) | NBA vs NHL is the same effect. Spend by league |
| Order books | Machine-learning metrics miss whether a forecast is usable; the paper scores whole transactions instead ([42](#s42)) | Keep scoring by capture, not AUC |
| Weather | Residual diffusion on a frozen deterministic backbone: 38-47% of missed heavy-rain pixels recovered in two events, but ensembles stayed under-dispersed ([41](#s41)) | Keep the known part fixed and learn the residual. Calibrate after |
| Weather | NowcastNet combines an advection physics step with a learned residual out to 3 h (snippet-only, [43](#s43)) | Our "physics" is the look-ahead: known facts first, learned residual after |
| Weather | GenCast: more skill than ECMWF ENS on 97.4% of 1,320 targets ([44](#s44), abstract) | Impressive, but not a template at $0 |
| Betting | Sportsbooks suspend markets using sensor and video cues with confidence scores per cue (patent text, snippet-only, [45](#s45)) | The closest production analogue to our dead-ball gate: cues plus a confidence total |
| Broadcast | Prime's predictive features rely on Next Gen tracking, with no published accuracy or latency ([46](#s46)) | Not available to us. Do not copy |
| Highlights | WSC ingests the same stream viewers see, reportedly with whistle audio (snippet-only, [47](#s47)) | Audio for the dead-ball cue is worth a separate look |
| Claims | "85-90% accuracy" on micro-events appeared in one consumer article with no source | Ignore |

## Joint where-and-when design

One model family, four layers. Layers 0 and 3 are code.

```
Layer 0  FACTS (code)      Everything inside the look-ahead L is known. No model.

Layer 1  HAZARD SURFACE    For each game, at each new play past the feed head:
                           lambda_c(b | x, s), hazard of cause c in time bucket b.
                           Causes: T1, T2, T3, play_end, restart.
                           Buckets past the feed head, seconds:
                           0-5, 5-10, 10-15, 15-20, 20-30, 30-45, 45-60, 60-90,
                           90-120, 120-180, 180-240, 240+.

Layer 2  CALIBRATION       Value: isotonic on the integrated value.
                           Time: Mondrian conformal (kind x elapsed x lag bucket),
                           then risk-control margin, then ECI drift per night.

Layer 3  DECISION (code)   Score, gates, Rule #1 vetoes, league order.
```

**Same surface, two readings:**

- **Where:** `value_i(H) = sum_c w_c * integral(lambda_ic(u) du)` over the window H past the look-ahead. Taking H = 180 s gives the same quantity as `value_next_3m`, so it drops in behind `BrainOutput`.
- **When:** `S_i(t) = exp(-integral(lambda_i,restart))`. The restart bound is the `alpha` quantile of `S_i`, corrected by Layer 2.

**Choosing a game:** for each candidate entry time `tau`,

`U_i(tau) = known_i(tau) + P_i(legal entry at tau) * value_i(tau, tau + H)`

The code picks the top K by `max_tau U_i(tau)`, then applies vetoes. A game that is exciting but unreachable now scores low on its own, without a separate rule.

**Handling the delay:** the surface starts at the feed head, not at the picture. Train with the feed lag sampled from the measured table. At live time, average the surface over two or three lag draws. Keep GameValue v1 as the fallback; it remains the model that any challenger must beat.

## Hype list

| Claim | Plain answer |
|---|---|
| A sports language model will predict the next moment | 3 B tokens of training reached 64% next-pitch accuracy in the one paper I found. It does not fit the Mac, and no decision result exists |
| Time-series foundation models work out of the box | On regular grids, sometimes. Our data is not on a grid |
| Diffusion gives better uncertainty | Weather's own residual diffusion stayed under-dispersed. A calibrated hazard costs far less |
| TabPFN beats trees | Up to 50 K rows, by its authors' report |
| Offline RL will find better schedules | We already have an exact oracle. PICK plateaued at about 94% of it |
| Neural Hawkes successors are a leap | Typical time-error gain is about 1-3%, and baselines often tie |

## Ranked top 5 experiments

Expected gains are my judgment on an optimistic-but-possible basis. "Pass" requires whole-night forward folds, report mean and worst fold, night-bootstrap 95% lower bound above zero, 0 mid-play entries in the sim, and a logged tuning budget. Baselines are GameValue v1 and Timing v1 on the same decisions. No locked night is touched.

### E1. Joint hazard surface

- **Method:** LightGBM `objective=poisson`, `init_score=log(exposure seconds)`, one head per cause (T1, T2, T3, play_end, restart), with bucket and elapsed-in-state as features. Rows are moment times bucket, using the 12 buckets above and Q=5 landmarks per possession where moments are sparse. 300 trees, 31 leaves, `min_data_in_leaf=500`. Features v4, plus lag draw.
- **Expected gain:** FC K=2: NFL +0 to +2, NBA +1 to +3. FC-reach gains more because value arrives with a time attached. **Why:** today's target gives a touchdown in 10 s the same credit as one in 170 s, and the reach decision needs that difference.
- **Cost:** about 20 M rows per head (1.7 M moments x 12). One 12-hour Kaggle CPU session for five heads (estimate). 0 GPU-hours. About 12 engineer-hours. Live: about 600 KB; about 2.9 ms per changed game at 150 trees (scaled from P-MIND's figure, not measured).
- **Pass or fail:** Pass if the integrated value beats GameValue v1 on FC K=2 by at least 1.0 point on NBA and is not worse on NFL, while restarts before the bound stay at or below Timing v1 (NFL 1.43%, NBA 0.56%).

### E2. Risk-controlled safety margin

- **Method:** Keep Timing v1. Add a margin `m` seconds chosen by conformal risk control, with loss 1 when a restart lands inside the bound minus `m`, weighted 5x at high leverage. Group by kind x elapsed bucket x feed-lag bucket. Then run ECI nightly on shadow logs. Live updates may only tighten; loosening waits for the weekly refit.
- **Expected gain:** 2-6 s less padding in the widest buckets at the same miss rate (estimate), worth FC-reach K=2 of +1 to +3. **Why:** one global conformal offset pads every case for the worst feed lag.
- **Cost:** 4-6 engineer-hours, 0 GPU, a few microseconds live.
- **Pass or fail:** Pass if FC-reach K=2 rises at least 1.0 point on NFL and NBA with restarts before the bound no higher than Timing v1 and 0 mid-play entries in the sim.

### E3. Fine-tune trees on the ESPN format

- **Method:** Take GameValue v1 log-predictions as `init_score`. Add 30-60 trees with 7 leaves, learning rate 0.02, `min_data_in_leaf=100`, on the older train-side ESPN dev nights only (NFL 33 nights, 5,214 rows). Also test a pooled-league model as the prior. This is the tree version of fine-tuning.
- **Expected gain:** FC K=2: +0.5 to +2. **Why:** the fair test already found that source differences between nflverse and ESPN cost real points. This fixes what v4 missed, and only a residual is learned.
- **Cost:** Minutes on Kaggle CPU, about 3 engineer-hours, no change in live size.
- **Pass or fail:** Pass if FC K=2 improves at least 0.5 point on both NFL and NBA and at least 4 of the forward dev folds improve, with no fold losing more than 1 point.

### E4. Tiny event transformer

- **Method:** Causal transformer on the last 128 events of one game: width 24, 2 blocks, 4 heads, about 15 K parameters, per-type time kernels in place of position encoding ([2](#s2)), zero-inflated LogNormal mixture for time gaps ([4](#s4)), and the same 12-bucket hazard heads as E1. Test it alone and as a 16-number embedding fed to E1's trees. NBA first. Sub-test: pretrain pooled, then LoRA rank 4 per league against full fine-tuning.
- **Expected gain:** NBA FC K=2 +1 to +3. NFL 0 to +1. **Why:** runs, foul trouble and tempo are ordering effects that hand features only summarize, and NBA is the league with headroom.
- **Cost:** About 8.4 M events (estimate from game counts). 2-4 T4-h a run, at most 8 a week. About 16 engineer-hours including a numpy exporter. Live: 39-100 kB, about 0.5-1 ms per game update, unbatched.
- **Pass or fail:** Pass if the best variant beats the best tree model by at least 1.5 points on NBA FC K=2 in Ring 1 and at least 0.5 point on the ESPN dev nights, and fails if either bar is missed.

### E5. Denoised teacher to a same-size tree student

- **Method:** Teacher: an OOF ensemble, grouped by whole night, of LightGBM, RealMLP, TabM and E4's embedding. Student: LightGBM at the same size as v1 with target `0.8 x teacher + 0.2 x y`, also trying 0.5. Use it for `value_next_3m` first.
- **Expected gain:** FC K=2 +0.5 to +1.5. **Why:** with about 90% noise, soft targets may stop the trees chasing individual events. Source [19](#s19) warns that the gain is small above 21 features, and we have about 40.
- **Cost:** 4-8 T4-h, about 8 engineer-hours, no change in live size.
- **Pass or fail:** Pass if the student beats its own direct-trained twin by at least 0.5 point on FC K=2 in both leagues, and fails if the teacher itself does not beat the twin.

**Order:** E2 and E3 first (a day of work and no GPU), then E1, then E4, then E5. Add FC-lag and FC-reach before any of them.

**Next up after these:** a Whittle-index policy over the hazard surface for the two sticks ([38](#s38)). It needs the sim, because fair capture cannot see switching costs.

## Not verified

- Every expected gain above. No model was trained for this report, and the Mac was not used for training.
- Kaggle's real quota. Kaggle does not publish it.
- Whether the 60 M rows are real distinct moments. P-MIND's point stands that many rows repeat the same games.
- The E1 and E4 latency figures are scaled, not measured.
- "Fair capture" is my reading of the term (see above).

## Bibliography

Status tags: **[opened]** means I read the page. **[abstract]** means I opened the page and it held only the abstract. **[snippet-only]** means I saw search text only. "Author-reported" applies to almost every result. Web text was treated as data.

1. <a id="s1"></a>Karpukhin et al., HoTPP Benchmark, arXiv 2406.14341, v4 Aug 2025. https://arxiv.org/html/2406.14341 [opened]
2. <a id="s2"></a>Tan et al., From Hawkes Processes to Attention, arXiv 2601.09220. https://arxiv.org/html/2601.09220v2 [opened]
3. <a id="s3"></a>Chang et al., Deep Continuous-Time State-Space Models for Marked Event Sequences (S2P2), NeurIPS 2025. https://arxiv.org/html/2412.19634v2 [opened]
4. <a id="s4"></a>MoTPP, Probabilistic Forecasting of Business Process Executions, arXiv 2609.21382. https://arxiv.org/html/2609.21382v1 [opened]
5. <a id="s5"></a>FIM-PP, In-Context Learning of TPPs with Foundation Inference Models, arXiv 2509.24762. https://arxiv.org/html/2509.24762 [opened]
6. <a id="s6"></a>TPP-TAL, Enhancing Temporal Awareness in LLMs for TPPs, arXiv 2601.00845. https://arxiv.org/html/2601.00845 [opened]
7. <a id="s7"></a>TPP-LLM, arXiv 2410.02062. https://arxiv.org/abs/2410.02062 [abstract]
8. <a id="s8"></a>UFM-TPP, Unified Flow Matching, arXiv 2508.04843. https://arxiv.org/html/2508.04843 [opened]
9. <a id="s9"></a>EdiTPP, Edit-Based Flow Matching, arXiv 2510.06050. https://arxiv.org/abs/2510.06050 [abstract]
10. <a id="s10"></a>Gibbs, Candès, Adaptive Conformal Inference, arXiv 2106.00170. https://arxiv.org/abs/2106.00170 [abstract]
11. <a id="s11"></a>Gui, Hore, Ren, Barber, Conformalized Survival Analysis with Adaptive Cutoffs, Biometrika 2024, arXiv 2211.01227. https://arxiv.org/abs/2211.01227 [abstract]
12. <a id="s12"></a>Error-quantified Conformal Inference, ICLR 2025, arXiv 2502.00818. https://arxiv.org/abs/2502.00818 [abstract]
13. <a id="s13"></a>Angelopoulos et al., Conformal Risk Control, ICLR 2024, arXiv 2208.02814. https://arxiv.org/abs/2208.02814 [abstract]
14. <a id="s14"></a>Lekeufack et al., Conformal Decision Theory, arXiv 2310.05921. https://arxiv.org/abs/2310.05921 [abstract]
15. <a id="s15"></a>TabArena, NeurIPS 2025 D&B, arXiv 2506.16791. https://arxiv.org/abs/2506.16791 [abstract]
16. <a id="s16"></a>TabReD, arXiv 2406.19380. https://arxiv.org/abs/2406.19380 [abstract]
17. <a id="s17"></a>Rubachev et al., On Finetuning Tabular Foundation Models, arXiv 2506.08982. https://arxiv.org/abs/2506.08982 [abstract]
18. <a id="s18"></a>TabPFN-2.5, arXiv 2511.08667. https://arxiv.org/abs/2511.08667 [abstract]
19. <a id="s19"></a>Pocket Foundation Models, arXiv 2605.18654. https://arxiv.org/abs/2605.18654 [abstract]
20. <a id="s20"></a>Low-Rank Adaptation of Time Series Foundational Models, arXiv 2405.10216. https://arxiv.org/html/2405.10216v1 [opened]
21. <a id="s21"></a>Chronos-2, arXiv 2510.15821. https://arxiv.org/html/2510.15821v1 [opened]
22. <a id="s22"></a>It's TIME: next-generation forecasting benchmarks, arXiv 2602.12147. https://arxiv.org/html/2602.12147v2 [opened]
23. <a id="s23"></a>Tan Jerome, Simon, When Do Foundation Models Pay Off?, arXiv 2607.04919. https://arxiv.org/abs/2607.04919 [opened, PDF text of abstract and introduction]
24. <a id="s24"></a>Noise Titration, arXiv 2603.22219. https://arxiv.org/abs/2603.22219 [abstract]
25. <a id="s25"></a>ScoringBench, arXiv 2603.29928. https://arxiv.org/abs/2603.29928 [abstract]
26. <a id="s26"></a>Sandqvist, Event history analysis via landmarking supermodels and boosted trees, arXiv 2601.17605. https://arxiv.org/html/2601.17605v1 [opened]
27. <a id="s27"></a>Alberge et al., Survival Models: Proper Scoring Rule and Stochastic Optimization with Competing Risks (SurvivalBoost), AISTATS 2025, arXiv 2410.16765. https://arxiv.org/abs/2410.16765 [abstract]
28. <a id="s28"></a>BoXHED 2.0, arXiv 2103.12591. https://arxiv.org/abs/2103.12591 [abstract]
29. <a id="s29"></a>Mandi et al., Decision-Focused Learning survey, arXiv 2307.13565. https://arxiv.org/html/2307.13565 [opened, results section]
30. <a id="s30"></a>Vivier-Ardisson et al., Differentiable Knapsack and Top-k Operators via DP, arXiv 2601.21775. https://arxiv.org/abs/2601.21775 [opened, PDF text, partial]
31. <a id="s31"></a>Schaurecker et al., Event History Over Scale (MBOFormer), arXiv 2610.02917. https://arxiv.org/html/2610.02917v1 [opened]
32. <a id="s32"></a>Conformal Predictive Distributions for Order Fulfillment Time, arXiv 2505.17340. https://arxiv.org/abs/2505.17340 [abstract]
33. <a id="s33"></a>Dheur et al., Conformal Joint Prediction Regions for Neural MTPPs, arXiv 2401.04612. https://arxiv.org/abs/2401.04612 [abstract]
34. <a id="s34"></a>RetroAdj, Online Conformal Inference with Retrospective Adjustment, arXiv 2511.04275. https://arxiv.org/html/2511.04275 [opened]
35. <a id="s35"></a>Neural Sabermetrics with World Model, arXiv 2602.07030. https://arxiv.org/abs/2602.07030 [abstract]
36. <a id="s36"></a>EventGPT, arXiv 2512.17266. https://arxiv.org/abs/2512.17266 [abstract]
37. <a id="s37"></a>Mandi et al., Decision-Focused Learning: Through the Lens of Learning to Rank, ICML 2022. https://proceedings.mlr.press/v162/mandi22a/mandi22a.pdf [opened, PDF text, partial]
38. <a id="s38"></a>Wang et al., Scalable Decision-Focused Learning in Restless Multi-Armed Bandits, AAAI 2023, arXiv 2202.00916. https://ar5iv.arxiv.org/html/2202.00916 [opened]
39. <a id="s39"></a>Park et al., Horizon Reduction Makes RL Scalable, NeurIPS 2025, arXiv 2506.04168. https://arxiv.org/abs/2506.04168 [abstract]
40. <a id="s40"></a>Off-Policy Evaluation for Ranking Policies under Deterministic Logging Policies, ICLR 2026, arXiv 2603.21485. https://arxiv.org/html/2603.21485v1 [opened]
41. <a id="s41"></a>exPreCast-ENS, Diffusion-Based Refinement for Probabilistic Nowcasting, arXiv 2608.30205. https://arxiv.org/html/2608.30205v1 [opened]
42. <a id="s42"></a>Briola, Bartolucci, Aste, Deep limit order book forecasting: a microstructural guide, Quantitative Finance 2025. https://pmc.ncbi.nlm.nih.gov/articles/PMC12315853/ [opened, about 55% of the page]
43. <a id="s43"></a>Zhang et al., NowcastNet, Nature 2023. https://www.nature.com/articles/s41586-023-06184-4 [snippet-only; Nature redirected to login, PMC showed a bot check that I did not try to pass]
44. <a id="s44"></a>Price et al., GenCast, arXiv 2312.15796. https://arxiv.org/abs/2312.15796 [abstract]
45. <a id="s45"></a>US patent 11,417,175, Video analysis for visual indicator of market suspension. https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11417175 [snippet-only; the PDF is a scan and unreadable]
46. <a id="s46"></a>Amazon, AI features on Prime Video's Thursday Night Football. https://www.aboutamazon.com/news/aws/prime-video-thursday-night-football-next-gen-stats-ai-features [opened; vendor source]
47. <a id="s47"></a>WSC Sports coverage (Sportcal, IBC, a German tools review). https://www.sportcal.com/features/wsc-sports-how-ai-has-led-the-highlights-revolution/ [snippet-only]
48. <a id="s48"></a>Free Cloud GPUs in 2026 (Kaggle quota summary). https://gpuperhour.com/blog/free-cloud-gpus-and-credits [snippet-only]
</content>
