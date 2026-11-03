"""Calibrate Brain v3's cautious restart bound (the decision core's break budget) per stoppage type.

The entry head's p10 is a fine 10% quantile overall, but a trip planned on it overruns into the
restart whenever the break ends earlier than p10, and some stoppages (a TV timeout late in a game,
a short score break) end early more often than the head's average. For each league and break type
(TV timeout, score break), and crunch or not, this finds the scale s <= 1 such that a break ends
before s * p10 no more than `alpha` of the time:

    safe = s[league][type][crunch] * p10

Fitted ONLY on nights the model never trained on, in time order: 5 forward-chaining folds (fit on
every earlier block, grade on the next) report the mean and the worst fold; the shipped scale is
fitted on all of those nights; nights after the training cutoff (our own archive) are graded once,
separately, and never used to fit.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from redzone.scoring import brain3 as B3
from redzone.scoring.brain import MODEL_DIR

CAL_TYPES = ("tv_timeout", "score_break")
ALPHA = 0.05            # a cautious budget overruns into the restart at most this often
MIN_ROWS = 40           # fewer calibration rows than this: the slice takes its type's (or 1.0)
FOLDS = 5
CAL_PATH = MODEL_DIR / "entry_cal_v3.json"
# brain3data.TYPES codes (kept here so the runtime-side helpers import without pandas)
CODE = {"tv_timeout": 1, "score_break": 6}


def _rows(table: str, league: str, nights_ok):
    """Break-start rows of the cal types with a known restart, scored by the shipped model."""
    import pandas as pd

    from redzone.lab.brain3data import TRAINING3_DIR, TYPES
    assert all(TYPES[c] == t for t, c in CODE.items())
    m = B3.load(MODEL_DIR / f"{league}.v3.npz")
    d = pd.read_parquet(TRAINING3_DIR / f"{table}.parquet")
    d = d[d.start_type.isin(list(CODE.values())) & d.entry_s.notna() & nights_ok(d)].copy()
    if d.empty:
        return d
    X = d.reindex(columns=m.features).to_numpy(dtype=float)
    # Through the live reading (sorted, clipped quantiles), one state at a time as the app does.
    q = np.array([B3.reading_from(m.predict(x), m, None).entry for x in X], dtype=float)
    d["p10"], d["p50"] = np.maximum(q[:, 0], 0.0), q[:, 1]
    d["type"] = d.start_type.map({v: k for k, v in CODE.items()})
    d["crunch"] = d.crunch.fillna(0).astype(float) > 0
    return d[["night", "game_id", "type", "crunch", "entry_s", "p10", "p50", "abs_diff"]]


def fit_scale(y: np.ndarray, p10: np.ndarray, alpha: float = ALPHA) -> float:
    """Largest s in (0, 1] with mean(y < s * p10) <= alpha."""
    ok = p10 > 0
    if ok.sum() == 0:
        return 1.0
    r = np.sort(y[ok] / p10[ok])          # a break overruns s * p10 when y / p10 < s
    k = int(np.floor(alpha * len(r)))     # at most k ratios may fall below s
    s = r[k] if k < len(r) else 1.0
    return float(min(1.0, max(0.05, s)))


def fit(d, alpha: float = ALPHA) -> dict:
    """{type: {"all": s, "crunch": s, "early": s}}; a thin slice falls back to its type's scale."""
    out = {}
    for t in CAL_TYPES:
        dt = d[d.type == t]
        base = fit_scale(dt.entry_s.to_numpy(), dt.p10.to_numpy(), alpha) if len(dt) >= MIN_ROWS else 1.0
        sl = {"all": round(base, 3)}
        for name, mask in (("crunch", dt.crunch), ("early", ~dt.crunch)):
            ds = dt[mask]
            sl[name] = round(min(base, fit_scale(ds.entry_s.to_numpy(), ds.p10.to_numpy(), alpha))
                             if len(ds) >= MIN_ROWS else base, 3)
        out[t] = sl
    return out


def apply(d, scales: dict) -> np.ndarray:
    s = np.array([scales.get(t, {}).get("crunch" if c else "early", 1.0) for t, c in zip(d.type, d.crunch)])
    return s * d.p10.to_numpy()


