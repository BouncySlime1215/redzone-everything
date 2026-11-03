"""Round 2+: build MILLIONS of outcome-labeled reasoner moments inside the Kaggle notebook.

    python agent/kaggle/build_moments_kaggle.py --out /kaggle/working/moments [--budget-h 6]
    python agent/kaggle/build_moments_kaggle.py --out /tmp/m --archive data/archive   # local test, no network

Runs on the notebook's CPUs while the teacher holds the GPUs. Internet on; ESPN's public site API,
no token. Nothing is uploaded: only this code pack.

1. Scoreboards: every day of the last SEASONS full seasons plus the current one, for NFL, CFB (FBS),
   NBA, men's college basketball (D-I), NHL and MLB (scripts/download_recent.py's own date and
   event rules: ET event date, final games only).
2. Split by time, whole nights, per league (agent/distill/split.plan_big). eval-data-v1's locked
   nights are locked and the locked block is widened to >= 25% of each league's nights; locked
   games are NEVER downloaded (asserted by game id and night).
3. Every other final game: download its summary, rebuild every live play exactly the way
   scripts/build_reasoner_data.py does (redzone.lab.reasoner_data.game_rows: espn.parse_summary +
   Scorer + brain + brief), label it from what happened next, then delete the summary (disk).
   Resumable: finished games are skipped on the next run.
4. The fast head (lock-in odds, scripts/train_reasoner.py's LightGBM params and features): OOF by
   night inside train, and one model on all train nights for valid/test, so no valid/test night
   ever scores itself. Reports AUC train(OOF) / valid / test per league: the gap check.
5. Export: scripts/export_reasoner_llm.py's brief_with + target, then agent/distill/gold.py's rules
   -> <out>/rows/<league>_<split>.jsonl.gz in the same schema as the pack's rows.

College rules: sportsgrade's big/crunch definitions are pro-league only, so college games are
graded by their base league's rules (CFB = NFL; men's college basketball = NBA with halves mapped
to the 2nd and 4th quarter, so "last 2:00 of the 4th" = "last 2:00 of the 2nd half").
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import os
import random
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

LEAGUES = ["nfl", "cfb", "nba", "cbb", "nhl", "mlb"]       # NFL first: it is processed first, too
ESPN = {"nfl": "football/nfl", "cfb": "football/college-football", "nba": "basketball/nba",
        "cbb": "basketball/mens-college-basketball", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}
GROUPS = {"cfb": "80", "cbb": "50"}           # FBS, Division I (else ESPN lists only featured games)
# (first month-day, last month-day, season spans the new year)
WINDOW = {"nfl": ("0901", "0215", True), "cfb": ("0820", "0125", True), "nba": ("1001", "0625", True),
          "cbb": ("1101", "0410", True), "nhl": ("1001", "0625", True), "mlb": ("0315", "1110", False)}
SEASONS = 3
UA = {"User-Agent": "RedZoneEverything/1.0 (public archive replay)"}


# ---------------------------------------------------------------- 1. dates and scoreboards
def season_days(lg: str, today: dt.date, seasons: int = SEASONS) -> list[dt.date]:
    """Every day of the last `seasons` complete seasons plus the current one, up to yesterday."""
    a, b, wraps = WINDOW[lg]
    days: list[dt.date] = []
    for y in range(today.year - seasons - 1, today.year + 1):
        start = dt.date(y, int(a[:2]), int(a[2:]))
        end = dt.date(y + 1 if wraps else y, int(b[:2]), int(b[2:]))
        d = start
        while d <= end and d < today:
            days.append(d)
            d += dt.timedelta(days=1)
    # keep only `seasons` complete seasons + the current one
    starts = sorted({dt.date(y, int(a[:2]), int(a[2:])) for y in range(today.year - seasons - 1, today.year + 1)
                     if dt.date(y, int(a[:2]), int(a[2:])) < today})
    keep_from = starts[-(seasons + 1)] if len(starts) > seasons else starts[0]
    return [d for d in days if d >= keep_from]


def _client():
    import httpx
    return httpx.Client(timeout=30, headers=UA, follow_redirects=True)


def _get(client, url, params, tries=5):
    for k in range(tries):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 429, 500, 502, 503, 504):
                time.sleep(5 * (k + 1))
                continue
            return None
        except Exception:                     # network blip: back off and retry
            time.sleep(3 * (k + 1))
    return None


def scoreboards(lg: str, days: list[dt.date], cache: Path, workers: int = 6) -> list[dict]:
    """Final games, one entry per game: {league, gid, night, date}. Cached per day."""
    from download_recent import event_date       # the repo's own ET-date rule
    cache.mkdir(parents=True, exist_ok=True)
    base = f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[lg]}/scoreboard"

    def one(day: dt.date):
        key = day.strftime("%Y%m%d")
        p = cache / f"{lg}_{key}.json"
        if p.exists():
            return json.loads(p.read_text())
        with _client() as c:
            params = {"dates": key, "limit": 1000}
            if lg in GROUPS:
                params["groups"] = GROUPS[lg]
            sb = _get(c, base, params)
        if sb is None:
            return None                                  # not cached: retried next run
        out = []
        for ev in sb.get("events") or []:
            if not isinstance(ev, dict) or event_date(ev) != day:
                continue
            if ev.get("status", {}).get("type", {}).get("state") != "post":
                continue
            out.append({"league": lg, "gid": str(ev.get("id")), "night": key, "date": ev.get("date")})
        p.write_text(json.dumps(out))
        return out

    games: dict[str, dict] = {}
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(one, days):
            for g in res or []:
                games.setdefault(g["gid"], g)           # the first night listing it wins (nights_of rule)
    return sorted(games.values(), key=lambda g: (g["night"], g["gid"]))


def archive_games(lg: str, archive: Path) -> list[dict]:
    """Local test mode: the games of data/archive/<league> (no network)."""
    from redzone.lab.reasoner_data import nights_of
    nights = nights_of(archive / lg)
    return sorted(({"league": lg, "gid": g, "night": n, "path": str(archive / lg / f"summary_{g}.json")}
                   for g, n in nights.items() if (archive / lg / f"summary_{g}.json").exists()),
                  key=lambda g: (g["night"], g["gid"]))


# ---------------------------------------------------------------- 3. rows per game
_PATCHED = False


def _college_rules() -> None:
    """Grade college games by their base league's big/crunch rules (see module doc)."""
    global _PATCHED
    if _PATCHED:
        return
    from redzone.lab import sportsgrade
    from redzone.models import League
    big0, crunch0 = sportsgrade.is_big, sportsgrade.is_crunch

    def as_pro(lg, p):
        if p is None or lg != League.CBB:
            return p
        per = ((p.get("period") or {}).get("number")) if isinstance(p.get("period"), dict) else None
        if per is None:
            return p
        q = dict(p)
        q["period"] = {**p["period"], "number": 2 if per == 1 else 4 if per == 2 else per + 2}
        return q

    def is_big(lg, p, prev):
        return big0(lg.base, as_pro(lg, p), as_pro(lg, prev))

    def is_crunch(lg, p):
        return crunch0(lg.base, as_pro(lg, p))

    sportsgrade.is_big, sportsgrade.is_crunch = is_big, is_crunch
    _PATCHED = True


