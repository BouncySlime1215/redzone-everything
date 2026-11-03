# Kaggle run

Account `nickmatta`, 2× T4, 30 GPU-h a week. Nick clicks in Chrome; Claude never signs in.
Kaggle runs cost no Claude usage and keep going during a usage pause. Detail: the notebook's first cell (PR #13).

1. **Inputs:** dataset `nickmatta/redzone-distill` (round-1 pack, from branch `kaggle-inputs`), `nickmatta/redzone-code` (round 2+: `rz_code.tar.gz`; Kaggle auto-extracts it into `code/`), plus the previous version's output.
2. **Uploads over 10 MB:** split into `.bin` parts first (the Chrome upload tool caps files at 10 MB).
3. **Notebook:** `agent/kaggle/teacher_distill.ipynb` (live copy `kaggle.com/code/nickmatta/notebooka466d7f817`). Accelerator T4 ×2, Internet On, Run All.
4. **Watch:** the log prints teacher progress (rows/min). Round 1 Version 2 ran ~45–65 labels/min.
5. **When done:** download `out.zip`, grade it against the gates (`scripts/release_gate.py`), record the result in docs/status.md. Never train on locked nights.
