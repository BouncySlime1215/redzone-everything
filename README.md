# redzone-distill (Kaggle dataset)

Built by `agent/kaggle/pack_data.py` from pinned eval-data-v1 (sha 8857f9c5…). Total 34.5 MB.
There are no secrets or tokens in it. The notebook needs nothing else.

## How to run (Kaggle)

1. Create a dataset: upload this folder as the private dataset **redzone-distill**.
2. Create a notebook: File, then Import Notebook, then choose `teacher_distill.ipynb` from this folder.
3. Settings: Accelerator **GPU T4 x2**, Internet **On**, Add Input **redzone-distill**.
4. Save Version, then **Save & Run All (Commit)**. It takes about 10.5 h: the teacher runs for 7 h, then the students, grading and zip run for about 3 h.
5. When it finishes, open the version, go to Output, and download **out.zip**.

To run the next round, add the previous version's output as an input and run it again. The teacher resumes where it stopped.

## Round 2 and later: upload only the code pack

`rz_code.tar.gz` (3.5 MB) is the round-2 code. You don't need to re-upload the data pack.

1. Create a new private dataset, for example **redzone-code**, holding just `rz_code.tar.gz`. For later rounds, upload a new version of that dataset.
2. In the notebook (File, then Import Notebook, then `teacher_distill.ipynb` from this branch), use Add Input to attach three things:
   - **redzone-distill**, the data pack as before
   - **redzone-code**
   - the **previous version's output** (Notebook Output Files): its teacher answers carry forward, and so does the multi-season build
3. Settings: **GPU T4 x2**, Internet **On**. Then Save Version, then **Save & Run All**. It takes about 11 h.
4. Download **out.zip**.

### V7 (code pack only, Nov 4): Nick's six V6 takeaways

Attach **redzone-distill** (unchanged, no re-upload), the new **rz_code.tar.gz**, and **V6's output** (its 22k teacher
answers and the multi-season build carry forward). GPU T4 x2, Internet On, Save & Run All. MODE is now `"full"`.

