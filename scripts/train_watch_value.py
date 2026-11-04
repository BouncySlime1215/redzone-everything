#!/usr/bin/env python3
"""HEAT 2.0: train the learned watch value per league (redzone/noggin/watch.py, contract redzone/contracts/watch.py).

    python scripts/train_watch_value.py nba nfl nhl --archive data/archive --config data/eval_config.yaml

Self-taught labels (no Nick signal): for each play, what happened next on the archived night:
  wv  = min(1, max |WP(t') - WP(t)| over t' in (t, t + H] / SWING_FULL)   the swing at stake, realized
  pb  = 1 if a single play within H moved WP by >= BIG_SWING                 a big play happened
WP is the brain track's home win probability (ESPN's when given, else the analytic model). The swing
measures leverage directly, so a one-score Q3 game counts and a blowout does not, whatever the scorecard's
clock-defined "big play" says (the V7 NBA lesson: those labels are a clock rule).

Splits by whole night (redzone/lab/splits.py): train fits, valid early-stops, test (locked) is graded ONCE;
forward-chaining CV over train+valid is reported as mean and worst fold. A context-only baseline (clock +
margin) is graded beside the model: WV must beat it on rank agreement.

Output per league: redzone/noggin/models/watch_<league>.v1.npz and data/training/watch_value_report.json with
metrics.watch_value = {calibration_err, rank_agree_pct} per split (the North Star card shape, #156).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.contracts.watch import HORIZON_S, features_for  # noqa: E402
from redzone.noggin import watch as W  # noqa: E402

SWING_FULL = 0.25
TICK_S = 60.0
CTX = ("period", "clock_s", "abs_diff")
PARAMS = dict(learning_rate=0.05, num_leaves=31, min_data_in_leaf=40, feature_fraction=0.9, bagging_fraction=0.8,
              bagging_freq=1, lambda_l2=5.0, max_depth=7, verbose=-1, seed=7, num_threads=2)


def labels(rows: list[dict], horizon_s: float) -> tuple[np.ndarray, np.ndarray]:
    """rows of ONE game in play order -> (wv, pb) from the plays after each one."""
    w = np.array([r["wall"] if r.get("wall") is not None else np.nan for r in rows], np.float64)
    wp = np.array([r["wp_home"] if r.get("wp_home") is not None else np.nan for r in rows], np.float64)
    step = np.abs(np.diff(wp, prepend=wp[:1]))
    wv, pb = np.zeros(len(rows)), np.zeros(len(rows))
    j = 0
    for i in range(len(rows)):
        j = max(j, i + 1)
        while j < len(rows) and w[j] <= w[i] + horizon_s:
            j += 1
        if j > i + 1 and np.isfinite(wp[i]):
            fut = wp[i + 1:j]
            wv[i] = min(1.0, float(np.nanmax(np.abs(fut - wp[i]))) / SWING_FULL) if np.isfinite(fut).any() else 0.0
            pb[i] = float(np.nanmax(step[i + 1:j]) >= W.BIG_SWING) if np.isfinite(step[i + 1:j]).any() else 0.0
    return wv, pb


LATE_FRAC = {"nba": 0.125, "nfl": 0.10, "nhl": 0.12, "mlb": 0.12}   # ~6 min NBA, ~6 min NFL, ~7 min NHL
CRUNCH_SWING = 0.15
EXIT_TOL_S = 60.0


def crunch_labels(rows: list[dict], lg: str) -> np.ndarray:
    """[Nick 4:15 PM] crunch as a STATE from hindsight: late, and the rest of the game still moves the win
    probability by >= CRUNCH_SWING. It ENDS at the last moment that is still true (decided, clock gone, the key
    moment passed), so exit is learned, not a clock rule."""
    wp = np.array([r["wp_home"] if r.get("wp_home") is not None else np.nan for r in rows], np.float64)
    frac = np.array([W._num((r["facts"] or {}).get("frac_left"), 1.0) for r in rows])
    n = len(rows)
    hi = np.full(n, -np.inf)
    lo = np.full(n, np.inf)
    for i in range(n - 2, -1, -1):                 # running max / min of WP over (i, end]
        v = wp[i + 1]
        hi[i] = max(hi[i + 1], v) if np.isfinite(v) else hi[i + 1]
        lo[i] = min(lo[i + 1], v) if np.isfinite(v) else lo[i + 1]
    rest = np.where(np.isfinite(wp), np.maximum(hi - wp, wp - lo), 0.0)
    return ((frac <= LATE_FRAC.get(lg, 0.12)) & (rest >= CRUNCH_SWING)).astype(np.float32)


def hysteresis(p: np.ndarray, on: float = 0.6, off: float = 0.35) -> np.ndarray:
    state, out = False, np.zeros(len(p), bool)
    for i, v in enumerate(p):
        state = v >= on if not state else v > off
        out[i] = state
    return out


def crunch_card(rows: list[dict], p: np.ndarray, y: np.ndarray) -> dict:
    """Entry / exit accuracy vs the hindsight oracle, per game that ever enters crunch: the predicted state's
    first-on / last-on time within EXIT_TOL_S of the oracle's. Flicker = extra on->off flips beyond one."""
    by: dict = {}
    for k, r in enumerate(rows):
        by.setdefault(r["gid"], []).append(k)
    ent = ext = n = flick = 0
    for ks in by.values():
        yo = y[ks] > 0
        if not yo.any():
            continue
        n += 1
        st = hysteresis(p[ks])
        w = np.array([rows[k]["wall"] or 0.0 for k in ks])
        if st.any():
            ent += abs(w[st][0] - w[yo][0]) <= EXIT_TOL_S
            ext += abs(w[st][-1] - w[yo][-1]) <= EXIT_TOL_S
            flick += max(0, int(np.sum(st[1:] & ~st[:-1])) - 1)
    pct = lambda a: round(100.0 * a / n, 1) if n else None   # noqa: E731
    return {"games_in_crunch": n, "crunch_entry_acc_pct": pct(ent), "crunch_exit_acc_pct": pct(ext),
            "flicker_per_game": round(flick / n, 2) if n else None}


