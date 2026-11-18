"""Kaggle GPU notebook: a big teacher model labels moments, small student models are trained and graded.

Why: first added in 5230b18c (2026-11-04) as "Salvage the Kaggle Mind pipeline (old draft #13 + Mind V7)
onto integration/next"; training stays off the Mac (CLAUDE.md hard constraint), and no separate decision is
recorded for this notebook.
"""
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
# **MODE** (next cell): "students" = skip the teacher and the multi-season build, reuse the teacher
# answers in the attached previous output, and train + grade the students (~2-3 GPU-h). "full" = the
# whole round above (the teacher resumes from the attached answers).
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

# [V7] "full" by default: the whole teacher queue (V6 had answered 22k of 81k) with the tiered teacher, inside
# one ~11.5 h session (Kaggle's cap is 12 h; ~13 GPU-h were left this week). Attach V6's output to resume.
# [V8-quick, overseer Nov 4 6:30 PM] PRESET "quick" = students only, the whole session capped at 2.25 h (this
# week's ~2.5 GPU-h): game-reasoner 1 epoch, the watchdog's base-model answers reused from the attached V7
# output (the base never changes, so it stays the compute-matched baseline), every test row still answered.
# "full" = the normal round (set it back at the weekly reset).
PRESET = os.environ.get("RZ_PRESET", "quick")
MODE = os.environ.get("RZ_MODE", "students" if PRESET == "quick" else "full")   # "full" | "students"
SESSION_H = float(os.environ.get("RZ_SESSION_H", "2.25" if PRESET == "quick" else "11.5"))   # every phase's budget is cut to what is left of this
TEACHER_BUDGET_H = 2.25     # the 32B on the hardest + gold-adjacent head of the queue (~65/min)
BIG_N = 8800                # how many open requests the 32B gets (2.25 h x ~65/min)
FAST_BUDGET_H = 4.25        # then the 14B (~3x faster, ~195/min) on the easier tail: ~50k, the rest of the queue
BUILD_BUDGET_H = 4.5        # stop downloading new games after this (on the CPUs, during the teacher)
BLIND_BUDGET_H = 0.4        # teacher on the dev-test grading sample
STUDENT_BUDGET_H = float(os.environ.get("RZ_STUDENT_BUDGET_H", "1.8" if PRESET == "quick" else "3.75"))   # both students
GRADE_RESERVE_H = 0.35      # grading + out.zip after the students
ROUND = time.strftime("%Y%m%d-%H%M")


def stamp() -> dict:
    """What makes this round reproducible: seed, the hashes of the data the students saw, the recipe, the pack."""
    import hashlib

    def h(path):
        if not os.path.exists(path):
            return None
        d = hashlib.sha256()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                d.update(b)
        return d.hexdigest()[:16]
    sd = os.path.join(WORK, "students", "data")
    data = {os.path.relpath(p, sd): h(p) for p in sorted(glob.glob(f"{sd}/*/*.jsonl"))}
    man = next(iter(glob.glob(f"{PACK}/**/manifest.json", recursive=True)), None)
    return {"seed": 7, "data_sha256_16": data, "teacher_out_sha256_16": h(os.path.join(WORK, "teacher_out.jsonl")),
            "recipe": {"preset": PRESET, "mode": MODE, "session_h": SESSION_H, "student_budget_h": STUDENT_BUDGET_H,
                       "epochs": "1 (quick)" if PRESET == "quick" else "game-reasoner 2, watchdog 1"},
            "code_pack": os.environ.get("RZ_CODE_PACK") or (json.load(open(man)).get("commit") if man else None)}

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
STUDENTS_ONLY = MODE == "students"
if STUDENTS_ONLY:
    from agent.kaggle.run_teacher import load_done
    answers = load_done(PREV)                     # every attached teacher_out, one answer per id
    if not answers and os.environ.get("RZ_JOBS", "") != "watchdog":   # the watchdog never reads teacher answers
        raise SystemExit('MODE="students" needs the previous version\'s output (with teacher_out.jsonl) attached')
    with open(os.path.join(WORK, "teacher_out.jsonl"), "w") as f:
        for v in answers.values():
            f.write(json.dumps(v) + "\n")
    print(f"students-only: reusing {len(answers)} teacher answers; teacher and multi-season build skipped")
    del answers
    ROUND2 = ROUND2 and os.path.exists(f"{MOMENTS}/DONE")    # a finished build attached: still use it
print("mode:", MODE)
print(json.dumps(json.load(open(os.path.join(PACK, "manifest.json")))["counts"], indent=1))


