# %% [markdown]
# # RedZone: big teacher -> small students (free Kaggle GPU)
#
# One notebook, one dataset (`redzone-distill`, built by `agent/kaggle/pack_data.py`). It runs, in order:
#
# 1. **Teacher** (Qwen3-32B-AWQ on both T4s, falls back to 14B): works down the ranked queue in
#    `teacher_requests.jsonl.gz` (hardest and most important first) until `TEACHER_BUDGET_H` runs out.
# 2. **Students**: one LoRA per job (`game-reasoner`, `watchdog`) on the Mac's own base
#    (mlx-community/Qwen3-1.7B-4bit, dequantized), the two jobs in parallel, one per T4.
# 3. **Convert** each adapter to mlx_lm format (agent/train/peft_to_mlx.py).
# 4. **Grade** today vs teacher vs student on the dev-test nights (newest before the locked set),
#    with the ship gates. The locked test nights are not in this dataset at all.
# 5. Everything goes into `/kaggle/working/out.zip`.
#
# **Run:** Add Input -> your private dataset `redzone-distill`. Settings: Accelerator **GPU T4 x2**,
# Internet **On**. Then **Save Version -> Save & Run All (Commit)**. It runs in the background,
# ~10.5 h, and uses ~10.5 of the 30 free GPU-hours a week. When it's done: open the version,
# Output, download **out.zip**.
#
# **Next round (resume):** Add Input -> this notebook's previous version output (it holds
# `rz/teacher_out.jsonl`), then Save & Run All again. Answered requests are skipped, the teacher
# continues down the queue, and the students retrain on everything so far.

# %%
import glob
import json
import os
import shutil
import subprocess
import sys
import time

TEACHER_BUDGET_H = 7.0      # per session; students + grading take ~3 h more, inside Kaggle's 12 h limit
ROUND = time.strftime("%Y%m%d-%H%M")
WORK = "/kaggle/working/rz"
T0 = time.time()

manifests = sorted(glob.glob("/kaggle/input/**/manifest.json", recursive=True))
PACK = next(os.path.dirname(m) for m in manifests if os.path.exists(os.path.join(os.path.dirname(m), "code")))
print("pack:", PACK)
os.makedirs(WORK, exist_ok=True)
CODE = os.path.join(WORK, "code")
if os.path.exists(CODE):
    shutil.rmtree(CODE)
shutil.copytree(os.path.join(PACK, "code"), CODE)
sys.path.insert(0, CODE)
# answers from an earlier round, if its output is attached
PREV = [p for p in glob.glob("/kaggle/input/**/teacher_out.jsonl", recursive=True) if not p.startswith(PACK)]
print("resume from:", PREV)
print(json.dumps(json.load(open(os.path.join(PACK, "manifest.json")))["counts"], indent=1))


def run(args, env=None):
    """Run a phase script, streaming its output into this cell; raise if it fails."""
    p = subprocess.Popen([sys.executable, *args], cwd=CODE, env={**os.environ, "PYTHONPATH": CODE, **(env or {})},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
    if p.wait() != 0:
        raise RuntimeError(f"{args[0]} failed with exit code {p.returncode}")


# %%
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "vllm", "peft", "accelerate", "bitsandbytes",
                "huggingface_hub", "safetensors"], check=False)

# %%
# Phase 1: teacher (its own process, so vLLM's GPU memory is freed for the students)
run(["agent/kaggle/run_teacher.py", "--requests", os.path.join(PACK, "teacher_requests.jsonl.gz"),
     "--out", WORK, "--budget-h", str(TEACHER_BUDGET_H), "--resume-from", *PREV])

# %%
# Phase 2 + 3: students on both T4s, then mlx_lm adapters
run(["agent/kaggle/run_students.py", "--pack", PACK, "--teacher-out", os.path.join(WORK, "teacher_out.jsonl"),
     "--out", os.path.join(WORK, "students")])

# %%
# Phase 4: grade on held-out nights, and the teacher's new watchdog wordings
run(["agent/kaggle/grade.py", "--pack", PACK, "--work", WORK])
from agent.distill.teacher import read_jsonl, watchdog_wordings  # noqa: E402

novel = json.load(open(os.path.join(PACK, "data/watchdog/novel.json")))
held = [w for v in novel.values() if isinstance(v, dict) for s in ("test", "valid") for w in v.get(s, [])]
w = watchdog_wordings(read_jsonl(os.path.join(WORK, "teacher_out.jsonl")), held)
json.dump(w, open(os.path.join(WORK, "wordings_teacher.json"), "w"), indent=1)
print("teacher wordings per kind:", {k: len(v) for k, v in w.items()})

# %%
# Phase 5: out.zip (adapters for the Mac, answers, reports). The base model is not included.
keep = os.path.join("/kaggle/working", "out")
if os.path.exists(keep):
    shutil.rmtree(keep)
os.makedirs(keep)
for f in ("teacher_out.jsonl", "teacher_report.json", "report.json", "report.md", "wordings_teacher.json"):
    if os.path.exists(os.path.join(WORK, f)):
        shutil.copy(os.path.join(WORK, f), keep)
for job in ("game-reasoner", "watchdog"):
    src = os.path.join(WORK, "students", job)
    if not os.path.exists(src):
        continue
    for sub in ("mlx", "peft"):
        if os.path.exists(os.path.join(src, sub)):
            shutil.copytree(os.path.join(src, sub), os.path.join(keep, job, sub))
    for f in ("test_out.jsonl", "test_out_base.jsonl", "train_report.json", "train.log"):
        if os.path.exists(os.path.join(src, f)):
            shutil.copy(os.path.join(src, f), os.path.join(keep, job, f))
json.dump({"round": ROUND, "wall_h": round((time.time() - T0) / 3600, 2)}, open(os.path.join(keep, "round.json"), "w"))
shutil.make_archive("/kaggle/working/out", "zip", keep)
# keep the version output small: the 3.4 GB dequantized base is rebuilt next session in ~2 min
shutil.rmtree(os.path.join(WORK, "students", "base-deq"), ignore_errors=True)
print(open(os.path.join(WORK, "report.md")).read())
print("download: /kaggle/working/out.zip", os.path.getsize("/kaggle/working/out.zip") // 1_000_000, "MB")
