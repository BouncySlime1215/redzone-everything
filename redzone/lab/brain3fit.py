"""Train and grade Brain v3 (``scripts/train_brain3.py`` is the CLI). Training only: needs lightgbm.

Per league, every head of ``redzone.scoring.brain3`` trains on the public table's TRAIN nights,
early-stops on VALID nights, gets isotonic calibration fit on VALID nights, and is graded on TEST
nights (``redzone.lab.brain3data.night_splits``, the time split of ``redzone.lab.splits``). The head-to-head with today's rules and today's
brain (v2) runs through the live code path on our own ESPN archive (``live_eval3``), which neither
brain trained on (NHL's stoppage-length and entry heads are the exception: the public NHL feed has no
wall clock, so those two train on the archive's train nights and are graded on its other nights).

Why: first added in afd3508e (2026-11-03) as "Brain v3 (WIP): one compact model per league, numpy runtime, calibrated stoppage odds/type/length..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from redzone.lab import brain3data as D
from redzone.lab.brainfit import auc, binary_report, calib
from redzone.scoring import brain3 as B3

PARAMS = dict(learning_rate=0.1, num_leaves=15, max_depth=5, min_data_in_leaf=300, feature_fraction=0.9,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=5.0, max_bin=63, num_threads=4, verbose=-1, seed=7)
ROUNDS = {"binary": 160, "multiclass": 50, "quantile": 90, "regression": 120}
MAX_ROWS = 600_000
MIN_TYPE_SHARE = 0.01
ISO_POINTS = 48


def _pinball(y, q, a):
    d = y - q
    return float(np.mean(np.maximum(a * d, (a - 1) * d)))


def _iso(p_valid, y_valid) -> list[list[float]] | None:
    from sklearn.isotonic import IsotonicRegression
    if len(np.unique(y_valid)) < 2:
        return None
    ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p_valid, y_valid)
    xs = np.unique(np.quantile(p_valid, np.linspace(0, 1, ISO_POINTS)))
    ys = ir.predict(xs)
    return [[round(float(x), 6) for x in xs], [round(float(y), 6) for y in ys]]


def _apply_iso(p, iso):
    return p if not iso else np.interp(p, iso[0], iso[1])


def _sample(df, n, rng):
    return df if len(df) <= n else df.iloc[np.sort(rng.choice(len(df), n, replace=False))]


def train_league(league: str, root: Path | None = None, out_dir: Path | None = None, log=print,
                 plots: Path | None = None) -> dict:
    import lightgbm as lgb

    root = root or D.TRAINING3_DIR
    feats = B3.features_for(league)
    pub = pd.read_parquet(root / f"{league}.parquet")
    espn_p = root / f"espn_{league}.parquet"
    espn = pd.read_parquet(espn_p) if espn_p.exists() else None
    rng = np.random.default_rng(7)
    if league == "nhl":
        # The live feed only sometimes knows the skaters: hide them in 30% of training games (as v2).
        hide = set(g for g in pub.game_id.unique() if rng.random() < 0.3)
        pub.loc[pub.game_id.isin(hide), ["home_skaters", "away_skaters"]] = np.nan
    X = lambda d: d[feats].to_numpy(np.float32)  # noqa: E731
    heads: dict[str, list[dict]] = {}
    meta_heads: dict[str, dict] = {}
    report: dict = {"league": league, "features": feats, "nights": {k: int(pub[pub.split == k].night.nunique())
                                                                    for k in ("train", "valid", "test")},
                    "games": {k: int(pub[pub.split == k].game_id.nunique()) for k in ("train", "valid", "test")}}
    slot = 0
    test_preds: dict[str, tuple] = {}

    def fit(name, kind, data, label, alpha=None, num_class=None, src="public"):
        nonlocal slot
        d = data[data[label].notna()]
        tr, va, te = d[d.split == "train"], d[d.split == "valid"], d[d.split == "test"]
        tr = _sample(tr, MAX_ROWS, rng)
        params = dict(PARAMS)
        if kind == "binary":
            params["objective"] = "binary"
        elif kind == "multiclass":
            params.update(objective="multiclass", num_class=num_class)
        elif kind == "quantile":
            params.update(objective="quantile", alpha=alpha, min_data_in_leaf=100 if src == "espn" else 300)
        else:
            params["objective"] = "regression"
        t0 = time.time()
        dtr = lgb.Dataset(X(tr), tr[label].to_numpy(np.float32), feature_name=feats, free_raw_data=True)
        dva = lgb.Dataset(X(va), va[label].to_numpy(np.float32), reference=dtr)
        bst = lgb.train(params, dtr, ROUNDS[kind], valid_sets=[dva], callbacks=[lgb.early_stopping(15, verbose=False)])
        dump = bst.dump_model(num_iteration=bst.best_iteration)
        trees = dump["tree_info"]
        k = num_class or 1
        slots = list(range(slot, slot + k))
        for i, t in enumerate(trees):
            t["slot"] = slots[i % k]
        heads[name] = trees
        h = {"kind": kind, "slots": slots, "trees": len(trees), "train_rows": int(len(tr)), "src": src}
        p_va = bst.predict(X(va), num_iteration=bst.best_iteration)
        p_te = bst.predict(X(te), num_iteration=bst.best_iteration)
        if kind == "binary":
            h["iso"] = _iso(p_va, va[label].to_numpy())
            h["rate"] = round(float(tr[label].mean()), 4)
            p_te = _apply_iso(p_te, h["iso"])
        meta_heads[name] = h
        slot += k
        test_preds[name] = (te, p_te)
        log(f"  {league} {name:8s} {kind:10s} trees={len(trees):4d} rows={len(tr):7d} {time.time() - t0:5.1f}s")
        return te, p_te

    # stoppage horizons, big plays
    for name in ("stop15", "stop30", "stop60", "big60", "big180"):
        fit(name, "binary", pub, name)
    # type given a stoppage within 60 s
    d = pub[pub.stop_type.fillna(0) > 0].copy()
    share = d.stop_type.value_counts(normalize=True)
    keep = sorted(int(c) for c, s in share.items() if s >= MIN_TYPE_SHARE)
    other = D.TYPE_CODE["other"]
    if other not in keep:
        keep.append(other)
    keep = sorted(keep)
    remap = {c: (keep.index(c) if c in keep else keep.index(other)) for c in range(len(D.TYPES))}
    d["type_lbl"] = d.stop_type.astype(int).map(remap)
    type_names = [D.TYPES[c] for c in keep]
    fit("type", "multiclass", d, "type_lbl", num_class=len(keep))
    # lengths and entry: wall-clock leagues from the public table, NHL from our archive
    lsrc, ldata = ("espn", espn) if league == "nhl" else ("public", pub)
    for q, a in (("10", 0.1), ("50", 0.5), ("90", 0.9)):
        fit(f"len{q}", "quantile", ldata, "len_s", alpha=a, src=lsrc)
        fit(f"entry{q}", "quantile", ldata, "entry_s", alpha=a, src=lsrc)
    fit("hot", "regression", pub, "hotness")
    # which types are commercial-length (median >= 90 s), for long_p
    if league == "nhl":   # lengths from the archive's train nights only (see fit(src="espn"))
        ldata = ldata[ldata.split == "train"]
    type_len = _type_lengths(ldata)
    type_q = {t: [_type_lengths(ldata, 0.1).get(t, v), v, _type_lengths(ldata, 0.9).get(t, v)]
              for t, v in type_len.items()}
    long_types = _long_types(type_len)
    arrays, meta = B3.flatten(heads, feats)
    meta.update(nslots=slot, heads=meta_heads,
                info={"league": league, "trained": time.strftime("%Y-%m-%d"), "split": "night",
                      "type_names": type_names, "long_types": long_types, "type_len": type_len, "type_q": type_q,
                      "clock": "game" if league == "nhl" else "wall", "nights": report["nights"]})
    out = (out_dir or B3_MODEL_DIR()) / f"{league}.v3.npz"
    B3.save(out, arrays, meta)
    report["file_kb"] = round(out.stat().st_size / 1024, 1)
    report["trees"] = int(len(arrays["roots"]))
    report["nodes"] = int(len(arrays["feat"]))
    report["type_names"] = type_names
    report["long_types"] = long_types
    report["type_len"] = type_len
    # ---- test-night report
    forest = B3.load(out)
    report["test"] = _grade(league, test_preds, type_names, forest, plots)
    report["numpy_check"] = _numpy_check(forest, test_preds, feats)
    return report


def _type_lengths(ldata, q: float = 0.5) -> dict[str, float]:
    """A quantile (default the median) of each stoppage type's dead time (s), from the train nights'
    stoppage starts."""
    d = ldata[(ldata.split == "train") & ldata.len_s.notna() & (ldata.start_type.fillna(0) > 0)]
    out = {}
    for c, v in d.groupby(d.start_type.astype(int)).len_s:
        if len(v) >= 30:
            out[D.TYPES[int(c)]] = round(float(v.quantile(q)), 1)
    return out


def _long_types(lengths: dict[str, float]) -> list[str]:
    """Types whose median dead time is commercial length (>= 90 s)."""
    return sorted(t for t, v in lengths.items() if v >= 90) or ["tv_timeout", "period_end"]


def _nhl_heldout(nights) -> dict[str, bool]:
    """night -> True when NHL's length / entry heads did NOT train on it (a valid or locked test night)."""
    return {n: s != "train" for n, s in D.night_splits("nhl", nights).items()}


