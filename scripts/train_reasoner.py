"""Train the reasoner's fast head (lock_in odds) per league, cross-fitted by night.

    python scripts/build_reasoner_data.py nfl nba nhl     # first: the rows
    python scripts/train_reasoner.py nfl nba nhl

For each league:
  1. Nights are split into K folds. For each fold a model is trained on the other folds only (one
     of them used for early stopping) and scores the held-out fold: every row gets an
     out-of-fold (OOF) prediction from a model that never saw its night.
  2. The cut points (watch / must_see) are picked on the OOF predictions.
  3. The shipped model is trained on every night, with the mean best round count.

Writes redzone/reasoner/models/<league>.npz (the shipped head), data/training/reasoner_folds/
<league>_f<k>.npz (the fold models, for cross-fitted scorecards) and data/training/
reasoner_<league>_oof.json (OOF odds + the lift report vs the brain alone).
Why: Trains the reasoner's fast lock-in head per league, cross-fitted by night (docstring).
"""
import argparse
import gzip
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab.reasoner_data import HORIZON_S, fold_of  # noqa: E402
from redzone.reasoner import brief  # noqa: E402
from redzone.reasoner.head import MODEL_DIR  # noqa: E402
from redzone.scoring import brain as B  # noqa: E402

PARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=15, min_data_in_leaf=60,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0,
              max_depth=6, verbose=-1, seed=7, num_threads=2)
ROUNDS = 600
K = {"nfl": 5, "nba": 3, "nhl": 3, "mlb": 3}


def load_rows(lg: str) -> list[dict]:
    with gzip.open(ROOT / f"data/training/reasoner_{lg}.jsonl.gz", "rt") as fh:
        return [json.loads(x) for x in fh]


def matrix(rows, feats):
    return np.array([[(r["facts"].get(f) if r["facts"].get(f) is not None else np.nan) for f in feats]
                     for r in rows], dtype=np.float32)


def auc(y, p):
    y, p = np.asarray(y), np.asarray(p)
    pos, neg = y == 1, y == 0
    if not pos.any() or not neg.any():
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    ranks[order] = np.arange(1, len(p) + 1)
    # ties: average ranks
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, ranks)
    ranks = (sums / cnt)[inv]
    return float((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * neg.sum()))


def train(Xtr, ytr, Xva, yva, feats, rounds=None):
    import lightgbm as lgb
    dtr = lgb.Dataset(Xtr, ytr, feature_name=feats, free_raw_data=True)
    if rounds is not None:
        return lgb.train(PARAMS, dtr, rounds), rounds
    dva = lgb.Dataset(Xva, yva, reference=dtr)
    bst = lgb.train(PARAMS, dtr, ROUNDS, valid_sets=[dva], callbacks=[lgb.early_stopping(30, verbose=False)])
    return bst, max(20, bst.best_iteration)


def save_head(path: Path, bst, feats, info, rounds):
    arrays, meta = B.trees_from_lightgbm(bst.dump_model(num_iteration=rounds), feats)
    B.save(path, {"lock": (arrays, meta)}, info)


def pick_cuts(y, p, ok):
    """watch: odds where precision first reaches 1.5x the base rate; must_see: 2.5x (or the top 8%
    of states when that is never reached). Garbage-time rows (ok=False) don't count."""
    y, p = np.asarray(y)[ok], np.asarray(p)[ok]
    base = y.mean()
    order = np.argsort(-p)
    ys, ps = y[order], p[order]
    prec = np.cumsum(ys) / np.arange(1, len(ys) + 1)

    def cut(mult, top):
        need = min(0.95, base * mult)
        k_min = int(0.01 * len(ys))
        good = np.where((prec >= need) & (np.arange(len(ys)) >= k_min))[0]
        k = good[-1] if len(good) else int(top * len(ys))
        return float(ps[min(k, len(ps) - 1)])
    must = cut(2.5, 0.08)
    watch = min(cut(1.5, 0.25), must)
    return round(watch, 4), round(must, 4), float(base)


def big_recall(rows, flag, lg):
    """Of the real big plays that followed a live state, how many had an alert (flag) on that game in
    the H seconds before them? Counted per big play via the rows' t_big (seconds to the next big)."""
    H = HORIZON_S[lg]
    hits: dict[tuple, bool] = {}
    for r, f in zip(rows, flag):
        tb = r["t_big"]
        if tb is None or not math.isfinite(tb) or tb > H:
            continue
        key = (r["gid"], round(r["t_big"] + r["facts"].get("events", 0) * 0))  # placeholder, replaced below
        key = (r["gid"], r["_big_id"])
        hits[key] = hits.get(key, False) or bool(f)
    return sum(hits.values()) / max(1, len(hits)), len(hits)


