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

### V6 (code pack only, Nov 4)

- Each student job's progress shows in the notebook output, prefixed `[job]`: step, loss and ETA every 50 steps, and answer batches done/total.
- The round grade answers a stratified 1,500-row dev-test sample (by task and league). Set `RZ_ANSWER_ALL=1` to answer the full set for a release run.
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
