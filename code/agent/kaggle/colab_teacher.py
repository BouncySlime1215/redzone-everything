# %% [markdown]
# # RedZone Teacher 32B on Colab (EXP-086 under plan B, EXP-102 under plan A; self-contained)
#
# Builds reasoner moments, asks Qwen3-32B-AWQ about TRAIN nights only, grades its blind calls against what really
# happened (beside the fast head), and hands you teacher_out.jsonl for the students.
# No Drive, no secrets, nothing to upload: the code comes from the public `kaggle-inputs` branch at a pinned commit.
#
# **Data gate (Nick's A/B call, decisions.md):** as shipped `DATA_PLAN = ""` and the run stops at once.
# - `"A"` (EXP-102, catalog-OK only): NFL + NBA moments rebuilt from nflverse and NBA.com cdn play-by-play.
# - `"B"` (EXP-086): CBB, NHL, MLB moments from ESPN's site API, which the #202 catalog rates red for training.
#
# Runtime: **A100 GPU**, then Runtime > **Run all** (~3.5 h, ~45 CU at 13 CU/h). If the runtime restarts but keeps
# its disk, Run all again: finished games and answers are kept. The last cell prints grades.json and the CU spent.

# %%
PIN = "__PIN__"                 # kaggle-inputs commit: code pack + data (+ the factory pack for plan A)
REPO = "BouncySlime1215/redzone-everything"
DATA_PLAN = ""                  # "A" or "B" only after Nick picks it; "" stops before any download
LEAGUES = {"A": "nfl nba", "B": "cbb nhl mlb"}.get(DATA_PLAN, "")
EXP = {"A": "EXP-102", "B": "EXP-086"}.get(DATA_PLAN, "")
BUILD_H = 1.0                   # moment downloads (CPU) before the Teacher starts
TEACHER_H = 2.3                 # Teacher budget; it stops early when the queue is empty
CU_PER_H = {"A100": 13.0, "L4": 5.0, "T4": 2.0}

# %%
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

T_START = time.time()
if DATA_PLAN not in ("A", "B"):
    raise SystemExit("DATA_PLAN is not set: put 'A' or 'B' in the first cell once Nick has picked a plan (decisions.md)")
import torch  # noqa: E402

if not torch.cuda.is_available():
    raise SystemExit("No GPU: Runtime > Change runtime type > A100 GPU, then Run all again")
GPU = torch.cuda.get_device_name(0)
RATE = next((v for k, v in CU_PER_H.items() if k in GPU), None)
print("GPU:", GPU, "| CU/h:", RATE)

# %%
RUN = "/content/rzteacher"
PACK = f"{RUN}/input/kaggle-inputs"
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
CODE = f"{RUN}/rzcode"
if not os.path.exists(f"{CODE}/code/agent/kaggle/teacher_only.py"):
    with tarfile.open(f"{PACK}/rz_code.tar.gz") as t:
        t.extractall(CODE)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "vllm", "httpx", "pyyaml", "lightgbm"], check=False)
ARCHIVE = ""
if DATA_PLAN == "A":            # green summaries from nflverse + NBA.com cdn (factory pack, same pinned commit)
    FCODE = f"{RUN}/fcode"
    if not os.path.exists(f"{FCODE}/rz/scripts/green_summaries.py"):
        with tarfile.open(f"{PACK}/factory/rz_factory_code.tar.gz") as t:
            t.extractall(FCODE)
    ARCHIVE = f"{RUN}/green_archive"
    if not os.path.exists(f"{ARCHIVE}/green_report.json"):
        subprocess.run([sys.executable, "-u", f"{FCODE}/rz/scripts/green_summaries.py", "--out", ARCHIVE,
                        "--leagues", *LEAGUES.split(), "--seasons", "3"], cwd=f"{FCODE}/rz", check=True)

# %%
env = {**os.environ, "RZ_DATA_PLAN": DATA_PLAN, "RZ_CODE_PACK": f"kaggle-inputs@{PIN}", "PYTHONUNBUFFERED": "1",
       "PYTHONPATH": f"{CODE}/code", **({"RZ_BUILD_ARCHIVE": ARCHIVE} if ARCHIVE else {})}
log_path = f"{RUN}/teacher_{time.strftime('%Y%m%d-%H%M')}.log"
with open(log_path, "w") as log:
    p = subprocess.Popen([sys.executable, "-u", f"{CODE}/code/agent/kaggle/teacher_only.py", "--out", f"{RUN}/out",
                          "--leagues", *LEAGUES.split(), "--build-h", str(BUILD_H), "--budget-h", str(TEACHER_H),
                          "--system", f"{PACK}/data/reasoner/system.txt", "--exp", EXP,
                          "--source", "green" if DATA_PLAN == "A" else "espn"],
                         cwd=f"{CODE}/code", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1)
    for line in p.stdout:
        print(line, end="")
        log.write(line)
    rc = p.wait()
print("teacher exit code:", rc)

# %%
wall_h = (time.time() - T_START) / 3600
cu = round(wall_h * RATE, 1) if RATE else None
gp = f"{RUN}/out/grades.json"
if os.path.exists(gp):
    g = json.load(open(gp))
    g["colab"] = {"gpu": GPU, "wall_h": round(wall_h, 2), "cu_spent": cu, "pin": PIN}
    json.dump(g, open(gp, "w"), indent=1)
    print("===== grades.json =====")
    print(json.dumps(g, indent=1))
print(f"compute: {wall_h:.2f} h on {GPU} = ~{cu} CU")
zp = f"{RUN}/teacher_out.zip"
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for f in ("teacher_out.jsonl", "grades.json", "teacher_report.json", "teacher_requests.jsonl"):
        if os.path.exists(f"{RUN}/out/{f}"):
            z.write(f"{RUN}/out/{f}", f)
try:
    from google.colab import files
    files.download(zp)
except Exception as e:  # noqa: BLE001 - outside Colab, or the browser blocked it
    print("teacher_out.zip is at", zp, f"({type(e).__name__})")
