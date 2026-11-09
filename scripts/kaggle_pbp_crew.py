"""Kaggle CPU notebook: builds the shared play-by-play table for Data Division A (EXP-1000 / EXP-097).

Why: Data Division A needs real event tokens for world-model pretraining off the Mac; first added in b717ee65
(2026-11-05) as "Data Division A: shared play table + crew converters ... pre-register EXP-1000". Output shards
are read by lane 1's tokenizer v2 (scripts/pbp/schema.py is the table schema).
"""
# %% [markdown]
# # RedZone Data Division A: play-by-play crew (free Kaggle CPU, no GPU quota)
#
# Serves EXP-1000 / EXP-097 (real event tokens for world-model pretraining). Owner: Data Planner A.
# Pulls every crew source into one shared play table (scripts/pbp/schema.py): one parquet shard per
# league-season plus a sidecar JSON with events, games, v1 tokens, the license line and the B tag.
# Lane 1's tokenizer v2 reads these shards.
#
# **Self-contained:** the crew's code is embedded below. No code dataset is needed.
# **Settings:** Accelerator None (CPU), Internet On, then Save & Run All (Commit). Keep the notebook PRIVATE: plan-B data is
# "personal-use, not for commercial release" and must never be published.
# **Resume:** attach this notebook's previous version output as an input and run again. Finished shards are
# copied, not refetched.
# **Jobs:** RZ_PBP_JOBS selects them (default `files`):
# - `files`: wehoop WNBA + WCBB, Cricsheet, StatsBomb, Wyscout. These are file downloads, about one session.
# - `baseball`: MiLB AAA to Single-A, then MLB 2008-24 via the MLB Stats API. This is a polite crawl capped at
#   1 request/s, resumable and newest season first, and takes several sessions.
# - `euro`: EuroLeague (E) then EuroCup (U) via the EuroLeague live API, newest season first, game codes probed
#   1.. until 20 empties, capped at 1 request/s, resumable from the raw cache.
#
# **Guardrails:** public endpoints only, no keys, no logins, rate limits respected, no video.

# %%
import base64
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import traceback
from pathlib import Path

T0 = time.time()
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "py7zr"], check=False)   # SportVU archives (.7z)
SESSION_H = float(os.environ.get("RZ_SESSION_H", "11.3"))
KW = Path(os.environ.get("RZ_KAGGLE_WORKING", "/kaggle/working"))
INP = Path(os.environ.get("RZ_KAGGLE_INPUT", "/kaggle/input"))
OUT = KW / "pbp"
JOBS = os.environ.get("RZ_PBP_JOBS", "files").split(",")
SMOKE = os.environ.get("RZ_SMOKE") == "1"          # 1 season / few games per source, to check endpoints
OUT.mkdir(parents=True, exist_ok=True)


def left_s():
    return SESSION_H * 3600 - (time.time() - T0)


# %%
# code: the embedded pbp package (pack_pbp_notebook.py fills CODE_B64), else a repo checkout on sys.path
CODE_B64 = ""
CODE = KW / "code"
if CODE_B64 and not (CODE / "pbp").exists():
    CODE.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(CODE_B64)), mode="r:gz") as tar:
        try:
            tar.extractall(CODE, filter="data")
        except TypeError:                           # Python without extraction filters (our own tar, fixed names)
            tar.extractall(CODE)
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(Path.cwd() / "scripts"))
from pbp import cricsheet, euroleague, mlb_statsapi, sportvu, statsbomb, tennis, wehoop, wyscout  # noqa: E402
from pbp.schema import PERSONAL  # noqa: E402

# resume: copy finished shards (and their sidecars) from a previous version's output
for side in glob.glob(str(INP / "**/pbp/**/*.parquet.json"), recursive=True):
    rel = side.split("/pbp/", 1)[1]
    dst = OUT / rel
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(side, dst)
        shutil.copy(side[: -len(".json")], str(dst)[: -len(".json")])
print("resumed shards:", len(list(OUT.rglob("*.parquet.json"))))

LOG = []


def run(name, fn, *a, **kw):
    if left_s() < 900:
        LOG.append({"job": name, "status": "skipped: session time"})
        return None
    t = time.time()
    try:
        res = fn(*a, **kw)
        LOG.append({"job": name, "status": "ok", "s": round(time.time() - t)})
        return res
    except Exception as e:  # noqa: BLE001 - one source failing never stops the rest
        LOG.append({"job": name, "status": f"error: {e!r}"[:300], "s": round(time.time() - t)})
        traceback.print_exc()
        return None


def have(league_dir: Path, season) -> bool:
    return (league_dir / f"{season}.parquet.json").exists()


# %%
if "files" in JOBS:
    for lg, spec in wehoop.LEAGUES.items():
        lo, hi = spec["seasons"]
        for y in range(hi, lo - 1, -1)[: 1 if SMOKE else None]:
            if not have(OUT / lg, y):
                run(f"wehoop/{lg}/{y}", wehoop.pull_season, lg, y, OUT)
    run("cricsheet", cricsheet.pull_all, OUT)
    run("statsbomb", statsbomb.pull_all, OUT, competitions=None)
    run("wyscout", wyscout.pull_all, OUT)
    run("tennis", tennis.pull_all, OUT)
    run("sportvu", sportvu.pull_all, OUT)

# %%
if "baseball" in JOBS:
    # MiLB first (more new info: 4 levels we hold none of), newest season first; MLB 2008-24 after
    order = [(sid, y) for y in range(2025, 2004, -1) for sid in (11, 12, 13, 14)]
    order += [(1, y) for y in range(mlb_statsapi.MLB_SEASONS[1], mlb_statsapi.MLB_SEASONS[0] - 1, -1)]
    for sid, y in order[: 2 if SMOKE else None]:
        if left_s() < 1800:
            break
        if not have(OUT / mlb_statsapi.LEAGUES[sid], y):
            run(f"mlb_statsapi/{sid}/{y}", mlb_statsapi.pull_season, sid, y, OUT, max_games=5 if SMOKE else None)

# %%
if "euro" in JOBS:
    # EuroLeague first, then EuroCup; season labels = end year (E2025 -> 2026), newest first
    lo, hi = euroleague.SEASON_CODES
    for lg in ("euroleague", "eurocup"):
        for y in range(hi + 1, lo, -1)[: 1 if SMOKE else None]:
            if left_s() < 1800:
                break
            if not have(OUT / lg, y):
                run(f"euroleague/{lg}/{y}", euroleague.pull_season, lg, y, OUT, max_games=5 if SMOKE else None)

# %%
# manifest + ledger check: every shard has a license line; every plan-B shard carries the personal-use tag
shards = [json.loads(p.read_text()) for p in sorted(OUT.rglob("*.parquet.json"))]
bad = [s for s in shards if not s.get("license") or (s.get("data_plan") == "B" and s.get("tag") != PERSONAL)]
tot = {k: sum(int(s.get(k) or 0) for s in shards) for k in ("games", "events", "v1_tokens", "bytes")}
man = {"jobs": JOBS, "log": LOG, "shards": len(shards), "total": tot, "license_problems": len(bad),
       "tag": PERSONAL, "hours": round((time.time() - T0) / 3600, 2)}
(OUT / "manifest.json").write_text(json.dumps(man, indent=1))
for raw in OUT.rglob("raw"):
    shutil.rmtree(raw, ignore_errors=True)          # never ship raw caches in the output
print(json.dumps({k: v for k, v in man.items() if k != "log"}, indent=1))
print("\n".join(f"{x['job']}: {x['status']}" for x in LOG))
