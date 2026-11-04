# 04. Architecture, distillation and training recipe (Nov 3, 2026)

Scope: how to get "billions of parameters of intelligence" into ~1.5-2 GB on the 8 GB M2 Air, trained for $0 on
Kaggle's 2x T4 (30 h/week), without breaking the 95%-on-locked-nights gate. Sources: the repo
(`agent/distill/*`, `agent/kaggle/*`, `agent/train/*`, `agent/llm.py` on `origin/claude/distill-teacher-student`,
`docs/brain3/REPORT.md`, `docs/reasoner.md`, `docs/handoff.md`) plus web checks dated Nov 2026 (links at the end).
Numbers marked **measured** come from the repo's own logs; everything else is an estimate to confirm with the named command.

---

## A. Top 10 recommendations

### Do these first (each is a bounded change)

1. **Split the brain: GBMs decide, the LLM explains and judges.** Brain v3 + the lock-in head already beat rules on
   every stoppage/big-play head at 0.5 ms and 2.4 MB (measured). Stop asking the 1.7B to emit odds, lengths, entry
   points or `stay_s`; feed those numbers INTO its brief as tool results and let it write `storyline / why / next`
   plus a small enum (`hold | leave_ok | escalate`) the decider may use only inside rule bounds. The 95% lock-in gate
   then sits on the GBM/rules, where it is already ~100% by construction, and the LLM gate becomes "100% valid JSON,
   grounded words, teacher-judged quality." About 2 days of work; it also halves the student's output length.
2. **Keep Qwen3-1.7B-4bit as the student; run one bake-off, not a migration.** It is Apache-2.0, shares the Qwen3
   tokenizer with every Qwen3 teacher (needed for logit KD), and has the proven mlx-lm LoRA path. Bake-off candidates
   that fit the RAM budget: `Qwen3.5-2B` text-only 4-bit (needs mlx-lm >= 0.30.7; new 248k-token vocabulary, so no
   logit KD from Qwen3 teachers), `LFM2.5-1.2B` (~0.7 GB; top of a 21-model tool-call benchmark), and `Qwen3-4B` at
   3-bit (~1.9 GB, over budget unless the governor allows it). Gemma 4 E2B is 5.1 B real parameters (per-layer
   embeddings): ~2.6 GB at 4-bit, out.
3. **Re-tier the teacher by FLOPs, because the T4 teacher is prefill-bound.** Round 1 **measured** 53-65
   labels/min on Qwen3-32B-AWQ (22,272 in 7 h). At ~500 prompt tokens that is ~25-35 TFLOPS sustained across both
   T4s: compute-bound on the prompt, not the answer. So the levers are prompt tokens and teacher size, not shorter
   outputs. Plan: 32B (TP=2) on the hardest 5-10%; 14B-AWQ run as TWO data-parallel copies (one per T4, no PCIe
   all-reduce) on the middle; Qwen3-8B-AWQ x2 on the easy tail. Expect ~3.5k / ~11k / ~22k labels per session-hour
   (first measured, others estimated; see D.4).
4. **Pack 3-4 consecutive moments of one game into one teacher prompt (train nights only).** The brief's static part
   (teams, league, your-team flags) is paid once; each extra moment adds ~80-120 tokens. 2.5-3.5x more labels per
   GPU-hour. Never pack test/locked nights or blind tasks (moment k+1 is moment k's future).
5. **Make every teacher and student answer schema-constrained.** Teacher: vLLM structured outputs (XGrammar backend)
   with `maxLength` on each string: 100% parseable, no `<think>` leaks, shorter outputs, more concurrent sequences.
   Student on the Mac: Outlines `from_mlxlm` or XGrammar's `[metal]` extra through mlx-lm's `logits_processors`;
   forced tokens (keys, quotes, enums) skip the sampler, so ~60-token JSON costs ~25-30 free tokens.

### Then

6. **LoRA on all 28 layers, rank 16-32, alpha 32, LR ~2e-4, batch <= 8, MLP included.** Today: rank 8 / alpha 16 /
   last 8 layers (2.5 M params). "LoRA Without Regret" (Thinking Machines, Sept 2025): MLP+attention on every layer,
   LR ~10x the full-FT rate, rank matters once the dataset is big (ours is 24 M+ tokens), LoRA dislikes large batches.
   Rank 16 on all layers is 17.4 M params = 35 MB fp16; the PEFT->MLX converter already accepts "all layers"
   (contiguous run ending at the last layer).
7. **Three-stage student recipe: SFT -> soft-target KD on decision tokens -> iterated on-policy distillation.**
   Store the teacher's top-20 logprobs per generated token (vLLM `logprobs=20`, ~16 KB/row) and add a KL term on the
   enum/decision tokens; then run 3-5 rounds of "student samples 4 answers per prompt, teacher scores them with
   `prompt_logprobs`, train on reverse-KL" (Thinking Machines, Oct 2025: ~150 steps x 64 prompts x 4 samples closed
   a 10-pt gap; LoRA students work). One round on 1,000 prompts is ~40 min wall on 2x T4 (D.5). DPO/ORPO only if the
   words are the problem (D.6).
