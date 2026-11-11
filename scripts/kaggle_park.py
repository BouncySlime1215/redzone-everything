#!/usr/bin/env python3
"""Train the PARK models on Kaggle CPU from the two scripts/park_table.py tables. Never run on the Mac.

    python scripts/kaggle_park.py --kind unknown  --tables /kaggle/input/park-tables/park_table_landings*.csv --out /kaggle/working/park
    python scripts/kaggle_park.py --kind landings --tables /kaggle/input/park-tables/park_table_landings*.csv --out /kaggle/working/park
    python scripts/kaggle_park.py --kind actions  --tables /kaggle/input/park-tables/park_table_actions*.csv  --out /kaggle/working/park

It needs: the repo (it imports redzone.scoring.brain3.flatten and redzone.models_rt.forest for the tree file), pandas,
numpy, lightgbm, and the table CSVs named above. Both tables are made from the PLANNER's decisions (park_plan.py).

--kind unknown   reads the landings table. One row per (night, decision second, game): the seconds of a visit the feed had
                 NOT reported at the decision (`unk_s`) and what they turned out to be worth (`unk_value`). Target: value
                 per unreported second, weighted by `unk_s`. Features: park_table.UNKNOWN_FEATURES (the game at the last
                 second the feed has reported). Writes park_unknown.npz: the file park_table.ModelBrain.load reads and
                 `park_plan.py --model` uses in place of unk_w x leverage. Reported beside two stand-ins on the test nights:
                 the planner's own rule (0.02 x leverage) and the training mean.
--kind landings  reads the landings table. The model scores each landing the planner looked at from what the feed had
                 reported; the top-scored one of each decision is the pick. Target: `gain_per_s` (hindsight). Reported: the
                 gain lost against the best landing, beside the planner's own pick (`picked`), its own gain estimate
                 (`plan_gain`) and the shortest wait. Writes park_landings.npz.
--kind actions   reads the actions table. Q(state, action): the target is the action's `q_value` less the mean of that
                 decision's actions; an action whose horizon held a Rule #1 break gets the worst target of its decision less
                 `--rule1-penalty`. Reported: mean regret against the best action, beside the planner's own action
                 (`planner_pick`), always stay, always jump. Writes park_actions.npz.
Every kind also writes <kind>_features.json and <kind>_report.json to --out.

Whole nights are split oldest to newest on `night_id`, the calendar day of the games: the newest `--test-frac` of the
recorded nights are the test nights, never fitted on, under every slate they appear in (the same night sits under NBA 2,
NBA 4, MNF+3 ...). A tree file is written only when a numpy walk of it gives the booster's own numbers on the test rows.

Why: the planner's weak spot is the seconds the feed has not reported yet, and what a landing is worth is exactly what a
model can learn from nights already played; the 8 GB Mac must not train, so the coordinator launches this on Kaggle.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEYS = ("league", "games", "night", "night_id", "error", "t")
ACTIONS = ("stay", "jump", "park")
ROUTES = ("hold", "roll", "skip", "tune")
UNKNOWN_FEATURES = ("k_lev", "k_margin", "k_left_s", "k_dead", "k_since", "k_val60", "k_live60")      # = park_table.UNKNOWN_FEATURES
LANDING_FEATURES = ("held", "tentative", "wait_s", "behind_s", "dead_before_play_s", "known_s", "val_known", "live_known", *UNKNOWN_FEATURES,
                    "s_lev", "s_margin", "s_left_s", "s_on_screen_s", "s_behind_s", "games_live", "n_landings", *(f"route_{r}" for r in ROUTES))
UNKNOWN_MIN_S = 10          # a visit with fewer unreported seconds than this says too little about them
PLANNER_UNK_W = 0.02        # park_plan.Plan.unk_w: the rule the unknown model has to beat


def load(paths: list[str]):
    import pandas as pd
    frames = [pd.read_csv(p) for pat in paths for p in sorted(glob.glob(pat))]
    if not frames:
        raise SystemExit("no tables found")
    df = pd.concat(frames, ignore_index=True)
    for col in ("error", "night_id"):
        if col not in df.columns:
            df[col] = ""
    df["decision"] = df[list(KEYS)].astype(str).agg("|".join, axis=1)
    return df


def split(df, test_frac: float):
    """Whole recorded nights: the newest `test_frac` of the `night_id`s are the test nights, under every slate."""
    if "night_id" not in df.columns:
        raise SystemExit("the tables have no night_id column: regenerate them with scripts/park_table.py")
    nights = sorted(df["night_id"].astype(str).unique())
    k = max(1, int(round(len(nights) * test_frac)))
    is_test = df["night_id"].astype(str).isin(set(nights[-k:]))
    train, held = df[~is_test], df[is_test]
    if train.empty or held.empty:
        raise SystemExit("need at least two recorded nights: one to fit, one to test")
    return train, held


def nights_of(df) -> list[str]:
    return sorted(df["night_id"].astype(str).unique())


def fit(x, y, weight=None):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.03, num_leaves=15, max_depth=6, min_child_samples=40, subsample=0.8,
                          subsample_freq=1, colsample_bytree=0.8, verbose=-1)
    m.fit(x, y, sample_weight=weight)
    return m


def export(model, features: list[str], kind: str, check_x, path: Path) -> dict:
    """Write the booster as the tree file park_table.ModelBrain reads, after checking a numpy walk of it against the booster."""
    import numpy as np
    from redzone.models_rt.forest import Nodes
    from redzone.scoring.brain3 import flatten
    booster = model.booster_
    arrays, info = flatten({"h": [dict(t, slot=0) for t in booster.dump_model()["tree_info"]]}, list(features))
    arrays.pop("slot", None)
    nodes = Nodes(arrays, info["depth"])
    x = np.asarray(check_x, dtype=np.float32)
    walked = nodes.value[nodes.leaves_batch(x)].sum(axis=1)
    worst = float(np.abs(walked - booster.predict(x)).max()) if len(x) else 0.0
    if worst > 1e-5:
        raise SystemExit(f"{kind}: the tree file differs from the booster by {worst}: nothing written")
    meta = {"features": list(features), "depth": int(info["depth"]), "init": 0.0, "kind": kind, "trees": int(len(arrays["roots"]))}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), **arrays)
    return {"file": path.name, "trees": meta["trees"], "depth": meta["depth"], "max_abs_diff_vs_booster": worst}


# ---- unknown: what a second the feed has not reported is worth ------------------------------------------------------------

def unknown_rows(df):
    d = df[df["unk_s"] >= UNKNOWN_MIN_S].copy()
    d = d.sort_values("unk_s").drop_duplicates(["league", "night", "error", "t", "game"], keep="last")
    d["unk_rate"] = d["unk_value"] / d["unk_s"]
    return d


def judge_unknown(d, pred) -> dict:
    w = d["unk_s"]
    err = (d["unk_rate"] - pred).abs()
    return {"rows": int(len(d)), "weighted_mae": round(float((err * w).sum() / w.sum()), 5),
            "weighted_bias": round(float(((pred - d["unk_rate"]) * w).sum() / w.sum()), 5)}


def main_unknown(a) -> int:
    df = unknown_rows(load(a.tables))
    feats = list(UNKNOWN_FEATURES)
    train, test = split(df, a.test_frac)
    model = fit(train[feats], train["unk_rate"], train["unk_s"])
    mean = float((train["unk_rate"] * train["unk_s"]).sum() / train["unk_s"].sum())
    report = {"kind": "unknown", "rows_train": int(len(train)), "rows_test": int(len(test)), "features": feats, "test_nights": nights_of(test),
              "test": {"model": judge_unknown(test, model.predict(test[feats]).clip(min=0.0)),
                       "planner_rule_0.02_x_leverage": judge_unknown(test, PLANNER_UNK_W * test["k_lev"]),
                       "training_mean": judge_unknown(test, mean + 0.0 * test["k_lev"])}}
    out = Path(a.out)
    report["export"] = export(model, feats, "unknown", test[feats], out / "park_unknown.npz")
    return finish(out, "unknown", feats, report)


# ---- landings: which landing to have ready ---------------------------------------------------------------------------------

def judge_landings(df, score) -> dict:
    d = df.assign(_s=score)
    pick = d.loc[d.groupby("decision")["_s"].idxmax()].set_index("decision")["gain_per_s"]
    best = d.groupby("decision")["gain_per_s"].max()
    return {"decisions": int(len(best)), "mean_gain_lost": round(float((best - pick).mean()), 5),
            "picked_best": round(float((best - pick <= 1e-9).mean()), 4), "mean_gain_picked": round(float(pick.mean()), 5),
            "mean_gain_best": round(float(best.mean()), 5)}


def main_landings(a) -> int:
    df = load(a.tables)
    for r in ROUTES:
        df[f"route_{r}"] = (df["route"] == r).astype(float)
    feats = list(LANDING_FEATURES)
    train, test = split(df, a.test_frac)
    model = fit(train[feats], train["gain_per_s"])
    report = {"kind": "landings", "rows_train": int(len(train)), "rows_test": int(len(test)), "features": feats, "test_nights": nights_of(test),
              "test": {"model": judge_landings(test, model.predict(test[feats])), "planner_pick": judge_landings(test, test["picked"]),
                       "planner_gain": judge_landings(test, test["plan_gain"]), "shortest_wait": judge_landings(test, -test["wait_s"])}}
    out = Path(a.out)
    report["export"] = export(model, feats, "landings", test[feats], out / "park_landings.npz")
    return finish(out, "landings", feats, report)


# ---- actions: stay, jump or park this game next ---------------------------------------------------------------------------

def target(df, penalty: float):
    g = df.groupby("decision")["q_value"]
    y = df["q_value"] - g.transform("mean")
    bad = df["q_rule1"] > 0
    y[bad] = (g.transform("min") - g.transform("mean"))[bad] - penalty
    return y


def judge(df, score) -> dict:
    """Pick the top-scored action of every decision; compare with the best action of that decision."""
    d = df.assign(_s=score)
    pick = d.loc[d.groupby("decision")["_s"].idxmax()]
    best = d.groupby("decision")["q_value"].max()
    got = pick.set_index("decision")["q_value"]
    regret = (best - got)
    return {"decisions": int(len(best)), "mean_regret": round(float(regret.mean()), 4), "within_0.25": round(float((regret <= 0.25).mean()), 4),
            "value_kept": round(float(got.sum() / best.sum()), 4) if float(best.sum()) else None,
            "rule1_picked": int((pick["q_rule1"] > 0).sum()), "same_as_planner": round(float(pick["planner_pick"].mean()), 4)}


def main_actions(a) -> int:
    df = load(a.tables)
    for act in ACTIONS:
        df[f"act_{act}"] = (df["action"] == act).astype(float)
    feats = [c for c in df.columns if c.startswith(("s_", "h_", "a_", "act_")) or c in ("on_screen_s", "games_live", "n_actions")]
    train, test = split(df, a.test_frac)
    model = fit(train[feats], target(train, a.rule1_penalty))
    report = {"kind": "actions", "rows_train": int(len(train)), "rows_test": int(len(test)), "features": feats, "test_nights": nights_of(test),
              "test": {"model": judge(test, model.predict(test[feats])), "planner": judge(test, test["planner_pick"]),
                       "always_stay": judge(test, test["act_stay"]), "always_jump": judge(test, test["act_jump"])},
              "by_slate": {lg: judge(part, model.predict(part[feats])) for lg, part in test.groupby("league")}}
    out = Path(a.out)
    report["export"] = export(model, feats, "actions", test[feats], out / "park_actions.npz")
    return finish(out, "actions", feats, report)


def finish(out: Path, kind: str, feats: list[str], report: dict) -> int:
    (out / f"{kind}_features.json").write_text(json.dumps(feats, indent=1))
    (out / f"{kind}_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tables", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kind", choices=("unknown", "landings", "actions"), default="unknown")
    ap.add_argument("--test-frac", type=float, default=0.25)
    ap.add_argument("--rule1-penalty", type=float, default=5.0)
    a = ap.parse_args()
    return {"unknown": main_unknown, "landings": main_landings, "actions": main_actions}[a.kind](a)


if __name__ == "__main__":
    raise SystemExit(main())