def _clean(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    return x


_CFG = None


def game_rows(g: dict) -> list[dict]:
    """Download (or read) one summary and return its raw reasoner rows (build_reasoner_data's)."""
    global _CFG
    from redzone.config import Config
    from redzone.lab import reasoner_data
    from redzone.lab.backtest import Replay
    from redzone.models import League
    _college_rules()
    if _CFG is None:
        pinned = next((p for p in (ROOT / "data/eval_config.yaml", ROOT / "agent/kaggle/assets/eval_config.yaml")
                       if p.exists()), ROOT / "data/config.yaml")
        _CFG = Config(user_path=pinned)
    lg = League(g["league"])
    tmp = None
    try:
        if g.get("path"):
            path = Path(g["path"])
        else:
            with _client() as c:
                data = _get(c, f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[g['league']]}/summary",
                            {"event": g["gid"]})
            if not data or str((data.get("header") or {}).get("id", "")) != g["gid"]:
                return []
            fd, tmp = tempfile.mkstemp(suffix=f"_{g['gid']}.json")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            path = Path(tmp)
        r = Replay.load(lg, path)
        if r is None or len(r.plays) < 20:
            return []
        mine = set(_CFG.get(f"teams.{lg.value}") or ["WSH"])
        rows = reasoner_data.game_rows(lg, r, g["night"], _CFG, mine)
        return [_clean(x.as_dict()) for x in rows]
    finally:
        if tmp:
            os.unlink(tmp)


