# Stage 6 reranker arm (A-03)

The value model as a **reranker inside the show's planner**, run as a plug-in arm of the F-04 harness.
Code: `redzone/lab/armeval/stage6.py`. Tests: `tests/test_stage6_arm.py`. Research only: the show never imports it.

## What it does

The planner keeps every decision about what is legal, ready and warranted (Rule #1 mask, guard, high-leverage
seconds). The model only replaces the planner's guess for the worth of a second the feed has not reported
(`Planner.unknown.rate(game, last reported second, now)`, today `unk_w x leverage`):

- the model's number for the game, read at the last 10 s step at or before the reported second, from rows stamped at
  or before that moment (causal);
- scaled **per league** so its running mean equals the guess's running mean in that league;
- **clamped** to 0.1x .. 4x the guess for that second (`ARMEVAL_CLAMP`);
- a league the model does not hold (football, hockey ...) keeps the planner's guess.

This is `ModelRate` from the stage 6 notebook (`research/brain/notebooks/stage6_rerank.py`, ledger L105), ported
line for line. Only the **state-only** export is ported (4 features: `lev_now`, `since_stop`, `last_kind`,
`since_to`). The full v3 model (60 features) is not ported: its L105 verdict was NOT THE LEVER.

## Run it

```sh
# 1. one fenced model file from the Kaggle export pair (the export has no training_cutoff: stamp it)
python -m redzone.lab.armeval.stage6 pack --json stage4_state_only.json --npz stage4_state_only.npz \
    --training-cutoff 2025-09-30 --out stage6_state_only_oof.npz
python -m redzone.lab.armeval.stage6 check stage6_state_only_oof.npz --nights 20261021

# 2. the arm, paired with the planner, on a 15-minute slice of a dev night (about 15 s on the Mac)
python scripts/arm_verdict.py run --arm stage6_rerank --hook redzone.lab.armeval.stage6:factory \
    --model stage6_state_only_oof.npz --nights 20261021 --minutes 15

# 3. recount from the raw logs, then L105's metric on that run
python scripts/arm_verdict.py recompute data/armeval/<run>
python scripts/arm_verdict.py l105 data/armeval/<run>
```

Model files and data are never committed. Run at `taskpolicy -b nice -n 15` on the Mac.

## How the model is loaded

- **Arm (research):** the harness fence-checks `--model` (`redzone/lab/fence.py`: a `training_cutoff` before the
  first scored night, no fenced night trained on) and records its sha256. The factory reads the file **once**, refuses
  bytes that do not hash to what was checked, re-runs the fence on those bytes, requires `stage6_kind: state_only`
  and requires the trees to reproduce the export's own stored check rows.
- **Show (runtime):** only through the F-02 registry: `stage6_promoted(run_id)` uses `registry.read_promoted`,
  which refuses any unpromoted, revoked or changed model. Nothing calls it yet; a model is promoted only after a PASS.
- **The shipped stage 6 files are refused** (`models/value-v1/` and `models/value-v1-oof/`): they carry no
  `training_cutoff`. The fence says: `no training_cutoff: every model file must say the last night it learned from`.
  `pack` stamps it; it refuses a cutoff earlier than the day before the export's first test night (`split.first_test`
  is 20251001 for the out-of-fold export, so 2025-09-30 is the earliest honest cutoff).

## L105 is not reproduced here: what differs

L105: state-only out-of-fold, pooled over leagues, **+3.2 [1.2, 5.6]** high-leverage points against a +5 bar;
NBA-only +6.5 [2.6, 10.4] passes; Rule #1 0/0/0 in every arm. What the harness run differs in:

| Piece | L105 (Kaggle) | This arm (harness) |
|---|---|---|
| Engine | `park_sim.run` + `park_plan.Planner` pinned at park-prime `6cf57858`, brain `foresight`, `setting("today", shots=False)` | `park_replay` build + drive: the show on fake sticks with the feed edge, at the PR's commit |
| Baseline | that pinned planner | the planner at the PR's commit |
| Nights | 140 headline nights (59 NBA-only, 67 NBA + college, 14 football + NBA) from the stage 5 slates, built from the Kaggle history datasets | dev nights of the local archive only; `load_replay` refuses other nights and builds NBA slates (+ one shifted NFL game): **no NBA + college mix** |
| Locked window | the 140 nights may include 20251212..20260105 (to check: the night list is in the full report on Kaggle) | locked nights are refused outright |
| Model file | the export pair, no cutoff | one packed file with `training_cutoff` |
| Row stamps | raw `wallclock` (monotone fill) | the show's repaired stamps (late-edit rows put back) |
| Interval | `stage5_policy.boot` (400 draws, one shared RNG(5) consumed across arms) | same procedure, a fresh RNG(5) per call: not bit-identical |
| Diagnostics | clamped share, flip divergence, guard margins per flip | the hook's counters (`stats()`); not in the verdict |

## What must run on Kaggle / Colab (nothing remote runs from the Mac until N1/N2)

**Route A: rerun L105 itself** (reproduces the number on its own engine; about 40 minutes of Kaggle CPU):

1. Attach the stage 5 slates output, the three history datasets (NBA/college, older, NFL), the notebooks folder,
   `models/value-v1` and the out-of-fold retrain output (stage 4 v3.1 export, `S4_FIRST_TEST=20251001`).
2. `S6_POOL_SMOKE=1` first: one cell `!S6_POOL_SMOKE=1 python "$(find /kaggle/input -name stage6_rerank.py | head -1)"`,
   which must print POOL SMOKE PASS.
3. The real run: `!python "$(find /kaggle/input -name stage6_rerank.py | head -1)"`; read the four VERDICT lines and
   `stage6_report.json` (night list included).

**Route B: the repo arm at scale** (the new standard; every piece below is missing today):

1. A packed, stamped model: the `pack` command above on the out-of-fold export (coordinator's OK: it writes the
   cutoff into a model record).
2. The 140 nights in the archive layout (`data/archive/{nba,cbb,nfl}/summary_<gid>.json` plus scoreboards) on the
   Kaggle/Colab machine, from the history datasets.
3. A night loader for history nights with mixed leagues: `scripts/park_replay.py load_replay` takes one league and
   only `splits.nights_for(..., "fit")` dev nights (`redzone/lab/splits.py` is protected and frozen). Needs a
   reviewed door for non-dev history nights; locked nights stay refused.
4. Pre-registered bars the harness can read: the L105 bars are on high-leverage points, which are not in
   `ARMEVAL_PAIRED`, so today they are applied by `arm_verdict.py l105` after the run, not by the verdict.
5. The F-03 job runner (or the Chrome fallback) to launch it. Then, per batch of nights:

```sh
python scripts/arm_verdict.py run --arm stage6_rerank --hook redzone.lab.armeval.stage6:factory \
    --model stage6_state_only_oof.npz --nights <the 140 night specs> --out data/armeval/stage6-l105
python scripts/arm_verdict.py recompute data/armeval/stage6-l105
python scripts/arm_verdict.py l105 data/armeval/stage6-l105
```

Expect different numbers from L105 even when all five are done: the engine and baseline are newer (table above).
