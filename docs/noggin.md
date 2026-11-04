# The Noggin: one decision brain

Nick, Nov 4 2026 1:45 AM: **the Noggin is the #1 focus, and everything combines into it.** One AI brain,
three layers, one training pipeline, one scoreboard (the North Star, `docs/northstar.md`). Owner: **NOGGIN**
(`docs/status.md`). Every Noggin PR names the North Star line it moves.

```
                 offline (Kaggle GPU)                      on the Mac
            ┌──────────────────────────┐
 nights ──► │ NOGGIN TEACHER (32B)     │── labels, night reviews ──┐
 moments    │ "should have gone to UNC │                           ▼
            │  at 2:10"                │              ┌───────────────────────────┐
            └──────────────────────────┘              │ NOGGIN MIND (1.7B, async, │
                                                      │ every 30-60 s)            │
 data factory ─► Kaggle CPU / Actions ─► npz ─┐       │ storylines, stakes,       │
 (historical PBP with wall clocks)            │       │ lock-in, watchdog, "why"  │
                                              ▼       └────────────┬──────────────┘
 time-shift buffer (#61) ─► ┌──────────────────────────┐  priority  │
 (30-60 s head start,       │ NOGGIN REFLEX (<5 ms,    │◄── vector ─┘
  input priority #1)        │ every tick, numpy only)  │
 feeds, sync, switching ──► │ timing · predict · pick  │── views ──► DecisionCore + guards ──► TV
                            └──────────────────────────┘
```

## 1. The three layers

