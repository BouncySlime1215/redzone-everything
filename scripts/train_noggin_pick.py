#!/usr/bin/env python3
"""Train Noggin Reflex PICK v1 by imitating the hindsight oracle on whole factory nights (offline, cloud).

    python scripts/train_noggin_pick.py nba --raw /home/claude/factory_raw/nba --out data/noggin/pick

Nights come from the factory's raw play-by-play (hoopR for NBA, wallclocks 2013-14+), every game of the
night together, the screens at the candidate's lag (behind_live 90 + the 26 s channel delay = 116 s).
Splits are whole nights, oldest -> newest: train seasons | calib seasons (early stopping + the switch
margin) | test seasons (graded once). Forward CV over the train seasons reports the mean and worst fold.
Every night is graded with the app's own grader (sportsgrade.grade) for the PICK rollout, the oracle, and
a hand baseline (crunch first, then the closest game), on the FULL calib and test splits.
Why: Trains Noggin Reflex PICK v1 by imitating the hindsight oracle, offline in the cloud (docstring).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab import oracle as O  # noqa: E402
from redzone.lab import sportsgrade  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin import pick as PK  # noqa: E402

LAG_S = 116.0
TUNE_S = 5.0
# [possession-start entry] > 0: entry cues to the target's last dead ball up to CUE_S behind live (the
# time-shift buffer): labels from oracle.solve_cue, land_ok = a dead ball in reach, rollouts carry the offset
CUE_S = float(os.environ.get("RZ_PICK_CUE_S", "0") or 0)
CUE_FEATS = os.environ.get("RZ_PICK_CUE_FEATS") == "1"    # + pick.CUE_FEATURES (what the cue replays)
MINE = {"WSH", "UNC"}
COLS = ["id", "sequence_number", "type_text", "text", "period_number", "clock_display_value", "wallclock",
        "scoring_play", "score_value", "home_score", "away_score", "game_id", "home_team_abbrev",
        "away_team_abbrev"]
PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=63, min_data_in_leaf=200, feature_fraction=0.9,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, num_threads=4)


def season_nights_espn(league: str, path: Path) -> dict:
    """[NHL / ESPN leagues] factory espn_<year>.jsonl.gz (factory.espn_game records) -> {night: [Replay]},
    the same shape as the hoopR loader: plays in ESPN's own format, wall clocks made non-decreasing."""
    import gzip

    from redzone.lab.backtest import Replay
    from redzone.noggin import factory as FA
    from redzone.sources import espn
    lg = League(league)
    out: dict[str, list] = {}
    with gzip.open(path, "rt") as f:
        for line in f:
            g = json.loads(line)
            plays, wall, last = [], [], None
            for p in g["plays"]:
                t = espn._ts(p.get("wallclock"))
                t = t if t is not None else (last + 5 if last else None)
                if t is None:
                    continue
                plays.append(p)
                wall.append(max(t, last) if last else t)
                last = wall[-1]
            if len(plays) < 2:
                continue
            night = FA.night_of(wall[0])
            if night < FA.EVAL_FIRST_NIGHT[league]:
                out.setdefault(night, []).append(Replay(lg, str(g["id"]), {}, plays, wall, g.get("matchup") or "? @ ?"))
    return {k: v for k, v in out.items() if len(v) >= 2}


def season_nights(league: str, path: Path) -> dict:
    import pandas as pd
    if str(path).endswith(".jsonl.gz"):
        return season_nights_espn(league, path)

    from redzone.lab.backtest import Replay
    from redzone.noggin import factory as FA
    from redzone.sources import espn
    lg = League(league)
    df = pd.read_parquet(path, columns=COLS)
    df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
    out: dict[str, list] = {}
    for gid, g in df.groupby("game_id"):
        g = g.sort_values("sq")
        plays = FA._plays_basketball(g)
        wall, last = [], None
        for p in plays:
            t = espn._ts(p.get("wallclock"))
            t = t if t is not None else (last + 5 if last else None)
            wall.append(t)
            last = t if t is not None else last
        if not wall or wall[0] is None:
            continue
        for i in range(1, len(wall)):
            wall[i] = max(wall[i], wall[i - 1])
        r = Replay(lg, str(gid), {}, plays, wall, f"{g.away_team_abbrev.iloc[0]} @ {g.home_team_abbrev.iloc[0]}")
        night = FA.night_of(wall[0])
        if night < FA.EVAL_FIRST_NIGHT[league]:
            out.setdefault(night, []).append(r)
    return {k: v for k, v in out.items() if len(v) >= 2}