# ---------------------------------------------------------------- 4. head
def train_head(lg: str, raw_dir: Path, plan: dict[str, str]) -> tuple[dict, dict]:
    """{gid:i -> head_p} for every row of the league, and the gap report."""
    import lightgbm as lgb
    import numpy as np
    from redzone.lab.reasoner_data import fold_of
    from redzone.reasoner import brief
    feats = brief.features_for(lg)
    keys, X, y, sp, fold = [], [], [], [], []
    for f in sorted(raw_dir.glob(f"{lg}_*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                keys.append(f"{r['gid']}:{r['i']}")
                X.append([r["facts"].get(k) if r["facts"].get(k) is not None else np.nan for k in feats])
                y.append(r["y"])
                sp.append(plan[r["night"]])
                fold.append(fold_of(r["night"], 3))
    if not keys:
        return {}, {}
    X = np.asarray(X, np.float32)
    y = np.asarray(y, np.float32)
    sp = np.asarray(sp)
    fold = np.asarray(fold)
    params = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=60, feature_fraction=0.8,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, max_depth=6, verbose=-1, seed=7,
                  num_threads=max(1, (os.cpu_count() or 2) - 1))
    tr = sp == "train"
    va = sp == "valid"
    p = np.zeros(len(y))
    # rounds picked on valid nights (newer than every train night), then OOF by night inside train
    dtr = lgb.Dataset(X[tr], y[tr], feature_name=feats)
    if va.any():
        b = lgb.train(params, dtr, 600, valid_sets=[lgb.Dataset(X[va], y[va], reference=dtr)],
                      callbacks=[lgb.early_stopping(30, verbose=False)])
        rounds = max(20, b.best_iteration)
    else:
        rounds = 200
    for k in range(3):
        te = tr & (fold == k)
        fit = tr & (fold != k)
        if te.any() and fit.any():
            p[te] = lgb.train(params, lgb.Dataset(X[fit], y[fit], feature_name=feats), rounds).predict(X[te])
    full = lgb.train(params, dtr, rounds)
    p[~tr] = full.predict(X[~tr])

    from train_reasoner import auc as auc_fn                 # the repo's own AUC
    gap = {"rows": int(len(y)), "rounds": rounds, "base_rate": round(float(y.mean()), 4)}
    for s in ("train", "valid", "test"):
        m = sp == s
        v = auc_fn(y[m], p[m]) if m.any() else float("nan")
        gap[f"auc_{s}"] = round(v, 4) if math.isfinite(v) else None
        gap[f"rows_{s}"] = int(m.sum())
    return dict(zip(keys, p.round(4).tolist())), gap


# ---------------------------------------------------------------- 5. export
def export(lg: str, raw_dir: Path, plan: dict[str, str], head: dict, out_dir: Path, seed: int = 7) -> Counter:
    from export_reasoner_llm import brief_with, target
    from agent.distill import gold
    from redzone.models import League
    base = League(lg).base.value
    rng = random.Random(f"{seed}{lg}")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {s: gzip.open(out_dir / f"{lg}_{s}.jsonl.gz", "wt", compresslevel=5) for s in ("train", "valid", "test")}
    n = Counter()
    for f in sorted(raw_dir.glob(f"{lg}_*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                s = plan[r["night"]]
                assert s != "locked", f"locked night {r['night']} reached the export"
                p = float(head.get(f"{r['gid']}:{r['i']}", 0.0))
                for k in ("t_big", "t_break"):
                    r[k] = math.inf if r[k] is None else r[k]
                tgt = target({**r, "league": base}, p)
                row = {"id": f"{lg}:{r['gid']}:{r['i']}", "game_id": r["gid"], "night": r["night"], "split": s,
                       "league": lg, "i": r["i"], "brief": brief_with(r, p, on_screen=rng.random() < 0.3),
                       "target": tgt, **{k: r[k] for k in ("y", "big_next", "crunch_next", "next_text", "garbage",
                                                          "crunch", "late", "mine")},
                       "t_big": None if math.isinf(r["t_big"]) else r["t_big"],
                       "t_break": None if math.isinf(r["t_break"]) else r["t_break"], "head_p": round(p, 4)}
                g, _ = gold.gold_target({**row, "league": base})
                row["export_target"], row["target"] = row["target"], g
                row["hard"] = gold.hardness(row, g)
                files[s].write(json.dumps(row, separators=(",", ":")) + "\n")
                n[s] += 1
    for fh in files.values():
        fh.close()
    return n


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--leagues", nargs="*", default=LEAGUES)
    ap.add_argument("--seasons", type=int, default=SEASONS)
    ap.add_argument("--today", default=None, help="YYYYMMDD (default: today, UTC)")
    ap.add_argument("--archive", default=None, help="local test: build from this archive, no network")
    ap.add_argument("--locked", default=str(ROOT / "agent/kaggle/assets/locked_v1.json"))
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 1))
    ap.add_argument("--budget-h", type=float, default=6.0, help="stop downloading new games after this")
    ap.add_argument("--max-games", type=int, default=None, help="per league (tests)")
    a = ap.parse_args()
    t0 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    today = dt.datetime.strptime(a.today, "%Y%m%d").date() if a.today else dt.datetime.now(dt.timezone.utc).date()
    v1 = json.loads(Path(a.locked).read_text())
    locked_nights = {k: set(v) for k, v in v1["nights"].items()}
    locked_games = {k: set(v) for k, v in v1["games"].items()}
    from agent.distill import split

    # 1 + 2: games and the split plan (written first, so the notebook can filter the teacher queue)
    games: dict[str, list[dict]] = {}
    for lg in a.leagues:
        games[lg] = archive_games(lg, Path(a.archive)) if a.archive else \
            scoreboards(lg, season_days(lg, today, a.seasons), out / "scoreboards", a.workers * 2)
        print(f"{lg}: {len(games[lg])} final games listed", flush=True)
    plan = split.plan_big({lg: [g["night"] for g in gs] for lg, gs in games.items()},
                          {lg: sorted(v) for lg, v in locked_nights.items()})
    for lg, gs in games.items():
        for g in gs:
            if g["gid"] in locked_games.get(lg, ()):
                assert plan[lg][g["night"]] == "locked", f"{lg} game {g['gid']} is locked in eval-data-v1"
    (out / "split_plan.json").write_text(json.dumps(plan))
    summary = {lg: dict(Counter(plan[lg].values())) for lg in plan}
    print("nights per split:", json.dumps(summary), flush=True)

    # 3: rows, game by game, NFL first and leagues interleaved so a budget stop stays balanced
    done_p = out / "done.txt"
    done = set(done_p.read_text().split()) if done_p.exists() else set()
    todo = {lg: [g for g in gs if plan[lg][g["night"]] != "locked" and f"{lg}:{g['gid']}" not in done]
            for lg, gs in games.items()}
    for lg in todo:
        random.Random(lg).shuffle(todo[lg])
        if a.max_games:
            todo[lg] = todo[lg][:a.max_games]
    order = []
    while any(todo.values()):
        for lg in a.leagues:
            if todo.get(lg):
                order.append(todo[lg].pop())
    for g in order:                                   # the hard rule, asserted per game
        assert plan[g["league"]][g["night"]] != "locked" and g["gid"] not in locked_games.get(g["league"], ())
    print(f"games to build: {len(order)} ({len(done)} already done)", flush=True)
    shard = f"{os.getpid()}_{int(time.time())}"
    writers = {lg: gzip.open(raw / f"{lg}_{shard}.jsonl.gz", "wt", compresslevel=5) for lg in a.leagues}
    stats = Counter()
    stopped = False
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(a.workers) as ex, open(done_p, "a") as dl:
        futs = {}
        it = iter(order)
        for g in it:
            futs[ex.submit(game_rows, g)] = g
            if len(futs) >= a.workers * 4:
                break
        while futs:
            from concurrent.futures import FIRST_COMPLETED, wait
            fin, _ = wait(futs, return_when=FIRST_COMPLETED)
            for fu in fin:
                g = futs.pop(fu)
                try:
                    rows = fu.result()
                except Exception as e:                     # one bad game never stops the build
                    stats[f"{g['league']}_errors"] += 1
                    if stats[f"{g['league']}_errors"] <= 3:
                        print(f"{g['league']} {g['gid']}: {type(e).__name__}: {e}", flush=True)
                    rows = None
                if rows is not None:
                    w = writers[g["league"]]
                    for r in rows:
                        w.write(json.dumps(r, separators=(",", ":")) + "\n")
                    stats[f"{g['league']}_games"] += 1
                    stats[f"{g['league']}_rows"] += len(rows)
                    dl.write(f"{g['league']}:{g['gid']}\n")
                if not stopped and time.time() - t0 > a.budget_h * 3600:
                    stopped = True
                    print("budget reached: no new games", flush=True)
                if not stopped:
                    nxt = next(it, None)
                    if nxt is not None:
                        futs[ex.submit(game_rows, nxt)] = nxt
            n_done = sum(v for k, v in stats.items() if k.endswith("_games"))
            if n_done and n_done % 500 == 0:
                print(f"{n_done} games, {sum(v for k, v in stats.items() if k.endswith('_rows'))} rows, "
                      f"{(time.time() - t0) / 60:.0f} min", flush=True)
    for w in writers.values():
        w.close()
    print("rows built:", json.dumps(stats), flush=True)

    # 4 + 5: head (gap report) and export
    report = {"today": str(today), "nights": summary, "build": dict(stats), "stopped_early": stopped,
              "locked_v1_games_excluded": {k: len(v) for k, v in locked_games.items()}, "head": {}, "rows": {}}
    for lg in a.leagues:
        head, gap = train_head(lg, raw, plan[lg])
        report["head"][lg] = gap
        report["rows"][lg] = dict(export(lg, raw, plan[lg], head, out / "rows"))
        print(lg, "head", json.dumps(gap), "rows", report["rows"][lg], flush=True)
    report["total_rows"] = sum(sum(v.values()) for v in report["rows"].values())
    report["minutes"] = round((time.time() - t0) / 60, 1)
    (out / "build_report.json").write_text(json.dumps(report, indent=1))
    (out / "DONE").write_text("ok")
    print(json.dumps({k: report[k] for k in ("rows", "total_rows", "minutes")}), flush=True)


if __name__ == "__main__":
    main()