def B3_MODEL_DIR() -> Path:
    from redzone.scoring import brain
    return brain.MODEL_DIR


def _grade(league, preds, type_names, forest, plots) -> dict:
    rep = {}
    for name in ("stop15", "stop30", "stop60", "big60", "big180"):
        te, p = preds[name]
        rep[name] = binary_report(te[name].to_numpy(), p)
    te, p = preds["type"]
    y = te.type_lbl.to_numpy().astype(int)
    rep["type"] = {"n": int(len(y)), "accuracy": round(float((p.argmax(1) == y).mean()), 4),
                   "logloss": round(float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1)))), 4),
                   "base_logloss": round(float(-np.mean(np.log(np.clip(np.bincount(y, minlength=p.shape[1])[y] / len(y), 1e-9, 1)))), 4),
                   "share": {n: round(float((y == i).mean()), 4) for i, n in enumerate(type_names)}}
    for what in ("len", "entry"):
        te, p50 = preds[f"{what}50"]
        _, p10 = preds[f"{what}10"]
        _, p90 = preds[f"{what}90"]
        y = te[f"{what}_s" if what == "len" else "entry_s"].to_numpy()
        med = float(np.median(y))
        rep[what] = {"n": int(len(y)), "mae_p50": round(float(np.mean(np.abs(p50 - y))), 1),
                     "mae_const_median": round(float(np.mean(np.abs(med - y))), 1),
                     "pinball10": round(_pinball(y, p10, 0.1), 2), "pinball90": round(_pinball(y, p90, 0.9), 2),
                     "below_p10": round(float((y < p10).mean()), 3), "above_p90": round(float((y > p90).mean()), 3)}
    te, p = preds["hot"]
    from redzone.lab.brainfit import spearman
    rep["hot"] = {"spearman": round(spearman(te.hotness.to_numpy(), p), 4)}
    if plots is not None:
        _plot(league, preds, plots)
    return rep


