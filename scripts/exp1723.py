#!/usr/bin/env python3
"""EXP-1723 (prereg docs/prereg/exp-1723.md, WV loop cycle 3): Watch Value as the windowed oracle's beyond value.

    python scripts/exp1723.py --league nba --out out/exp1723        # one league; run nba then nfl

Arms, per night, inside the forward chain (redzone/lab/forward_chain.Chain over FIT nights, oldest first):
  B0  oracle_window.solve_window with the v1 past-mean beyond value (control)
  B1  beyond value = IsoMap(WV), WV = the A0 recipe (train_watch_value: wv head, same features, PARAMS) refit on
      the SEEN nights only, unit weights (prereg amendment 21:35 UTC: the shipped A0 file saw these nights)
  B2  B1 with the WV fit weighted by mistake_ledger.sample_weights (app vs full hindsight) of the seen nights
  Inner early stopping: the last 20 % of seen nights (>= 1); B1/B2 need 2 seen nights, before that they are B0.
  plus the app (backtest.run) and full hindsight (oracle.solve) as 100 %.
Each night is graded FIRST with maps / models fit on earlier nights only; then its ledger (app vs full hindsight)
is written; then it joins the iso fits and B2's weights. Locked nights never enter (Chain raises).

v1 fit nights are dev history; the counting nights are v3's new nights (Friday) and live nights (prereg).
Runs where the pinned eval archive lives (cloud box / Kaggle), never on the Mac. Output: derived numbers only.
personal-use, not for commercial release.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))

from redzone.config import Config  # noqa: E402
from redzone.lab import forward_chain as FC, mistake_ledger as ML, oracle as O, oracle_window as OW  # noqa: E402
from redzone.lab import splits, wv_beyond as WB  # noqa: E402
from redzone.lab.backtest import feed_lag_knobs, run  # noqa: E402
from redzone.models import League  # noqa: E402

HORIZON_STEPS = int(120.0 // O.STEP_S)      # solve_window's default horizon_s (120 s): the beyond value's span


def wv_rows(lg: str, cfg_path: Path) -> tuple[list[dict], list[str]]:
    """Play rows with WV's facts. As in train_watch_value.main, the brain TRACK (win probability, swings) must be
    on for the facts WV reads; its learned outputs are not WV features. The replay/backtest config is untouched."""
    import tempfile
    from redzone.contracts.watch import features_for
    from redzone.lab import reasoner_data as RD
    tmp = Path(tempfile.mkdtemp()) / "wv_rows_config.yaml"
    tmp.write_text((cfg_path.read_text() if cfg_path.exists() else "") + "\nbrain:\n  enabled: true\n")
    cfg = Config(user_path=tmp)
    rows = [r.as_dict() for r in RD.league_rows(League(lg), ROOT / "data/archive" / lg, cfg, {"WSH", "UNC"}, every=1)]
    return rows, features_for(League(lg).base.value)


def fit_wv(X: np.ndarray, y: np.ndarray, w: np.ndarray, night_of: np.ndarray, seen: list[str], feats: list[str],
           params: dict) -> np.ndarray | None:
    """The A0 recipe on seen nights only: lightgbm regression, early stopping (40) on the last 20 % of seen nights.
    Returns the prediction for every row (None with < 2 seen nights)."""
    import lightgbm as lgb
    seen = sorted(seen)
    if len(seen) < 2:
        return None
    n_va = max(1, int(round(0.2 * len(seen))))
    tr, va = np.isin(night_of, seen[:-n_va]), np.isin(night_of, seen[-n_va:])
    if tr.sum() < 200 or va.sum() < 20:
        return None
    p = dict(params, objective="regression")
    d = lgb.Dataset(X[tr], y[tr], weight=w[tr], feature_name=feats)
    b = lgb.train(p, d, 800, valid_sets=[lgb.Dataset(X[va], y[va], weight=w[va], reference=d)],
                  callbacks=[lgb.early_stopping(40, verbose=False)])
    return b.predict(X, num_iteration=max(20, b.best_iteration))


def info_pool(rows: list[dict]) -> dict:
    """Information only (WV loop thread): the pool with full > 0 alone, your-team crunch nights kept."""
    out = {"nights": len(rows)}
    for k in ("app", "B0", "B1", "B2"):
        rr = [r for r in rows if r[f"v_{k}"] is not None]
        out[k] = WB.pooled_pct(np.array([r[f"v_{k}"] for r in rr]), np.array([r["v_full"] for r in rr]))["pct"] if rr else None
    return out


def main(argv=None) -> int:
    import buffer_sweep as BS
    import eval_sports as ES
    import train_watch_value as TW
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="nba")
    ap.add_argument("--buffer", type=float, default=90.0)
    ap.add_argument("--feed-lag", type=float, default=None, help="s; default p90 via buffer_sweep (prereg)")
    ap.add_argument("--max-nights", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    lg = a.league
    t_start = time.time()
    fl = a.feed_lag if a.feed_lag is not None else BS.feed_lag_for(lg, "p90")
    cfg = Config(user_path=ROOT / "data/eval_config.yaml")
    sp = splits.league_split(lg, splits.archive_nights(ROOT / "data/archive" / lg, None))
    locked = set(sp.test)
    a.out.mkdir(parents=True, exist_ok=True)

    # ---- WV per play (A0 file; B2 retrains the same head with ledger weights) ----
    rows, feats = wv_rows(lg, ROOT / "data/eval_config.yaml")
    if not any(r.get("wp_home") is not None for r in rows):
        raise SystemExit("no win probability on the WV rows: WV facts would be empty")
    rows = [r for r in rows if r["night"] not in locked]              # locked plays never even get a WV here
    X = TW.matrix(rows, feats).astype(np.float64)
    HZ = float(cfg.get("scoring.watch_horizon_s")[lg])               # noqa: N806
    by_game: dict[str, list[int]] = {}
    for k, r in enumerate(rows):
        by_game.setdefault(r["gid"], []).append(k)
    y_wv = np.zeros(len(rows))
    for ks in by_game.values():
        y_wv[ks] = TW.labels([rows[k] for k in ks], HZ)[0]
    night_of = np.array([r["night"] for r in rows])
    wall = np.array([r["wall"] if r.get("wall") is not None else np.nan for r in rows], float)

    def plays_of(pred: np.ndarray) -> dict:
        out = {}
        for gid, ks in by_game.items():
            ks = [k for k in ks if np.isfinite(wall[k])]
            o = np.argsort(wall[ks], kind="stable")
            ks = np.asarray(ks)[o]
            out[gid] = (wall[ks], pred[ks])
        return out

    st = {"plays_B1": None, "plays_B2": None, "w_row": np.ones(len(rows)), "seen": []}
    iso1, iso2 = WB.IsoMap(), WB.IsoMap()
    seen_led: dict[str, tuple] = {}      # night -> (ledger rows, K, t0, {gid: screen lag}) for B2's weights
    seen_data: dict[str, tuple] = {}     # night -> (gids, steps, lag, R, t0) for refitting iso2 after a retrain
    per_night: list[dict] = []
    ledgers: list = []

    def refit_wv(arm: str, w: np.ndarray) -> None:
        pred = fit_wv(X, y_wv, w, night_of, st["seen"], feats, TW.PARAMS)
        st[f"plays_{arm}"] = plays_of(pred) if pred is not None else None

    def first_pass(day: str, reps, targets) -> dict:
        rep = run(reps, cfg, {"WSH", "UNC"}, targets=targets, **feed_lag_knobs(fl, a.buffer))
        n = O.Night(reps, {"WSH", "UNC"}, rep.lag_true, 5.0, rep.t0, rep.t1)
        steps = np.arange(n.t0, n.t1, O.STEP_S)
        tab = O.tables(n, steps)
        wb, wc, wm, wd, _ = O.OBJECTIVES["combined"]
        R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
        gids = [r.gid for r in reps]
        W = WB.window_of(n, fl)
        full = O.solve(n, "combined", tab)
        b0 = OW.solve_window(n, fl, "combined", tab)
        sched = {}
        for arm, iso in (("B1", iso1), ("B2", iso2)):
            sched[arm] = b0
            if iso.f is not None and st[f"plays_{arm}"] is not None:
                g = WB.wv_grid(gids, st[f"plays_{arm}"], steps, n.lag)
                sched[arm] = WB.solve_with(n, fl, tab, WB.beyond_from_wv(g, iso, W))
        b1, b2 = sched["B1"], sched["B2"]
        # the app can tune a non-game target (NFL: the RedZone channel itself); the oracle grid has no row for it,
        # so such a night has no app value and no ledger (B2 weights stay 1 there)
        app_ok = all(sw_.to in set(gids) for sw_ in rep.switches)
        val = {k: O.schedule_value(n, tab, "combined", s) for k, s in
               (("full", full), ("app", rep.switches), ("B0", b0), ("B1", b1), ("B2", b2)) if k != "app" or app_ok}
        row = {"night": day, "games": len(reps), "W_steps": W, "your_team_crunch": bool(tab["mine"].sum() > 0),
               "b1_fallback": b1 is b0, "b2_fallback": b2 is b0, "seen_before": len(st["seen"]),
               "b2_rows_weighted": int((st["w_row"] > 1.0).sum()),
               "app_gradable": app_ok, "v_app": None,
               **{f"v_{k}": round(float(v), 3) for k, v in val.items()},
               **{f"switches_{k}": len(s) for k, s in (("app", rep.switches), ("B0", b0), ("B1", b1), ("B2", b2))}}
        per_night.append(row)
        seen_data[day] = (gids, steps, n.lag, R)
        if not app_ok:
            seen_led[day] = ([], len(steps), n.t0, {})
            return row
        # ledger (app vs full hindsight), AFTER the first pass
        blk = max(1, int(math.ceil(n.tune_s / O.STEP_S)))
        gi = {g: i for i, g in enumerate(gids)}
        app_on = ML.on_screen(len(steps), n.t0, O.STEP_S, rep.switches, gi, blk)
        orc_on = ML.on_screen(len(steps), n.t0, O.STEP_S, full, gi, blk)
        sw = lambda ss: {int(round((s.t - n.t0) / O.STEP_S)) for s in ss if s.frm is not None}  # noqa: E731
        led = ML.ledger(day, lg, R, tab, gids, app_on, orc_on, n.t0, O.STEP_S, locked=locked,
                        app_switch_steps=sw(rep.switches), oracle_switch_steps=sw(full), blk=blk)
        ledgers.extend(led)
        seen_led[day] = (led, len(steps), n.t0, {gid: float(O.lag_of(n.lag, gid)) for gid in gids})
        return row

    def reweight() -> None:
        """B2 weights (mistake_ledger.sample_weights, #349 b31caf4): every seen night's episodes, scale = the median
        value lost over ALL seen nights' episodes (recomputed as the chain grows); a play gets its screen step's weight."""
        lost = [r.value_lost for led, *_ in seen_led.values() for r in led]
        scale = float(np.median(lost)) if lost else None
        for led, K, t0, lags in seen_led.values():
            lw = ML.sample_weights(led, K, scale=scale)
            for gid, lag in lags.items():
                ks = by_game.get(gid, [])
                if ks:
                    ks = np.asarray(ks)
                    st["w_row"][ks] = WB.step_weights(lw, t0, O.STEP_S, wall[ks] + lag)

    def refit(seen) -> None:
        st["seen"] = list(seen)
        reweight()
        refit_wv("B1", np.ones(len(rows)))
        refit_wv("B2", st["w_row"])
        for arm, iso in (("B1", iso1), ("B2", iso2)):
            iso.x, iso.y = [], []
            if st[f"plays_{arm}"] is None:
                iso.f = None
                continue
            for gids, steps, lag, R in seen_data.values():
                iso.add(*WB.iso_pairs(WB.wv_grid(gids, st[f"plays_{arm}"], steps, lag), R, HORIZON_STEPS))
            iso.refit()

    ch = FC.Chain(locked=locked)
    fit_nights = set(sp.train + sp.valid)
    done = 0
    for day, reps, targets in ES.nights(League(lg), 10_000, None, "fit"):
        day = str(day)
        if day not in fit_nights:
            continue
        if a.max_nights and done >= a.max_nights:
            break
        ch.step(day, lambda d, r=reps, t=targets: first_pass(d, r, t), lambda d: None, refit)
        done += 1
        print(json.dumps(per_night[-1]), flush=True)

    # pooled over nights without a your-team crunch (prereg) and with full-hindsight value > 0: the combined
    # objective's dead-air term makes some nights' totals negative, where a % of full is undefined (the
    # cycle-2 ledger read "6/6 positive nights" the same way)
    ok = [r for r in per_night if not r["your_team_crunch"] and r["v_full"] > 0]
    full = np.array([r["v_full"] for r in ok])
    arms = {k: np.array([r[f"v_{k}"] for r in ok]) for k in ("B0", "B1", "B2")}
    oka = [r for r in ok if r["app_gradable"]]               # nights where the app's schedule is gradable
    full_a = np.array([r["v_full"] for r in oka])
    arms_a = {k: np.array([r[f"v_{k}"] for r in oka]) for k in ("app", "B1", "B2")}
    summ = {"exp": "EXP-1723", "league": lg, "buffer_s": a.buffer, "feed_lag_s": fl, "horizon_steps": HORIZON_STEPS,
            "nights_chain": len(per_night), "nights_pooled": len(ok),
            "nights_excluded": {"your_team_crunch": sum(r["your_team_crunch"] for r in per_night),
                                "full_value_not_positive": sum((not r["your_team_crunch"]) and r["v_full"] <= 0 for r in per_night)},
            "pct_of_full": {**{k: WB.pooled_pct(v, full) for k, v in arms.items()},
                            "app": WB.pooled_pct(arms_a["app"], full_a) if oka else None},
            "app_gradable_nights": len(oka),
            "pct_of_full_on_app_nights": {k: WB.pooled_pct(v, full_a)["pct"] for k, v in arms_a.items()} if oka else None,
            "info_pool_full_positive_only": info_pool([r for r in per_night if r["v_full"] > 0]),
            "B1_minus_B0": WB.delta_ci(arms["B1"], arms["B0"], full), "B2_minus_B0": WB.delta_ci(arms["B2"], arms["B0"], full),
            "B1_minus_app": WB.delta_ci(arms_a["B1"], arms_a["app"], full_a) if oka else None,
            "B2_minus_app": WB.delta_ci(arms_a["B2"], arms_a["app"], full_a) if oka else None,
            "ledger_causes_app_vs_full": ML.summarize(ledgers), "locked_nights_read": 0,
            "evidence": "dev history only (eval-data-v1 fit nights); counting nights = v3 new + live",
            "seconds": round(time.time() - t_start), "tag": "personal-use, not for commercial release"}
    for arm in ("B1", "B2"):
        d, pa = summ[f"{arm}_minus_B0"], summ["pct_of_full_on_app_nights"]
        summ[f"{arm}_bar"] = bool(d["delta"] >= 5.0 and d["ci"][0] > 0 and pa is not None and pa[arm] >= pa["app"])
    (a.out / f"first_pass_{lg}.jsonl").write_text(ch.jsonl())
    (a.out / f"nights_{lg}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in per_night))
    (a.out / f"summary_{lg}.json").write_text(json.dumps(summ, indent=1))
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
