# %% [markdown]
# # RedZone: big teacher -> small students (free Kaggle GPU)
#
# Inputs:
# * the private dataset `redzone-distill` (round 1's pack: the folder, or its rz_pack.part*.bin pieces);
# * round 2+: the small code pack `rz_code.tar.gz` (any dataset), which replaces the pack's code;
# * optional: the previous version's output (teacher answers + the multi-season build to resume).
#
# Round 2+ runs, in one session (~11 h of the 12 h cap):
# 1. **Multi-season build** on the CPUs (`build_moments_kaggle.py`): ESPN public API, NFL, CFB, NBA,
#    men's college basketball, NHL and MLB, 3 seasons + the current one, every live play, labeled
#    from what happened next, split by time; the locked nights are never downloaded.
# 2. **Teacher** on both T4s at the same time: Qwen3-32B-AWQ on the hardest + gold-adjacent head of
#    the queue, then Qwen3-14B-AWQ (~3x faster) on the easier tail; the queue is filtered by the
#    build's time split (hindsight only on train nights, nothing locked).
# 3. **Teacher, blind** on the dev-test grading sample (so the teacher is graded like the student).
# 4. **Students**: a weighted pick from the millions (NFL first, crunch and your-team up), one LoRA per
#    job on the Mac's own 4-bit base (dequantized), one job per T4; mlx_lm adapters.
# 5. **Grade** on dev-test nights: per league, train/valid/test gap, slices, calibrated "I don't know".
#
# **Run:** Accelerator **GPU T4 x2**, Internet **On**, inputs attached, **Save & Run All (Commit)**.
# Download **out.zip** from the version's Output. Next round: attach this version's output too.

# %%
import glob
import json
import os
import shutil
import subprocess
import sys
import time

TEACHER_BUDGET_H = 2.5      # the 32B on the hardest + gold-adjacent head of the queue
BIG_N = 9000                # how many open requests the 32B gets (round 1 measured ~65/min)
FAST_BUDGET_H = 3.0         # then the 14B (~3x faster) on the easier tail
BUILD_BUDGET_H = 4.5        # stop downloading new games after this (the head + export follow)
BLIND_BUDGET_H = 0.5        # teacher on the dev-test grading sample
ROUND = time.strftime("%Y%m%d-%H%M")
KW = os.environ.get("RZ_KAGGLE_WORKING", "/kaggle/working")        # overridable for the CPU smoke test
INP = os.environ.get("RZ_KAGGLE_INPUT", "/kaggle/input")
WORK = f"{KW}/rz"
MOMENTS = f"{KW}/moments"
T0 = time.time()


def unpack(parts_glob, dest, strip=1):
    """Cat split parts (name order) or one tarball into dest."""
    parts = sorted(glob.glob(parts_glob, recursive=True), key=os.path.basename)
    if not parts or os.path.exists(os.path.join(dest, ".unpacked")):
        return bool(parts)
    os.makedirs(dest, exist_ok=True)
    tgz = dest + ".tar.gz"
    with open(tgz, "wb") as f:
        for p in parts:
            with open(p, "rb") as part:
                shutil.copyfileobj(part, f)
    subprocess.run(["tar", "-xzf", tgz, "-C", dest, f"--strip-components={strip}"], check=True)
    os.remove(tgz)
    open(os.path.join(dest, ".unpacked"), "w").close()
    return True


# The web upload caps files at 10 MB: the pack may arrive as rz_pack.part{aa,ab,..}.bin
unpack(f"{INP}/**/rz_pack.part*.bin", f"{KW}/pack")
manifests = sorted(glob.glob(f"{KW}/pack/**/manifest.json", recursive=True)) + \
    sorted(glob.glob(f"{INP}/**/manifest.json", recursive=True))
PACK = next(os.path.dirname(m) for m in manifests if os.path.isdir(os.path.join(os.path.dirname(m), "data")))
os.makedirs(WORK, exist_ok=True)
CODE = os.path.join(WORK, "code")
if os.path.exists(CODE):
    shutil.rmtree(CODE)



def extracted_code():
    """Kaggle unpacks an uploaded .tar.gz by itself: the round-2 code pack then shows up as a folder
    (.../code/{agent,redzone,scripts}), not as rz_code.tar.gz. Find that folder; skip the round-1
    pack's code and the copies inside an attached previous output (.../rz/code)."""
    for hit in sorted(glob.glob(f"{INP}/**/agent/kaggle/build_moments_kaggle.py", recursive=True)):
        root = hit[: -len("agent/kaggle/build_moments_kaggle.py")].rstrip("/")
        if root.startswith(PACK) or root.endswith("/rz/code"):
            continue
        if os.path.isdir(os.path.join(root, "redzone")) and os.path.exists(os.path.join(root, "scripts/export_reasoner_llm.py")):
            return root
    return None