def grade(d, safe: np.ndarray) -> dict:
    y = d.entry_s.to_numpy()
    out = {"n": int(len(d)), "overrun": round(float((y < safe).mean()), 4) if len(d) else None,
           "overrun_p10": round(float((y < d.p10.to_numpy()).mean()), 4) if len(d) else None,
           "budget_s": round(float(np.mean(safe)), 1) if len(d) else None,
           "budget_p10_s": round(float(d.p10.mean()), 1) if len(d) else None}
    return out


def slices(d, safe: np.ndarray) -> dict:
    out = {}
    for t in CAL_TYPES:
        for name, mask in (("crunch", d.crunch), ("early", ~d.crunch), ("close", d.abs_diff.fillna(99) <= 8),
                           ("blowout", d.abs_diff.fillna(0) > 16)):
            m = ((d.type == t) & mask).to_numpy()
            if m.sum():
                out[f"{t}/{name}"] = grade(d[m], safe[m])
    return out


def forward_cv(d, alpha: float = ALPHA, folds: int = FOLDS) -> dict:
    """Nights in time order cut into folds+1 blocks; fold k fits on blocks < k, grades block k."""
    nights = np.sort(d.night.unique())
    blocks = np.array_split(nights, folds + 1)
    res = []
    for k in range(1, folds + 1):
        tr = d[d.night.isin(np.concatenate(blocks[:k]))]
        te = d[d.night.isin(blocks[k])]
        sc = fit(tr, alpha)
        g = grade(te, apply(te, sc))
        g["nights"] = f"{blocks[k][0]}-{blocks[k][-1]}"
        for t in CAL_TYPES:
            m = (te.type == t).to_numpy()
            g[t] = grade(te[m], apply(te[m], sc)) if m.sum() else None
        res.append(g)
    ov = [r["overrun"] for r in res if r["overrun"] is not None]
    return {"folds": res, "overrun_mean": round(float(np.mean(ov)), 4), "overrun_worst": round(float(np.max(ov)), 4),
            "budget_mean_s": round(float(np.mean([r["budget_s"] for r in res])), 1)}


# Held-out nights per league: the public table's valid/test nights (the trees never fit them), and
# for NHL (whose entry head trained on the archive's train nights) the archive's other nights.
SOURCES = {"nfl": ("nfl", "espn_nfl"), "nba": ("nba", "espn_nba"), "nhl": ("nhl", "espn_nhl")}


def run(leagues=("nfl", "nba", "nhl"), alpha: float = ALPHA) -> dict:
    out = {"alpha": alpha, "scales": {}, "report": {}}
    for lg in leagues:
        pub, arch = SOURCES[lg]
        held = _rows(pub, lg, lambda d: d.split.isin(["valid", "test"]))
        fresh = _rows(arch, lg, (lambda d: d.split != "train") if lg == "nhl" else (lambda d: d.split.notna()))
        if lg == "nhl":
            # NHL's public feed has no wall clock: its entry head (and so this scale) lives on the archive.
            # Time-ordered: the earlier half of its held-out nights fits, the later half grades.
            nights = np.sort(fresh.night.unique())
            cut = nights[len(nights) // 2] if len(nights) else 0
            held, fresh = fresh[fresh.night < cut], fresh[fresh.night >= cut]
        cv = forward_cv(held, alpha) if held.night.nunique() > FOLDS else None
        sc = fit(held, alpha)
        rep = {"calibration_rows": int(len(held)), "calibration_nights": int(held.night.nunique()),
               "cv": cv, "fit_on_all_held_out": grade(held, apply(held, sc)),
               "after_cutoff": grade(fresh, apply(fresh, sc)) if len(fresh) else None,
               "after_cutoff_nights": int(fresh.night.nunique()),
               "after_cutoff_slices": slices(fresh, apply(fresh, sc)) if len(fresh) else None}
        out["scales"][lg] = sc
        out["report"][lg] = rep
    return out


def save(res: dict, path: Path = CAL_PATH) -> None:
    path.write_text(json.dumps({"alpha": res["alpha"], "types": list(CAL_TYPES), "scales": res["scales"]}, indent=1))
