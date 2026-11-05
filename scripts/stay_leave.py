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
        F, _hl = SL.context(n, tab)
        yield day, n, tab, F, SL.floor_table(n, F.shape[1])      # the no-leave floor: high leverage OR crunch


def load(leagues, split, cache: Path | None):
    """[(league, day, n, tab, F, HL)], tables cached per split (they take most of the time)."""
    p = cache / f"nights_{split}.pkl" if cache else None
    if p and p.exists():
        out = pickle.loads(p.read_bytes())
        return [(lg, d, n, tab, F, SL.floor_table(n, F.shape[1])) for lg, d, n, tab, F, _HL in out]
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


def load_extra(leagues, root: Path, before: dict, cache: Path | None):
    """[EXP-128] Older nights from another pinned archive (eval-data-v2 last season), TRAIN only, before `before`."""
    import json as _j

    from redzone.lab.backtest import Replay
    p = cache / "nights_extra.pkl" if cache else None
    if p and p.exists():
        out = pickle.loads(p.read_bytes())
        return [(lg, d, n, tab, F, SL.floor_table(n, F.shape[1])) for lg, d, n, tab, F, _HL in out]
    out = []
    for lg in leagues:
        cut = before.get(lg.value)
        if not cut:
            continue
        arch = root / "data/archive" / lg.value
        for sb in sorted(arch.glob("scoreboard_*.json")):
            day = sb.stem.split("_")[-1]
            if day >= cut:
                continue
            reps = [r for e in _j.loads(sb.read_text()).get("events", [])
                    if (f := arch / f"summary_{e['id']}.json").exists() and (r := Replay.load(lg, f))]
            if len(reps) < 2:
                continue
            t0, t1 = min(r.wall[0] for r in reps) - 600.0, max(r.wall[-1] for r in reps) + 600.0
            n = O.Night(reps, SL.MINE, STREAM_DELAY_S + BUFFER_S, TUNE_S, t0, t1)
            tab = O.tables(n, np.arange(n.t0, n.t1, O.STEP_S))
            F, _hl = SL.context(n, tab)
            out.append((lg.value, day, n, tab, F, SL.floor_table(n, F.shape[1])))
        print(f"extra {lg.value}: {sum(1 for o in out if o[0] == lg.value)} nights", flush=True)
    if p:
        p.write_bytes(pickle.dumps(out))
    return out


