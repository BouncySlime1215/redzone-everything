# %% [markdown]
# # RedZone Mind V8, students only, on free Google Colab (one T4)
#
# Trains and grades the two students (game-reasoner, watchdog) from V7's teacher answers, in one free
# Colab session (~3.6 h), with no secrets: the code and data come from the public `kaggle-inputs` branch
# at a pinned commit, and V7's answers come from your own Google Drive.
#
# **One-time setup:** put V7's Kaggle output **out.zip** (or just its `teacher_out.jsonl`, plus
# `watchdog/test_out_base.jsonl` if you have it) in Google Drive at `MyDrive/redzone_v8/inputs/`.
#
# **Run:** Runtime > Change runtime type > **T4 GPU**, then Runtime > **Run all**. Keep the tab open.
#
# **If Colab disconnects:** just Run all again. Everything lives in `MyDrive/redzone_v8/students/`:
# a training checkpoint every 30 min, answers appended as they come, finished jobs skipped.
#
# **Results:** `MyDrive/redzone_v8/grades.json` (the summary; `complete: true` once every test row is
# answered) and `MyDrive/redzone_v8/results/<time>/` (out.zip with the Mac adapters, report.md, the log).

# %%
PIN = "__PIN__"                 # kaggle-inputs commit: the code pack + data this notebook was written for
REPO = "BouncySlime1215/redzone-everything"
DRIVE_DIR = "/content/drive/MyDrive/redzone_v8"
SESSION_H = 3.6                 # the whole run's budget; students get STUDENT_H of it, grading the rest
STUDENT_H = 3.2
CKPT_MIN = 30                   # training checkpoint to Drive this often
# False: one session; if time runs out the reasoner keeps its partly trained adapter (graded, marked
# stopped_early). True: it checkpoints instead and the next Run all finishes the epoch (two sessions).
FINISH_TRAINING = False

# %%
import glob
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

from google.colab import drive

drive.mount("/content/drive")
STUDENTS = f"{DRIVE_DIR}/students"
for d in (f"{DRIVE_DIR}/inputs", STUDENTS, f"{DRIVE_DIR}/results"):
    os.makedirs(d, exist_ok=True)
import torch  # noqa: E402

if not torch.cuda.is_available():
    raise SystemExit("No GPU: Runtime > Change runtime type > T4 GPU, then Run all again")
print("GPU:", torch.cuda.get_device_name(0))

# %%
# Inputs: the public pack at PIN (no git, no login), and V7's answers from Drive.
INP = "/content/input"
PACK = f"{INP}/kaggle-inputs"
if not os.path.exists(f"{PACK}/manifest.json"):
    os.makedirs(PACK, exist_ok=True)
    url = f"https://codeload.github.com/{REPO}/tar.gz/{PIN}"
    print("downloading", url)
    urllib.request.urlretrieve(url, "/content/kaggle-inputs.tar.gz")
    with tarfile.open("/content/kaggle-inputs.tar.gz") as t:
        for m in t.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) == 2 and parts[1] and ".." not in parts[1].split("/"):
                m.name = parts[1]
                t.extract(m, PACK)
    os.remove("/content/kaggle-inputs.tar.gz")
print("pack:", json.load(open(f"{PACK}/manifest.json"))["counts"])

V7 = f"{INP}/v7"
if not glob.glob(f"{V7}/**/teacher_out.jsonl", recursive=True):
    os.makedirs(V7, exist_ok=True)
    zips = sorted(glob.glob(f"{DRIVE_DIR}/inputs/*.zip"))
    if zips:
        with zipfile.ZipFile(zips[-1]) as z:
            z.extractall(V7)
        print("V7 output:", zips[-1])
    elif os.path.exists(f"{DRIVE_DIR}/inputs/teacher_out.jsonl"):
        shutil.copy(f"{DRIVE_DIR}/inputs/teacher_out.jsonl", V7)
        base = f"{DRIVE_DIR}/inputs/test_out_base.jsonl"
        if os.path.exists(base):
            os.makedirs(f"{V7}/watchdog", exist_ok=True)
            shutil.copy(base, f"{V7}/watchdog/")
    if not glob.glob(f"{V7}/**/teacher_out.jsonl", recursive=True):
        raise SystemExit(f"Put V7's out.zip (or teacher_out.jsonl) in {DRIVE_DIR}/inputs/ and Run all again")
print("teacher answers:", glob.glob(f"{V7}/**/teacher_out.jsonl", recursive=True),
      "| watchdog base answers:", glob.glob(f"{V7}/**/watchdog/test_out_base.jsonl", recursive=True))

# %%
# Run the round: the same script as on Kaggle, students only, with its state on Drive.
KW = "/content/working"
os.makedirs(f"{KW}/rz", exist_ok=True)
if not os.path.islink(f"{KW}/rz/students"):
    os.symlink(STUDENTS, f"{KW}/rz/students")         # checkpoints, data, adapters and answers on Drive
CODE = "/content/rzcode"
if not os.path.exists(f"{CODE}/code/agent/kaggle/teacher_distill.py"):
    with tarfile.open(f"{PACK}/rz_code.tar.gz") as t:
        t.extractall(CODE)
env = {**os.environ,
       "RZ_KAGGLE_WORKING": KW, "RZ_KAGGLE_INPUT": INP,
       "RZ_PRESET": "quick", "RZ_MODE": "students",
       "RZ_SESSION_H": str(SESSION_H), "RZ_STUDENT_BUDGET_H": str(STUDENT_H),
       "RZ_CKPT_S": str(CKPT_MIN * 60), "RZ_FINISH_TRAINING": "1" if FINISH_TRAINING else "0",
       "RZ_JOB_ORDER": "watchdog,game-reasoner",      # the short job first: a graded watchdog in ~40 min
       "RZ_ANSWER_S_PER_ROW": "0.8",                   # each job's answer reserve on the T4 (fp16, batch 8)
       "RZ_BASE_CACHE": "/content/base-deq",           # the 3.4 GB dequantized base stays on local disk
       "RZ_PREV_ROOTS": "",                            # only this run's own adapters are ever reused
       "RZ_CODE_PACK": f"kaggle-inputs@{PIN}",          # grades.json's stamp names the exact pack
       "PYTHONUNBUFFERED": "1"}
stamp = time.strftime("%Y%m%d-%H%M")
log_path = f"{DRIVE_DIR}/results/run_{stamp}.log"
rc = None
with open(log_path, "w") as log:
    p = subprocess.Popen([sys.executable, "-u", f"{CODE}/code/agent/kaggle/teacher_distill.py"], cwd="/content",
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
        log.write(line)
        log.flush()
    rc = p.wait()
print("round exit code:", rc)

# %%
# Results to Drive (whatever exists, even after a failure).
res = f"{DRIVE_DIR}/results/{stamp}"
os.makedirs(res, exist_ok=True)
for f in (f"{KW}/grades.json", f"{KW}/out.zip", f"{KW}/rz/report.md", f"{KW}/rz/report.json"):
    if os.path.exists(f):
        shutil.copy(f, res)
shutil.copy(log_path, res)
if os.path.exists(f"{KW}/grades.json"):
    shutil.copy(f"{KW}/grades.json", f"{DRIVE_DIR}/grades.json")
    g = json.load(open(f"{KW}/grades.json"))
    print(json.dumps(g, indent=1))
    print("\nALL DONE" if g.get("complete") else
          "\nNOT COMPLETE (see coverage above): Run all again; it resumes from Drive")
else:
    print("no grades.json this session; see", log_path, "and Run all again to resume")
drive.flush_and_unmount()
print("saved to", res)
