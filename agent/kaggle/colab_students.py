# %% [markdown]
# # RedZone Mind students on Colab (self-contained)
#
# __TITLE__
#
# No Drive, no secrets, nothing to upload: the code and data come from the public `kaggle-inputs` branch at a
# pinned commit, and the teacher answers (when a job needs them) from a public URL. Runtime: **__GPU__**, then
# Runtime > **Run all** (background execution is fine). If the runtime restarts but keeps its disk, Run all
# again: it resumes from `/content/rzrun` (training checkpoint every 30 min, answers kept, finished jobs
# skipped). The last cell prints **grades.json** and the compute units spent, and offers out.zip to download.

# %%
PIN = "__PIN__"                 # kaggle-inputs commit: code pack + data
REPO = "BouncySlime1215/redzone-everything"
JOBS = "__JOBS__"               # "watchdog", "game-reasoner" or "game-reasoner,watchdog"
ANSWERS_URL = "__ANSWERS__"     # public teacher answers (.zip with teacher_out.jsonl, or .jsonl/.jsonl.gz); "" = none
SESSION_H = float("__SESSION_H__")       # the whole run's budget
STUDENT_H = float("__STUDENT_H__")       # of it, the students (train + answer every test row); grading gets the rest
EPOCHS = "__EPOCHS__"           # "" = the recipe's own (game-reasoner 2, watchdog 1)
CKPT_MIN = 30
CU_PER_H = {"A100": 13.0, "L4": 5.0, "T4": 2.0}    # Colab compute units per GPU-hour (Nov 2026)

# %%
import glob
import gzip
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

T_START = time.time()
import torch  # noqa: E402

if not torch.cuda.is_available():
    raise SystemExit("No GPU: Runtime > Change runtime type > a GPU, then Run all again")
GPU = torch.cuda.get_device_name(0)
RATE = next((v for k, v in CU_PER_H.items() if k in GPU), None)
print("GPU:", GPU, "| CU/h:", RATE)

# %%
# Inputs, all public: the pack at PIN, and (optional) teacher answers from ANSWERS_URL.
RUN = "/content/rzrun"
INP = f"{RUN}/input"
PACK = f"{INP}/kaggle-inputs"
if not os.path.exists(f"{PACK}/manifest.json"):
    os.makedirs(PACK, exist_ok=True)
    url = f"https://codeload.github.com/{REPO}/tar.gz/{PIN}"
    print("downloading", url)
    urllib.request.urlretrieve(url, f"{RUN}/kaggle-inputs.tar.gz")
    with tarfile.open(f"{RUN}/kaggle-inputs.tar.gz") as t:
        for m in t.getmembers():
            parts = m.name.split("/", 1)
            if len(parts) == 2 and parts[1] and ".." not in parts[1].split("/"):
                m.name = parts[1]
                t.extract(m, PACK)
    os.remove(f"{RUN}/kaggle-inputs.tar.gz")
print("pack:", json.load(open(f"{PACK}/manifest.json"))["counts"])
PREV = f"{INP}/prev"
if ANSWERS_URL and not glob.glob(f"{PREV}/**/teacher_out.jsonl", recursive=True):
    os.makedirs(PREV, exist_ok=True)
    name = ANSWERS_URL.rsplit("/", 1)[-1].split("?")[0]
    dst = f"{RUN}/{name}"
    print("downloading", ANSWERS_URL)
    urllib.request.urlretrieve(ANSWERS_URL, dst)
    if name.endswith(".zip"):
        with zipfile.ZipFile(dst) as z:
            z.extractall(PREV)
    elif name.endswith(".gz"):
        with gzip.open(dst, "rb") as f, open(f"{PREV}/teacher_out.jsonl", "wb") as g:
            shutil.copyfileobj(f, g)
    else:
        shutil.copy(dst, f"{PREV}/teacher_out.jsonl")
print("teacher answers:", glob.glob(f"{PREV}/**/teacher_out.jsonl", recursive=True) or "none")

# %%
# The round: the same script as on Kaggle, students only.
KW = f"{RUN}/working"
os.makedirs(KW, exist_ok=True)
CODE = f"{RUN}/rzcode"
if not os.path.exists(f"{CODE}/code/agent/kaggle/teacher_distill.py"):
    with tarfile.open(f"{PACK}/rz_code.tar.gz") as t:
        t.extractall(CODE)
env = {**os.environ,
       "RZ_KAGGLE_WORKING": KW, "RZ_KAGGLE_INPUT": INP,
       "RZ_PRESET": "full", "RZ_MODE": "students", "RZ_JOBS": JOBS,
       "RZ_SESSION_H": str(SESSION_H), "RZ_STUDENT_BUDGET_H": str(STUDENT_H),
       "RZ_CKPT_S": str(CKPT_MIN * 60),
       "RZ_JOB_ORDER": "watchdog,game-reasoner",
       "RZ_ANSWER_S_PER_ROW": "0.5" if RATE and RATE >= 5 else "0.8",
       "RZ_BASE_CACHE": f"{RUN}/base-deq",
       "RZ_PREV_ROOTS": "",                        # only this run's own adapters are ever reused
       "RZ_CODE_PACK": f"kaggle-inputs@{PIN}",
       "PYTHONUNBUFFERED": "1",
       **({"RZ_EPOCHS": EPOCHS} if EPOCHS else {})}
log_path = f"{RUN}/run_{time.strftime('%Y%m%d-%H%M')}.log"
with open(log_path, "w") as log:
    p = subprocess.Popen([sys.executable, "-u", f"{CODE}/code/agent/kaggle/teacher_distill.py"], cwd=RUN,
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
        log.write(line)
    rc = p.wait()
print("round exit code:", rc)

# %%
# grades.json (printed: it is the result), the compute spent, out.zip.
wall_h = (time.time() - T_START) / 3600
cu = round(wall_h * RATE, 1) if RATE else None
if os.path.exists(f"{KW}/grades.json"):
    g = json.load(open(f"{KW}/grades.json"))
    g["colab"] = {"gpu": GPU, "wall_h": round(wall_h, 2), "cu_spent": cu, "jobs": JOBS, "pin": PIN}
    json.dump(g, open(f"{KW}/grades.json", "w"), indent=1)
    print("===== grades.json =====")
    print(json.dumps(g, indent=1))
    print("COMPLETE" if g.get("complete") else "NOT COMPLETE: Run all again on this runtime to resume")
else:
    print("no grades.json; see", log_path)
print(f"compute: {wall_h:.2f} h on {GPU} = ~{cu} CU")
try:
    from google.colab import files
    if os.path.exists(f"{KW}/out.zip"):
        files.download(f"{KW}/out.zip")
except Exception as e:  # noqa: BLE001 - outside Colab, or the browser blocked it
    print("out.zip is at", f"{KW}/out.zip", f"({type(e).__name__})")