def run(args, env=None):
    """Run a phase script, streaming its output into this cell; raise if it fails."""
    p = subprocess.Popen([sys.executable, *args], cwd=CODE, env={**os.environ, "PYTHONPATH": CODE, **(env or {})},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        print(line, end="")
    if p.wait() != 0:
        raise RuntimeError(f"{args[0]} failed with exit code {p.returncode}")


def left_h(reserve=0.0):
    """Hours of the session budget still free after `reserve`."""
    return max(0.1, SESSION_H - (time.time() - T0) / 3600 - reserve)


def ticker(label, fn, every_s=600):
    """[V7] a status line in this cell every `every_s` while a background phase runs (fn() -> str|None)."""
    import threading
    stop = threading.Event()

    def loop():
        while not stop.wait(every_s):
            try:
                msg = fn()
            except Exception as e:  # noqa: BLE001
                msg = f"(status unavailable: {e})"
            if msg:
                print(f"[{label} {(time.time() - T0) / 3600:.2f} h] {msg}", flush=True)
    threading.Thread(target=loop, daemon=True).start()
    return stop


def tail(path, n=20):
    return "".join(open(path).readlines()[-n:]) if os.path.exists(path) else ""


# %%
if not os.environ.get("RZ_SMOKE"):
    # [Colab V8] students-only needs no vLLM (and its install would replace the runtime's torch)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *([] if STUDENTS_ONLY else ["vllm"]), "peft",
                    "accelerate", "bitsandbytes", "huggingface_hub", "safetensors", "lightgbm", "httpx", "pyyaml"],
                   check=False)
    # Kaggle ships torchao 0.10; peft's LoRA needs torchao >= 0.16 or none at all (V3 died on it)
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", "torchao"], check=False)

# %%
# Phase 0 (round 2+): start the multi-season build on the CPUs; it writes its time split first.
build = None
if ROUND2 and not STUDENTS_ONLY:
    blog = open(f"{KW}/build.log", "a")
    build = subprocess.Popen([sys.executable, "agent/kaggle/build_moments_kaggle.py", "--out", MOMENTS,
                              "--budget-h", str(BUILD_BUDGET_H), "--workers", "3",
                              *(["--archive", os.environ["RZ_BUILD_ARCHIVE"]] if os.environ.get("RZ_BUILD_ARCHIVE") else [])],
                             cwd=CODE, env={**os.environ, "PYTHONPATH": CODE}, stdout=blog, stderr=subprocess.STDOUT)
    while build.poll() is None and not os.path.exists(f"{MOMENTS}/split_plan.json") and time.time() - T0 < 3600:
        time.sleep(30)
    print(tail(f"{KW}/build.log"))
    build_tick = ticker("build", lambda: tail(f"{KW}/build.log", 1).strip() if build.poll() is None else None)
    if not os.path.exists(f"{MOMENTS}/split_plan.json"):
        print("build has no split plan (see build.log): this round runs on the pack only")

# %%
# Phase 1: the teacher (own process; vLLM's GPU memory is freed after). Round 2+: its queue is
# filtered by the build's time split, so hindsight is used on train nights only and nothing locked
# is ever asked.
queue = os.path.join(PACK, "teacher_requests.jsonl.gz")
if STUDENTS_ONLY:
    queue = None
if queue and not os.path.exists(queue):
    queue = os.path.join(PACK, "teacher_requests.jsonl")
