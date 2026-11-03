# Big teacher, small student

The Mac runs one small model (Qwen3-1.7B 4-bit, ~1 GB, `agent/llm_server.py`) with one LoRA adapter
per job (`watchdog`, `game-reasoner`). It can't run anything bigger inside the 1.5 GB budget, so a
big open model teaches it offline: it writes the words and judgments, the small one learns them.

No paid APIs. The cloud sessions have no GPU and can't reach Hugging Face, so the teacher and the
student training run on a free Kaggle GPU notebook (2x T4, 30 GPU-hours a week) that Nick starts.

## The loop

| step | where | command / file | output |
|---|---|---|---|
| 0. briefs + hindsight | anywhere | `scripts/export_reasoner_llm.py` (reasoner thread) | `rows_*.jsonl`, `chat_*.jsonl` |
| 1. teacher requests | anywhere | `scripts/distill.py requests --rows-dir … --watchdog` | `data/distill/teacher_requests.jsonl` |
| 2. teacher | Kaggle | `agent/distill/kaggle/teacher_kaggle.ipynb` | `teacher_out.jsonl`, `run_report.json` |
| 3. student data | anywhere | `scripts/distill.py student-bundle --answers teacher_out.jsonl --watchdog-data agent/train/data` | `data/distill/student-input/` |
| 3b. watchdog wordings | anywhere | `scripts/distill.py wordings --answers teacher_out.jsonl` | `agent/train/wordings_teacher.json` |
| 4. students | Kaggle | `agent/distill/kaggle/student_kaggle.ipynb` | `adapters/<job>/` (PEFT), `<job>_test_out.jsonl` |
| 5. to the Mac | anywhere | `agent/train/peft_to_mlx.py` (watchdog thread) | `agent/model/<job>/adapters.safetensors` |
| 6. grade | anywhere | `scripts/distill.py eval --teacher … --student game-reasoner_test_out.jsonl` | today / head / teacher / student table |

## What the teacher does, and what it never decides

* **Game reasoner, held-out nights (blind).** The teacher answers the exact question the student gets
  (same system prompt + brief). Only used to grade the teacher against today and the student.
* **Game reasoner, train/valid nights (grounded).** The teacher sees the brief *and* what actually
  happened next, and writes `storyline` / `why` / `next` as a forecast a sharp viewer could have made
  from the brief alone. `lock_in`, `safe_to_leave`, `stay_s` and `confidence` stay exactly as the
  export wrote them from hindsight and the decider's rules: that is what gets graded, and a teacher
  never overrides it. Answers that quote the future ("in hindsight", "what happened next") are dropped.
* **Watchdog.** New wordings of each error kind (only the TRAIN wordings are shown as examples, never
  the held-out ones), each checked by a second blind teacher pass that must name the same kind. Only
  agreements go to `agent/train/wordings_teacher.json`; the watchdog's `make_data.py` adds them to
  TRAIN only after its own leak check. Labels still come from `agent/policy.py` rules and
  `teach_novel`, so the action a line maps to is never the teacher's call.
* **Jump vs stay.** Prompt and parser are in place (`jump_requests`, `clean_jump`); they run once the
  highlight-jump thread exports dead-ball rows with a `jump` context.

Held-out = whole nights: the export's `test` split is the nights the scorecards grade; nothing from
those nights is ever trained on (`student.build` refuses test rows).

## Grading (`agent/distill/evaluate.py`)

All sources are scored against hindsight on the test nights. `today` = the brain's "key soon" odds in
the brief and `head` = the reasoner thread's fast lock-in head, each mapped to lock-in levels with cuts
fitted per league on valid nights. Lines: must_see precision / recall / lift over the base rate of
big plays, lock-in accuracy, unsafe leaves (said safe, a big play followed), over-cautious stays, and
the error of the time named in `next`.
