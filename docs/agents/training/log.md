# Training research (shared by the two training agents; append-only)

Rules: append new entries at the END with `cat >>`, never rewrite or reorder the file. One entry per finding:
`### YYYY-MM-DD agent-N: title` then: what it is, evidence (verified link, date), cost in $ and GPU hours, fits the
8 GB Mac? (RAM to run), what to do in this repo (paths), priority (now / P2 / later). Reviewed by the coordinator
before P2 training starts; reviewed items are marked in docs/PLAN.md.


### 2026-11-08 agent-2: Play-by-play as a token stream is real, but only as a generator, not a switcher
What: GPT-style next-event models over sports event streams. ScoutGPT (Hong et al., arXiv 2603.15212, v1 2026-03-16, v2 2026-08-04, K League football, NanoGPT-based, size not stated) uses Monte Carlo rollouts to simulate counterfactuals. "Neural Sabermetrics with World Model" (arXiv 2602.07030, 2026-02-02): one LLM continuously pretrained on >10 yrs MLB data (~7M pitch sequences, ~3B tokens); ~64% next-pitch and ~78% swing accuracy (author-reported). Evidence: both abs pages fetched 2026-11-08. No basketball/NFL equivalent found.
Cost: pretraining at ~3B tokens is not $0-friendly; a tiny NanoGPT-size model on our own PBP is a few Kaggle GPU hours (estimate, unverified). Fits 8 GB Mac: a <100M param model, yes (<0.5 GB).
Repo: not a switcher. Possible later use: next-event model as a win-probability/"what happens next" feature or to sample futures in the simulator. Tokenize PBP in redzone/lab (new file), whole-night splits only. Skip for the switching decision.
Priority: later (P3 research spike). Do not start before the GBDT ranker (see next entries) has a locked-set number.

### 2026-11-08 agent-2: Mamba/state-space event models: promising on paper, no sports proof
What: Mamba Hawkes Process (arXiv 2407.05302, v1 2024-07-07, v2 revised 2026-10-29) integrates selective SSMs with temporal point processes, linear in sequence length, claims gains on long event sequences (author-reported, general datasets, not sports). SportMamba (arXiv 2506.03335, Jun 2025) is object tracking, irrelevant. A football-Mamba post is a student blog (opinion; snippet-only, not verified).
Cost: $0 on Kaggle T4; small. Fits Mac: yes if exported, but Mamba kernels on MPS/CoreML are rough (inference, not verified).
Repo: nothing to adopt now. A long-context night-level model is attractive only for modelling inter-game state across a whole night; a plain GBDT on rolling features (noggin/pick.py) is the baseline to beat.
Priority: later. Marked UNPROVEN for sports.

### 2026-11-08 agent-2: Tabular foundation model TabPFN-2.5 as a cheap second opinion for the ranker
What: Prior Labs TabPFN-2.5 (arXiv 2511.08667, 2025-11-11, v2 2026-02-05): in-context tabular model for up to 50k rows / 2k features; claims to beat tuned trees on TabArena and 100% win rate vs default XGBoost on <=10k rows classification (author-reported; no independent leaderboard found, search snippet for the TabPFN-3 successor is vendor-only and snippet-only). Has a distillation engine to compact MLP/tree ensembles; abstract does not state the license (CHECK before use; weights historically carry a non-commercial-style licence in earlier versions, unverified here).
Cost: $0; GPU needed for fit/predict on larger data (Kaggle T4); distilled student runs on the Mac.
Repo: run it offline as a challenger against LightGBM on the per-game features in scripts/train_noggin_pick_dagger.py (same night-grouped folds). Ship only the distilled tree/MLP if licence allows. Caveat: it is a pointwise classifier/regressor; use for value-to-go labels, not directly lambdarank.
Priority: P2 (one Kaggle notebook, about 2 hours of work).

