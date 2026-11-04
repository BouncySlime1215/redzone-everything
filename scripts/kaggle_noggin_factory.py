# %% [markdown]
# # RedZone Noggin data factory (free Kaggle CPU, no GPU quota)
#
# Billions of labeled examples for the Reflex models, built from free public play-by-play, never on the Mac
# and never on GitHub Actions. One session runs up to ~11.5 h and saves everything it finished; attach this
# notebook's previous output as an input and run it again to continue (every shard is resumable).
#
# Inputs: the code pack `rz_factory_code.tar.gz` (dataset, from the repo's kaggle-inputs branch).
# Optional: this notebook's previous version output (finished shards are copied, not rebuilt).
# Settings: Accelerator **None (CPU)**, Internet **On**. Save & Run All (Commit).
#
# Per league, per season (newest first), into /kaggle/working/factory:
# 1. `moments/<league>/<season>.parquet`: every dead ball / stoppage as a labeled moment (scripts/noggin_factory.py:
#    stoppage type, length, restart instant, rule state, big play next; night id on each row).
# 2. `selfplay/<league>/<season>/part-NNNN.parquet` (NBA, CBB, NHL; 100 slates per part): PICK rows from the hindsight oracle on every real night
#    plus SYNTHETIC self-play slates (random games of the same season, re-timed, random screen lag), labels from
#    the oracle only: no human input anywhere.
# 3. `manifest.json`: rows per league / season / kind, and what is still missing.
# Locked and scorecard nights are never fetched or written (factory.EVAL_FIRST_NIGHT).

# %%
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

T0 = time.time()
SESSION_H = float(os.environ.get("RZ_SESSION_H", "11.5"))
KW = os.environ.get("RZ_KAGGLE_WORKING", "/kaggle/working")
INP = os.environ.get("RZ_KAGGLE_INPUT", "/kaggle/input")
OUT = f"{KW}/factory"
RAW = f"{KW}/raw"
JOBS = int(os.environ.get("RZ_JOBS", str(os.cpu_count() or 4)))
SMOKE = os.environ.get("RZ_SMOKE") == "1"
# league -> (seasons newest first, synthetic self-play slates per real night; 0 = moments only)
PLAN = {
    "nba": (list(range(2026, 2001, -1)), 20),
    "nfl": (list(range(2025, 2010, -1)), 0),
    "nhl": (list(range(2026, 2010, -1)), 20),   # ESPN wall clocks from 2019-20 on: older seasons give 0 rows
    "cbb": (list(range(2026, 2005, -1)), 4),
}
if os.environ.get("RZ_LEAGUES"):
    PLAN = {k: v for k, v in PLAN.items() if k in os.environ["RZ_LEAGUES"].split(",")}
os.makedirs(OUT, exist_ok=True)


def left_s():
    return SESSION_H * 3600 - (time.time() - T0)


# %%
# code: the factory code pack (Kaggle may have auto-extracted it into a folder)
CODE = f"{KW}/code"
packs = glob.glob(f"{INP}/**/rz_factory_code.tar.gz", recursive=True)
if packs and not os.path.exists(CODE):
    os.makedirs(CODE)
    subprocess.run(["tar", "-xzf", packs[0], "-C", CODE, "--strip-components=1"], check=True)
elif not os.path.exists(CODE):
    hits = [h for h in glob.glob(f"{INP}/**/scripts/noggin_selfplay.py", recursive=True)]
    if not hits:
        raise SystemExit("attach the rz_factory_code.tar.gz dataset")
    shutil.copytree(os.path.dirname(os.path.dirname(hits[0])), CODE)
sys.path.insert(0, CODE)
sys.path.insert(0, f"{CODE}/scripts")
if not SMOKE:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pandas", "pyarrow", "requests", "pyyaml"], check=False)
# resume: everything an attached previous output finished (moments shards, self-play parts, their json)
prev = sorted(glob.glob(f"{INP}/**/factory/manifest.json", recursive=True))
for m in prev:
    src = os.path.dirname(m)
    for f in glob.glob(f"{src}/**/*", recursive=True):
        dst = f"{OUT}/{os.path.relpath(f, src)}"
        if os.path.isfile(f) and not f.endswith(("manifest.json", ".tmp")) and not os.path.exists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(f, dst)
