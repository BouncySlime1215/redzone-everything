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

## 8. Priority (Nick, Nov 4 2:05 AM: "priority is key")

Its own module, `redzone/noggin/priority.py`, tests in `tests/test_noggin_priority.py`. Owner: NOGGIN.
Four layers, applied in order:

| Layer | Source | What it holds | Can it be overridden? |
|---|---|---|---|
| **L1 Nick's hard rules** | config only (`teams`, `core.league_priority`, banned apps) | your-team (WSH: Commanders/Wizards/Capitals/Nationals; UNC) big plays 100 %; crunch time first; league order NFL > CFB > NBA > CBB > NHL > MLB; never Peacock | **never**, by AI or by any other layer |
| **L2 Nick's preferences** | phone-page settings (`noggin.priority.prefs`) | bets weight, rivalries, NHL PP/OT boost, reels on/off, switch frequency; each clamped to a range | only by Nick; learned 👍/👎 suggestions are shown to him, never auto-applied (`suggest_from_feedback` returns `applied: false`) |
| **L3 Mind read** | the Mind, every 30-60 s | narrative and stakes numbers in [-1, 1]: comeback brewing, star heating up, playoff race, upset alert, bet decisive next | bounded: the total is capped at ±15 points and expires after its TTL (90 s); it can reorder games only inside the same L1 tier |
| **L4 Reflex live value** | the Reflex, every tick | red-zone drive, close-late, power play, win-prob swing, restart countdown | it is the base score |

**How L1 is impossible to break.** Games are sorted by the tuple `(tier, league rank inside crunch,
−score, game id)`. Tier is computed from L1 alone (0 your team in a big moment or crunch, 1 crunch,
2 the rest); L2-L4 only change `score`. A game on a banned app is never ranked. `violations()` audits a
ranking, and a 2,000-slate property test feeds hostile L2-L4 inputs (huge weights, NaN, stale Mind
reads) and asserts zero violations.

**Deterministic.** `rank(games, rules, prefs, minds, now)` is pure: no clock reads, no randomness, ties
broken by game id. The night lab replays it and gets the same order and reasons.

**Reason strings.** Every ranked game carries its top three positive contributions, e.g.
`UNC your team + run + your bet + rivalry` or `KU run + your bet + crunch`, for the phone page and the
switch trace.

**North Star rows** (sent to the harness thread): your-team big plays 100 %, crunch live ≥ 95 %,
league order respected (no lower-league non-crunch over a higher-league crunch), and "right game"
agreement with Nick's choices once 👍/👎 data exists.

**Wiring (next).** The module is not yet on the live path. It goes in behind `noggin.priority.enabled`
(OFF), first in shadow next to today's `score.league_weight` / crunch priority in the decider, and is
scored on the same nights before it turns on.

## 9. Phase 1 results (Nov 4, NBA)

**Data factory v1** (`redzone/noggin/factory.py`, `features.py`, `scripts/noggin_factory.py`, workflow
`noggin-factory.yml`): hoopR NBA play-by-play, every season with a wall clock (2013-14 on; older hoopR
seasons have none). 17,219 games, **1,090,023 labeled dead windows**, each labeled by the live
`cuts.DeadTracker` (same kind names, same restart rule) with 16 rule/state features plus
`big_next60`. Splits by whole night, time-ordered: train 2013-02 → 2022-10 (1,967 nights), calib
2022-10 → 2024-10 (421), test 2024-10 → 2026-06 (421). Nothing on or after the scorecard window
(2026-10-20) is written. Build: ~1 min for 14 seasons on 4 CPU cores.

**Noggin Timing v1** (`redzone/noggin/timing.py`, `timing_fit.py`, `live.py`): discrete-time hazard,
5 s bins, LightGBM (300 trees, 18,300 leaves, 17 features incl. elapsed), trained on **8.97M person-period
rows**; split-conformal lower bound (alpha 2 %, Mondrian by kind × elapsed bucket, never loosened past
the raw bound). Exported to numpy: `redzone/noggin/models/timing_nba.v1.npz` (121 KB), numpy vs
LightGBM max diff 1.2e-7, one reading 2.2 ms p50 / 3.7 ms p99, cached per window per 5 s bin.

Factory test nights (421 unseen nights, 166k windows, 982k checkpoints):

| | Timing v1 lower bound | today's `cuts.FLOOR_S` |
|---|---|---|
| restarts before the bound | **0.56 %** (every kind ≤ 1.9 %) | 7.33 % |
| mean bound (s of dead time left) | 11.5 | 12.2 |
| ECE, P(restart within 30 s) | **0.053** (target ≤ 0.10) | – |

**Live path, NBA scorecard at B = 90 with measured feed lag** (`--real-lag --behind-live 90`, dev
nights = split `fit`; locked nights not read): with `noggin.timing.leagues: [nba]`, entries mid-play
5/61 → 5/61, plays missed on entry 3 → 3, dead air 19.1 → 19.2 %, big plays seen 84.5 → 84.5 %,
crunch live 90.9 → 90.9 %, 0 mid-play cuts. **Timing v1 alone does not move the 9 → 0 target.**