def _numpy_check(forest, preds, feats) -> dict:
    """The numpy forest must reproduce LightGBM (+ isotonic): max abs difference on 200 test rows."""
    te, p = preds["stop60"]
    xs = te[feats].to_numpy(np.float32)[:200]
    mine = np.array([forest.predict(x)["stop60"] for x in xs])
    t0 = time.perf_counter()
    for x in xs:
        forest.predict(x)
    ms = (time.perf_counter() - t0) / max(1, len(xs)) * 1000
    return {"stop60_max_diff": float(np.max(np.abs(mine - p[:len(xs)]))) if len(xs) else 0.0,
            "predict_ms": round(ms, 3)}


def _plot(league, preds, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 5, figsize=(17, 3.6))
    for ax, name in zip(axes, ("stop15", "stop30", "stop60", "big60", "big180")):
        te, p = preds[name]
        _, table = calib(te[name].to_numpy(), p, bins=10)
        if table:
            t = np.array(table)
            ax.plot([0, 1], [0, 1], color="#999", lw=1, ls="--")
            ax.plot(t[:, 0], t[:, 1], marker="o", color="#2a6fdb", lw=1.5)
        ax.set_title(f"{league} {name}  AUC {auc(te[name].to_numpy(), p):.3f}", fontsize=9)
        ax.set_xlim(0, 1), ax.set_ylim(0, 1)
        ax.set_xlabel("predicted", fontsize=8)
    axes[0].set_ylabel("observed (test nights)", fontsize=8)
    fig.tight_layout()
    fig.savefig(out / f"calibration_{league}.png", dpi=110)
    plt.close(fig)


# ----------------------------------------------------------------------------- live head-to-head