1. **B11 split reasoner gate.** The report gets a "[V7] Split reasoner gate" table next to the legacy one: usable JSON
   100%, rule conformance >= 99% (rows the brief alone decides: garbage, your team late, NFL drive, NHL power play),
   confidence ECE <= 0.10, and versus OFF (today's rules): unsafe leaves no worse, must-see recall and lock_acc at most
   2 points worse, plus an abstain cut that answers >= 50% of test rows at unsafe <= 0.5%.
2. **D19 recipe.** fp16 frozen base, fp32 LoRA on all 28 layers incl. the MLP, LR 2e-4; rank 32 for the
   game-reasoner, 16 for the watchdog. V6's adapters are not reused (recipe stamp `v7`).
3. **Whole teacher queue, tiered.** The 32B takes the hardest 8,800 (2.25 h), then the 14B takes the rest (4.25 h,
   about 50k at ~195/min). Every phase's budget shrinks to fit an 11.5 h session, so the run ends with a grade.
4. **JSON-constrained decoding** (`agent/kaggle/constrained.py`) for both students and the base: every answer is
   its schema's JSON by construction (V6 watchdog: 37% unusable).
5. **More watchdog data** for paused, prompt, frozen and upstream: +10 train wordings each and 3x rows per wording
   (train 6,548 to 11,488 rows; 1,440 to 1,680 per weak kind). Valid and test are byte-identical to V6, so V6 and V7
   compare directly. Generator: `agent/train/make_data.py` on branch `claude/watchdog-v7-data`.
6. **Progress in the notebook.** Teacher lines show answers/min, ETA and budget left; the build prints its last
   log line every 10 min; the students print every job's heartbeat every 10 min, besides the `[job]` tee.

Budget: about 11.5 h wall on T4 x2 (setup 0.3, 32B 2.25, 14B 4.25, blind 0.4, students 3.75, grade 0.35),
under the ~13 GPU-h left this week. `RZ_MODE=students` still skips the teacher (~4 h).

### V6 (code pack only, Nov 4)

- Each student job's progress shows in the notebook output, prefixed `[job]`: step, loss and ETA every 50 steps, and answer batches done/total.
- Every round answers and grades the FULL test set (no sampling), in fp16, 8 at a time, with a progress line every 400 rows.
- A trained adapter in an attached notebook output (`students/<job>/peft`) is reused, so a crash after training never costs the training again. Set `RZ_RETRAIN=1` to retrain.
- The frozen base runs in fp16; only the LoRA weights are fp32.
- Each job writes `heartbeat.json`. There is an overall budget, `RZ_STUDENT_BUDGET_S` (default 4.5 h), so the run always ends with partial results and a grade.

Inside Kaggle, round 2 runs these steps:
- It downloads 3 seasons plus the current one of NFL, CFB, NBA, men's college basketball, NHL and MLB from ESPN's public API, on the CPUs. It builds every live play into a labeled moment, split by fixed dates (`agent/distill/split.py` BIG_CUTS). Locked nights are never downloaded.
- At the same time, the 32B teacher takes the hardest 9,000 requests and the 14B teacher takes the easier tail.
- The student trains on a weighted pick from the millions: NFL first, with crunch and your-team moments up-weighted.
- Grading reports per-league results, the train/valid/test gap, slices and the calibrated "I don't know" cut.

## Split, by time and by whole nights

| league | train | valid | dev-test | locked (NOT in this dataset) |
|---|---|---|---|---|
| NFL | 40 nights, 2025-09-05..11-10, 26,497 | 8 nights, 11-11..11-24, 5,133 | 8 nights, 11-25..12-08, 5,361 | 19 nights, 2025-12-09..2026-11-01, 13,727 |
| NBA | 7 nights, 2026-10-20..10-26, 26,780 | 10-27, 2,097 | 10-28, 5,915 | 10-29..11-01, 11,890 |
| NHL | 6 nights, 2026-10-19..10-24, 13,708 | 10-25..10-26, 2,510 | 10-27..10-28, 4,751 | 10-29..11-01, 8,965 |

The locked set includes architecture §7's held-out nights (NFL last 2 slates, NBA/NHL 10-29..11-01, the Nov 1 demo), widened so it holds at least 25% of nights. That is 27 of 102 nights and 34,582 moments. Those rows are left out of this dataset completely, so no teacher request, gold label or training row can touch them. Pack-time checks assert three things:
- no game spans two splits
- every train night is older than every valid, test or locked night
- grounded (hindsight) teacher requests come from train nights only

## Tiers

| tier | what | count |
|---|---|---|
| (a) outcome labels | every non-locked moment, with Nick's rules applied (gold.py) | 92,752 (train 66,985 / valid 9,740 / dev-test 16,027) |
| (a) jump or stay | dead balls with hindsight jump labels | 10,281 |
| (b) teacher queue | ranked hardest and most important first | 81,372: blind grade 4,002, watchdog 104 (fan-out), jump 10,281, grounded train 66,985 |
| (c) gold | Claude-written: 500 lock-in plus 500 jump-or-stay | lock train 360 / valid 53 / test 87; jump 360 / 66 / 74 |
| watchdog | train / valid / test | 6,548 / 275 / 686 |

## Files

| file | bytes |
|---|---|
| data/gold/jump_gold.jsonl | 658823 |
| data/gold/lock_gold.jsonl | 552085 |
| data/grade_ids.json | 82407 |
| data/jump_rows.jsonl.gz | 2371333 |
| data/reasoner/rows_test.jsonl.gz | 1091973 |
| data/reasoner/rows_train.jsonl.gz | 4672983 |
| data/reasoner/rows_valid.jsonl.gz | 739682 |
| data/reasoner/system.txt | 516 |
| data/watchdog/novel.json | 38685 |
| data/watchdog/test.jsonl | 1218945 |
| data/watchdog/test.labels.jsonl | 42744 |
| data/watchdog/train.jsonl | 11588446 |
| data/watchdog/valid.jsonl | 480680 |
| data/watchdog/valid.labels.jsonl | 16738 |
| manifest.json | 7028 |
| teacher_distill.ipynb | 7696 |
| teacher_requests.jsonl.gz | 10818958 |
| code/ (pipeline source, 18 files) | 121664 |

## Noggin data factory (Kaggle CPU, Nov 4)

`factory/rz_factory_code.tar.gz` (code pack) + `factory/kaggle_noggin_factory.ipynb` (notebook). Make the
pack a dataset, attach it, Accelerator None, Internet On, Save & Run All. From run 2, also attach the
previous version's output: it resumes, nothing is rebuilt. Output `factory/` = moments + self-play shards +
`manifest.json` (rows per league, missing seasons). Source: scripts/kaggle_noggin_factory.py on integration/next.