if unpack(f"{INP}/**/rz_code.tar.gz", f"{KW}/code_new"):
    CODE_SRC = "rz_code.tar.gz"
    shutil.copytree(f"{KW}/code_new", CODE, ignore=shutil.ignore_patterns(".unpacked"))   # round 2+ code
elif extracted_code():
    CODE_SRC = extracted_code()
    shutil.copytree(CODE_SRC, CODE)                                # round 2+ code, unpacked by Kaggle
else:
    CODE_SRC = "pack (round 1 code)"
    shutil.copytree(os.path.join(PACK, "code"), CODE)
sys.path.insert(0, CODE)
print("pack:", PACK, "| code:", CODE_SRC)
PREV = [p for p in glob.glob(f"{INP}/**/teacher_out.jsonl", recursive=True) if not p.startswith(PACK)]
print("resume teacher from:", PREV)
# resume the multi-season build from the previous version's output (games already built are skipped)
prev_m = [os.path.dirname(p) for p in glob.glob(f"{INP}/**/moments/split_plan.json", recursive=True)]
if prev_m and not os.path.exists(MOMENTS):
    shutil.copytree(prev_m[0], MOMENTS, ignore=shutil.ignore_patterns("DONE", "rows"))
    print("resume build from:", prev_m[0])
ROUND2 = os.path.exists(os.path.join(CODE, "agent/kaggle/build_moments_kaggle.py"))
print(json.dumps(json.load(open(os.path.join(PACK, "manifest.json")))["counts"], indent=1))


def run(args, env=None):
    """Run a phase script, streaming its output into this cell; raise if it fails."""
    p = subprocess.Popen([sys.executable, *args], cwd=CODE, env={**os.environ, "PYTHONPATH": CODE, **(env or {})},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
    if p.wait() != 0:
        raise RuntimeError(f"{args[0]} failed with exit code {p.returncode}")


def tail(path, n=20):
    return "".join(open(path).readlines()[-n:]) if os.path.exists(path) else ""


# %%
if not os.environ.get("RZ_SMOKE"):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "vllm", "peft", "accelerate", "bitsandbytes",
                    "huggingface_hub", "safetensors", "lightgbm", "httpx", "pyyaml"], check=False)

# %%
# Phase 0 (round 2+): start the multi-season build on the CPUs; it writes its time split first.
build = None
if ROUND2:
    blog = open(f"{KW}/build.log", "a")
    build = subprocess.Popen([sys.executable, "agent/kaggle/build_moments_kaggle.py", "--out", MOMENTS,
                              "--budget-h", str(BUILD_BUDGET_H), "--workers", "3",
                              *(["--archive", os.environ["RZ_BUILD_ARCHIVE"]] if os.environ.get("RZ_BUILD_ARCHIVE") else [])],
                             cwd=CODE, env={**os.environ, "PYTHONPATH": CODE}, stdout=blog, stderr=subprocess.STDOUT)
    while build.poll() is None and not os.path.exists(f"{MOMENTS}/split_plan.json") and time.time() - T0 < 3600:
        time.sleep(30)
    print(tail(f"{KW}/build.log"))
    if not os.path.exists(f"{MOMENTS}/split_plan.json"):
        print("build has no split plan (see build.log): this round runs on the pack only")

# %%
# Phase 1: the teacher (own process; vLLM's GPU memory is freed after). Round 2+: its queue is
# filtered by the build's time split, so hindsight is used on train nights only and nothing locked
# is ever asked.
queue = os.path.join(PACK, "teacher_requests.jsonl.gz")
if not os.path.exists(queue):
    queue = os.path.join(PACK, "teacher_requests.jsonl")
if os.path.exists(f"{MOMENTS}/split_plan.json"):
    from agent.distill.teacher import read_jsonl, write_jsonl
    plan = json.load(open(f"{MOMENTS}/split_plan.json"))
    where = {}
    for f in glob.glob(os.path.join(PACK, "data/reasoner/rows_*.jsonl*")) + glob.glob(os.path.join(PACK, "data/jump_rows.jsonl*")):
        for r in read_jsonl(f.replace(".gz", "")):
            where[r["id"]] = plan.get(r["league"], {}).get(str(r["night"]), "locked")
    keep = []
    for q in read_jsonl(queue.replace(".gz", "")):
        rid = q["id"].split("|")[0]
        s = where.get(rid)
        if q["task"].startswith("wd_"):
            keep.append(q)
        elif s is None or s == "locked":
            continue
        elif q["task"] == "reasoner_grounded" and s != "train":
            continue
        elif q["task"] == "reasoner_blind":
            continue                  # round 2+ grades on the build's dev-test sample (phase 3)
        else:
            keep.append(q)
    queue = f"{WORK}/teacher_requests_r2.jsonl"
    write_jsonl(queue, keep)
    print("teacher queue after the time split:", len(keep))
run(["agent/kaggle/run_teacher.py", "--requests", queue, "--out", WORK, "--budget-h", str(TEACHER_BUDGET_H),
     "--limit", str(BIG_N), "--resume-from", *PREV])