if queue and os.path.exists(f"{MOMENTS}/split_plan.json"):
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
    from agent.distill.teacher import balance_by_league
    keep = balance_by_league(keep)            # every league gets answers before the budget runs out
    queue = f"{WORK}/teacher_requests_r2.jsonl"
    write_jsonl(queue, keep)
    print("teacher queue after the time split:", len(keep),
          {k: sum(1 for q in keep if str(q.get("id", "")).split(":")[0] == k) for k in ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")})
if queue:
    tail_h = BLIND_BUDGET_H + STUDENT_BUDGET_H + GRADE_RESERVE_H        # what must still fit after the teacher
    run(["agent/kaggle/run_teacher.py", "--requests", queue, "--out", WORK,
         "--budget-h", f"{min(TEACHER_BUDGET_H, left_h(tail_h + 0.5)):.2f}", "--limit", str(BIG_N), "--resume-from", *PREV])
    shutil.copy(f"{WORK}/teacher_report.json", f"{WORK}/teacher_report_32b.json")
    run(["agent/kaggle/run_teacher.py", "--requests", queue, "--out", WORK,
         "--budget-h", f"{min(FAST_BUDGET_H, left_h(tail_h)):.2f}", "--fast"])
    shutil.copy(f"{WORK}/teacher_report.json", f"{WORK}/teacher_report_14b.json")

# %%
# Phase 2: wait for the build, then write the student data (weighted pick from the millions)
if build is not None:
    while build.poll() is None:
        time.sleep(60)
    build_tick.set()
    print(tail(f"{KW}/build.log", 40))
student_args = ["--pack", PACK, "--teacher-out", os.path.join(WORK, "teacher_out.jsonl"),
                "--out", os.path.join(WORK, "students")]
if os.path.exists(f"{MOMENTS}/DONE"):
    student_args += ["--moments", MOMENTS]
if os.path.exists(os.path.join(WORK, "students", "student_data.json")):     # [Colab V8] a resumed run: same data
    print("student data already written (resumed run):", open(os.path.join(WORK, "students", "student_data.json")).read())
else:
    run(["agent/kaggle/run_students.py", *student_args, "--prepare-only"])

# %%
# Phase 3: the teacher answers the dev-test grading sample blind (graded exactly like the student)
gr = os.path.join(WORK, "students/data/game-reasoner/grade_rows.jsonl")
if os.path.exists(gr) and not STUDENTS_ONLY:
    from agent.distill.teacher import read_jsonl, reasoner_requests, write_jsonl
    rows = [r for r in read_jsonl(gr) if r["split"] == "test"]
    system = open(os.path.join(PACK, "data/reasoner/system.txt")).read()
    reqs = reasoner_requests(rows, system, grounded_splits=(), blind_splits=("test",))
    write_jsonl(f"{WORK}/teacher_blind_r2.jsonl", reqs)
    run(["agent/kaggle/run_teacher.py", "--requests", f"{WORK}/teacher_blind_r2.jsonl", "--out", WORK,
         "--budget-h", f"{min(BLIND_BUDGET_H, left_h(STUDENT_BUDGET_H + GRADE_RESERVE_H)):.2f}"])

# %%
# Phase 4: students on both T4s, then mlx_lm adapters
sb = min(STUDENT_BUDGET_H, left_h(GRADE_RESERVE_H))
print(f"students: {sb:.2f} h budget ({left_h():.2f} h left in the session)", flush=True)
prev_base = [p for p in glob.glob(f"{INP}/**/watchdog/test_out_base.jsonl", recursive=True) if not p.startswith(PACK)]
quick_env = {"RZ_EPOCHS": "1", "RZ_ANSWER_RESERVE_S": str(60 * 60),
             **({"RZ_REUSE_BASE": prev_base[0]} if prev_base else {})} if PRESET == "quick" else {}
print("quick preset:", quick_env, flush=True)
run(["agent/kaggle/run_students.py", *student_args, "--prepared"],
    env={"RZ_STUDENT_BUDGET_S": str(int(sb * 3600)), **quick_env})

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
json.dump({"round": ROUND, "mode": MODE, "preset": PRESET, "wall_h": round((time.time() - T0) / 3600, 2), "stamp": stamp()},
          open(os.path.join(keep, "round.json"), "w"))
from agent.kaggle.grade import short_grades  # noqa: E402

# grades.json at the output ROOT: the few numbers the overseer reads from Kaggle's Output tab (the log is cut)
try:
    rep = json.load(open(os.path.join(WORK, "report.json"))) if os.path.exists(os.path.join(WORK, "report.json")) else {}
    g = short_grades(rep, ROUND, MODE, round((time.time() - T0) / 3600, 2))
    g["stamp"] = stamp()                        # [Nick 6:40 PM rule 10] seed, data hash, recipe, code pack id
    cov = {}
    for job in ("game-reasoner", "watchdog"):  # [Colab V8] how much of each test set is answered, and resumes
        try:
            tr = json.load(open(os.path.join(WORK, "students", job, "train_report.json")))
            cov[job] = {"complete": tr.get("complete"), "stopped_early": tr.get("stopped_early"),
                        "ckpt_resumes": tr.get("ckpt_resumes"), "steps": tr.get("steps"),
                        **{k: {m: v.get(m) for m in ("answered", "of", "reused")} for k, v in tr.get("answered", {}).items()}}
        except (OSError, ValueError):
            cov[job] = None
    g["coverage"] = cov
    ran = [j for j in os.environ.get("RZ_JOBS", "game-reasoner,watchdog").split(",") if j in cov]
    g["complete"] = all(cov[j] and cov[j].get("complete") for j in ran)
    json.dump(g, open(os.path.join(KW, "grades.json"), "w"), indent=1)
    print("grades.json:", open(os.path.join(KW, "grades.json")).read())
except Exception as e:  # noqa: BLE001 - the summary never fails the round
    print("grades.json not written:", type(e).__name__, e)
shutil.make_archive(f"{KW}/out", "zip", keep)
# keep the version output small: the 3.4 GB dequantized base is rebuilt next session in ~2 min
shutil.rmtree(os.path.join(WORK, "students", "base-deq"), ignore_errors=True)
shutil.rmtree(f"{KW}/pack", ignore_errors=True)
print(open(os.path.join(WORK, "report.md")).read())
print("download: /kaggle/working/out.zip", os.path.getsize(f"{KW}/out.zip") // 1_000_000, "MB")