def live_eval3(league: str, root: Path | None = None, archive: Path | None = None, cfg=None,
               max_games: int | None = None) -> dict:
    """Replay every archived ESPN game one play at a time through the live code path
    (espn.parse_summary -> rules / brain v2 / brain v3) and grade all three on the same rows, with
    the v3 labels of the espn_<league> table (same plays, same order). Neither brain trained on these
    games (NHL's length and entry heads did train on the archive's train nights: graded separately)."""
    from redzone.models import League, Phase
    from redzone.scoring import brain as B, breaks
    from redzone.sources import espn

    root = root or D.TRAINING3_DIR
    lg = League(league)
    archive = archive or (D.datasets.ROOT / "data" / "archive" / league)
    lab = pd.read_parquet(root / f"espn_{league}.parquet")
    gids = list(dict.fromkeys(lab.game_id))[: max_games or None]
    lab = lab.set_index(["game_id", "seq"]).sort_index()
    rows = []
    t3 = t2 = 0.0
    n3 = 0
    for gid in gids:
        path = archive / f"summary_{gid}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        plays = espn.flatten_plays(lg, data)
        b2 = B.Brain(model_dir=B.MODEL_DIR, enabled=True, version=2)
        b3 = B.Brain(model_dir=B.MODEL_DIR, enabled=True, version=3)
        book = breaks.BreakBook(clock=lambda: 0.0)
        for i in range(len(plays)):
            g = espn.parse_summary(lg, data, upto=i, plays=plays)
            if g is None or g.phase != Phase.LIVE:
                continue
            try:
                y = lab.loc[(gid, i)]
            except KeyError:
                continue
            book.observe(g)
            f = breaks.forecast(g, book.clocks(g.game_id, g.period))
            s = time.perf_counter()
            r2 = b2.read(g)
            t2 += time.perf_counter() - s
            s = time.perf_counter()
            r3 = b3.read(g)
            t3 += time.perf_counter() - s
            n3 += 1
            v = r3.v3
            if v is None or r2.break_soon_p is None:
                continue
            rows.append(dict(split=y.split, night=y.night, stop60=y.stop60, stop30=y.stop30, stop15=y.stop15, big180=y.big180,
                             big60=y.big60, len_s=y.len_s, entry_s=y.entry_s, break_soon=y.break_soon,
                             rules_p=f.p, rules_len=f.expect_s if f.now else np.nan, rules_now=f.now,
                             v2_break=r2.break_soon_p, v2_key=r2.key_soon_p,
                             v3_s15=v.stop[15], v3_s30=v.stop[30], v3_s60=v.stop[60], v3_long=v.long_p,
                             v3_b60=v.big[60], v3_b180=v.big[180], v3_len50=v.length[1], v3_len10=v.length[0],
                             v3_len90=v.length[2], v3_e10=v.entry[0], v3_e50=v.entry[1], v3_e90=v.entry[2]))
    a = pd.DataFrame(rows)
    out = {"league": league, "games": len(gids), "rows": int(len(a)),
           "ms_per_state_v3": round(t3 / max(1, n3) * 1000, 3), "ms_per_state_v2": round(t2 / max(1, n3) * 1000, 3)}
    if a.empty:
        return out
    if league == "nhl":
        out["note"] = "length/entry heads trained on this archive's train nights: graded on its other nights only"
    def rep(y, p):  # noqa: E306
        m = y.notna() & p.notna()
        r = binary_report(y[m].to_numpy(), p[m].to_numpy())
        r.pop("calibration", None)
        return r
    inplay = a.stop60.notna()
    ai = a[inplay]
    out["stop60"] = {"rules": rep(ai.stop60, ai.rules_p), "v2_brain": rep(ai.stop60, ai.v2_break),
                     "v3": rep(ai.stop60, ai.v3_s60)}
    out["stop30"] = {"rules": rep(ai.stop30, ai.rules_p), "v2_brain": rep(ai.stop30, ai.v2_break),
                     "v3": rep(ai.stop30, ai.v3_s30)}
    out["stop15"] = {"v3": rep(ai.stop15, ai.v3_s15)}
    out["v2_label_break_soon"] = {"rules": rep(ai.break_soon, ai.rules_p), "v2_brain": rep(ai.break_soon, ai.v2_break),
                                  "v3_long": rep(ai.break_soon, ai.v3_long)}
    out["big180"] = {"v2_brain_key": rep(a.big180, a.v2_key), "v3": rep(a.big180, a.v3_b180)}
    out["big60"] = {"v2_brain_key": rep(a.big60, a.v2_key), "v3": rep(a.big60, a.v3_b60)}
    sel = a[a.len_s.notna()]
    if league == "nhl":
        sel = sel[sel.night.map(_nhl_heldout(sel.night.unique()))]
    if len(sel):
        rl = sel[sel.rules_len.notna()]
        out["length"] = {"n": int(len(sel)), "v3_mae_p50": round(float((sel.v3_len50 - sel.len_s).abs().mean()), 1),
                         "rules_LEN_mae": round(float((rl.rules_len - rl.len_s).abs().mean()), 1) if len(rl) else None,
                         "v3_mae_on_rules_rows": round(float((rl.v3_len50 - rl.len_s).abs().mean()), 1) if len(rl) else None,
                         "rules_rows": int(len(rl)),
                         "below_p10": round(float((sel.len_s < sel.v3_len10).mean()), 3),
                         "above_p90": round(float((sel.len_s > sel.v3_len90).mean()), 3)}
    se = a[a.entry_s.notna()]
    if league == "nhl":
        se = se[se.night.map(_nhl_heldout(se.night.unique()))]
    if len(se):
        out["entry"] = {"n": int(len(se)), "v3_mae_p50": round(float((se.v3_e50 - se.entry_s).abs().mean()), 1),
                        "const_median_mae": round(float((se.entry_s.median() - se.entry_s).abs().mean()), 1),
                        "below_p10": round(float((se.entry_s < se.v3_e10).mean()), 3),
                        "above_p90": round(float((se.entry_s > se.v3_e90).mean()), 3)}
    return out