shutil.copy(f"{WORK}/teacher_report.json", f"{WORK}/teacher_report_32b.json")
run(["agent/kaggle/run_teacher.py", "--requests", queue, "--out", WORK, "--budget-h", str(FAST_BUDGET_H), "--fast"])
shutil.copy(f"{WORK}/teacher_report.json", f"{WORK}/teacher_report_14b.json")

# %%
# Phase 2: wait for the build, then write the student data (weighted pick from the millions)
if build is not None:
    while build.poll() is None:
        time.sleep(60)
    print(tail(f"{KW}/build.log", 40))
student_args = ["--pack", PACK, "--teacher-out", os.path.join(WORK, "teacher_out.jsonl"),
                "--out", os.path.join(WORK, "students")]
if os.path.exists(f"{MOMENTS}/DONE"):
    student_args += ["--moments", MOMENTS]
run(["agent/kaggle/run_students.py", *student_args, "--prepare-only"])

# %%
# Phase 3: the teacher answers the dev-test grading sample blind (graded exactly like the student)
gr = os.path.join(WORK, "students/data/game-reasoner/grade_rows.jsonl")
if os.path.exists(gr):
    from agent.distill.teacher import read_jsonl, reasoner_requests, write_jsonl
    rows = [r for r in read_jsonl(gr) if r["split"] == "test"]
    system = open(os.path.join(PACK, "data/reasoner/system.txt")).read()
    reqs = reasoner_requests(rows, system, grounded_splits=(), blind_splits=("test",))
    write_jsonl(f"{WORK}/teacher_blind_r2.jsonl", reqs)
    run(["agent/kaggle/run_teacher.py", "--requests", f"{WORK}/teacher_blind_r2.jsonl", "--out", WORK,
         "--budget-h", str(BLIND_BUDGET_H)])

# %%
# Phase 4: students on both T4s, then mlx_lm adapters
run(["agent/kaggle/run_students.py", *student_args, "--prepared"])

# %%
# Phase 5: grade, and the teacher's new watchdog wordings
run(["agent/kaggle/grade.py", "--pack", PACK, "--work", WORK])
from agent.distill.teacher import read_jsonl, watchdog_wordings  # noqa: E402

novel = json.load(open(os.path.join(PACK, "data/watchdog/novel.json")))
held = [w for v in novel.values() if isinstance(v, dict) for s in ("test", "valid") for w in v.get(s, [])]
w = watchdog_wordings(read_jsonl(os.path.join(WORK, "teacher_out.jsonl")), held)
json.dump(w, open(os.path.join(WORK, "wordings_teacher.json"), "w"), indent=1)
print("teacher wordings per kind:", {k: len(v) for k, v in w.items()})

# %%
# Phase 6: out.zip (adapters for the Mac, answers, reports). The base model and the millions of rows
# are not in it; the rows stay in this version's output for the next round to resume from.
keep = os.path.join(KW, "out")
if os.path.exists(keep):
    shutil.rmtree(keep)
os.makedirs(keep)
for f in ("teacher_out.jsonl", "teacher_report.json", "teacher_report_32b.json", "teacher_report_14b.json",
          "report.json", "report.md", "wordings_teacher.json"):
    if os.path.exists(os.path.join(WORK, f)):
        shutil.copy(os.path.join(WORK, f), keep)
for f in ("build_report.json", "split_plan.json"):
    if os.path.exists(os.path.join(MOMENTS, f)):
        shutil.copy(os.path.join(MOMENTS, f), keep)
if os.path.exists(f"{KW}/build.log"):
    shutil.copy(f"{KW}/build.log", keep)
    if ROUND2 and not os.path.exists(f"{MOMENTS}/DONE"):
        print("WARNING: the multi-season build did not finish; students trained on the pack only. See build.log")
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
gdir = os.path.join(WORK, "students/data/game-reasoner")
for f in ("grade_rows.jsonl", "jump_rows.jsonl"):
    if os.path.exists(os.path.join(gdir, f)):
        os.makedirs(os.path.join(keep, "graded"), exist_ok=True)
        shutil.copy(os.path.join(gdir, f), os.path.join(keep, "graded", f))
json.dump({"round": ROUND, "wall_h": round((time.time() - T0) / 3600, 2)}, open(os.path.join(keep, "round.json"), "w"))
shutil.make_archive(f"{KW}/out", "zip", keep)
# keep the version output small: the 3.4 GB dequantized base is rebuilt next session in ~2 min
shutil.rmtree(os.path.join(WORK, "students", "base-deq"), ignore_errors=True)
shutil.rmtree(f"{KW}/pack", ignore_errors=True)
print(open(os.path.join(WORK, "report.md")).read())
print("download: /kaggle/working/out.zip", os.path.getsize(f"{KW}/out.zip") // 1_000_000, "MB")