def night_pack(reps):
    t0 = min(r.wall[0] for r in reps) - 600.0
    t1 = max(r.wall[-1] for r in reps) + 600.0
    n = O.Night(reps, MINE, LAG_S, TUNE_S, t0, t1)
    steps = np.arange(t0, t1, O.STEP_S)
    tab = O.tables(n, steps)
    X = PK.features_for_night(reps, cue_tab(tab), steps, LAG_S, MINE)
    if CUE_S > 0 and CUE_FEATS:
        X = np.concatenate([X, PK.cue_features(reps, O.cue_steps(tab, CUE_S), steps, LAG_S)], axis=2)
    return n, steps, tab, X


def cue_tab(tab):
    """The tables the features see: with CUE_S, a game is landable when a dead ball is in reach behind live."""
    return dict(tab, land=O.cue_steps(tab, CUE_S) >= 0) if CUE_S > 0 else tab


def oracle_solve(n, tab):
    return O.solve_cue(n, "combined", tab, max_cue_s=CUE_S) if CUE_S > 0 else O.solve(n, "combined", tab)


def labels(n, steps, tab):
    sw = oracle_solve(n, tab)
    on = PK.oracle_on(sw, n.replays, steps, TUNE_S)
    blk = max(1, int(math.ceil(TUNE_S / O.STEP_S)))
    tgt = np.concatenate([on[blk:], np.repeat(on[-1:], blk)])
    return sw, tgt


def rows(X, tgt, every: int):
    """Training rows: (game, step) where the game could be landed on or is the one on screen."""
    G, K, _ = X.shape
    ks = np.arange(0, K, every)
    cur = PK.PICK_FEATURES.index("land_ok")
    xs, ys = [], []
    for g in range(G):
        keep = ks[(X[g, ks, cur] > 0) | (tgt[ks] == g)]
        xs.append(X[g, keep])
        ys.append((tgt[keep] == g).astype(np.float32))
    return np.concatenate(xs), np.concatenate(ys)


def build_season(args):
    league, path, every = args
    out = []
    for night, reps in sorted(season_nights(league, path).items()):
        n, steps, tab, X = night_pack(reps)
        sw, tgt = labels(n, steps, tab)
        x, y = rows(X, tgt, every)
        out.append((night, x, y))
    return out


def score_of(model, X):
    G, K, F = X.shape
    return model.predict(X.reshape(-1, F), num_threads=1).reshape(G, K) if model is not None else _hand(X)


def _hand(X):
    """Hand baseline: crunch first, then the closest live game, a small pull to what's on (no learning)."""
    c = {f: i for i, f in enumerate(PK.PICK_FEATURES)}
    live = (X[:, :, c["pre"]] == 0) & (X[:, :, c["post"]] == 0)
    s = 2.0 * X[:, :, c["head_crunch"]] + 1.0 / (1.0 + X[:, :, c["margin"]]) - 0.5 * X[:, :, c["dead_screen"]]
    return np.where(live, s, -1.0)


