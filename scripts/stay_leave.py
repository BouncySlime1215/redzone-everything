#!/usr/bin/env python3
"""[EXP-1712 / EXP-1713] Fit and grade the game-context STAY/LEAVE model (docs/prereg/exp-1712.md).

    python scripts/stay_leave.py fit   --out /mnt/project-files/lane1/stay_leave      # train nights; picks on valid
    python scripts/stay_leave.py grade --split valid --out /mnt/project-files/lane1/stay_leave
    python scripts/stay_leave.py grade --split test  --out ...   # LOCKED: the integrator runs this once

Arms: A0 v1 past-mean beyond value (oracle.beyond_value, 120 s and 300 s horizons), A1 learned (EXP-1712),
A2 learned + hysteresis (EXP-1713); the full-hindsight oracle is the 100 %, and the windowed oracle without
the high-leverage floor is reported beside them. Buffer B = 60 s (screen lag STREAM_DELAY_S + 60), feed lag
8 s, tune 5 s, the sweep's defaults. Writes <out>/model.pkl, <out>/fit.json, <out>/grade_<split>.json.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.lab import oracle as O  # noqa: E402
from redzone.lab import splits  # noqa: E402
from redzone.lab import stay_leave as SL  # noqa: E402
from redzone.lab.backtest import STREAM_DELAY_S  # noqa: E402

BUFFER_S, FEED_LAG_S, TUNE_S = 60.0, 8.0, 5.0


def league_splits(leagues) -> dict:
    return {lg.value: splits.league_split(lg.value, splits.archive_nights(ROOT / "data/archive" / lg.value))
            for lg in leagues}


def nights(league, split: str):
    import eval_sports as ES
    for day, reps, _t in ES.nights(league, 10_000, None, split):
        t0 = min(r.wall[0] for r in reps) - 600.0
        t1 = max(r.wall[-1] for r in reps) + 600.0
        n = O.Night(reps, SL.MINE, STREAM_DELAY_S + BUFFER_S, TUNE_S, t0, t1)
        tab = O.tables(n, np.arange(n.t0, n.t1, O.STEP_S))
        F, HL = SL.context(n, tab)
        yield day, n, tab, F, HL


def load(leagues, split, cache: Path | None):
    """[(league, day, n, tab, F, HL)], tables cached per split (they take most of the time)."""
    p = cache / f"nights_{split}.pkl" if cache else None
    if p and p.exists():
        return pickle.loads(p.read_bytes())
    out = []
    for lg in leagues:
        t = time.time()
        for day, n, tab, F, HL in nights(lg, split):
            out.append((lg.value, day, n, tab, F, HL))
        print(f"{lg.value} {split}: {sum(1 for o in out if o[0] == lg.value)} nights, {time.time() - t:.0f} s",
              flush=True)
    if p:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pickle.dumps(out))
    return out


def xy(data):
    Xs, ys = [], []
    for _lg, _d, _n, tab, F, _HL in data:
        X, y = SL.rows(F, SL.label(SL.reward(tab)))
        Xs.append(X)
        ys.append(y)
    return np.concatenate(Xs), np.concatenate(ys)


def run_arms(data, model, margin: float) -> dict:
    """{arm: {league: [night rows]}}"""
    arms = {"full": {}, "A0_past120": {}, "A0_past300": {}, "A1_learned": {}, "A2_hysteresis": {},
            "window_nofloor": {}}
    for lg, day, n, tab, F, HL in data:
        t = time.time()
        sch = {
            "full": O.solve(n, "combined", tab),
            "A0_past120": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, None, horizon_s=120.0),
            "A0_past300": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, None, horizon_s=300.0),
            "A1_learned": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, model),
            "A2_hysteresis": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, model, margin=margin),
            "window_nofloor": O.solve_window(n, FEED_LAG_S, "combined", tab),
        }
        for a, s in sch.items():
            arms[a].setdefault(lg, []).append({"night": day, **SL.night_metrics(n, tab, HL, s)})
        print(f"  {lg} {day}: {len(n.replays)} games, {time.time() - t:.0f} s", flush=True)
    return arms


def summarize(arms: dict) -> dict:
    out = {}
    lgs = sorted({lg for a in arms.values() for lg in a})
    for scope in lgs + ["pooled"]:
        pick = (lambda a: arms[a].get(scope, [])) if scope != "pooled" else (lambda a: [r for v in arms[a].values() for r in v])
        full = pick("full")
        s = {}
        for a in arms:
            rs = pick(a)
            base = pick("A1_learned") if a == "A2_hysteresis" else pick("A0_past120")
            ci = SL.pct_ci(rs, full, None if a in ("full", "A0_past120") else base)
            tot = {k: sum(r[k] for r in rs) for k in ("hl_leaves", "entries_mid_play", "mid_play_cuts", "switches",
                                                       "crunch_exits", "crunch_exits_ok", "big", "big_live",
                                                       "crunch", "crunch_live", "dead_s", "watched_s")}
            tot["crunch_exit_acc_pct"] = round(100 * tot["crunch_exits_ok"] / tot["crunch_exits"], 1) if tot["crunch_exits"] else None
            tot["switches_per_h"] = round(float(np.mean([r["switches_per_h"] for r in rs])), 2) if rs else None
            tot["dead_air_pct"] = round(100 * tot["dead_s"] / max(1, tot["watched_s"]), 1)
            tot["big_live_pct"] = round(100 * tot["big_live"] / max(1, tot["big"]), 1)
            tot["crunch_live_pct"] = round(100 * tot["crunch_live"] / max(1, tot["crunch"]), 1)
            s[a] = {**ci, **tot}
        s["status"] = "PENDING (<8 nights)" if len(full) < 8 else "graded"
        out[scope] = s
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("fit", "grade"))
    ap.add_argument("--leagues", default="nba,nfl,nhl")
    ap.add_argument("--split", default="valid", choices=("valid", "test", "train"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=None)
    a = ap.parse_args(argv)
    from redzone.models import League
    leagues = [League(x) for x in a.leagues.split(",")]
    a.out.mkdir(parents=True, exist_ok=True)
    sp = league_splits(leagues)
    if a.cmd == "fit":
        tr, va = load(leagues, "train", a.cache), load(leagues, "valid", a.cache)
        Xt, yt = xy(tr)
        Xv, yv = xy(va)
        print(f"rows train {len(yt)} valid {len(yv)}", flush=True)
        sel = {}
        for mi in SL.MAX_ITER_GRID:
            m = SL.fit(Xt, yt, mi)
            sel[mi] = {"train_mse": float(np.mean((m.predict(Xt) - yt) ** 2)),
                       "valid_mse": float(np.mean((m.predict(Xv) - yv) ** 2))}
            print(mi, sel[mi], flush=True)
        best = min(sel, key=lambda k: sel[k]["valid_mse"])
        model = SL.fit(Xt, yt, best)
        base_mse = float(np.mean((Xv[:, SL.FEATURES.index("past_mean_10")] * SL.HZ - yv) ** 2))
        # hysteresis margin, on VALID nights only (EXP-1713): the best % of oracle, ties to the larger band
        msel = {}
        for mg in SL.MARGIN_GRID:
            vals = [sum(SL.night_metrics(n, tab, HL, SL.solve_policy(n, FEED_LAG_S, tab, F, HL, model, margin=mg))["value"]
                        for _lg, _d, n, tab, F, HL in va)]
            msel[mg] = float(sum(vals))
            print("margin", mg, msel[mg], flush=True)
        top = max(msel.values())
        margin = max(m for m, v in msel.items() if v >= top - 1e-9)
        # ablations (report only): the same learner refit with each feature group removed
        abl = {g: SL.fit(Xt, yt, best, drop=(g,)) for g in ("state", "consequence", "history", "your_team")}
        (a.out / "model.pkl").write_bytes(pickle.dumps({"model": model, "margin": margin, "max_iter": best,
                                                        "ablations": abl}))
        info = {"exp": "EXP-1712/1713", "features": SL.FEATURES, "hp": SL.HP, "max_iter_select": sel,
                "max_iter": best, "valid_mse_past_mean_baseline": base_mse, "margin_select": msel, "margin": margin,
                "rows": {"train": int(len(yt)), "valid": int(len(yv))}, "buffer_s": BUFFER_S,
                "feed_lag_s": FEED_LAG_S, "tune_s": TUNE_S, "horizon_s": SL.HORIZON_S,
                "manifest": SL.manifest(sp)}
        (a.out / "fit.json").write_text(json.dumps(info, indent=1))
        print(json.dumps({k: info[k] for k in ("max_iter", "margin", "valid_mse_past_mean_baseline")}, indent=1))
        return 0
    blob = pickle.loads((a.out / "model.pkl").read_bytes())
    data = load(leagues, a.split, a.cache)
    arms = run_arms(data, blob["model"], blob["margin"])
    summ = summarize(arms)
    # ablations (report only, valid nights): A1 with the model refit without each feature group
    if a.split == "valid":
        full = [r for v in arms["full"].values() for r in v]
        base = [r for v in arms["A1_learned"].values() for r in v]
        for g, m in (blob.get("ablations") or {}).items():
            rs = [{"night": d, **SL.night_metrics(n, tab, HL, SL.solve_policy(n, FEED_LAG_S, tab, F, HL, m))}
                  for _lg, d, n, tab, F, HL in data]
            summ.setdefault("ablation_pooled", {})[f"drop_{g}"] = SL.pct_ci(rs, full, base)
    (a.out / f"grade_{a.split}.json").write_text(json.dumps({"summary": summ, "nights": arms}, indent=1))
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
