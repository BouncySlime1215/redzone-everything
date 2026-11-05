"""Train and grade the brain's per-league models (``scripts/train_brain.py`` is the CLI).

Split is strictly by time: older seasons train; the newest season is cut in two by schedule order
(valid = first half, test = second half). A league with only one season trains on its first 70%
of games (schedule order), validates on the next 15% and tests on the last 15%. No game is in two
splits. The ESPN archive (our own recorded feed, 2026-27 season for NBA/NHL/MLB) is a second,
later test set graded through the live code path by ``live_eval``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from redzone.lab import datasets
from redzone.scoring import brain as B

PARAMS = dict(learning_rate=0.08, num_leaves=31, max_depth=7, min_data_in_leaf=400, feature_fraction=0.9,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=5.0, max_bin=63, num_threads=4, verbose=-1,
              seed=7)
ROUNDS = 300
MAX_TRAIN_ROWS = 700_000
LABEL = {"break": "break_soon", "key": "key_soon", "hot": "hotness"}


# --- metrics -------------------------------------------------------------------------------------

def auc(y: np.ndarray, p: np.ndarray) -> float:
    y = np.asarray(y, bool)
    n1, n0 = y.sum(), (~y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ps = np.asarray(p)[order]
    ranks = np.empty(len(p))
    # average ranks for ties
    _, start, counts = np.unique(ps, return_index=True, return_counts=True)
    avg = np.repeat(start + (counts + 1) / 2.0, counts)
    ranks[order] = avg
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def calib(y: np.ndarray, p: np.ndarray, bins: int = 10) -> tuple[float, list[list[float]]]:
    """Expected calibration error (equal-width bins) and the table [mean p, observed rate, share]."""
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), 0, 1)
    b = np.minimum((p * bins).astype(int), bins - 1)
    ece, table = 0.0, []
    for k in range(bins):
        m = b == k
        if not m.any():
            continue
        mp, my, w = p[m].mean(), y[m].mean(), m.mean()
        ece += w * abs(mp - my)
        table.append([round(mp, 3), round(my, 3), round(w, 3)])
    return float(ece), table


def binary_report(y, p) -> dict:
    y, p = np.asarray(y, float), np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    base = y.mean()
    brier = float(np.mean((p - y) ** 2))
    brier0 = float(base * (1 - base))
    ece, table = calib(y, p)
    return {"n": int(len(y)), "rate": round(float(base), 4), "auc": round(auc(y, p), 4),
            "brier": round(brier, 4), "brier_skill": round(1 - brier / brier0, 4) if brier0 else None,
            "logloss": round(float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))), 4),
            "ece": round(ece, 4), "calibration": table}


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3:
        return float("nan")
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def reg_report(y, p) -> dict:
    y, p = np.asarray(y, float), np.asarray(p, float)
    err = p - y
    ss = float(np.sum((y - y.mean()) ** 2)) or 1.0
    return {"n": int(len(y)), "mae": round(float(np.mean(np.abs(err))), 2),
            "rmse": round(float(np.sqrt(np.mean(err ** 2))), 2), "r2": round(1 - float(np.sum(err ** 2)) / ss, 4),
            "spearman": round(spearman(y, p), 4)}


# --- split ---------------------------------------------------------------------------------------

def _order_key(gid: str):
    s = str(gid)
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def time_split(df) -> dict[str, set]:
    """{'train'|'valid'|'test': set of game ids}, by season then schedule order."""
    g = df.groupby("game_id", sort=False).season.first()
    seasons = sorted(g.unique())
    if len(seasons) > 1:
        train = set(g[g < seasons[-1]].index)
        newest = sorted(g[g == seasons[-1]].index, key=_order_key)
        h = len(newest) // 2
        return {"train": train, "valid": set(newest[:h]), "test": set(newest[h:])}
    ids = sorted(g.index, key=_order_key)
    a, b = int(len(ids) * 0.70), int(len(ids) * 0.85)
    return {"train": set(ids[:a]), "valid": set(ids[a:b]), "test": set(ids[b:])}


def task_rows(df, task: str):
    """Rows a task is trained and graded on: break = in play only (a break already in the data
    needs no forecast); key / hot = every live row. Game-end rows are never live."""
    live = df.stoppage != datasets.STOPPAGE_CODE["game_end"]
    if task == "break":
        live &= df.in_break == 0
    return df[live]


# --- training ------------------------------------------------------------------------------------

def train_league(league: str, root: Path | None = None, out_dir: Path | None = None, log=print) -> dict:
    import lightgbm as lgb

    feats = B.features_for(league)
    cols = ["game_id", "season", "seq"] + feats + ["break_soon", "key_soon", "hotness", "in_break"]
    df = datasets.load(league, columns=list(dict.fromkeys(cols)), root=root)
    split = time_split(df)
    part = df.game_id.map(lambda g: "train" if g in split["train"] else "valid" if g in split["valid"] else "test")
    rng = np.random.default_rng(7)
    if B.BASE[league] == "nhl":
        # The live feed only sometimes knows the skaters (power plays come from the NHL feed):
        # hide them for 30% of training games so the trees learn what to do without them.
        hide = {g for g in split["train"] if rng.random() < 0.3}
        m = df.game_id.isin(hide)
        df.loc[m, ["home_skaters", "away_skaters"]] = np.nan
    models, report = {}, {"league": league, "features": feats,
                          "games": {k: len(v) for k, v in split.items()},
                          "seasons": sorted(int(s) for s in df.season.unique())}
    for task in ("break", "key", "hot"):
        y_col = LABEL[task]
        rows = task_rows(df, task)
        p = part.loc[rows.index]
        tr, va, te = rows[p == "train"], rows[p == "valid"], rows[p == "test"]
        if len(tr) > MAX_TRAIN_ROWS:
            tr = tr.iloc[np.sort(rng.choice(len(tr), MAX_TRAIN_ROWS, replace=False))]
        X = lambda d: d[feats].to_numpy(np.float32)  # noqa: E731
        params = dict(PARAMS, objective="binary" if task != "hot" else "regression")
        t0 = time.time()
        dtr = lgb.Dataset(X(tr), tr[y_col].to_numpy(np.float32), feature_name=feats, free_raw_data=True)
        dva = lgb.Dataset(X(va), va[y_col].to_numpy(np.float32), reference=dtr)
        bst = lgb.train(params, dtr, ROUNDS, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(25, verbose=False)])
        secs = time.time() - t0
        arrays, meta = B.trees_from_lightgbm(bst.dump_model(num_iteration=bst.best_iteration), feats)
        meta["rate"] = round(float(tr[y_col].mean()), 4)  # training base rate (key points are relative to it)
        models[task] = (arrays, meta)
        trees = B.Trees(arrays, meta)
        xt = X(te)
        pred = bst.predict(xt, num_iteration=bst.best_iteration)
        # The numpy evaluator must reproduce LightGBM exactly (spot check).
        k = min(300, len(xt))
        mine = np.array([trees.predict(xt[i])[0] for i in range(k)])
        drift = float(np.max(np.abs(mine - pred[:k]))) if k else 0.0
        t1 = time.perf_counter()
        for i in range(k):
            trees.predict(xt[i])
        us = (time.perf_counter() - t1) / max(1, k) * 1e6
        rep = binary_report(te[y_col], pred) if task != "hot" else reg_report(te[y_col], np.clip(pred, 0, 100))
        rep.update(trees=int(len(arrays["roots"])), train_rows=int(len(tr)), train_s=round(secs, 1),
                   numpy_vs_lgb_max_diff=drift, predict_us=round(us, 1))
        if task != "hot":
            rep["valid"] = binary_report(va[y_col], bst.predict(X(va), num_iteration=bst.best_iteration))
            rep["valid"].pop("calibration", None)
        imp = bst.feature_importance("gain")
        rep["top_features"] = [feats[i] for i in np.argsort(-imp)[:6]]
        report[task] = rep
        log(f"  {league:5s} {task:5s} trees={rep['trees']:3d} {secs:5.1f}s  "
            + (f"AUC {rep['auc']:.3f} Brier {rep['brier']:.4f} ECE {rep['ece']:.3f}" if task != "hot"
               else f"MAE {rep['mae']:.1f} Spearman {rep['spearman']:.3f}")
            + f"  numpy diff {drift:.1e}  {us:.0f} us")
    out = (out_dir or B.MODEL_DIR) / f"{league}.npz"
    B.save(out, models, {"league": league, "trained": time.strftime("%Y-%m-%d"),
                         "split": "time", "games": report["games"], "seasons": report["seasons"]})
    report["file_kb"] = round(out.stat().st_size / 1024, 1)
    return report


def write_metrics(reports: dict, path: Path) -> None:
    path.write_text(json.dumps(reports, indent=1) + "\n")


# --- live-path head-to-head: brain vs the hand-written heuristics ---------------------------------

def live_eval(league: str, archive: Path | None = None, cfg=None, max_games: int | None = None,
              weight: float = 0.5) -> dict:
    """Replay every archived ESPN game (our own recorded feed, after every training game) one
    play at a time through the live code path: espn.parse_summary -> brain / BreakBook / Scorer.
    Labels come from the espn_<league> training table (same plays, same order).

    Compared, on the same rows:
      break  brain.break_soon_p  vs  breaks.forecast().p (rules)  vs  their blend (brain.break_weight)
      key    brain.key_soon_p    vs  Scorer moment M (hand-written excitement, 0-100)
      hot    brain.hotness       vs  Scorer raw score (Spearman with the game-flow label)
    """
    from redzone.config import Config
    from redzone.models import League, Phase
    from redzone.scoring import breaks
    from redzone.scoring.engine import Scorer
    from redzone.sources import espn

    lg = League(league)
    archive = archive or (datasets.ROOT / "data" / "archive" / league)
    lab = datasets.load(f"espn_{league}", columns=["game_id", "seq", "break_soon", "key_soon", "hotness",
                                                   "in_break", "stoppage"])
    gids = list(dict.fromkeys(lab.game_id))[: max_games or None]
    lab = lab.set_index(["game_id", "seq"]).sort_index()
    cfg = cfg or Config(user_path=datasets.ROOT / "data" / "_none.yaml")
    rows = []
    t_brain = 0.0
    n_brain = 0
    for gid in gids:
        path = archive / f"summary_{gid}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        plays = espn.flatten_plays(lg, data)
        wall = [espn._ts(p.get("wallclock")) for p in plays]
        clock = {"t": 0.0}
        # The rules' own numbers: a scorer that never sees a model, whatever brain.enabled says.
        scorer = Scorer(cfg, clock=lambda: clock["t"], brain=B.Brain(enabled=False))
        brain = B.Brain(version=2)
        book = breaks.BreakBook(clock=lambda: clock["t"])
        last_t = 0.0
        for i in range(len(plays)):
            last_t = wall[i] or (last_t + 5.0)
            clock["t"] = last_t
            g = espn.parse_summary(lg, data, upto=i, plays=plays)
            if g is None or g.phase != Phase.LIVE:
                continue
            try:
                y = lab.loc[(gid, i)]
            except KeyError:
                continue
            book.observe(g)
            f = breaks.forecast(g, book.clocks(g.game_id, g.period))
            sc = scorer.score(g)
            t0 = time.perf_counter()
            r = brain.read(g)
            t_brain += time.perf_counter() - t0
            n_brain += 1
            if r.break_soon_p is None:
                continue
            fb = breaks.blend(f, r.break_soon_p, g, weight)
            rows.append((y.break_soon, y.key_soon, y.hotness, y.in_break, y.stoppage, f.p, fb.p,
                         r.break_soon_p, r.key_soon_p, r.hotness, sc.moment, sc.raw))
    if not rows:
        return {"league": league, "games": 0}
    a = np.array(rows, dtype=float)
    yb, yk, yh, inb, st = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    play = (inb == 0) & (st != datasets.STOPPAGE_CODE["game_end"])
    live = st != datasets.STOPPAGE_CODE["game_end"]
    out = {"league": league, "games": len(gids), "rows": int(live.sum()),
           "brain_ms_per_state": round(t_brain / max(1, n_brain) * 1000, 3)}
    out["break"] = {"rules": binary_report(yb[play], a[play, 5]), "brain": binary_report(yb[play], a[play, 7]),
                    "blend": binary_report(yb[play], a[play, 6])}
    out["key"] = {"rules_auc": round(auc(yk[live], a[live, 10]), 4), "brain": binary_report(yk[live], a[live, 8])}
    out["hot"] = {"rules_spearman": round(spearman(yh[live], a[live, 11]), 4),
                  "brain": reg_report(yh[live], a[live, 9])}
    for v in out["break"].values():
        v.pop("calibration", None)
    out["key"]["brain"].pop("calibration", None)
    return out