def grade_split(args):
    league, path, nights, model_path, margins = args
    model = None
    if model_path:
        import lightgbm as lgb
        model = lgb.Booster(model_file=str(model_path))
    sn = season_nights(league, path)
    res = []
    for night in sorted(set(sn) & set(nights)):
        reps = sn[night]
        n, steps, tab, X = night_pack(reps)
        row = {"night": night}
        cards = {}
        cue = O.cue_steps(tab, CUE_S) if CUE_S > 0 else None
        cards["oracle"] = O.grade(n, oracle_solve(n, tab))
        if cue is not None:
            cards["oracle_live"] = O.grade(n, O.solve(n, "combined", tab))
        cards["hand"] = O.grade(n, PK.rollout(_hand(X), X, reps, steps, TUNE_S, 0.5, "hand", cue))
        if model is not None:
            sc = score_of(model, X)
            for m in margins:
                cards[f"pick_{m}"] = O.grade(n, PK.rollout(sc, X, reps, steps, TUNE_S, m, "pick", cue))
        for k, c in cards.items():
            row[k] = dict(big=c.big, big_live=c.big_live, crunch=c.crunch, crunch_live=c.crunch_live,
                          my_crunch=c.my_crunch, my_crunch_live=c.my_crunch_live, dead_s=c.dead, watched_s=c.watched,
                          switches=c.switches, cuts=len(c.cuts), entries_mid_play=c.cut.summary()["entries_mid_play"],
                          hours=(n.t1 - n.t0) / 3600)
        res.append(row)
    return res