### 2026-11-08 agent-2: Learning-to-rank: trees still the safe default, neural only if labels are scarce
What: Evidence is mixed. "Which Tricks Are Important for Learning to Rank?" (arXiv 2204.01500, 2022 [old]) finds fairly-tuned GBDT still beats neural rankers; Hou et al. (arXiv 2308.00177, 2023 [old], v3 2024) find pretrained deep LTR wins under label scarcity; Lutz et al. (arXiv 2507.20753, Jul 2025) find a simple DNN beat a production LambdaMART on revenue/clicks in e-commerce (author-reported, different domain). All via search snippets; not opened.
Cost: LightGBM lambdarank is CPU, minutes, a few MB. Fits Mac trivially.
Repo: keep GBDT lambdarank (group = decision step, items = live games, graded labels from oracle value-to-go) as the primary; add a small MLP ranker only as a challenger. Report mean and worst forward-chaining fold, and NDCG@1 / regret vs oracle (redzone/lab/oracle.py), not accuracy.
Priority: now (cheap, matches policy-learning.md rank 2).

### 2026-11-08 agent-2: Offline RL 2025: simpler, fairer baselines; still not worth it over oracle imitation
What: "A Clean Slate for Offline RL" (Tarasov-style unification, arXiv 2504.11453, 2025-04-15) argues comparisons are unfair because of undocumented online tuning; releases single-file Unifloral and TD3-AWR / MoBRAC claimed to beat established baselines (author-reported). "Simple Ingredients" (arXiv 2403.13097, 2024 [old]): bigger actor/critic nets help most offline RL algorithms. No 2025 critic-free scaling result found (search came up empty; do not assume one exists).
Cost: continuous-control benchmarks, GPU hours on Kaggle; irrelevant action spaces.
Repo: agrees with policy-learning.md. Our replay simulator gives exact counterfactuals, so imitation + DAgger + OPE with night-bootstrap CIs (redzone/lab/offpolicy.py) suffices. Take the lesson only: report the tuning budget used on every model comparison.
Priority: later; adopt the "tuning budget disclosed" rule now (costs nothing).

### 2026-11-08 agent-2: Synthetic data from our replay simulator: use it, but leak-proof
What: The full-night replay simulator can generate many decision trajectories (DAgger rollouts, perturbed timing, shuffled game orderings of the same night, dropped feeds). This is data augmentation of the scenario, not of the sports events. Supporting method: SWE-Gym style environment-with-checker training (arXiv 2412.21139, ICML 2025) shows verified-environment rollouts are the useful synthetic signal; it is the same pattern as DAgger in our simulator.
Risk: augmentations of the same night inherit its identity; they must stay in the train side of the night split. Leakage evidence from sports ML: chronological/grouped validation is the stated best practice (arXiv 2403.07669 soccer, snippet-only; 2025 MDPI leakage paper snippet-only, links not opened).
Cost: CPU on the Mac is light-ish but the CLAUDE.md says no heavy jobs: run generation on Kaggle CPU. Output sizes should be small (parquet, a few hundred MB).
Repo: add a `source_night_id` column to every synthetic row and a CI test (next to redzone/lab/splits.py) that fails if any night id appears in both train and locked set; dedupe near-identical windows (hash of game state + play ids) before training so repeated segments do not leak across folds.
Priority: now (cheap guard), generation P2.

### 2026-11-08 agent-2: Experiment tracking at $0: local MLflow or a git-tracked results log, not hosted W&B
What: MLflow open source is free but self-hosted; W&B free tier reported as 5 seats, 5 GB storage (vendor comparison blogs, snippet-only, may be stale; verify on vendor site before relying). Pro tiers cost money (reported $50-60/mo), so avoid.
Cost: $0. Fits Mac: MLflow file store is tiny, but do not run the server on the live Mac; run it in the Kaggle notebook and export.
Repo: simplest option: each training run appends one JSON line (git sha, data version eval-data-v1, night-split seed, fold scores, tuning budget, flag name) to a tracked results file under docs/ or reports/, and CI/check_memory.py checks it exists for any PR that flips a default. Use MLflow only if run count exceeds about 50.
Priority: now (about 1 hour).

### 2026-11-08 agent-2: Free GPU budget is about 30 GPU-hours per week on Kaggle, not documented officially
What: Third-party 2026 guides say about 30 GPU-hours/week (one P100 16 GB or 2x T4), 12 h sessions, TPU 9 h sessions with ~20 h/week; one review (Sept 2026) says Kaggle's own docs state no quota and it floats. TPU generation conflicts (v3-8 vs v5e-8). Snippet-only; not opened from Kaggle.
Cost: $0. Fits Mac: n/a (offline).
Repo: plan P2 around at most ~20 GPU-hours/week with checkpointing every 30 min and resume (scripts/ for Kaggle runs per docs/memory/runbooks Kaggle run). Check the real figure in Nick's Kaggle account page and write it into docs/compute/ (already untracked there).
Priority: now (1 minute check by Nick).