def xy(data, target: str = "reward"):
    Xs, ys = [], []
    for _lg, _d, _n, tab, F, _HL in data:
        lab = {"reward": SL.reward, "nomine": SL.reward_nomine, "crunch": lambda t: t["crunch"]}[target](tab)
        X, y = SL.rows(F, SL.label(lab))
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
    ap.add_argument("cmd", choices=("fit", "grade", "fit1714", "grade1714", "grade125", "exp126", "exp128"))
    ap.add_argument("--extra-root", type=Path, default=None, help="exp128: root holding data/archive of eval-data-v2")
    ap.add_argument("--leagues", default="nba,nfl,nhl")
    ap.add_argument("--split", default="valid", choices=("valid", "test", "train"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache", type=Path, default=None)
    ap.add_argument("--feed-lag", default="8", help="grade125: seconds, or p90 = noggin.pick.FEED_LAG_P90 per league")
    a = ap.parse_args(argv)
    from redzone.models import League
    leagues = [League(x) for x in a.leagues.split(",")]
    a.out.mkdir(parents=True, exist_ok=True)
    sp = league_splits(leagues)
    if a.cmd == "fit1714":
        tr, va = load(leagues, "train", a.cache), load(leagues, "valid", a.cache)
        heads, info = {}, {"exp": "EXP-1714", "features": SL.CONTEXT_FEATURES, "hp": SL.HP, "manifest": SL.manifest(sp)}
        for tgt in ("nomine", "crunch"):
            Xt, yt = xy(tr, tgt)
            Xv, yv = xy(va, tgt)
            sel = {mi: float(np.mean((SL.fit(Xt, yt, mi).predict(Xv) - yv) ** 2)) for mi in SL.MAX_ITER_GRID}
            best = min(sel, key=sel.get)
            heads[tgt] = {"all": SL.fit(Xt, yt, best), "no_state": SL.fit(Xt, yt, best, drop=("state",))}
            info[tgt] = {"valid_mse": sel, "max_iter": best}
            print(tgt, sel, best, flush=True)
        blob = {k: SL.TwoHead(heads["nomine"][k], heads["crunch"][k]) for k in ("all", "no_state")}
        (a.out / "model1714.pkl").write_bytes(pickle.dumps(blob))
        (a.out / "fit1714.json").write_text(json.dumps(info, indent=1, default=float))
        return 0
    if a.cmd == "grade1714":
        blob = pickle.loads((a.out / "model1714.pkl").read_bytes())
        arms = {k: {} for k in ("full", "full_floored", "A0_past120", "B1_two_heads", "B2_no_state")}
        for lg, day, n, tab, F, HL in load(leagues, a.split, a.cache):
            sch = {"full": O.solve(n, "combined", tab), "full_floored": O.solve(n, "combined", SL.floored(tab, HL)),
                   "A0_past120": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, None, horizon_s=120.0),
                   "B1_two_heads": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, blob["all"]),
                   "B2_no_state": SL.solve_policy(n, FEED_LAG_S, tab, F, HL, blob["no_state"])}
            for k, sw in sch.items():
                arms[k].setdefault(lg, []).append({"night": day, **SL.night_metrics(n, tab, HL, sw)})
            print(f"  {lg} {day}", flush=True)
        out = {}
        for ceil in ("full_floored", "full"):
            sub = {"full": arms[ceil], **{k: v for k, v in arms.items() if k not in ("full", "full_floored")}}
            sub["A1_learned"] = sub["B1_two_heads"]          # summarize() deltas: B1 vs A0
            out[ceil] = summarize(sub)
        (a.out / f"grade1714_{a.split}.json").write_text(json.dumps({"summary": out, "nights": arms}, indent=1,
                                                                    default=float))
        print(json.dumps(out, indent=1, default=float))
        return 0
    if a.cmd == "exp128":
        from redzone.noggin import pick as PK
        tr, va = load(leagues, "train", a.cache), load(leagues, "valid", a.cache)
        ex = load_extra(leagues, a.extra_root, {"nba": "20261020", "nhl": "20261019"}, a.cache)
        heads, sel_all = {}, {}
        for tgt in ("nomine", "crunch"):
            Xa, ya = xy(tr, tgt)
            Xe, ye = xy(ex, tgt)
            Xt, yt = np.concatenate([Xa, Xe]), np.concatenate([ya, ye])
            w = np.concatenate([np.ones(len(ya)), np.full(len(ye), 0.5)])
            Xv, yv = xy(va, tgt)
            sel = {mi: float(np.mean((SL.fit(Xt, yt, mi, weight=w).predict(Xv) - yv) ** 2)) for mi in SL.MAX_ITER_GRID}
            best = min(sel, key=sel.get)
            heads[tgt], sel_all[tgt] = SL.fit(Xt, yt, best, weight=w), sel
            print(tgt, "rows", len(ya), "+", len(ye), sel, best, flush=True)
        n1 = SL.TwoHead(heads["nomine"], heads["crunch"])
        b1 = pickle.loads((a.out / "model1714.pkl").read_bytes())["all"]
        (a.out / "model128.pkl").write_bytes(pickle.dumps(n1))
        res = {}
        for fl_mode in ("p90", "8"):
            arms = {k: {} for k in ("full", "A0_past120", "B1_two_heads", "N1_last_season")}
            for lg, day, n, tab, F, HL in va:
                fl = PK.FEED_LAG_P90.get(League(lg), 60.0) if fl_mode == "p90" else 8.0
                sch = {"full": O.solve(n, "combined", SL.floored(tab, HL)),
                       "A0_past120": SL.solve_policy(n, fl, tab, F, HL, None, horizon_s=120.0),
                       "B1_two_heads": SL.solve_policy(n, fl, tab, F, HL, b1),
                       "N1_last_season": SL.solve_policy(n, fl, tab, F, HL, n1)}
                for k, sw in sch.items():
                    arms[k].setdefault(lg, []).append({"night": day, **SL.night_metrics(n, tab, HL, sw)})
            res[fl_mode] = {"summary": summarize(arms), "nights": arms}
        res["extra_nights"] = sorted({f"{lg}:{d}" for lg, d, *_ in ex})
        res["max_iter"] = sel_all
        (a.out / "exp128_valid.json").write_text(json.dumps(res, indent=1, default=float))
        print("done", flush=True)
        return 0
    if a.cmd == "exp126":
        from redzone.noggin import pick as PK
        tr, va = load(leagues, "train", a.cache), load(leagues, "valid", a.cache)

        def rel(data):
            Xs, ys = [], []
            for _lg, _d, _n, tab, F, _HL in data:
                Y = SL.label(SL.reward_nomine(tab))
                X, y = SL.rows_relative(F, Y)
                Xs.append(X)
                ys.append(y)
            return np.concatenate(Xs), np.concatenate(ys)
        Xt, yt = rel(tr)
        Xv, yv = rel(va)
        sel = {mi: float(np.mean((SL.fit(Xt, yt, mi).predict(Xv) - yv) ** 2)) for mi in SL.MAX_ITER_GRID}
        best = min(sel, key=sel.get)
        b1 = pickle.loads((a.out / "model1714.pkl").read_bytes())["all"]
        r1 = SL.TwoHead(SL.fit(Xt, yt, best), b1.crunch)
        (a.out / "model126.pkl").write_bytes(pickle.dumps(r1))
        print("max_iter", sel, best, flush=True)
        arms = {k: {} for k in ("full", "A0_past120", "B1_two_heads", "R1_ranker")}
        for lg, day, n, tab, F, HL in va if a.split == "valid" else load(leagues, a.split, a.cache):
            fl = PK.FEED_LAG_P90.get(League(lg), 60.0)
            sch = {"full": O.solve(n, "combined", SL.floored(tab, HL)),
                   "A0_past120": SL.solve_policy(n, fl, tab, F, HL, None, horizon_s=120.0),
                   "B1_two_heads": SL.solve_policy(n, fl, tab, F, HL, b1),
                   "R1_ranker": SL.solve_policy(n, fl, tab, F, HL, r1)}
            for k, sw in sch.items():
                arms[k].setdefault(lg, []).append({"night": day, **SL.night_metrics(n, tab, HL, sw)})
            print(f"  {lg} {day}", flush=True)
        summ = summarize(arms)
        (a.out / f"exp126_{a.split}.json").write_text(json.dumps({"summary": summ, "nights": arms, "max_iter": sel},
                                                                 indent=1, default=float))
        print(json.dumps(summ["pooled"], indent=1, default=float))
        return 0
    if a.cmd == "grade125":
        blob = pickle.loads((a.out / "model1714.pkl").read_bytes())
        from redzone.noggin import pick as PK
        arms = {k: {} for k in ("full", "A0_past120", "B1_m0", *[f"C_m{m:g}" for m in SL.MARGIN_GRID])}
        for lg, day, n, tab, F, HL in load(leagues, a.split, a.cache):
            fl = PK.FEED_LAG_P90.get(League(lg), 60.0) if a.feed_lag == "p90" else float(a.feed_lag)
            sch = {"full": O.solve(n, "combined", SL.floored(tab, HL)),
                   "A0_past120": SL.solve_policy(n, fl, tab, F, HL, None, horizon_s=120.0),
                   "B1_m0": SL.solve_policy(n, fl, tab, F, HL, blob["all"])}
            for m in SL.MARGIN_GRID:
                sch[f"C_m{m:g}"] = SL.solve_policy(n, fl, tab, F, HL, blob["all"], margin=m)
            for k, sw in sch.items():
                arms[k].setdefault(lg, []).append({"night": day, **SL.night_metrics(n, tab, HL, sw)})
            print(f"  {lg} {day}", flush=True)
        summ = summarize(arms)
        (a.out / f"grade125_{a.split}_fl{a.feed_lag}.json").write_text(json.dumps({"summary": summ, "nights": arms}, indent=1,
                                                                   default=float))
        print(json.dumps(summ, indent=1, default=float))
        return 0
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
        base_mse = float(np.mean((Xv[:, SL.CONTEXT_FEATURES.index("past_mean_10")] * SL.HZ - yv) ** 2))
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
        info = {"exp": "EXP-1712/1713", "features": SL.CONTEXT_FEATURES, "hp": SL.HP, "max_iter_select": sel,
                "max_iter": best, "valid_mse_past_mean_baseline": base_mse, "margin_select": msel, "margin": margin,
                "rows": {"train": int(len(yt)), "valid": int(len(yv))}, "buffer_s": BUFFER_S,
                "feed_lag_s": FEED_LAG_S, "tune_s": TUNE_S, "horizon_s": SL.LABEL_HORIZON,
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
    (a.out / f"grade_{a.split}.json").write_text(json.dumps({"summary": summ, "nights": arms}, indent=1, default=float))
    print(json.dumps(summ, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