| Layer | Runs | What it does | Absorbs |
|---|---|---|---|
| **Reflex** | every tick, < 5 ms per tick for all games, numpy only, no LLM | **Timing**: dead now? restart when (calibrated lower bound)? **Predict**: big play next? **Pick**: which game, when to cut, when to enter | Brain v3 (#26), A3 restart countdown, Noggin Timing / Predict / Pick, the decider weights (`core.*` knobs, auto-tune #16) |
| **Mind** | every 30-60 s, async, background priority, ≤ ~2.5 GB RSS, lazy | Storylines and stakes, lock-in, **sets the Reflex's priorities** (per-game weight / attention), explains switches, runs the watchdog | distill track (#13, Kaggle V5), live game reasoner (`redzone/reasoner/`), watchdog adapter |
| **Teacher** | offline, Kaggle GPU (32B) | Labels moments, reviews each night, generates training data for both layers | Kaggle teacher notebooks (`/mnt/project-files/distill/`) |

Architecture rule (research queue, Nov 3): **GBMs and rules own timing; the LLM writes words and
priorities only, async.** The Mind can never make a cut happen sooner, only change which game is worth more.
Rule #1 (never land mid-play) is enforced by the Reflex's guard layer, not by any learned score.

## 2. Interfaces

All types are the canonical ones in `redzone/decider/core.py`; the Noggin fills them, it adds no new
path to the TV.

### Mind → Reflex: the priority vector

One message per Mind cycle, per live game. The Reflex keeps the last one until it expires.

```python
@dataclass(frozen=True)
class MindPriority:              # redzone/noggin/contracts.py (Phase 2)
    game_id: str
    weight: float                # multiplies core.Priority.weight, clamped to [0.5, 2.0]
    lock_in: int                 # 0 free .. 3 never leave (raises friction to leave; never forces a cut)
    must_see: bool               # same meaning as MomentView.must_see
    why: str                     # one line for the phone tile / trace
    at: float                    # epoch s it was written
    ttl_s: float = 90.0          # stale after this: weight falls back to 1.0
```

Bounds are part of the contract: a missing, late or broken Mind leaves every weight at 1.0 and the
Reflex runs exactly as it would with no Mind (today's `reasoner.lock_points` cap of 15 points stays).

### Reflex → decider: the views

| Reflex head | Fills | Field(s) |
|---|---|---|
| Timing (dead now / restart) | `BreakView` | `on_break`, `remaining_p10_s` (= conformal lower bound), `remaining_p50_s`, `restart_at` |
| Timing (break soon) | `BreakView` | `break_soon_p`, `break_soon_s` |
| Timing (entry) | `EntryView` | `ok`, `wait_s` (enter at the lower bound minus switch cost and settle) |
| Predict | `MomentView` | `p_big_soon` |
| Pick | `Knobs` | `core.*` weights, tuned by replaying nights (forward-chaining CV by whole nights) |

Timing also replaces the fixed floors in `redzone/decider/screen.py` (`MIN_BREAK_S`): with a break that
has run `e` seconds and no resume in the data, the leave gate and `_stoppage_remaining` ask the Reflex
for `q_lo(remaining | league, kind, e, rules)` instead of a constant. Behind `noggin.timing.leagues`
(default `[]`, so OFF).

### Inputs, in priority order

1. **The live time-shift buffer** (#61, TIMING): streams held `B` s behind live (default 60, cap ~90)
   give the Reflex a 30-60 s head start: lead = buffer − feed lag. Every Noggin number is reported at the
   buffer the live app runs.
2. The feeds (ESPN, NHL api-web primary, NBA liveData), with measured per-source lag.
3. Sync (per-channel delay ± error), switching paths (SmartThings ~2 s), user state.
4. The Mind's priority vector.

## 3. Training pipeline

| Stage | Where | In | Out |
|---|---|---|---|
| **Data factory** | Kaggle CPU or GitHub Actions (never the Mac, never GPU quota) | public PBP with real wall clocks: nflfastR (`time_of_day` 2011+), hoopR NBA 2002+ / MBB 2006+ (`wallclock`), cfbfastR 2014+, fastRhockey + archived ESPN NHL wallclocks, MLB pitch timestamps 2008+ | labeled moments (parquet): stoppage type, length, restart instant, rule state, big-play-next; each row carries its night and split |
| **Reflex fit** | Kaggle CPU / Actions | factory moments (train nights only) | LightGBM per league and head → exported to numpy `.npz` (Brain v3 pattern) + conformal offsets from the calibration nights |
| **Teacher** | Kaggle GPU | moments, replayed nights | worded labels, night reviews, preference pairs |
| **Mind fit** | Kaggle GPU | teacher rows (D19 recipe: LoRA r16-32, fp16 base + fp32 LoRA) | MLX adapter for the 1.7B student |
| **Nightly self-review** | Mac records the night, cloud/Kaggle labels it at 5 AM | decision records (E23), 👍/👎 | new labels for both layers |

**Splits (no-overfitting rule):** by whole nights (5 AM ET boundary), time-ordered: train = oldest,
calibration = the next block, test = newest (`redzone/lab/splits.py`). The scorecard archive
(eval-data-v1) is the end-to-end judge, so the factory drops every public night on or after its first
night per league: the locked nights (`splits.split_nights(...).test`, `docs/architecture.md` §7) can
never reach training or calibration, and live-path scoring stays out-of-sample. Tuning
decisions use 5-fold forward-chaining CV over nights and report the mean and the worst fold.

## 4. Gates

A Noggin part turns on only when all of these hold:

1. **Beats the current brain on locked unseen nights** (and on fresh nights after the training cutoff),
   scored through the live path (`scripts/eval.py --real-lag --behind-live B`), with the night-level
   cluster bootstrap from #51 (PASS needs ≥ 8 nights).
2. **No regression** in mid-play cuts (hard 0), entries mid-play, plays missed on entry or dead air.
3. **Calibration:** ECE ≤ 0.10 on every probability head; conformal lower bounds cover at their stated
   level on the test nights (per league).
4. **Shadow mode first:** the new head runs next to the live one and logs what it would have done;
   promoted after 7/7 paired nights (or 10 of 12).
5. **Behind a flag**, OFF by default, until 1-4 pass. Flags: `noggin.timing.leagues`,
   `noggin.predict.leagues`, `noggin.pick.enabled`, `noggin.mind.priorities`.

## 5. Phases

| Phase | Work | Target (North Star line) |
|---|---|---|
| 1 (now) | Data factory + **Noggin Timing v1**: discrete-time boosted hazard (LightGBM) per league for break length and restart, conditioned on elapsed time and rule features (national/local, TV-timeout rules); numpy export < 5 ms; conformal lower bounds | NBA entries mid-play 9 → 0 at B=90 (`mid_play`); ECE ≤ 0.10 |
| 2 | **Noggin Predict** (big play next; NFL drive / red-zone and NHL power-play features; today AUC 0.68 / 0.58) and **Noggin Pick** (decider weights tuned by replaying nights, forward-chaining CV by night; fixes #16's overfit) | `big_live_*`, `dead_air` |
| 3 | Mind priority vector live in shadow, Teacher night reviews feeding labels | `your_team_big`, `big_seen_10min` |

## 6. Who does what

| Track | Thread | Notes |
|---|---|---|
| Reflex: data factory, Timing v1, Predict, Pick | Noggin training | NOGGIN owner |
| Reflex input #1: live buffer (#61), feed lag, SmartThings cost | Dial in switch timing | timing scored at B=90 is the Timing v1 baseline |
| Mind + Teacher: distill, Kaggle V5, reasoner | LOCAL AI / distill (#13) | priority vector contract above |
| Scoreboard | North Star harness | Noggin deltas reported in its lines |

## 7. Scale targets (Nick, Nov 4 1:55 AM: "billions")

| Layer | Target | Constraint | How we check |
|---|---|---|---|
| **Mind** | 1.7B → **~3-4B parameters**, 4-bit MLX (Qwen3-4B, a ~3B Qwen/Llama-class model, or newer) | local AI RAM budget **~2.5 GB RSS total** (raised from ~2 GB); ≤ 5 s per call (runs every 30-60 s async); the Mac stays usable | measure RSS and latency on the M2; bake off against 1.7B on the dev-test grade |
| **Reflex** | **20,000+ variables**, trained on **billions of data points** (millions of moments × 20k features) | < 5 ms inference per tick: GBM / small NN with feature selection or hashing, exported to numpy like Brain v3 | latency test in CI; per-head feature importance |
| **Teacher** | 32B for the hard cases + 14B for volume | Kaggle GPU only | writes per-game storylines and narrative labels for every archived game: they train the Mind and become Reflex features |
| **Data** | all seasons (2002+) | feature tables built on Kaggle CPU in chunks, split by whole nights, locked nights never touched | row and night counts per split in the factory manifest |

Reflex feature families: game state; rolling team and player stats (form, pace, clutch, injuries); win
probability and its swings; live odds and line movement (free sources only); standings, playoff stakes,
rivalry; broadcast market and TV-timeout rules (national/local); **narrative features** produced by the
Mind/Teacher as numbers ("comeback brewing", "star heating up", "upset alert" embeddings).

Gates are unchanged (section 4). **Every Noggin PR reports:** parameter count (trees × leaves or NN
weights; LLM params for the Mind), feature count, training row count, RAM, and latency (p50/p99 per tick).