### 2026-11-08 agent-2: Test-time compute helps small models only where an answer is checkable
What: Liu et al. (arXiv 2502.06703, 2025-02-10) show compute-optimal test-time scaling lets a 1B model beat a 405B model on MATH-500 (abstract, fetched). T1 (Kang et al., ICLR 2026, mlanthology page fetched) shows small models are weak verifiers on memorization-heavy steps, so tools do the checking first and the small model verifies last. All results are math; verifier quality is the bottleneck.
Cost: inference-time only; on an 8 GB Mac, N samples multiply latency (not measured here).
Repo: for the brain, the "checker" is already deterministic: the shield and the simulator replay. Realistic pattern: model proposes K candidate switches, shield filters illegal ones, a cheap scorer picks. Do not use sampling loops on the live path; use them offline for label generation and LLM-judge labelling (docs/distill.md) with gold-label gating.
Priority: P2 offline only; live path later.

### 2026-11-08 agent-2: Conformal/risk control wrapper stays outside the model; world-model hype is not evidence
What: Brill, Yurko & Wyner (2024) and Pipping-Gamon & Wyner (2026) per leverage-excitement.md show WP estimates are noisy and need slack; combine with conformal risk control in policy-learning.md. RLVR-World (arXiv 2505.13934, 2025) and StatePlay (arXiv 2607.26754, Jul 2026) are game/text world models, not sports decision tools (search snippets only; not opened).
Cost: conformal calibration is CPU minutes.
Repo: calibrate "safe to leave / land" heads on whole-night calibration nights and set the abstain threshold so high-leverage mid-play leaves stay at 0 on the locked set; the deterministic shield remains the final gate. Fix the league-key blind spot in redzone/lab/leverage.py noted in leverage-excitement.md before trusting any number.
Priority: now for the leverage fix and calibration split; world models: ignore.

### 2026-11-08 agent-2: "Fix our mistakes and try new code": realistic ceiling is a sandboxed patch proposer with test verifier, not an autonomous coder
What: SWE-Gym (arXiv 2412.21139, 2025-06 v2, ICML 2025, fetched): 2,438 Python tasks with tests; fine-tuning a 32B Qwen2.5-Coder on 491 trajectories gave up to +19 points; with trained verifiers 32.0% SWE-Bench Verified / 26.0% Lite; 7B self-improvement reached 10.0% on Lite. Search snippets (unverified, mixed sources): Qwen3-Coder-30B-A3B ~33.2% bash-only SWE-bench Verified in one eval; Devstral Small 53.6% vs 46.8% in other guides (conflict); an 8 GB claim for the 30B MoE rests on one community post (unverified). Small local coders are well below frontier agents; a 30B MoE does not fit 8 GB alongside the app.
Cost: local 30B: no. Cloud open models on Kaggle for generation: free hours but slow; frontier-model work (this Claude session) remains the main code author.
Repo (safe loop): (1) the model may only emit a patch to a scratch branch targeting integration/next; (2) CI-style gate runs pytest, ruff, scripts/ci_scorecard.py and release_gate.py in a clean sandbox off the Mac; (3) auto-reject if entries_mid_play, plays_missed_entry or dead_air_pct rise, any shield file or CLAUDE.md/secrets is touched; (4) human merge only. Start with narrow scopes: parameter/threshold proposals in config and new feature code behind a flag (ships OFF until the locked-set gate passes).
Priority: P2 for the harness (about 1 day of work), later for any local coder model.