def totals(rows_, key):
    t = {}
    for r in rows_:
        for k, v in r.get(key, {}).items():
            t[k] = t.get(k, 0) + v
    if not t:
        return {}
    pct = lambda a, b: round(100 * a / b, 1) if b else None   # noqa: E731
    return dict(nights=len(rows_), big_live_pct=pct(t["big_live"], t["big"]),
                crunch_live_pct=pct(t["crunch_live"], t["crunch"]),
                my_crunch_live=f"{t['my_crunch_live']}/{t['my_crunch']}",
                dead_air_pct=pct(t["dead_s"], t["watched_s"]), switches_per_hour=round(t["switches"] / t["hours"], 2),
                mid_play_cuts=t["cuts"], entries_mid_play=t["entries_mid_play"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--raw", type=Path, default=Path("/home/claude/factory_raw/nba"))
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "noggin" / "pick")
    ap.add_argument("--train", default="2016-2022")
    ap.add_argument("--calib", default="2023-2024")
    ap.add_argument("--test", default="2025-2026")
    ap.add_argument("--every", type=int, default=3, help="sample every n-th step for training rows")
    ap.add_argument("--rounds", type=int, default=600)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--model", type=Path, default=None, help="grade this saved model (skip building + training)")
    ap.add_argument("--cv-json", type=Path, default=None, help="with --model: the CV rows from the training run")
    ap.add_argument("--cue-s", type=float, default=None,
                    help="possession-start entry: cue to the last dead ball up to this many s behind live")
    ap.add_argument("--cue-feats", action="store_true", help="with --cue-s: add pick.CUE_FEATURES")
    a = ap.parse_args()
    if a.cue_feats:
        global CUE_FEATS
        CUE_FEATS = True
        os.environ["RZ_PICK_CUE_FEATS"] = "1"
    if a.cue_s is not None:
        global CUE_S
        CUE_S = a.cue_s
        os.environ["RZ_PICK_CUE_S"] = str(a.cue_s)        # the spawned graders read it at import
    import lightgbm as lgb
    rng = lambda s: list(range(int(s.split("-")[0]), int(s.split("-")[1]) + 1))   # noqa: E731
    a.out.mkdir(parents=True, exist_ok=True)
    seasons = {"train": rng(a.train), "calib": rng(a.calib), "test": rng(a.test)}
    t0 = time.time()
    if a.model:
        model, mp_ = lgb.Booster(model_file=str(a.model)), a.model
        cv = json.loads(a.cv_json.read_text()) if a.cv_json else []
        ytr = np.zeros(0)
        calib_nights = None
    else:
        data = {}
        with ProcessPoolExecutor(a.jobs) as ex:
            allyrs = seasons["train"] + seasons["calib"]
            for y, res in zip(allyrs, ex.map(build_season, [(a.league, a.raw / f"pbp_{y}.parquet", a.every)
                                                            for y in allyrs])):
                data[y] = res
                print(f"  {a.league} {y}: {len(res)} nights, {sum(len(r[2]) for r in res):,} rows "
                      f"({time.time() - t0:.0f} s)", flush=True)
        stack = lambda yrs: (np.concatenate([x for y in yrs for _, x, _ in data[y]]),   # noqa: E731
                             np.concatenate([yy for y in yrs for _, _, yy in data[y]]))
        # forward CV over the train seasons: fit on everything before season s, score s
        cv = []
        for s in seasons["train"][-5:]:
            xtr, ytr = stack([y for y in seasons["train"] if y < s])
            xva, yva = stack([s])
            m = lgb.train(PARAMS, lgb.Dataset(xtr, ytr), 300)
            p = np.clip(m.predict(xva), 1e-6, 1 - 1e-6)
            base = np.clip(ytr.mean(), 1e-6, 1 - 1e-6)
            ll = float(-np.mean(yva * np.log(p) + (1 - yva) * np.log(1 - p)))
            llb = float(-np.mean(yva * np.log(base) + (1 - yva) * np.log(1 - base)))
            cv.append(dict(season=s, logloss=round(ll, 4), base_logloss=round(llb, 4)))
            print("  cv", cv[-1], flush=True)
        xtr, ytr = stack(seasons["train"])
        xca, yca = stack(seasons["calib"])
        model = lgb.train(PARAMS, lgb.Dataset(xtr, ytr), a.rounds, valid_sets=[lgb.Dataset(xca, yca)],
                          callbacks=[lgb.early_stopping(50, verbose=False)])
        model_path = a.out / (f"pick_{a.league}.cue{int(CUE_S)}{'f' if CUE_FEATS else ''}.txt" if CUE_S > 0 else f"pick_{a.league}.v1.txt")
        model.save_model(str(model_path))
        print(f"  model: {model.num_trees()} trees, {len(ytr):,} train rows, {xtr.shape[1]} features "
              f"({time.time() - t0:.0f} s)", flush=True)
        mp_ = model_path
        calib_nights = {n for y in seasons["calib"] for n, _, _ in data[y]}

    margins = (0.0, 0.05, 0.1, 0.2, 0.3)
    report = {"cv": cv, "info": dict(train_rows=int(len(ytr)), trees=model.num_trees(), features=list(PK.PICK_FEATURES) + (list(PK.CUE_FEATURES) if CUE_S > 0 and CUE_FEATS else []),
                                     lag_s=LAG_S, tune_s=TUNE_S, feed_lag_p90=PK.FEED_LAG_P90[League(a.league)])}
    for split in ("calib", "test"):
        yrs = seasons[split]
        jobs = []
        for y in yrs:
            p = a.raw / f"pbp_{y}.parquet"
            nn = sorted(season_nights(a.league, p))          # every night of the split (the full set)
            for c in range(0, len(nn), 24):                  # small chunks keep every worker busy
                jobs.append((a.league, p, nn[c:c + 24], mp_, margins))
        # spawn, not fork: a forked child inherits LightGBM's OpenMP pool and hangs in predict
        with ProcessPoolExecutor(a.jobs, mp_context=mp.get_context("spawn")) as ex:
            res = [r for part in ex.map(grade_split, jobs) for r in part]
        keys = ["oracle", "hand"] + (["oracle_live"] if CUE_S > 0 else []) + [f"pick_{m}" for m in margins]
        report[split] = {k: totals(res, k) for k in keys}
        if split == "calib":
            # the margin is picked on calib only, by the oracle's own objective (never on test)
            def val(t):
                return (3 * t["big_live_pct"] + t["crunch_live_pct"] - 2 * t["dead_air_pct"]
                        - 0.5 * t["switches_per_hour"])
            best = max(margins, key=lambda m: val(report["calib"][f"pick_{m}"]))
            report["margin"] = best
        print(f"  {split}: " + json.dumps(report[split]), flush=True)
    report["info"]["cue_s"] = CUE_S
    (a.out / (f"pick_{a.league}.cue{int(CUE_S)}{'f' if CUE_FEATS else ''}.report.json" if CUE_S > 0 else f"pick_{a.league}.report.json")).write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("margin", "test")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
