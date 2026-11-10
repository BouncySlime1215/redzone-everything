#!/usr/bin/env python3
"""Train the PARK action-value model on Kaggle CPU from scripts/park_table.py tables. Never run on the Mac.

    python scripts/kaggle_park.py --tables /kaggle/input/park-tables/*.csv --out /kaggle/working/park_model

Input: one row per (decision second, legal action) with the features the live app could know and the label `q_value`, the
brain value the hindsight planner's screen showed over the next 180 s after taking that action under the measured load,
flip and position error (see park_table.py). The model learns Q(state, action); at run time the action with the highest
predicted value is "stay", "jump" or "park this game next".

`--kind landings` trains on the second table instead (park_table.py --landings: one row per legal landing of a decision,
labelled with the value it gains per second of screen time): the model scores each landing from what the feed had reported
(a visit length the feed could not yet know is hidden from it), the top-scored landing of each decision is the pick, and the
report is the gain lost against the best landing, beside two stand-ins (the shortest wait; the most reported value).

What it does: whole nights are split oldest to newest (the last `--test-frac` of each league's nights are the test nights,
never fitted on); the target is the action's value less the mean value of that decision's actions, so the model ranks the
actions of one decision and is not paid for knowing that a night was exciting; any action whose horizon held a Rule #1
break (`q_rule1` > 0) is given the worst target of its decision less `--rule1-penalty`, so the model is never rewarded
for a mid-play cut. LightGBM when it imports, else scikit-learn's histogram gradient boosting.

Reported on the test nights, beside three stand-ins on the same rows: the mean regret against the hindsight planner
(the value lost by the action picked, 0 = always the best action), the share of decisions within 0.25 points of the best,
and the share of the hindsight planner's value kept. Stand-ins: always stay; always jump when a stick is parked; the
action whose game has the most feed-reported value ahead (`a_val_known`, what the foresight brain uses).
Writes model.txt (LightGBM) or model.joblib, features.json and report.json to --out.

Why: the brain needs "which game to park next" and "jump now or wait" learned on what two sticks can really deliver, and
the 8 GB Mac must not train; Nick launches this on Kaggle.
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

LABELS = ("q_value", "q_must", "q_jumps", "q_rule1", "q_regret")
KEYS = ("league", "games", "night", "t")
ACTIONS = ("stay", "jump", "park")


def load(paths: list[str]):
    import pandas as pd
    frames = [pd.read_csv(p) for pat in paths for p in sorted(glob.glob(pat))]
    if not frames:
        raise SystemExit("no tables found")
    df = pd.concat(frames, ignore_index=True)
    for a in ACTIONS:
        df[f"act_{a}"] = (df["action"] == a).astype(float)
    df["decision"] = df[list(KEYS)].astype(str).agg("|".join, axis=1)
    return df


def split(df, test_frac: float):
    """Whole nights, per league: the newest `test_frac` are the test nights."""
    test = set()
    for lg, part in df.groupby("league"):
        nights = sorted(part["night"].unique())
        k = max(1, int(round(len(nights) * test_frac)))
        test |= {(lg, n) for n in nights[-k:]}
    is_test = [(lg, n) in test for lg, n in zip(df["league"], df["night"])]
    return df[[not x for x in is_test]], df[is_test]


def target(df, penalty: float):
    g = df.groupby("decision")["q_value"]
    y = df["q_value"] - g.transform("mean")
    bad = df["q_rule1"] > 0
    y[bad] = (g.transform("min") - g.transform("mean"))[bad] - penalty
    return y


def fit(x, y):
    try:
        import lightgbm as lgb
        m = lgb.LGBMRegressor(n_estimators=600, learning_rate=0.03, num_leaves=31, min_child_samples=40, subsample=0.8,
                              subsample_freq=1, colsample_bytree=0.8, verbose=-1)
        m.fit(x, y)
        return m, "lightgbm"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        m = HistGradientBoostingRegressor(max_iter=600, learning_rate=0.03, max_leaf_nodes=31, min_samples_leaf=40)
        m.fit(x, y)
        return m, "sklearn_hgb"


def judge(df, score) -> dict:
    """Pick the top-scored action of every decision; compare with the hindsight planner's best."""
    d = df.assign(_s=score)
    pick = d.loc[d.groupby("decision")["_s"].idxmax()]
    best = d.groupby("decision")["q_value"].max()
    got = pick.set_index("decision")["q_value"]
    regret = (best - got)
    return {"decisions": int(len(best)), "mean_regret": round(float(regret.mean()), 4), "within_0.25": round(float((regret <= 0.25).mean()), 4),
            "value_kept": round(float(got.sum() / best.sum()), 4) if float(best.sum()) else None,
            "rule1_picked": int((pick["q_rule1"] > 0).sum())}