print("resumed from", prev, "| moments shards", len(glob.glob(f"{OUT}/moments/*/*.json")),
      "| self-play parts", len(glob.glob(f"{OUT}/selfplay/*/*/part-*.parquet")),
      "| jobs", JOBS, "| plan", {k: (len(v[0]), v[1]) for k, v in PLAN.items()})
DEADLINE = T0 + SESSION_H * 3600 - 900          # every job stops starting new work 15 min before the end


# %%
def season_job(league, year, synthetic, deadline):
    """One season: moments shard, then (basketball) the self-play shard; the raw file is deleted after."""
    import noggin_factory as NF
    import noggin_selfplay as SP
    from redzone.noggin import factory as F
    t0 = time.time()
    res = {"league": league, "season": year}
    mo = f"{OUT}/moments/{league}/{year}.parquet"
    if not os.path.exists(mo[:-8] + ".json"):
        if time.time() > deadline:
            return dict(res, skipped="session deadline")
        y, t = NF._one((league, year, RAW, True, 40 if SMOKE else None))
        if t is not None and len(t):
            n_all = len(t)
            t = F.drop_eval_nights(t, league)
            os.makedirs(os.path.dirname(mo), exist_ok=True)
            t.to_parquet(mo + ".tmp", compression="zstd", index=False)
            os.replace(mo + ".tmp", mo)
            info = {"rows": int(len(t)), "dropped_eval_window": int(n_all - len(t)), "complete": True,
                    "nights": int(t["night"].nunique()) if "night" in t else None}
        else:
            info = {"rows": 0, "complete": True, "note": "no data (season missing or no wall clocks)"}
        json.dump(info, open(mo[:-8] + ".json", "w"))
    res["moments"] = json.load(open(mo[:-8] + ".json"))["rows"]
    espn_src = bool(F.SOURCES[league].get("espn"))
    raw = f"{RAW}/{league}/{'espn' if espn_src else 'pbp'}_{year}{'.jsonl.gz' if espn_src else '.parquet'}"
    spj = f"{OUT}/selfplay/{league}/{year}.json"
    sp_done = os.path.exists(spj) and json.load(open(spj)).get("complete")
    tkj = f"{OUT}/tokens/{league}/{year}.json"
    tk_done = os.path.exists(tkj) and json.load(open(tkj)).get("complete")
    if ((synthetic and not sp_done) or not tk_done) and not os.path.exists(raw) and time.time() < deadline:
        if espn_src:
            NF.fetch_espn_season(league, year, __import__("pathlib").Path(raw), max_games=40 if SMOKE else None)
        else:
            NF._fetch(F.SOURCES[league]["url"].format(y=year), __import__("pathlib").Path(raw))
    if not tk_done and os.path.exists(raw) and time.time() < deadline:     # NFM event tokens (every league)
        import noggin_tokens as NT
        res["tokens"] = NT.build(league, __import__("pathlib").Path(raw), year, __import__("pathlib").Path(f"{OUT}/tokens"))["tokens"]
    elif tk_done:
        res["tokens"] = json.load(open(tkj))["tokens"]
    if synthetic and not sp_done and os.path.exists(raw) and time.time() < deadline:
        sp = SP.build(league, __import__("pathlib").Path(raw), year, __import__("pathlib").Path(f"{OUT}/selfplay"),
                      1 if SMOKE else synthetic, 7, 1, deadline - time.time())
        res["selfplay"] = sp["rows"]
        res["selfplay_complete"] = sp["complete"]
    elif sp_done:
        res["selfplay"] = json.load(open(spj))["rows"]
    for p in glob.glob(f"{RAW}/{league}/*{year}*"):
        os.remove(p)
    res["seconds"] = round(time.time() - t0)
    return res


