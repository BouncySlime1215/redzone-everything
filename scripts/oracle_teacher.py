#!/usr/bin/env python3
"""[time-shift buffer, T3] Fit, select and grade the windowed-hindsight oracle teacher (EXP-120..122).

    python scripts/oracle_teacher.py fit   --out OUT            # train nights -> model per H; pick H on valid
    python scripts/oracle_teacher.py grade --split test --model OUT/model.json --out OUT/locked.json
    python scripts/oracle_teacher.py smoke                      # 1 night per league, a few seconds

Arms (docs/plans/2026-11-05-exp120-oracle-teacher.md): P = solve_window (past-mean placeholder), L = learned
value, LC = learned + 15 s play-start backoff; ceilings full = solve, cue = solve_cue(15). Locked nights are only
read by `grade --split test`, never by `fit`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.grading import RESUME_LEAD  # noqa: E402
from redzone.lab import oracle as O  # noqa: E402
from redzone.lab import oracle_teacher as T  # noqa: E402
from redzone.lab import oracle_window as OW  # noqa: E402
from redzone.lab.backtest import STREAM_DELAY_S  # noqa: E402
from redzone.models import League  # noqa: E402

TUNE_S = 5.0
MINE = {"WSH", "UNC"}
LEAGUES = ("nba", "nfl", "nhl")
HORIZONS = (24, 48)
BACKOFF_S = 15.0


def feed_lag_p90(lg: League) -> float:
    from redzone.noggin import pick as PK
    return float(PK.FEED_LAG_P90.get(lg, 60.0))


def nights(lg: League, split: str, max_nights: int = 0):
    import eval_sports as ES
    for i, (day, reps, _t) in enumerate(ES.nights(lg, 10_000, None, split)):
        if max_nights and i >= max_nights:
            return
        yield day, reps


def night(reps, B: float) -> O.Night:
    t0 = min(r.wall[0] for r in reps) - 600.0
    t1 = max(r.wall[-1] for r in reps) + 600.0
    return O.Night(reps, MINE, STREAM_DELAY_S + B, TUNE_S, t0, t1)


def tab_of(n: O.Night) -> dict:
    return O.tables(n, np.arange(n.t0, n.t1, O.STEP_S))


def counts(n, sw) -> dict:
    c = O.counts(O.grade(n, sw))
    return {k: c[k] for k in ("big", "big_live", "crunch", "crunch_live", "my_crunch", "my_crunch_live",
                              "entries", "entries_mid_play", "mid_play_cuts", "dead_s", "watched_s", "switches")}


def run_night(n: O.Night, tab: dict, feed_lag: float, model: T.ValueModel | None, arms=("P", "L", "LC"),
              grade: bool = True) -> dict:
    vf = T.value_fn(model, n, tab) if model else None
    sch = {"full": O.solve(n, "combined", tab), "cue": O.solve_cue(n, "combined", tab, max_cue_s=BACKOFF_S)}
    if "P" in arms:
        sch["P"] = OW.solve_window(n, feed_lag, "combined", tab)
    if "L" in arms and vf:
        sch["L"] = OW.solve_window(n, feed_lag, "combined", tab, value_fn=vf)
    if "LC" in arms and vf:
        sch["LC"] = T.solve_window_cue(n, feed_lag, "combined", tab, value_fn=vf, backoff_s=BACKOFF_S)
    out = {}
    for k, sw in sch.items():
        out[k] = {"value": round(O.schedule_value(n, tab, "combined", sw), 4)}
        if grade:
            out[k].update(counts(n, sw))
    full = out["full"]["value"]
    for k in out:
        out[k]["pct_oracle"] = round(100 * out[k]["value"] / full, 2) if full > 0 else None
    return out


# ------------------------------------------------------------------------------------------------- fit
def cmd_fit(a) -> int:
    t_start = time.time()
    a.out.mkdir(parents=True, exist_ok=True)
    rows = {H: {} for H in HORIZONS}
    for lg in LEAGUES:
        for day, reps in nights(League(lg), "train", a.max_nights):
            n = night(reps, 75.0)
            tab = tab_of(n)
            for H in HORIZONS:
                X, Y = T.training_rows(n, tab, H)
                rows[H].setdefault(lg, ([], []))
                rows[H][lg][0].append(X)
                rows[H][lg][1].append(Y)
        print(f"fit rows {lg}: {time.time() - t_start:.0f} s", flush=True)
    models = {H: T.fit(rows[H], H, alpha=a.alpha) for H in HORIZONS}
    # pick H on VALID nights: mean % of oracle of L over B in {60, 90}, ESPN p90 feed lag, all leagues
    sel = {H: [] for H in HORIZONS}
    sel_p = []
    r2 = {H: {} for H in HORIZONS}
    valid_rows = {}
    for lg in LEAGUES:
        league = League(lg)
        for day, reps in nights(league, "valid", a.max_nights):
            for B in (60.0, 90.0):
                n = night(reps, B)
                tab = tab_of(n)
                res = run_night(n, tab, feed_lag_p90(league), None, arms=("P",), grade=False)
                sel_p.append(res["P"]["pct_oracle"])
                for H, m in models.items():
                    vf = T.value_fn(m, n, tab)
                    sw = OW.solve_window(n, feed_lag_p90(league), "combined", tab, value_fn=vf)
                    v = O.schedule_value(n, tab, "combined", sw)
                    sel[H].append(100 * v / res["full"]["value"] if res["full"]["value"] > 0 else None)
                if B == 60.0:
                    for H, m in models.items():
                        X, Y = T.training_rows(n, tab, H)
                        P = m.predict(league, X)
                        r2[H].setdefault(lg, [])
                        r2[H][lg].append((Y, P))
            valid_rows.setdefault(lg, 0)
            valid_rows[lg] += 1
        print(f"valid {lg}: {time.time() - t_start:.0f} s", flush=True)
    score = {H: float(np.mean([x for x in v if x is not None])) for H, v in sel.items()}
    best = max(score, key=score.get)
    r2_out = {}
    for H in HORIZONS:
        for lg, pairs in r2[H].items():
            Y = np.concatenate([p[0] for p in pairs])
            P = np.concatenate([p[1] for p in pairs])
            r2_out.setdefault(str(H), {})[lg] = [round(1 - float(((Y[:, c] - P[:, c]) ** 2).sum()) /
                                                       max(1e-9, float(((Y[:, c] - Y[:, c].mean()) ** 2).sum())), 3)
                                                 for c in range(len(T.COMPONENTS))]
    m = models[best]
    m.meta.update({"selected_on": "valid", "valid_pct_oracle_by_H": {str(k): round(v, 2) for k, v in score.items()},
                   "valid_pct_oracle_placeholder": round(float(np.mean([x for x in sel_p if x is not None])), 2),
                   "valid_r2_by_H": r2_out, "valid_nights": valid_rows, "alpha": a.alpha, "train_lag_B": 75.0})
    m.save(a.out / "model.json")
    sha = hashlib.sha256((a.out / "model.json").read_bytes()).hexdigest()
    print(json.dumps({"best_H": best, "sha256": sha, **m.meta}, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------- grade
def boot(deltas: list[float], draws: int = 2000, seed: int = 8) -> tuple[float, float, float]:
    d = np.asarray(deltas, float)
    rng = np.random.default_rng(seed)
    means = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(draws)]
    return round(float(d.mean()), 3), round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3)


def cmd_grade(a) -> int:
    model = T.ValueModel.load(a.model)
    sha = hashlib.sha256(Path(a.model).read_bytes()).hexdigest()
    out = {"split": a.split, "model_sha256": sha, "horizon": model.horizon, "feed": a.feed, "nights": {}}
    t_start = time.time()
    for lg in LEAGUES:
        league = League(lg)
        fl = feed_lag_p90(league) if a.feed == "p90" else float(a.feed)
        for day, reps in nights(league, a.split, a.max_nights):
            for B in (60.0, 90.0):
                n = night(reps, B)
                tab = tab_of(n)
                res = run_night(n, tab, fl, model)
                out["nights"].setdefault(f"{lg}:{day}", {"league": lg, "games": len(reps)})[str(int(B))] = {
                    "feed_lag_s": fl, "window_s": OW.window_steps(n.lag, fl, TUNE_S, RESUME_LEAD.get(league, 0.0)) * O.STEP_S, **res}
            print(f"{lg} {day}: {len(reps)} games, {time.time() - t_start:.0f} s", flush=True)
    out["verdict"] = verdict(out["nights"])
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["verdict"], indent=1))
    return 0


def verdict(nts: dict) -> dict:
    def per_night(arm_a, arm_b, lg=None):
        d = []
        for v in nts.values():
            if lg and v["league"] != lg:
                continue
            xs = [v[b][arm_a]["pct_oracle"] - v[b][arm_b]["pct_oracle"] for b in ("60", "90")
                  if v[b]["full"]["pct_oracle"] is not None]
            if xs:
                d.append(float(np.mean(xs)))
        return d

    def tot(arm, key, lg=None):
        return sum(v[b][arm][key] for v in nts.values() for b in ("60", "90") if not lg or v["league"] == lg)

    mid = {arm: tot(arm, "entries_mid_play") for arm in ("full", "cue", "P", "L", "LC")}
    res = {"rule1_mid_play_entries": mid}
    for exp, (x, y) in {"EXP-120": ("L", "P"), "EXP-121": ("LC", "L")}.items():
        m, lo, hi = boot(per_night(x, y))
        ok_mid = mid[x] == 0
        big_x = 100 * tot(x, "big_live") / max(1, tot(x, "big"))
        big_y = 100 * tot(y, "big_live") / max(1, tot(y, "big"))
        guard = exp != "EXP-120" or big_x >= big_y - 1.0
        v = "FAIL" if (hi < 0 or not ok_mid) else ("PASS" if lo > 0 and guard else "INCONCLUSIVE")
        res[exp] = {"delta_pct_oracle": m, "ci95": [lo, hi], "verdict": v, "nights": len(per_night(x, y)),
                    f"big_live_pct_{x}": round(big_x, 1), f"big_live_pct_{y}": round(big_y, 1)}
    res["by_league"] = {}
    for lg in LEAGUES:
        r = {}
        for arm in ("full", "cue", "P", "L", "LC"):
            vals = [v[b][arm]["pct_oracle"] for v in nts.values() for b in ("60", "90")
                    if v["league"] == lg and v[b][arm]["pct_oracle"] is not None]
            if not vals:
                continue
            r[arm] = {"pct_oracle": round(float(np.mean(vals)), 2),
                      "big_live_pct": round(100 * tot(arm, "big_live", lg) / max(1, tot(arm, "big", lg)), 1),
                      "crunch_live_pct": round(100 * tot(arm, "crunch_live", lg) / max(1, tot(arm, "crunch", lg)), 1),
                      "dead_air_pct": round(100 * tot(arm, "dead_s", lg) / max(1, tot(arm, "watched_s", lg)), 1),
                      "switches": tot(arm, "switches", lg), "entries_mid_play": tot(arm, "entries_mid_play", lg)}
        if r:
            r["nights"] = sum(1 for v in nts.values() if v["league"] == lg)
            res["by_league"][lg] = r
    return res


def cmd_smoke(a) -> int:
    """One train night per league: fit H=24, run every arm on one valid night at B=60, assert Rule #1."""
    rows = {}
    for lg in LEAGUES:
        for day, reps in nights(League(lg), "train", 1):
            n = night(reps, 75.0)
            X, Y = T.training_rows(n, tab_of(n), 24)
            rows[lg] = ([X], [Y])
    m = T.fit(rows, 24)
    for lg in LEAGUES:
        for day, reps in nights(League(lg), "valid", 1):
            n = night(reps, 60.0)
            res = run_night(n, tab_of(n), feed_lag_p90(League(lg)), m)
            assert all(r["entries_mid_play"] == 0 for r in res.values()), res
            print(lg, day, {k: (r["pct_oracle"], r["entries_mid_play"]) for k, r in res.items()}, flush=True)
    print("smoke ok")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit")
    f.add_argument("--out", type=Path, required=True)
    f.add_argument("--alpha", type=float, default=1.0)
    f.add_argument("--max-nights", type=int, default=0)
    g = sub.add_parser("grade")
    g.add_argument("--split", default="valid", choices=("train", "valid", "test"))
    g.add_argument("--model", type=Path, required=True)
    g.add_argument("--feed", default="p90", help="'p90' (league ESPN p90) or seconds")
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--max-nights", type=int, default=0)
    sub.add_parser("smoke")
    a = ap.parse_args(argv)
    return {"fit": cmd_fit, "grade": cmd_grade, "smoke": cmd_smoke}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