def tag_big_ids(rows):
    """Give each row the id of the big play it precedes (wall time of that play, rounded)."""
    for r in rows:
        tb = r["t_big"]
        r["_big_id"] = None if tb is None or not math.isfinite(tb) else round(r["_wall"] + tb)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="+")
    a = ap.parse_args()
    folds_dir = ROOT / "data/training/reasoner_folds"
    folds_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for lg in a.leagues:
        rows = load_rows(lg)
        feats = brief.features_for(lg)
        X = matrix(rows, feats)
        y = np.array([r["y"] for r in rows], np.float32)
        k = K.get(lg, 3)
        nights = sorted({r["night"] for r in rows})
        fold = np.array([fold_of(r["night"], k) for r in rows])
        oof = np.full(len(rows), np.nan)
        best = []
        for f in range(k):
            te = fold == f
            va_f = (f + 1) % k
            va = fold == va_f
            tr = ~te & ~va
            bst, it = train(X[tr], y[tr], X[va], y[va], feats)
            # refit on train+valid with the found rounds, so each fold model sees all but its own night
            bst, it = train(X[~te], y[~te], None, None, feats, rounds=it)
            best.append(it)
            oof[te] = bst.predict(X[te], num_iteration=it)
            save_head(folds_dir / f"{lg}_f{f}.npz", bst, feats,
                      {"league": lg, "fold": f, "k": k, "nights": sorted({r["night"] for r, t in zip(rows, te) if t})},
                      it)
        ok = np.array([not r["garbage"] for r in rows])
        watch, must, base = pick_cuts(y, oof, ok)
        rounds = int(np.mean(best))
        bst, _ = train(X, y, None, None, feats, rounds=rounds)
        info = {"league": lg, "watch": watch, "must_see": must, "base_rate": round(base, 4), "rounds": rounds,
                "horizon_s": HORIZON_S[lg], "rows": len(rows), "nights": len(nights),
                "target": "scorecard big play or crunch play in the next horizon_s, not garbage time"}
        save_head(MODEL_DIR / f"{lg}.npz", bst, feats, info, rounds)
        # write fold cut points into each fold model too (same cuts: picked on OOF)
        for f in range(k):
            p = folds_dir / f"{lg}_f{f}.npz"
            trees, finfo = B.load(p)
            finfo.update(watch=watch, must_see=must)
            with np.load(p, allow_pickle=False) as z:
                flat = {kk: z[kk] for kk in z.files if kk != "meta"}
                meta = json.loads(bytes(z["meta"]).decode())
            meta["info"] = finfo
            flat["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
            np.savez_compressed(p, **flat)

        # ---- lift vs the brain alone (OOF, non-garbage rows) ----
        bk = np.array([r["facts"].get("brain_key") if r["facts"].get("brain_key") is not None else np.nan
                       for r in rows])
        has_b = ok & ~np.isnan(bk)
        rate_must = float((oof[ok] >= must).mean())
        # brain threshold at the same alert rate
        b_thr = float(np.quantile(bk[has_b], 1 - rate_must)) if has_b.any() else float("nan")
        crunch = np.array([r["crunch"] for r in rows])
        head_flag = ok & ((oof >= must) | crunch)
        brain_flag = ok & ((np.nan_to_num(bk, nan=-1) >= b_thr) | crunch)
        for r in rows:
            r["_wall"] = 0.0
        # wall per row isn't stored; the big play id is (gid, index of the next big) via t_big chains
        ids, last = [], {}
        for r in rows:
            ids.append(None)
        # recompute: within a game, rows are in order; a row's next big is the same as the next row's
        # unless the next row is itself after it. Group by decreasing t_big resets.
        cur = 0
        prev_gid, prev_tb = None, None
        bid = []
        for r in rows:
            tb = r["t_big"] if r["t_big"] is not None else math.inf
            if r["gid"] != prev_gid:
                cur += 1
            elif prev_tb is not None and tb > prev_tb + 1e-6:
                cur += 1       # t_big jumped up: the previous big play has happened
            bid.append(cur)
            prev_gid, prev_tb = r["gid"], tb
        H = HORIZON_S[lg]

        def recall(flag):
            hit, seen = {}, set()
            for r, f, b in zip(rows, flag, bid):
                tb = r["t_big"]
                if tb is None or not math.isfinite(tb) or r["garbage"]:
                    continue
                seen.add(b)
                if tb <= H:
                    hit[b] = hit.get(b, False) or bool(f)
            n = len(hit)
            return round(100 * sum(hit.values()) / max(1, n), 1), n

        def precision(flag):
            f = np.asarray(flag) & ok
            return round(100 * float(y[f].mean()), 1) if f.any() else float("nan")
        rep = {
            "rows": len(rows), "nights": len(nights), "base_rate_pct": round(100 * base, 1),
            "auc_head": round(auc(y[ok], oof[ok]), 3),
            "auc_brain_key": round(auc(y[has_b], bk[has_b]), 3) if has_b.any() else None,
            "must_see_rate_pct": round(100 * rate_must, 1),
            "must_see_precision_head_pct": precision(head_flag),
            "must_see_precision_brain_pct": precision(brain_flag),
            "big_preceded_by_must_see_head_pct": recall(head_flag)[0],
            "big_preceded_by_must_see_brain_pct": recall(brain_flag)[0],
            "big_plays": recall(head_flag)[1],
            "cuts": {"watch": watch, "must_see": must},
        }
        summary[lg] = rep
        out = {"report": rep, "oof": {f"{r['gid']}:{r['i']}": round(float(p), 4) for r, p in zip(rows, oof)}}
        (ROOT / f"data/training/reasoner_{lg}_oof.json").write_text(json.dumps(out))
        print(lg, json.dumps(rep))
    (ROOT / "data/training/reasoner_report.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