Why: the dev-night mid-play entries are not break-length misses. 3 of 5 are crunch-time entries
(`decider.entry.crunch_now: live` goes in at once inside the last 2:00) and 2 are entries off a
finished game after the 180 s wait cap (`max_wait_s.nba`), both landing on a live ball with no whistle
in between. Entry policy knobs (measured, dev nights, not shipped):

| setting | entries mid-play | plays missed on entry | big plays seen | crunch live |
|---|---|---|---|---|
| today | 5 / 61 | 3 | 84.5 % | 90.9 % |
| `crunch_now: wait` | 3 / 61 | 2 | 80.3 % | 90.0 % |
| `crunch_now: wait` + `max_wait_s.nba: 600` | **1 / 61** | 0 | 80.3 % | 90.0 % |

So the next step is NOGGIN PICK on entries: wait for the next dead window the buffer already shows
(the 45 s lead at B = 90 shows the next whistle before it airs), scored with Timing v1's bound, and
tuned with forward-chaining CV over nights so the big-play cost is measured, not guessed. Timing v1
ships OFF behind `noggin.timing.leagues`.

Next leagues for the factory: NFL (nflfastR `time_of_day`), CBB/WCBB (same loader as NBA, needs a
live-path check), CFB, NHL (fastRhockey + ESPN wallclock), MLB.

## 10. Phase 1b + 2 results (Nov 4, NFL Timing, NBA Predict)

**Data factory, NFL** (`factory.build_football`): nflverse / nflfastR play-by-play 2011-2024
(`time_of_day` is the snap, like ESPN's NFL wallclock). **587,528 labeled windows**, 3,811 games; train
2011-09 → 2021-09 (571 nights), calib → 2023-09 (122), test → 2025-02 (122). The 2025 season (the scorecard
window) is never written. To make a label mean what the live tracker sees on ESPN (checked against 150
archived ESPN games): the extra point / two-point try folds into the touchdown entry, END QUARTER / END GAME
take the previous play's wallclock (ESPN: median 0 s), and the windows around each 2:00 crossing in Q2/Q4
are dropped (nflfastR has no two-minute-warning row, so that break is unseen). ESPN's "Official Timeout"
(TV) entries have no nflfastR row: the model learns the physical restart after a punt/score/injury, which
is the safe direction for a lower bound, and the live hook returns no reading for kinds it never trained
on (`tv_timeout`, `two_minute`), so those keep `cuts.FLOOR_S`.

**Noggin Timing v1, NFL** (`models/timing_nfl.v1.npz`, 120 KB, 300 trees, 4.77M person-period rows,
17 features). Test: 122 unseen nights, 82k windows:

| | Timing v1 | `cuts.FLOOR_S` |
|---|---|---|
| restarts before the bound | **1.43 %** | 9.1 % |
| mean bound (s left) | 6.3 | 13.0 |
| ECE, P(restart within 30 s) | 0.076 | – |

Injury (8.1 %) and review (4.7 %) are above alpha on the test nights: too few calib rows to calibrate them per
kind. Candidate NFL scorecard with `noggin.timing.leagues: [nba, nfl]`, dev / locked: mid-play 0 / 0 (=),
big plays live 49.4 → 49.6 / 45.6 → 45.8 %, crunch live 65.4 → 65.2 / 67.0 → 68.1 %, dead air
12.5 → 12.3 / 12.6 → 12.5 %. A safer bound at no measured cost; ships OFF.

**Noggin Predict v1, NBA** (`predict.py`, `predict_fit.py`, `models/predict_nba.v1.npz`, 302 KB): P(big play
within 30 / 60 / 120 s of the restart), 13 features (`features.PREDICT_FEATURES`: window + game state at
the restart, one definition with the live `PredictHook`), LightGBM per horizon (225 / 211 / 238 trees) +
isotonic calibration on calib. 757k train windows. 5-fold season forward CV (2021-2025 seasons), and one
grade on the FULL test split (421 nights, 166k windows):

| horizon | CV logloss mean / worst | test logloss (baseline) | test PR-AUC (baseline) | test ECE | beats baseline every fold |
|---|---|---|---|---|---|
| 30 s | 0.491 / 0.498 | 0.495 (0.509) | 0.270 (0.240) | 0.006 | yes |
| 60 s | 0.624 / 0.631 | 0.629 (0.652) | 0.444 (0.416) | 0.011 | yes |
| 120 s | 0.652 / 0.653 | 0.647 (0.684) | 0.642 (0.615) | 0.012 | yes |

Baseline = base rate × game state (train-night rate per period × 2-minute clock bucket × margin bucket).
Latency: 8 games × 3 horizons 0.75 ms p50 / 1.35 ms p99. The signal is real but small (AUC ~0.59): made
threes are close to random. As an L4 term in the Priority pick (`noggin.predict.points` × (P60 − base
rate), needs `noggin.priority.enabled`), candidate NBA scorecard dev / locked: big plays live 81.7 → 81.7 /
100 → 100 %, crunch live 90.9 → 89.6 / 93.3 → 93.3 %, entries mid-play 5 → 4 / 4 → 4, dead air
19.2 → 18.8 / 24.2 → 24.2 %. The NBA sim switches ~1.2 times an hour, so a pick-time term has little to act
on. Ships OFF (`noggin.predict.leagues: []`) and does not join the candidate.