LANDING_FEATURES = ("held", "behind_s", "ready_s", "leave_wait_s", "wait_s", "dead_before_play_s", "value_known", "visit_known", "visit_seen_s",
                    "lev", "margin", "left_s", "s_lev", "s_margin", "s_left_s", "s_on_screen_s", "s_behind_s", "games_live", "n_landings")


def judge_landings(df, score) -> dict:
    d = df.assign(_s=score)
    pick = d.loc[d.groupby("decision")["_s"].idxmax()].set_index("decision")["gain_per_s"]
    best = d.groupby("decision")["gain_per_s"].max()
    return {"decisions": int(len(best)), "mean_gain_lost": round(float((best - pick).mean()), 5),
            "picked_best": round(float((best - pick <= 1e-9).mean()), 4), "mean_gain_picked": round(float(pick.mean()), 5),
            "mean_gain_best": round(float(best.mean()), 5)}


def main_landings(a) -> int:
    import pandas as pd
    df = pd.concat([pd.read_csv(p) for pat in a.tables for p in sorted(glob.glob(pat))], ignore_index=True)
    df["decision"] = df[list(KEYS)].astype(str).agg("|".join, axis=1)
    df["visit_seen_s"] = df["visit_s"].where(df["visit_known"] > 0, -1.0)     # a visit the feed has not finished reporting is not a feature
    feats = list(LANDING_FEATURES)
    train, test = split(df, a.test_frac)
    if train.empty or test.empty:
        raise SystemExit("need at least two nights per league: one to fit, one to test")
    model, kind = fit(train[feats], train["gain_per_s"])
    report = {"model": kind, "kind": "landings", "rows_train": int(len(train)), "rows_test": int(len(test)), "features": len(feats),
              "test": {"model": judge_landings(test, model.predict(test[feats])), "shortest_wait": judge_landings(test, -test["wait_s"]),
                       "most_reported_value": judge_landings(test, test["value_known"])}}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if kind == "lightgbm":
        model.booster_.save_model(str(out / "landings_model.txt"))
    else:
        import joblib
        joblib.dump(model, out / "landings_model.joblib")
    (out / "landings_features.json").write_text(json.dumps(feats, indent=1))
    (out / "landings_report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tables", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kind", choices=("actions", "landings"), default="actions")
    ap.add_argument("--test-frac", type=float, default=0.25)
    ap.add_argument("--rule1-penalty", type=float, default=5.0)
    a = ap.parse_args()
    if a.kind == "landings":
        return main_landings(a)
    df = load(a.tables)
    feats = [c for c in df.columns if c.startswith(("s_", "h_", "a_", "act_")) or c in ("on_screen_s", "games_live", "n_actions")]
    train, test = split(df, a.test_frac)
    if train.empty or test.empty:
        raise SystemExit("need at least two nights per league: one to fit, one to test")
    model, kind = fit(train[feats], target(train, a.rule1_penalty))
    report = {"model": kind, "rows_train": int(len(train)), "rows_test": int(len(test)), "features": len(feats),
              "test": {"model": judge(test, model.predict(test[feats])),
                       "always_stay": judge(test, test["act_stay"]),
                       "always_jump": judge(test, test["act_jump"]),
                       "most_reported_value": judge(test, test["a_val_known"])},
              "by_league": {lg: judge(part, model.predict(part[feats])) for lg, part in test.groupby("league")}}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if kind == "lightgbm":
        model.booster_.save_model(str(out / "model.txt"))
    else:
        import joblib
        joblib.dump(model, out / "model.joblib")
    (out / "features.json").write_text(json.dumps(feats, indent=1))
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
