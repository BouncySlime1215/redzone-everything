# Big teacher, small students (Kaggle)

Everything trains on Kaggle's free 2x T4. Nothing heavy runs on the Mac.

## Flow

1. `agent/kaggle/pack_data.py` runs in the cloud and builds the `kaggle-inputs` branch, which becomes the private Kaggle dataset `redzone-distill` (under 500 MB, no secrets). It writes:
   - (a) every moment, with Nick's rules applied (`agent/distill/gold.py`), plus jump-or-stay dead balls (`agent/distill/jump.py`)
   - (b) the teacher queue, ranked hardest and most important first
   - (c) 500 lock-in and 500 jump-or-stay gold labels
   - the watchdog data
   - the pipeline code itself
2. `agent/kaggle/teacher_distill.ipynb` (source: `teacher_distill.py`; rebuild it with `agent/kaggle/build_notebook.py`) runs these steps:
   - `run_teacher.py`: Qwen3-32B-AWQ on vLLM, with fallbacks. It is resumable and stops at its GPU-hour budget.
   - `run_students.py`: PEFT LoRA (r 8, last 8 layers) on the Mac's own base, `mlx-community/Qwen3-1.7B-4bit`, dequantized exactly (`dequant.py`). It trains one job per T4 and converts with `agent/train/peft_to_mlx.py`.
   - `grade.py`: today vs head vs teacher vs student, on dev-test nights, with the gates.
   - writes `out.zip`
3. To run the next round, attach the previous version's output. The teacher resumes, and the students retrain.

## No-overfitting rules (Nick, 2026-11-03)

`agent/distill/split.py` splits the data by time, in whole nights, per league:
- **locked**: the §7 held-out nights, widened to at least 25% of nights. They are left out of `kaggle-inputs` entirely and scored only for the release PR.
- **train**: the oldest nights, then **valid**, then **dev-test** (the newest nights before locked).

Checks that run at pack time and in the tests:
- no game spans two splits
- the splits are ordered in time
- hindsight (grounded) teacher requests use train nights only
- gold rows outside train are never used for training

Every round reports train vs valid vs dev-test, plus nights after the training cutoff. The 95% gates are judged only on the locked set plus fresh nights.

## Gates

- **Reasoner:** usable JSON 100%, lock_in at least 95% vs gold, unsafe leaves at most 0.5%.
- **Watchdog:** at least 95% correct, every kind at least 90%, 0 unsafe actions.