jobs = [(lg, y, syn) for lg, (years, syn) in PLAN.items() for y in (years[:1] if SMOKE else years)]
# interleave leagues so a cut-short session still has every league's newest seasons
jobs.sort(key=lambda j: (PLAN[j[0]][0].index(j[1]), list(PLAN).index(j[0])))
done = []
with ProcessPoolExecutor(JOBS) as ex:
    futs = {}
    for lg, y, syn in jobs:
        futs[ex.submit(season_job, lg, y, syn, DEADLINE)] = (lg, y)
    for f in as_completed(futs):
        lg, y = futs[f]
        try:
            r = f.result()
        except Exception as e:  # noqa: BLE001 - one bad season never stops the run
            r = {"league": lg, "season": y, "error": f"{type(e).__name__}: {e}"[:300]}
        done.append(r)
        print(f"[{(time.time() - T0) / 3600:.2f} h] {json.dumps(r)}", flush=True)
        if left_s() < 600:
            print("session budget reached: cancelling what has not started (finished shards are kept)", flush=True)
            for g in futs:
                g.cancel()
            break

# %%
man = {"built_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "wall_h": round((time.time() - T0) / 3600, 2),
       "leagues": {}}
for kind in ("moments", "selfplay"):
    for f in sorted(glob.glob(f"{OUT}/{kind}/*/*.json")):
        lg, season = f.split("/")[-2], f.split("/")[-1][:-5]
        i = json.load(open(f))
        L = man["leagues"].setdefault(lg, {"moments_rows": 0, "selfplay_rows": 0, "seasons": {}})
        L[f"{kind}_rows"] += int(i.get("rows") or 0)
        L["seasons"].setdefault(season, {})[kind] = {k: i.get(k) for k in
                                                     ("rows", "complete", "slates", "slates_total", "nights")}
for f in sorted(glob.glob(f"{OUT}/tokens/*/*.json")):         # NFM event tokens per league (and per season)
    lg, season = f.split("/")[-2], f.split("/")[-1][:-5]
    i = json.load(open(f))
    L = man["leagues"].setdefault(lg, {"moments_rows": 0, "selfplay_rows": 0, "seasons": {}})
    L["tokens"] = L.get("tokens", 0) + int(i.get("tokens") or 0)
    L["seasons"].setdefault(season, {})["tokens"] = {k: i.get(k) for k in ("tokens", "games", "events", "complete")}
    L["events"] = L.get("events", 0) + int(i.get("events") or 0)
    for k, v in (i.get("coverage") or {}).items():
        L[k] = L.get(k, 0) + int(v)
    L.setdefault("sources", {})
    L["sources"][i.get("source", "?")] = L["sources"].get(i.get("source", "?"), 0) + int(i.get("tokens") or 0)
man["total_tokens"] = sum(v.get("tokens", 0) for v in man["leagues"].values())
# DATA LEDGER (Nick 5:00 PM): tokens per league and source, real vs synthetic, crunch / OT / comeback / blowout share
man["ledger"] = {lg: {"tokens_real": v.get("tokens", 0), "sources": v.get("sources", {}),
                      "selfplay_rows_synthetic": v.get("selfplay_rows", 0), "moments_rows_real": v.get("moments_rows", 0),
                      "crunch_event_share_pct": round(100 * v.get("crunch_events", 0) / max(1, v.get("events", 0)), 2),
                      "ot_games": v.get("ot_games", 0), "comeback_games": v.get("comeback_games", 0),
                      "blowout_games": v.get("blowout_games", 0)}
                 for lg, v in man["leagues"].items()}
man["total_rows"] = sum(v["moments_rows"] + v["selfplay_rows"] for v in man["leagues"].values())
man["missing"] = sorted(f"{lg} {y}" for lg, (years, _) in PLAN.items() for y in years
                        if not os.path.exists(f"{OUT}/moments/{lg}/{y}.json"))
json.dump(man, open(f"{OUT}/manifest.json", "w"), indent=1)
print(json.dumps({lg: {"moments": v["moments_rows"], "selfplay": v["selfplay_rows"], "tokens": v.get("tokens", 0)}
                  for lg, v in man["leagues"].items()}, indent=1))
print("DATA LEDGER:", json.dumps(man["ledger"], indent=1))
print("TOTAL rows:", f"{man['total_rows']:,}", "| NFM tokens:", f"{man['total_tokens']:,}", "| missing seasons:", len(man["missing"]))
shutil.rmtree(RAW, ignore_errors=True)
shutil.rmtree(CODE, ignore_errors=True)
