# Noggin Foundation Model (NFM)

Owner: lane 1 (Noggin training). Serves: North Star "% of hindsight-oracle value achieved". It is the Noggin's
main track (Nick, Nov 4 4:45 PM). V8, meaning the Mind with the crunch and leak fixes plus the Reflex heads, stays the
near-term model. NFM heads replace those heads only when they beat them on locked nights, and the `Scored.*` contract
does not change ([contracts/watch.py](../redzone/contracts/watch.py)).

## Why

Every per-feature fix so far (heat weights, crunch rules, playoff and matchup terms) is a hand-made guess about what
matters. The NFM learns it from the data instead. It reads every game as a sequence of events, the way a language
model reads text, and learns from what actually happened next. The hand features (brief facts, the brain's WP track,
#163 postseason stakes) become optional inputs. They are no longer the design.

## 1. Data: event tokens

`scripts/noggin_tokens.py` (vocab v1) turns each game into `<BOS:league> <HOME> <AWAY>`, then **6 tokens per event**,
then `<EOS>`:

| field | values | source |
|---|---|---|
| KIND | 2,048 hashed slots per base league (crc32, stable across shards) | `espn._kind` (the live parser's own kind) |
| DT | 32 log buckets of wall seconds since the last event, from 0 s to about 2 h; game-clock delta when there is no wall clock | wallclock |
| CLOCK | period 1-10 × 16 buckets of the period clock | period, clock |
| MARGIN | home minus away, clipped to -20..+20 | score |
| PTS | points on this play, and which side scored | score delta |
| STOP | `models.Stoppage` after the play (live, timeout, end of period, review, score break ...) | `espn.stoppage_for` |

**Vocabulary** is 10,511 ids, so shards are flat `uint16` arrays with an index of game offsets. **Team** tokens are 512 hashed
slots per base league, so team strength and matchup quality are learnable without a features table. The **slate** context (what else
is live) is built at training time by interleaving the night's games by wall time. The factory's self-play slates
(`noggin_selfplay.synthetic_slates`) give extra, randomized slates from the same games.

**Leakage:** nights on or after `factory.EVAL_FIRST_NIGHT` are never tokenized, and the locked and scorecard nights
are never downloaded (`split.BIG_CUTS` / `locked_v1`).

**Measured counts** (this container, real data):

| league season | games | events | tokens | tokens / game |
|---|---:|---:|---:|---:|
| NBA 2025 | 1,324 | 625,000 | 3.76 M | 2,836 |
| NFL 2019 | 267 | 45,209 | 0.27 M | 1,020 |

**Corpus estimate** (unique real events, every season the factory plans):

| league | seasons | ≈ tokens |
|---|---|---:|
| NBA | 2002-2026 | ~90 M |
| CBB | 2006-2026 (hoopR men's D-I) | ~300 M |
| WCBB | 2006-2026 (wehoop) | ~250 M |
| NHL | 2011-2026 | ~40 M |
| NFL + CFB | 2011-2025 | ~20 M |
| MLB | ESPN from Kaggle (blocked here) | ~100-200 M |
| **total** | | **~0.7-0.9 B** |

Self-play does **not** add new events. It adds new slate orderings, which teach the cross-game part. So the
honest unique-token figure is about 0.8 B, and "1 B tokens" is reached by counting about 1.2 epochs.

## 2. Objective (self-supervised, no human labels)

This is a decoder-only transformer over one game's events, with a slate block on top.

1. **Next event.** Cross-entropy on each of the 6 fields of the next event. The fields are factorized, so the
   model predicts KIND, then DT given KIND, and so on, by conditioning in order.
2. **Time to next stoppage.** A regression head on log seconds to the next STOP ≠ live. This is the break model's
   pretraining.
3. **Slate.** At each minute of a night, pool each live game's last hidden state and run a small set-attention
   layer across games. The target is which game has the next scoring event, and how soon. This is what "what else is
   live" is for.

Nothing here reads Nick's taps. Every target is something that happened next in the pbp.

## 3. Heads (fine-tuned on top, same labels as today's baselines)

| head | label (already coded) | baseline to beat (locked) |
|---|---|---|
| Watch Value | realized WP swing over the next 3 min (`train_watch_value.labels`) | NBA rank agree 74.2, NFL 58.6 (#166) |
| crunch enter / exit | hindsight crunch state (`crunch_labels`) | NFL exit 92.1, NBA 71.4 |
| break start / length | the factory's break windows | brain3 break odds |
| big play next | a swing of 0.08 or more within 3 min | `pb` head |
| game importance | realized slate value per game (pre-game) | v1 importance (next) |
| possession policy | `oracle.solve_cue` labels | PICK-cue, 93.9% of oracle |

## 4. Size and compute, inside 30 T4-h per week

Training compute is about 6·N·D FLOPs (N parameters, D tokens seen). A Kaggle T4 peaks at 65 fp16 tensor TFLOPS. We
plan on **15 TFLOPS achieved**, about 23% utilization, which is typical for small transformers in PyTorch AMP on T4.
Kaggle's T4×2 runs DDP on both GPUs. Whether the quota counts session hours or GPU hours is to be checked on the first run, and the
numbers below assume GPU-hours, the worse case.

| model | params N | tokens seen D | FLOPs 6ND | T4-h at 15 TFLOPS |
|---|---:|---:|---:|---:|
| NFM-S | 25 M | 2 B (about 2.5 epochs of 0.8 B) | 3.0e17 | **5.6** |
| NFM-M | 100 M | 2 B | 1.2e18 | 22 |
| NFM-M, one epoch | 100 M | 0.8 B | 4.8e17 | 8.9 |
| NFM-L | 300 M | 0.8 B | 1.4e18 | 27 |

The data sets the size. Compute-optimal is about 20 tokens per parameter (Chinchilla), which puts 0.8 B tokens at
**~40 M parameters**. Repeating data up to about 4 epochs costs little (Muennighoff et al. 2023, data-constrained
scaling). So **NFM-S (25 M) first**: it fits one week with room for evals. Go to 100 M only if the S-to-M loss curve
says size still pays, and add tokens (MLB, WCBB, CFB) before adding size. 300 M would take a whole week's quota
for one epoch, and it is over-sized for this corpus. Nick's 100-300 M range is the ceiling, not the starting point.

## 5. Serving on the Mac

- **Reflex (< 5 ms per tick, every tick).** Distill the NFM into the streaming student: a 1-3 M parameter GRU or tiny
  transformer that steps once per event with a cached state, in numpy, at about 0.1-0.5 ms. The heads' soft targets
  also re-train today's GBDT heads, so WV, crunch and PICK keep their contracts.
- **NFM on MLX (a few Hz).** NFM-S at 25 M parameters in fp16 is about 50 MB. An incremental step with a KV cache costs
  a few ms on the M2, so the full model can score every live game a few times per second. That is cheaper and closer
  to the data than a 3 B language model.
- **Mind (3 B 4-bit, ~1.7-2 GB).** Stays the language tier for "why" lines and the phone, every 30-60 s. It is not
  in the per-tick path. The local AI budget stays at ~2.5 GB RSS.

## 6. Gates (same as every Noggin model)

Locked nights are scored once per release. Forward CV runs by whole night, with the mean and the worst fold. A head
ships only if, on locked:

- it beats its baseline from section 3 (WV rank agreement and calibration error, crunch exit accuracy with no extra
  flicker, % of oracle for policies);
- mid-play entries stay at 0;
- every other North Star line is no worse than live (bar B).

Pretraining loss itself is reported (per field, per league), but it is not a gate.

## 7. Milestones

| # | what | where | compute |
|---|---|---|---|
| M0 | the factory emits token shards for every league and season; tokens per league in the manifest and every PR | Kaggle CPU (next factory run) | 0 GPU |
| M1 | the NFM-S training script plus a CPU smoke test on 2 seasons; tokenizer frozen as vocab v1 | repo + CPU | 0 GPU |
| M2 | NFM-S pretrain on all shards | Kaggle T4×2 | ~6 T4-h |
| M3 | WV and crunch heads on NFM-S embeddings vs the #166 baselines (locked) | Kaggle T4 | ~1 T4-h |
| M4 | distill to the streaming Reflex student; latency and RAM on the Mac | CPU | 0 |
| M5 | scale decision: NFM-M if the M2 S-to-M curve says so | Kaggle T4×2 | ~9-22 T4-h |

This week's remaining GPU (~2.5 h) goes to the Mind V8 round. The NFM's first GPU run (M2) is next week's.

## 8. Risks

- **Wall clocks:** ESPN pbp has no wall clocks for NHL before 2019-20 and only partial coverage for older NBA seasons.
  DT then falls back to the game-clock delta, which is a different distribution. A flag token marks it (vocab v2) if it hurts.
- **Hash collisions** in KIND and TEAM: 2,048 and 512 slots against about 150 kinds and 30-360 teams per league, so
  collisions are rare. They are checked per shard.
- **ESPN terms:** the data is for personal, non-commercial use, which this is. No redistribution beyond private Kaggle datasets.