8. **Fix the T4/Kaggle environment once, in the notebook's first cell** (D.7): `pip uninstall -y torchao` before PEFT
   imports (PEFT issue #3964, fixed upstream Oct 16, 2026; Kaggle still ships 0.10); vLLM in its own venv so it
   cannot replace Kaggle's torch; `dtype=half`, `VLLM_ATTENTION_BACKEND=TRITON_ATTN` set explicitly (skip the
   FA2 probe); check the vLLM startup log says **Marlin** (Turing Marlin merged Dec 16, 2025, PR #29901) and upgrade
   if it says "Using AWQ kernel"; the raw-safetensors bf16 reader in `dequant.py` stays (safetensors.numpy cannot
   read bf16; `safetensors.torch` + `.float()` is the one-line alternative).
9. **Latency on the Mac without a draft model:** prompt cache for the system prompt + tool-description prefix
   (`make_prompt_cache`, trimmable caches), a prefix-stable brief layout (static header first, clock and last plays
   last) so per-game caches survive a trim, grammar jump-forward, and `batch_generate` to refresh all live games in
   one call. Target ~1 s per game call; 0.5 ms for the GBM path. A Qwen3-0.6B draft would add ~0.5 GB and break the
   2 GB budget; revisit only if the budget grows.
10. **Keep adapters unfused unless a measured fuse wins.** Merging into 4-bit directly loses accuracy; MLX unfused
    LoRA costs ~200 extra matmuls per token (7 targets x 28 layers), maybe 15-30% of decode. Measure
    `mlx_lm.generate --adapter-path` vs fused; if fused is >15% faster, fuse into fp16, requantize 4-bit affine
    (group 64), and ship only if `peft_to_mlx.py --verify` greedy agreement is >= 99% on 200 held-out prompts.

---

## B. Recommended stack (RAM and latency)

### B.1 Three tiers on the Mac, one offline

| Tier | What | Model / code | Budget | Decides |
|---|---|---|---|---|
| 0 Rules | hard constraints | `agent/distill/gold.py`, `redzone/decider` | us | never mid-play, crunch first, your team, NFL drives first, garbage never must_see |
| 1 Numbers | stoppage odds/type/length, entry point, big-play odds, lock-in odds | Brain v3 LightGBM->numpy (0.86 MB, 0.5-0.6 ms/game/tick **measured**), lock-in head (1.5 MB, 0.09 ms **measured**) | < 2 ms per tick, all games | when to cut, when to enter, jump feasibility, abstain |
| 2 Words + judgment | storyline / why / next, bounded enum advice, watchdog tool calls | Qwen3-1.7B-4bit + one multi-task LoRA via mlx-lm | ~1.4-1.6 GB RSS, ~1 s per call, event-driven | phone text, tie-breaks inside rule bounds, watchdog fix choice (guarded by `policy.guard`) |
| 3 Teacher | labels, rationales, blind grading, feature/rule proposals | Qwen3-32B-AWQ / 14B-AWQ / 8B-AWQ on Kaggle | 25 session-h/week | nothing live |

Why this split: the student's training targets for `lock_in`, `safe_to_leave`, `stay_s` are already rules + hindsight
("the teacher never overrides them", `agent/distill/teacher.py`). The LLM is being trained to restate a decision the
GBM makes faster and with calibration (Brier/ECE reported per head in `docs/brain3/REPORT.md`). Spend its
parameters on reading play text, novel situations, explanations and the watchdog's long tail.

### B.2 RAM on the M2 Air (estimates; confirm with `mlx_lm.generate --verbose`, peak memory line)

| Item | Estimate | Note |
|---|---|---|
| Qwen3-1.7B-4bit weights | ~1.0 GB | 4.5 bits/param incl. group-64 scales, embeddings quantized |
| KV cache, 1,024 tokens | ~115 MB | 28 layers x 8 KV heads x 128 x 2 x fp16; `--kv-bits 4` not needed at this length |
| LoRA rank 16, all layers | 35 MB | 17.4 M params fp16; rank 32 = 70 MB |
| MLX runtime + Python + tokenizer | 250-400 MB | the governor's `apply_mlx_limits(models_mb)` already caps MLX |
| GBMs (Brain v3 + head) | < 5 MB | numpy only |
| **Total** | **~1.4-1.6 GB** | fits 1.5-2 GB; a 0.6B draft (+0.45 GB + KV) does not |
| Qwen3.5-2B text-only 4-bit | ~1.6-1.9 GB RSS | borderline; bake-off only |
| Qwen3-4B 4-bit / 3-bit | ~2.5 / ~1.9 GB | over budget today |

Reference points: on an M2 Air, mlx-lm ran Llama-3.2-1B 4-bit at ~89 tok/s in ~1.5 GB and 3B at ~40 tok/s in 2.5 GB
(third-party page, mlx-lm 0.12); an M3 Air measured Qwen3-4B 4-bit at 10.8 tok/s / 2.4 GB.

### B.3 Latency per game call (estimates)

| Step | Today | With B.1 + rec. 5 and 9 |
|---|---|---|
| Prefill 450-500 tokens at ~400-700 tok/s | 0.7-1.2 s | system + tools prefix cached, brief tail ~150-250 new tokens: 0.3-0.6 s |
| Decode ~60-100 JSON tokens at ~55-70 tok/s | 1-1.5 s | ~25-30 free tokens (grammar jump-forward): 0.4-0.5 s |
| Total | ~2-2.5 s | **~0.8-1.2 s**; 6 games via `batch_generate` ~1.5-2x one call, not 6x |
| GBM path | 0.5 ms | 0.5 ms; the decider never waits on the LLM |

### B.4 Contracts that keep it safe

- The decider reads only enum fields from the LLM; words go to the phone.
- Confidence: derive it from the GBM probability + the student's logprob on the decision token (`batch_generate(...,
  return_logprobs=True)`), calibrated isotonically on valid nights, instead of a self-reported 0-1 (small models
  are poorly calibrated; `grade.py`'s abstain cut then works on a real signal).
- Watchdog tool loop: Qwen3's native `<tool_call>` format, mlx-lm's `tools` dataset type, 5 tools
  (`restart_service`, `reconnect_stick`, `prune_disk`, `read_log`, `probe_stick`), `policy.guard` has the last word.
- `enable_thinking=False` everywhere, training renders included (the template inserts the empty think block; the
  student must see the identical prefix).

---

## C. Why these models (verified Nov 2026)

| Candidate | 4-bit size | Verdict |
|---|---|---|
| **Qwen3-1.7B** (incumbent) | ~1.0 GB | keep: tokenizer shared with Qwen3 teachers, mlx-lm LoRA proven, JSON-disciplined with thinking off |
| Qwen3.5-2B (Mar 2, 2026; hybrid Gated DeltaNet + full attention every 4th layer, vision tower) | ~1.2-1.4 GB text-only | bake-off: mlx-lm >= 0.30.7 supports it; LoRA attaches automatically to its Linear layers; new vocab breaks logit KD from Qwen3; M2 speed of the linear-attention layers unknown |
| Qwen3-4B-Instruct-2507 | ~2.5 GB (3-bit ~1.9) | strongest small tool-caller in community benchmarks; over the RAM budget |
| LFM2.5-1.2B | ~0.7 GB | tied #1 (0.880) in a 21-model tool-call-decision benchmark with qwen3:0.6b, qwen3:4b, phi4-mini; cheap to test as watchdog-only |
| SmolLM3-3B / Phi-4-mini 3.8B | ~1.8-1.9 / ~2.3 GB | borderline / over |
| Gemma 4 E2B | 2.3 B effective, 5.1 B real | ~2.6 GB; out until PLE tables can stream |
| Llama 3.2 1B/3B | 0.7 / 1.8 GB | older generation; weaker JSON and tool use |

Teacher candidates on 2x T4 (16 GB each, fp16 only, no FA2):

| Teacher | Fit | Expected labels/session-h | Use |
|---|---|---|---|
| Qwen3-32B-AWQ, TP=2 | ~18 GB weights, ~4.5 GB KV/GPU (~60 x 600-token seqs) | 3.2-3.9k **measured** | hardest 5-10% + blind grading |
| Qwen3-14B-AWQ, DP=2 (one copy per GPU) | ~8 GB + ~6 GB KV each | ~10-12k (est.; "~3x" **measured** at TP=2) | workhorse |
| Qwen3-8B-AWQ, DP=2 | ~5.5 GB each | ~18-25k (est.) | paraphrase, easy wording, OPD scoring |
| Qwen3-30B-A3B-AWQ (MoE), TP=2 | ~17 GB | theoretical 8-10x the 32B (3.3 B active); Marlin MoE for sm75 is in PR #29901 but unproven on T4 | 15-min smoke test; if it runs, replaces 14B |
| Qwen3.5-9B / 27B (GDN hybrid) | 9B fp16 TP=2 or AWQ DP | best quality per FLOP on paper; GDN kernels gated by compute capability in vLLM (a Qwen3-Next AWQ run on sm75 failed in torch.compile) | test with `--enforce-eager --dtype half`; text labels only (new vocab) |

Gate the tiers with data: Phase 3 already grades the teacher blind on dev-test; route a task to a cheaper tier only
if that tier's blind lock-in agreement is within 1 point of the 32B's on that task.

---

## D. Training recipe

### D.1 Data (CPU, free, millions)

1. Hindsight labels for everything numeric: the Kaggle build (`build_moments_kaggle.py`, 3 seasons + current, 6
   leagues) is the right source for Brain v3 / head retraining AND for the student's enum fields. Zero GPU.
2. Teacher words on a curated 100-300k subset per week (D.4), hardest-first (`gold.hardness`), train nights only
   for grounded prompts, packed 3-4 moments per prompt.
3. Brief compaction: target <= 300 tokens; static header first (teams, league, your-team, bet), then score/period,
   then last plays and clock. This is the prefix-stable layout the Mac cache needs and cuts teacher cost ~1.5x.
4. Tool block in the brief: `TOOLS: stop30=0.62 stop60=0.81 type=tv_timeout len_p10/50/90=28/44/90 entry_p10=26
   big180=0.17 lock_odds=0.41 safe_entry=31` (Brain v3 fields already exist on every `Reading`).
5. Leak checks stay as they are (whole nights, time order, no brain outputs in the brief until Brain v3 is retrained
   before the test cutoff, locked nights never downloaded).

### D.2 Teacher pass (Kaggle, vLLM, own venv)

- `LLM(model, dtype="half", quantization="awq", max_model_len=2048, enable_prefix_caching=True, max_num_seqs=256,
  gpu_memory_utilization=0.92)`; structured outputs with the task's JSON schema (`maxLength` per string);
  `temperature 0.3-0.7` for wording variety, greedy for labels.
- Two processes for DP tiers: `CUDA_VISIBLE_DEVICES=0` and `=1`, each on a shard of the queue (`run_teacher.py`
  already resumes by id; add `--shard i/2`).
- Record `logprobs=20` on generated tokens for rows that feed the KD stage (~16 KB/row; 100k rows = 1.6 GB, inside
  Kaggle's 20 GB output).
- Max tokens by task: grounded 90, jump 60, blind 150, paraphrase 200 (schema-bounded anyway).

### D.3 Student SFT (Kaggle, PEFT, fp16)

| Setting | Today | Recommended | Why |
|---|---|---|---|
| Base | dequantized `mlx-community/Qwen3-1.7B-4bit` (exact Mac weights) | same | quantization-consistent; `--verify` proves greedy identity |
| Master weights | fp32 copy of the whole model (6.8 GB) | frozen base in fp16 (3.4 GB), LoRA params fp32, `torch.autocast(fp16)` + `GradScaler`, SDPA attention | halves memory, ~1.6-2x faster; Qwen3's QK-norm makes fp16 attention overflow unlikely; fall back to fp32 master only on NaN |
| LoRA | r 8, alpha 16, last 8 layers, q/k/v/o/gate/up/down | r 16-32, alpha 32, all 28 layers, same targets | capacity for 24-50 M tokens; MLP is where the parameters are |
| LR / batch / epochs | 1e-4 / 8 / 2 | 2e-4 cosine, warmup 30, batch 8 (grad-accum if memory allows), 1 epoch at 60-80k rows | LoRA's optimum is ~10x full-FT; small batches |
| Loss | CE on the answer only (`-100` on prompt) | same, plus the KD term in D.4 | |
| Tasks | 2 adapters (reasoner+jump, watchdog) | 1 multi-task adapter, task tag in the system prompt, mix ~70/15/15, per-task gates | shared JSON discipline helps at 1.7B; separate adapters remain the fallback |
| Speed option | plain PEFT | Unsloth (fp16 on T4 auto-detected; ~1.8x on T4 in its table) once the plain path is stable | export stays PEFT -> `peft_to_mlx.py` |

Throughput reference: 40k rows x ~600 tokens ~ 3 h on one T4 (**measured**, fp32 master). Expect 60-80k rows in
the same 3 h with the fp16 base.

### D.4 Throughput plan (25 session-hours/week; Kaggle bills session hours, not per GPU: round 1's 7 h run used 7.3 h of quota; verify on the account's quota page)

Per session-hour, both T4s busy, 500-token prompts, before packing:

| Tier | Labels/h | With 3.5x packing + 1.5x compaction |
|---|---|---|
| 32B TP=2 | 3.5k (**measured**) | ~12-15k (packing only on train-night grounded prompts) |
| 14B DP=2 | ~11k | ~40-55k |
| 8B DP=2 | ~22k | ~80-110k |

A week (two 11-h sessions + 3 h):

| Block | Hours | Output |
|---|---|---|
| ESPN build on CPU, in the background | (free) | millions of hindsight rows; retrain Brain v3 + head |
| 32B on the hardest head + blind grading | 3 | ~20-30k rows + the teacher's own dev-test score |
| 14B DP on the middle | 6 | ~250k rows |
| 8B DP on the tail + OPD scoring | 6 | ~500k rows or 4-5 OPD rounds |
| Students (two jobs, one per GPU) + convert + grade | 4 | adapters, report |
| Reserve (restarts, Colab overflow) | 3-6 | Colab free (1x T4, ~12 h max, unpublished weekly cap) runs 8B shards; the queue is resumable |

So ~300-800k teacher-worded rows per week at the mixed tiers; "millions" is a month at the 8B tier or a season at
14B. The numeric fields reach millions this week from hindsight alone, which is where millions matter.

### D.5 Distillation stages (after SFT)

1. **Soft targets on decision tokens.** For each enum field the teacher decided blind (jump/stay, advice), add
   `lambda * KL(teacher_top20 || student)` at the field's first token with the top-20 renormalized; `lambda` 0.5-1.
   Same tokenizer required (Qwen3 teacher -> Qwen3 student). Cost: zero extra GPU; one more column in the rows.
2. **Iterated on-policy distillation (Kaggle-shaped).** Per round over 1,000 prompts: student samples 4 answers
   (HF generate, batch 32, ~5 min) -> teacher scores them (`SamplingParams(prompt_logprobs=0)` on 14B/8B DP, ~7 min)
   -> one pass minimizing per-token reverse KL `log p_student - log p_teacher` (advantage = negative reverse KL,
   importance-weighted; ~25 min). ~40 min wall; 4-6 rounds per session. Expect most of the student-vs-teacher gap on
   the words and on jump/stay to close in 5-10k sequences (Thinking Machines closed 60 -> 70% AIME in ~38k).
   Reference implementation: TRL `GKDTrainer` (beta=1, on-policy); on T4 run the teacher in the other process.
3. **Rationale distillation, inference-cheap.** Teacher writes a one-line `why` before `next`; the student learns the
   same order (why -> next -> advice). It is ~20 tokens, and the decision no longer needs to come first because the
   GBM already made it.

### D.6 Preference tuning (optional)

Pairs: chosen = gold/teacher-agreeing answer, rejected = the student's own wrong answer on train nights (on-policy
negatives). ORPO (reference-free, one 1.7B in memory) with TRL on fp16; 5-10k pairs, 1 epoch, beta 0.1; gate: valid
JSON stays 100%, no lock-in regression on valid. Skip it if D.5 already meets the gates: DPO-style training can
inflate confidence, and confidence now comes from the GBM + logprob instead.

### D.7 Fixes for the four issues this pipeline hit

| Issue | Root cause | Fix in the recipe |
|---|---|---|
| `safetensors.numpy` cannot read bf16 | numpy has no bfloat16; mlx-community stores 4-bit scales/biases in bf16 | keep `dequant.load_file` (raw header + `<<16` to fp32, exact); or `safetensors.torch.load_file(...)[k].float().numpy()`; add a Mac-side test that compares one tensor with `mlx_lm.convert --dequantize` |
| Kaggle's torchao 0.10 breaks PEFT LoRA dispatch | PEFT >= 0.19 raises "Found an incompatible version of torchao. Found version 0.10.0, but only versions above 0.16.0 are supported" inside `dispatch_torchao` before checking whether the weight is a torchao tensor (issue #3964, fixed Oct 16, 2026 by PR #3989) | first cell: `pip uninstall -y torchao` (nothing here needs it), or `pip install -U peft` to the first release after Oct 16, 2026; restart the kernel if peft was already imported; Unsloth has the same hazard (PR #11168) |
| T4 lacks FA2 and bf16 | compute capability 7.5 | vLLM `dtype="half"`; training with fp16 autocast + fp32 LoRA params + GradScaler, SDPA; never `attn_implementation="flash_attention_2"` |
| vLLM 0.33 on T4 uses TRITON_ATTN | V1 engine's sm75 path | set `VLLM_ATTENTION_BACKEND=TRITON_ATTN` explicitly and drop the `None`/`FLEX_ATTENTION` probes from `MODELS` (each failed probe costs minutes of a 12-h session); confirm "Marlin" in the startup log (Turing Marlin, PR #29901, merged Dec 16, 2025; vLLM >= 0.12-era builds); pin the vLLM version that worked |

Plus one isolation fix: `python -m venv /kaggle/working/venv-vllm && venv-vllm/bin/pip install vllm==<pinned>` and
run `run_teacher.py` with that interpreter, so vLLM's torch never replaces the training environment's torch.

### D.8 Mac-side conversion and serving

1. `peft_to_mlx.py <peft> agent/model/<job> --layers-total 28 --verify 200 --torch-base <deq> --mlx-base <4bit>`:
   ship only at >= 99% identical greedy answers.
2. Load with `mlx_lm.load(model, adapter_path=...)`; build `make_prompt_cache` for the system + tools prefix once per
   adapter; per game keep a trimmable cache and re-prefill only the changed tail.
3. Structured output: Outlines `outlines.from_mlxlm(*mlx_lm.load(...))` with the task's JSON schema, or XGrammar
   (`pip install "xgrammar[metal]"`) through `logits_processors`; A/B both on valid nights (mask bugs show up as
   odd wording, not parse failures).
4. Optional final polish on the Mac: `mlx_lm.lora` QLoRA on the real 4-bit weights, 100-200 iters, batch 1, overnight
   at `nice -n 15` (the Nov 3 run hit 30 s/step at batch 2 / 16 layers and starved the Mac; keep it tiny or skip).
5. Fuse only per recommendation 10.

---

## E. Risks

| Risk | Signal | Mitigation |
|---|---|---|
| Cheaper teacher tiers write less grounded words | blind dev-test gap between 32B and 14B/8B on a task | tier gates (C); hardest 10% stays on 32B |
| Hindsight leaks into grounded wording ("will score") | `student.teacher_words` leak filter hits; future-tense + outcome regex on a sample | keep the filter, add a 32B blind "plausible forecast?" judge on 2% of rows |
| T4 kernel surprises (Marlin on sm75, GDN kernels, torch.compile bf16 asserts) | smoke test fails or falls to a slow kernel | pinned vLLM in a venv; `--enforce-eager` for hybrids; dense 14B/8B fallback chain already in `run_teacher.py` |
| fp16 overflow in student training | NaN loss | GradScaler + NaN guard; fall back to today's fp32 master path |
| RAM creep on the Mac | peak RSS > `models_mb` | governor cap; measure with both adapters loaded; no draft model |
| Unfused LoRA slows decode 15-30% | `mlx_lm.generate --verbose` tok/s with vs without adapter | fuse -> requantize -> `--verify` >= 99%, else keep unfused |
| Tokenizer mismatch for logit KD | Qwen3.5 student or teacher | text-only distillation across families; logit KD inside one family |
| Quota accounting | 2x T4 billed per GPU instead of per session | check the quota page after one session; plan assumes session-hours (round 1 evidence) |
| Constrained-decoding engine bugs on MLX | valid JSON but strange wording | A/B Outlines vs XGrammar on valid; fall back to unconstrained + `extract_json` |
| Over-fitting to the teacher's style on dev-test nights | train/valid/test gap report widens | the existing split rules; teacher words only on train nights; locked nights untouched until release |

## Sources

- Repo: `agent/distill/teacher.py`, `student.py`, `gold.py`; `agent/kaggle/run_teacher.py`, `run_students.py`, `dequant.py`, `student_data.py`, `teacher_distill.py`; `agent/train/peft_to_mlx.py`, `lora.yaml`; `docs/brain3/REPORT.md`; `docs/reasoner.md`; `docs/handoff.md` (Kaggle quota 7.3/30 h after a 7 h run).
- Thinking Machines, LoRA Without Regret (Sept 2025): https://thinkingmachines.ai/blog/lora/
- Thinking Machines, On-Policy Distillation (Oct 27, 2025; June 2026 update): https://thinkingmachines.ai/blog/on-policy-distillation/
- PEFT issue #3964 (torchao < 0.16 breaks LoRA injection; fixed by PR #3989, Oct 16, 2026): https://github.com/huggingface/peft/issues/3964
- vLLM PR #29901 (Marlin kernels for Turing sm75, GPTQ/AWQ/FP8/NVFP4, fp16 accumulator; merged Dec 16, 2025): https://github.com/vllm-project/vllm/pull/29901
- vLLM quantization hardware table (Turing: AWQ, GPTQ, Marlin): https://docs.vllm.ai/en/stable/features/quantization/
- vLLM 0.17.0 Qwen3.5 support: https://besthub.dev/articles/vllm-0-17-0-release-full-qwen-3-5-support-and-anthropic-api-compatibility-218ac81d23cd ; recipe https://recipes.vllm.ai/Qwen/Qwen3.5-4B
- mlx-lm (speculative decoding `--draft-model/--num-draft-tokens`, `--kv-bits`, `--prompt-cache-file`, `batch_generate`, `logits_processors`): https://github.com/ml-explore/mlx-lm ; LoRA guide (lora/dora/full, QLoRA on quantized models, `--mask-prompt`, `tools` data): https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md
- Qwen3.5 small models (0.8B/2B/4B/9B, Mar 2, 2026; GDN hybrid): https://qudata.com/en/news/exploring-qwen35-family/ ; https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.5 ; MLX 4-bit cards (mlx-lm >= 0.30.7): https://huggingface.co/mlx-community/Qwen3.5-2B-MLX-4bit , https://huggingface.co/augible/qwen3.5-2b-4bit-text-mlx
- Gemma 4 E2B/E4B parameter accounting: https://sebastianraschka.com/llm-architecture-gallery/per-layer-embeddings/
- Outlines mlx-lm integration: https://dottxt-ai.github.io/outlines/main/features/models/mlxlm ; XGrammar `[metal]` extra: https://pypi.org/project/xgrammar
- Merging QLoRA adapters into 4-bit (Kaitchup; QA-LoRA): https://kaitchup.substack.com/p/dont-merge-your-lora-adapter-into ; https://arxiv.org/pdf/2309.14717
- Small-model tool-calling benchmarks (LFM2.5-1.2B / qwen3 0.6b / 4b / phi4-mini tied; Qwen3-1.7B LoRA 0.853): https://ai-radar.it/article/valutazione-di-llm-piccoli-l-importanza-del-parsing-negli-agenti-locali ; https://reddit.sentinel-team.org/posts/1smna3c/snapshots/2026-04-17T16%3A21%3A57.298566Z
- Unsloth on T4 (fp16 auto-detect, ~1.8x): https://qwen.readthedocs.io/en/latest/training/unsloth.html
- M2 Air mlx-lm reference speeds: https://edge-llm-benchmark.vercel.app
- Kaggle/Colab limits (12 h sessions; ~30 h/week community figure): https://gpuperhour.com/blog/free-cloud-gpus-and-credits