### 2026-11-08 agent-1: Honest answer to "billions of parameters on a basic Mac"
What it is: on the 8 GB M2 with a ~2 GB RSS budget, 4-bit weights cost roughly 0.5 GB per billion params (inference), so ~2-3B dense is the ceiling inside the budget; a 4B model measured at 3.9-4.2 GB peak (entry below) already overshoots it. "Billions" is therefore true only for 1-3B resident, or for bigger models run offline/in the cloud (teacher). Training at $0 is fine-tuning only; pre-training any billions-scale model is not $0 (PLAN.md section 7 says the same).
Evidence: peak-memory measurement in the Qwen3.5-4B entry below; 4B bf16 LoRA needs ~10 GB VRAM per Unsloth (https://unsloth.ai/docs/models/qwen3.5/fine-tune, opened 2026-11-08). The 0.5 GB/B rule is my arithmetic (inference), not a source.
Cost: $0. Fits 8 GB Mac? Only <=~2B inside 2 GB; 4B needs ~4 GB (would pressure the live app).
Recommended split (inference/design): (1) Tier A, always on, tiny: LightGBM/tree models (existing `redzone/noggin/models/*.npz`, 0.1-0.3 MB each) make every timing/pick decision; (2) Tier B, small LLM 1-2B 4-bit, called sparingly (storyline text, explanations, code help on request), never in the dead-ball decision path; (3) Tier C, big teacher in the cloud/Kaggle, offline only (labels and distillation). Shield stays outside all models.
Repo: docs/PLAN.md section 7 Q2: recommend answering "fine-tune ~1-2B, run 4-bit; keep decisions in trees; big model offline". Priority: now (answer for Nick).

### 2026-11-08 agent-1: Base model shortlist 0.5-4B (verified on Hugging Face API, 2026-11-08)
What it is: Qwen3.5 small ladder (Qwen/Qwen3.5-0.8B-Base, 2B, 4B, plus post-trained Qwen/Qwen3.5-2B and Qwen/Qwen3.5-4B), created 2026-02-27/28; Apache-2.0 (4B card). Hybrid architecture: Gated DeltaNet linear-attention layers plus periodic full attention, 262k native context, vision encoder included (4B card: 4B language params, 5B total in HF metadata). Gemma 4 edge models google/gemma-4-E2B and E4B (+ -it, + QAT variants up to 2026-06-05), Apache-2.0 (E4B card): E4B is 4.5B "effective" but 8B with per-layer embeddings, 128k context. Qwen3 (older, 1.7B) is what docs/distill.md uses today.
Evidence: https://huggingface.co/api/models?author=Qwen (listing), https://huggingface.co/Qwen/Qwen3.5-4B (MMLU-Pro 79.1, IFEval 89.8, LiveCodeBench v6 55.8, self-reported), https://huggingface.co/google/gemma-4-E4B-it (MMLU-Pro 69.4, LiveCodeBench v6 52.0, self-reported). Benchmarks are vendor numbers, not re-run. Newer Qwen3.8 exists on the Qwen org (27B and up seen; I saw no small Qwen3.8 sibling, unverified).
Cost: $0. Fits 8 GB Mac? 0.8B/2B yes; Gemma E2B is MLX-ready (mlx-community/gemma-4-e2b-it-4bit and QAT 4-bit, 2026-04/06) but its embedding tables inflate file size vs "effective" params, so measure RSS before trusting 2B. 4B: see next entry.
Repo: candidate students for `agent/distill/run_students.py` (currently Qwen3-1.7B-4bit): test Qwen3.5-2B and Gemma-4-E2B against it on the gold sets. Priority: P2.

### 2026-11-08 agent-1: Measured speed of a 4B 4-bit model on an M2 8 GB
What it is: Qwen3.5-4B, 4-bit, M2 10-core GPU, 8 GB, oMLX v0.2.7, macOS 26.3.1 (benchmark dated 2026-03-11): generation 32.2 tok/s at 1k context, 30.4 tok/s at 4k; prompt processing ~200 tok/s; peak memory 3.9 GB (1k) / 4.2 GB (4k); time to first token 20.5 s at 4k.
Evidence: https://omlx.ai/benchmarks/hhfwaa55 (opened; community-submitted, single machine, not mlx-lm). 4-bit file ~2.9 GB on disk per a mirror (search snippet only, not opened).
Cost: $0. Fits 8 GB Mac? No for the ~2 GB budget (4 GB peak, runs fine only if the live app leaves headroom). A 20 s first token on a 4k prompt means prompts must stay short or be cached.
Repo: do not put a 4B in any real-time path; at ~200 tok/s prefill, a 500-token game-state prompt costs ~2.5 s (my arithmetic). Use trees for the dead-ball decision. Priority: now (sets the size ceiling). Not measured for 1-2B on this Mac: run a 10-minute `mlx_lm` benchmark of 2B before choosing.

### 2026-11-08 agent-1: Fine-tuning on Kaggle free GPUs: what fits
What it is: Unsloth minimum QLoRA VRAM: 3B 3.5 GB, 7B 5 GB, 8B 6 GB, 9B 6.5 GB, 14B 8.5 GB, 27B 22 GB, 32B 26 GB (absolute minimums, per Unsloth page). Kaggle: 2x T4 (16 GB each) or one P100 (16 GB); weekly quota reported as ~30 GPU h shared across all notebooks, 12 h GPU session limit (several sources; one 2026-09 source says the quota is NOT published in Kaggle docs, so check the account page). Inference from the table: any 1-4B LoRA or 7-14B QLoRA fits one 16 GB card; 27B QLoRA does not fit one T4 comfortably.
Evidence: https://unsloth.ai/docs/get-started/fine-tuning-for-beginners/unsloth-requirements (opened); Kaggle quota only from search snippets (gpuperhour.com, aimultiple, others), snippet-only. The Kaggle docs page https://www.kaggle.com/docs/efficient-gpu-usage returned no numbers when opened.
Cost: $0, ~30 h/week. No tokens/s measured: I found no trustworthy published T4 QLoRA throughput for 2026 (two blog figures had no GPU or source; not used). Plan on measuring the first epoch, then budget.
Fits 8 GB Mac? Training never runs on the Mac.
Repo: `agent/kaggle/teacher_distill.py`: log tokens/s and GPU h per job to calibrate; keep jobs resumable (12 h session). Priority: P2.

### 2026-11-08 agent-1: Qwen3.5 fine-tune gotchas (Unsloth guide)
What it is: Unsloth says do NOT QLoRA (4-bit) Qwen3.5 (larger-than-normal quantization error); use bf16 LoRA. bf16 LoRA VRAM: 0.8B ~3 GB, 2B ~5 GB, 4B ~10 GB, 9B ~22 GB, 27B ~56 GB. Needs transformers v5; Qwen3.5 uses custom Mamba/Triton kernels, slow to compile "especially on T4"; keep >=75% reasoning-style data to preserve reasoning; vLLM 0.16.0 does not support Qwen3.5; export covers GGUF, not MLX.
Evidence: https://unsloth.ai/docs/models/qwen3.5/fine-tune (opened 2026-11-08). Free notebooks listed are Colab (0.8B/2B/4B, 4B GRPO); no Kaggle notebook mentioned. Whether T4 (no native bf16, inference from hardware) runs bf16 LoRA well is untested; the guide says only that T4 compiles slowly.
Cost: $0. Fits Mac? Resulting 0.8B/2B adapters yes after merge and conversion (MLX path unverified for Qwen3.5 hybrid layers).
Repo: before swapping the student base in `agent/kaggle/run_students.py`, run a 30-minute smoke test: LoRA 2B on T4, merge, convert with `agent/train/peft_to_mlx.py`, load in mlx-lm. If MLX conversion fails, stay on Qwen3-1.7B (dense, already working) or Gemma-4-E2B. Priority: P2.

### 2026-11-08 agent-1: Teacher choice: big open models for Kaggle vs free APIs
What it is: docs/distill.md runs Qwen3-32B-AWQ on vLLM on 2x T4. Newer open options: Qwen3.8-27B (dense, Apache-2.0, Aug 2026 per card; claims LiveCodeBench v6 90.3, SWE-bench Pro 61.7, vendor-reported) and Gemma-4-31B-it (QAT W4A16 / q4_0 variants exist, 2026-05/06). 27-31B at 4-bit is ~22-26 GB VRAM by Unsloth's QLoRA table (a proxy for weight size, inference), so it needs both T4s; slow but feasible for labelling.
Evidence: https://huggingface.co/Qwen/Qwen3.8-27B (opened), https://huggingface.co/api/models?author=google&search=gemma (opened). vLLM compatibility for the Qwen3.8 architecture is not verified (vLLM 0.16.0 lacked Qwen3.5 support per Unsloth).
Cost: $0, many GPU hours (measure first). Fits Mac? n/a (cloud only).
Repo: `agent/kaggle/run_teacher.py`: add Qwen3.8-27B and Gemma-4-31B as fallbacks after a throughput test; use llama.cpp GGUF if vLLM breaks. Open licences (Apache-2.0) permit distillation; this is the clean path. Priority: P2.

### 2026-11-08 agent-1: Free-tier API teachers: licence risk, use only for non-sensitive spot labels
What it is: Google's Gemini API / AI Studio terms (page now at aistudio.google.com/docs/terms): unpaid services let Google use inputs and outputs to improve products, humans may read them, and you must not use the services to develop competing models or extract/replicate them. So distilling Gemini free-tier outputs into Noggin is a terms risk; also the repo is public and Nick's data should not go there.
Evidence: https://aistudio.google.com/docs/terms (opened 2026-11-08). Free-tier rate limits: the official page lists no per-model numbers and says to look in AI Studio (https://aistudio.google.com/docs/rate-limits, opened); blog numbers conflict and are snippet-only, not used. Terms of other providers (OpenAI, Anthropic, Mistral, Groq, OpenRouter free) NOT checked.
Cost: $0 but unreliable and not cleared. Fits Mac? n/a.
Repo: keep the open-weight teacher on Kaggle as the main label source; if any API is used, only for a small gold-check set and only after the terms of that provider are read. Do not add API keys to the repo. Priority: P2 (decision: avoid by default).

### 2026-11-08 agent-1: When LightGBM beats an LLM for timing decisions
What it is: dead-ball timing, game ranking and "safe to leave" are low-dimensional numeric problems (score margin, clock, leverage, play type flags). Evidence says boosted trees win on numeric tabular data: PHBench (arXiv 2605.02974, 2026) reports gradient-boosted models much better than zero-shot LLMs on purely numeric inputs (zero-shot only, not fine-tuned); a few-shot study (arXiv 2411.04324) finds a tuned LightGBM competes with or beats TabLLM; LLMs win mostly where column text carries meaning. I found no 2026 head-to-head of a fine-tuned small LLM vs tuned LightGBM, so test it here (inference).
Evidence: snippets from search only for both papers; arXiv 2411.04324 and 2605.02974 not opened (snippet-only). Project-side evidence is stronger: docs/research/2026-11-08/policy-learning.md ranks oracle imitation with gradient-boosted trees first, and the existing trees are 0.1-0.3 MB.
Cost: CPU minutes, $0; latency microseconds vs ~2.5 s+ for a 4B prefill (calc above). Fits Mac: yes, trivially.
Repo: keep `noggin/pick.py`, `timing.py`, `predict.py` as trees; route the LLM only to text tasks (storylines, explanations, code help). Add a benchmark script comparing tree vs distilled-LLM on the locked set; the LLM must beat the tree on the 95% gate to enter any decision path. Priority: now.

### 2026-11-08 agent-1: Recommended P2 plan at $0 (ordered, with budgets)
What it is: (1) keep trees as the decision core and widen them first (cheap wins: DAgger, ranking, calibration); (2) distill the cloud teacher's labels (storylines, rationale text, code-help Q&A) into one 1.7-2B student, LoRA, 4-bit on Mac; (3) optional: run a 4B only as an on-demand "ask the brain" tool, loaded while the live app is idle, unloaded after; (4) compare students Qwen3-1.7B (current), Qwen3.5-2B, Gemma-4-E2B on gold sets, pick by gate, not by benchmarks.
Budget (inference, to be confirmed by measurement): teacher labelling 10-20 Kaggle GPU h; each student LoRA run 1-3 h; leaves most of the ~30 h/week free. Mac RSS 1.2-2 GB for a 2B at 4-bit plus KV cache; 4B ~4 GB, outside budget.
Evidence: composed from the entries above; nothing here is new measurement.
Repo: update docs/distill.md "students" section after the smoke test entry; no code changes needed now. Priority: P2. Next 2-minute action: run `mlx_lm.generate` with mlx-community/gemma-4-e2b-it-4bit on the Mac and note RSS and tok/s.