def _spans(on: np.ndarray, w: np.ndarray) -> list[list[float]]:
    """[t0, t1] of each run of True (unix seconds of its first and last row)."""
    out, start = [], None
    for k, v in enumerate(on):
        if v and start is None:
            start = k
        if start is not None and (not v or k == len(on) - 1):
            end = k if v else k - 1
            out.append([float(w[start]), float(w[end])])
            start = None
    return out


def crunch_spans(rows: list[dict], p: np.ndarray, y: np.ndarray) -> dict:
    """{night: {gid: {"learned": spans, "oracle": spans}}}: crunch_card's own rule (learned = the hysteresis
    on-state, oracle = y > 0), for lane 5's replay-viewer crunch timeline (#177)."""
    by: dict = {}
    for k, r in enumerate(rows):
        if r["wall"] is not None:
            by.setdefault((r["night"], r["gid"]), []).append(k)
    out: dict = {}
    for (night, gid), ks in by.items():
        w = np.array([rows[k]["wall"] for k in ks], float)
        learned, oracle = _spans(hysteresis(p[ks]), w), _spans(y[ks] > 0, w)
        if learned or oracle:
            out.setdefault(night, {})[str(gid)] = {"learned": learned, "oracle": oracle}
    return out


def write_crunch_files(spans: dict, out_dir: Path) -> int:
    """data/crunch/<night>.json = {"tol_s": EXIT_TOL_S, "games": {gid: {...}}}; leagues merge into one night file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for night, games in spans.items():
        f = out_dir / f"{night}.json"
        cur = json.loads(f.read_text()) if f.exists() else {}
        f.write_text(json.dumps({"tol_s": EXIT_TOL_S, "games": {**cur.get("games", {}), **games}}))
    return len(spans)


def matrix(rows, feats) -> np.ndarray:
    return np.array([[W._num((r["facts"] or {}).get(f)) for f in feats] for r in rows], np.float32)


def realized_scale(y_ref: np.ndarray):
    """The calibrated scale applied to the LABEL: realized WV of a moment on the train-night distribution."""
    pcts = np.linspace(0, 100, 101)
    qs = np.percentile(y_ref, pcts)
    return lambda y: np.array([W.calibrate(float(v), list(qs), list(pcts)) for v in y])


def calibration_err(pred_wv: np.ndarray, real_wv: np.ndarray, bins: int = 10) -> float:
    """Reliability: mean |predicted - realized| watch value per predicted decile, weighted, in 0..1 units."""
    if not len(pred_wv):
        return float("nan")
    edges = np.percentile(pred_wv, np.linspace(0, 100, bins + 1))
    idx = np.clip(np.searchsorted(edges, pred_wv, side="right") - 1, 0, bins - 1)
    err = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            err += m.mean() * abs(pred_wv[m].mean() - real_wv[m].mean())
    return round(err / 100.0, 4)


def rank_agree(rows: list[dict], pred: np.ndarray, y: np.ndarray) -> tuple[float, int]:
    """Top-1 agreement per tick: of the games live in the same minute of the same night (>= 2), does the highest
    predicted WV game equal the game with the most realized swing? (%, ticks)."""
    groups: dict = {}
    for k, r in enumerate(rows):
        if r.get("wall") is None:
            continue
        key = (r["night"], int(r["wall"] // TICK_S))
        g = groups.setdefault(key, {})
        g[r["gid"]] = k                        # the game's latest play in this minute
    hit = n = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        ks = list(g.values())
        yy = y[ks]
        if yy.max() <= 0 or (yy == yy.max()).sum() > 1:
            continue                            # nothing happened, or a tie: no single right answer
        n += 1
        hit += int(np.argmax(pred[ks]) == np.argmax(yy))
    return (round(100.0 * hit / n, 1) if n else float("nan")), n


def card(rows, pred_raw, y, cal, realize) -> dict:
    pw = np.array([W.calibrate(float(v), cal[0], cal[1]) for v in pred_raw])
    ra, ticks = rank_agree(rows, pred_raw, y)
    top = pw >= 90
    return {"watch_value": {"calibration_err": calibration_err(pw, realize(y)), "rank_agree_pct": ra},
            "ticks": ticks, "rows": len(rows), "share_90_plus_pct": round(100 * float(top.mean()), 1),
            "realized_swing_top5_vs_rest": round(float(y[top].mean() / max(1e-6, y[~top].mean())), 2) if top.any() else None}


def fit(X, y, Xv, yv, feats, objective):
    import lightgbm as lgb
    p = dict(PARAMS, objective=objective)
    d = lgb.Dataset(X, y, feature_name=feats)
    b = lgb.train(p, d, 800, valid_sets=[lgb.Dataset(Xv, yv, reference=d)],
                  callbacks=[lgb.early_stopping(40, verbose=False)])
    return b, max(20, b.best_iteration)


def league(lg: str, archive: Path, cfg, out_dir: Path, crunch_dir: Path = ROOT / "data" / "crunch") -> dict:
    from redzone.lab import reasoner_data as RD
    from redzone.lab import splits
    from redzone.models import League
    from redzone.scoring import brain as B
    t0 = time.time()
    rows = [r.as_dict() for r in RD.league_rows(League(lg), archive / lg, cfg, {"WSH", "UNC"}, every=1)]
    by_game: dict = {}
    for k, r in enumerate(rows):
        by_game.setdefault(r["gid"], []).append(k)
    wv, pb, cr = np.zeros(len(rows)), np.zeros(len(rows)), np.zeros(len(rows))
    for ks in by_game.values():
        a, b = labels([rows[k] for k in ks], HORIZON_S[lg])
        wv[ks], pb[ks] = a, b
        cr[ks] = crunch_labels([rows[k] for k in ks], lg)
    sp = splits.split_nights({r["night"] for r in rows})
    part = {n: s for s, ns in (("train", sp.train), ("valid", sp.valid), ("test", sp.test)) for n in ns}
    split = np.array([part[r["night"]] for r in rows])
    feats = features_for(League(lg).base.value)
    X = matrix(rows, feats)
    tr, va, te = split == "train", split == "valid", split == "test"
    m_wv, n_wv = fit(X[tr], wv[tr], X[va], wv[va], feats, "regression")
    m_pb, n_pb = fit(X[tr], pb[tr], X[va], pb[va], feats, "binary")
    m_cr, n_cr = fit(X[tr], cr[tr], X[va], cr[va], feats, "binary")
    pcts = list(np.linspace(0, 100, 101))
    raw_tr = m_wv.predict(X[tr], num_iteration=n_wv)
    cal = (list(np.percentile(raw_tr, pcts)), pcts)
    realize = realized_scale(wv[tr])
    ci = [feats.index(f) for f in CTX]
    m_cx, n_cx = fit(X[tr][:, ci], wv[tr], X[va][:, ci], wv[va], list(CTX), "regression")
    cal_cx = (list(np.percentile(m_cx.predict(X[tr][:, ci], num_iteration=n_cx), pcts)), pcts)
    rep = {"league": lg, "rows": {s: int((split == s).sum()) for s in ("train", "valid", "test")},
           "nights": {s: len(getattr(sp, s)) for s in ("train", "valid", "test")}, "features": len(feats),
           "trees": {"wv": n_wv, "pb": n_pb, "cr": n_cr}, "crunch_label_rate": round(float(cr.mean()), 4)}
    for name, m in (("dev", va), ("locked", te)):            # locked: graded once, never tuned on
        sub = [rows[k] for k in np.where(m)[0]]
        rep[name] = card(sub, m_wv.predict(X[m], num_iteration=n_wv), wv[m], cal, realize)
        rep[name + "_context_only"] = card(sub, m_cx.predict(X[m][:, ci], num_iteration=n_cx), wv[m], cal_cx, realize)
        rep[name]["crunch"] = crunch_card(sub, m_cr.predict(X[m], num_iteration=n_cr), cr[m])
        rep[name]["crunch"]["night_files"] = write_crunch_files(
            crunch_spans(sub, m_cr.predict(X[m], num_iteration=n_cr), cr[m]), crunch_dir)
        # baseline: today's rule crunch flag (the engine's crunch_time), with the same hysteresis
        rep[name + "_context_only"]["crunch"] = crunch_card(sub, X[m][:, feats.index("crunch")].astype(float), cr[m])
    # forward-chaining CV over train + valid nights (mean and worst fold)
    folds = []
    for fit_n, chk_n in splits.folds(sp.train + sp.valid, 3):
        a, b = np.isin([r["night"] for r in rows], fit_n), np.isin([r["night"] for r in rows], chk_n)
        if a.sum() < 200 or b.sum() < 50:
            continue
        mf, nf = fit(X[a], wv[a], X[b], wv[b], feats, "regression")
        cf = (list(np.percentile(mf.predict(X[a], num_iteration=nf), pcts)), pcts)
        folds.append(card([rows[k] for k in np.where(b)[0]], mf.predict(X[b], num_iteration=nf), wv[b], cf,
                          realized_scale(wv[a]))["watch_value"])
    if folds:
        ra = [f["rank_agree_pct"] for f in folds if f["rank_agree_pct"] == f["rank_agree_pct"]]
        ce = [f["calibration_err"] for f in folds]
        rep["forward_cv"] = {"folds": len(folds), "rank_agree_mean": round(float(np.mean(ra)), 1) if ra else None,
                             "rank_agree_worst": min(ra) if ra else None, "calibration_err_mean": round(float(np.mean(ce)), 4),
                             "calibration_err_worst": max(ce)}
    out_dir.mkdir(parents=True, exist_ok=True)
    a_wv, meta_wv = B.trees_from_lightgbm(m_wv.dump_model(num_iteration=n_wv), feats)
    a_pb, meta_pb = B.trees_from_lightgbm(m_pb.dump_model(num_iteration=n_pb), feats)
    a_cr, meta_cr = B.trees_from_lightgbm(m_cr.dump_model(num_iteration=n_cr), feats)
    meta_wv["objective"] = "regression"
    path = out_dir / f"watch_{lg}.v1.npz"
    B.save(path, {"wv": (a_wv, meta_wv), "pb": (a_pb, meta_pb), "cr": (a_cr, meta_cr)},
           {"cal_q": [round(float(q), 6) for q in cal[0]], "cal_pct": [float(p) for p in cal[1]],
            "horizon_s": HORIZON_S[lg], "swing_full": SWING_FULL, "big_swing": W.BIG_SWING, "version": "v1",
            "train_nights": [sp.train[0], sp.train[-1]] if sp.train else [], "rows_train": int(tr.sum())})
    rep["model_kb"] = round(path.stat().st_size / 1024, 1)
    rep["seconds"] = round(time.time() - t0)
    return rep


def main(argv=None) -> int:
    from redzone.config import Config
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="+")
    ap.add_argument("--archive", type=Path, default=ROOT / "data" / "archive")
    ap.add_argument("--config", type=Path, default=ROOT / "data" / "eval_config.yaml")
    ap.add_argument("--out", type=Path, default=W.MODEL_DIR)
    ap.add_argument("--crunch-dir", type=Path, default=ROOT / "data" / "crunch",
                    help="per-night crunch spans for the replay viewer (lane 5 #177)")
    ap.add_argument("--report", type=Path, default=ROOT / "data" / "training" / "watch_value_report.json")
    a = ap.parse_args(argv)
    # the brain TRACK (win probability, swings, runs) is what WV reads: rows need it on. Its learned outputs
    # (brain_key / brain_break / brain_hot) are NOT features: those models saw the graded seasons.
    import tempfile
    text = (a.config.read_text() if a.config.exists() else "") + "\nbrain:\n  enabled: true\n"
    tmp = Path(tempfile.mkdtemp()) / "watch_value_config.yaml"
    tmp.write_text(text)
    cfg = Config(user_path=tmp)
    rep = {lg: league(lg, a.archive, cfg, a.out, a.crunch_dir) for lg in a.leagues}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
