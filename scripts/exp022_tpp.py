#!/usr/bin/env python3
"""[EXP-022] The event-token world model read as a timing point process vs Noggin Timing v1's trees, on green data.

    python scripts/world_model_pilot.py --out /content/wm --leagues nfl,nba --exp EXP-022 ...   # trains best.pt
    python scripts/exp022_tpp.py --wm /content/wm --leagues nfl,nba --out /content/tpp

Serves: North Star "Entries at a possession start" / "Mid-play entries" (a tighter, honest lower bound on the dead
time left lets the decider use more of each break without landing mid-play); owner: lane 1.

What is graded (docs/experiments.md EXP-022), per league on the pilot's test nights (time split by whole night, per
league; scorecard and locked nights never downloaded):
  (a) restart-time NLL over the tokenizer's 32 log-time bins, at every break stoppage (timeout, end of period, ...):
      the world model's time head vs Timing v1's LightGBM hazard, refit on the SAME train nights and collapsed to the
      same bins. Lower is better; PASS needs the model's CI upper bound below the trees' value.
  (b) a 2% lower bound q_lo for each: the raw 2% quantile of its distribution, shifted by split conformal on the valid
      nights (log scale). Coverage P(restart < q_lo) must be <= 2% + 1 pt on test.
  (c) mean q_lo, the usable dead time: the model's must be >= the trees' at equal coverage.

Units: the model sees each break as "the event after a break stoppage" and its dt token (seconds to the next logged
event); the trees see each break as a factory dead window and its length_s. Both come from the same games and nights,
read by their own extractors, so the counts differ a little; both counts are reported. Data: nflverse NFL and NBA.com
cdn NBA (shufinskiy/nba_data) only, no ESPN-derived source.
Why: EXP-022 grader: world model time head as a timing point process vs Timing v1 (docstring).
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import noggin_tokens as T  # noqa: E402
import world_model_pilot as W  # noqa: E402

ALPHA = 0.02
# The breaks both sides grade, like for like: the tokenizer's stop names and the factory's dead-window kinds.
MODEL_BREAKS = ("timeout", "two_minute_warning", "end_period", "halftime", "review", "injury")
TREE_BREAKS = ("team_timeout", "timeout", "timeout_long", "timeout_short", "two_minute_warning", "end_period",
               "halftime", "review", "injury")
EPS = 1e-6
EDGES = np.array([2 ** (b / 2.6) - 1 for b in range(T.DT_BUCKETS + 1)])   # bin b = [EDGES[b], EDGES[b+1])
EDGES[-1] = np.inf


def bin_of(sec: np.ndarray) -> np.ndarray:
    return np.minimum(T.DT_BUCKETS - 1, (np.log2(1.0 + np.maximum(0.0, sec)) * 2.6).astype(int))


def quantile_bins(p: np.ndarray, alpha: float) -> np.ndarray:
    """Raw alpha-quantile (seconds, a bin's lower edge) of each row's bin distribution: P(dt < q) <= alpha."""
    c = np.cumsum(p, axis=1)
    j = (c <= alpha).sum(axis=1)                       # bins wholly below the quantile
    return EDGES[np.minimum(j, T.DT_BUCKETS - 1)]


def conformal_shift(q_raw: np.ndarray, true: np.ndarray, alpha: float) -> float:
    """Log-scale shift c so that P(true < expm1(log1p(q) - c)) <= alpha on calibration (finite-sample rank)."""
    s = np.log1p(q_raw) - np.log1p(true)              # > c means a miss
    s = np.sort(s)[::-1]
    k = int(math.floor(alpha * (len(s) + 1)))
    return float(s[k]) if 0 <= k < len(s) else float(s[0])


def apply_shift(q_raw: np.ndarray, c: float) -> np.ndarray:
    return np.maximum(0.0, np.expm1(np.log1p(q_raw) - c))


# ---------------------------------------------------------------------------------------------- the world model
def model_breaks(model, games, ctx: int, dev: str) -> list[dict]:
    """Per game: (bin probs (n, 32), true seconds (n,)) at each event after a break stoppage."""
    import torch
    out = []
    dt_ids = torch.arange(T.DT0, T.DT0 + T.DT_BUCKETS, device=dev)
    model.eval()
    with torch.no_grad():
        for g in games:
            t = g["tokens"].astype(np.int64)
            k, dpos, s = W.positions(t)
            if len(k) < 3:
                continue
            step = (ctx // T.TOKENS_PER_EVENT) * T.TOKENS_PER_EVENT
            lg = []
            for a in range(0, len(t) - 1, step):
                x = torch.tensor(t[a:a + ctx], device=dev)[None]
                with torch.autocast(device_type=dev, dtype=torch.bfloat16, enabled=dev == "cuda"):
                    lg.append(model(x)[0].float())
            lg = torch.cat(lg)
            P, y = [], []
            for i in range(1, len(k)):
                if W.stop_name(t[s[i - 1]]) in MODEL_BREAKS and t[dpos[i]] != T.UNK_DT:
                    P.append(torch.softmax(lg[dpos[i] - 1][dt_ids], 0).cpu().numpy())
                    y.append(W.DT_MID[int(t[dpos[i]]) - T.DT0])
            if P:
                out.append({"p": np.array(P), "y": np.array(y)})
    return out


# ---------------------------------------------------------------------------------------------- the trees
def tree_bins(m, x: np.ndarray) -> np.ndarray:
    """Timing v1's restart distribution at the stoppage start (e0 = 0), collapsed to the 32 log-time bins."""
    from redzone.noggin import timing as TM
    t_end, S = TM.survival_batch(getattr(m, "_fast", None) or m.forest, x, np.zeros(len(x)))
    te = np.concatenate([[0.0], t_end[0]])
    F = np.concatenate([np.zeros((len(x), 1)), 1.0 - S], axis=1)            # P(restart <= te)
    fin = np.minimum(EDGES, te[-1])
    Fe = np.stack([np.interp(fin, te, row) for row in F])                   # CDF at each bin edge
    Fe[:, -1] = 1.0                                                         # mass past the horizon -> last bins
    return np.diff(Fe, axis=1).clip(0.0)


def tree_windows(league: str, raw_dir: Path, seasons, log=print):
    """Timing v1's dead windows from the same raw files the tokens came from (nflverse; cdn NBA in hoopR schema)."""
    import pandas as pd
    from redzone.noggin import factory as F
    parts = []
    for y in seasons:
        raw = W.fetch(league, y, raw_dir)                        # cached by the pilot run: no new download
        t = F.build(league, raw) if raw is not None else None
        if t is not None and len(t):
            parts.append(F.drop_eval_nights(t, league))
            log(f"  trees {league} {y}: {len(parts[-1]):,} windows", flush=True)
    return pd.concat(parts, ignore_index=True) if parts else None


# ---------------------------------------------------------------------------------------------- grades
def summarize(rows: list[dict], q_shift: float) -> dict:
    """rows: per game {p, y}. NLL, coverage and mean q_lo, each with a game-bootstrap CI."""
    def nll(pick):
        v = [-np.log(r["p"][np.arange(len(r["y"])), bin_of(r["y"])] + EPS) for r in pick]
        return float(np.mean(np.concatenate(v))) if v else None

    def q(pick):
        return [apply_shift(quantile_bins(r["p"], ALPHA), q_shift) for r in pick]

    def cover(pick):
        v = [(r["y"] < qq) for r, qq in zip(pick, q(pick))]
        return float(np.mean(np.concatenate(v))) if v else None

    def mean_q(pick):
        v = q(pick)
        return float(np.mean(np.concatenate(v))) if v else None

    out = {"games": len(rows), "breaks": int(sum(len(r["y"]) for r in rows))}
    for name, fn in (("nll", nll), ("below_q_lo", cover), ("q_lo_mean_s", mean_q)):
        val = fn(rows)
        out[name] = {"value": None if val is None else round(val, 4), "ci95": W.boot(rows, fn)}
    return out


def verdict(m: dict, tr: dict) -> dict:
    a = m["nll"]["ci95"] is not None and m["nll"]["ci95"][1] < tr["nll"]["value"]
    b = m["below_q_lo"]["value"] is not None and m["below_q_lo"]["value"] <= ALPHA + 0.01
    c = (m["q_lo_mean_s"]["value"] or 0) >= (tr["q_lo_mean_s"]["value"] or 0)
    return {"a_nll_below_trees": a, "b_coverage_ok": b, "c_q_lo_ge_trees": c, "pass": a and b and c}


def main(argv=None) -> int:
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("--wm", type=Path, required=True, help="the world_model_pilot --out dir (best.pt, grades.json)")
    ap.add_argument("--leagues", default="nfl,nba")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--exp", default="EXP-022")
    ap.add_argument("--smoke", action="store_true", help="the pilot ran with --smoke (one season per league)")
    a = ap.parse_args(argv)
    t0 = time.time()
    a.out.mkdir(parents=True, exist_ok=True)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    wg = json.loads((a.wm / "grades.json").read_text())
    rc = wg["recipe"]
    leagues = a.leagues.split(",")
    games = W.build_games(leagues, a.wm, smoke=a.smoke)      # cached raw files: no new download
    sp = W.split(games)
    model = W.make_model(T.VOCAB_SIZE, rc["d"], rc["layers"], rc["heads"], rc["ctx"]).to(dev)
    model.load_state_dict(torch.load(a.wm / "best.pt", map_location=dev))
    res = {"exp": a.exp, "seed": W.SEED, "world_model": {"recipe": rc, "data_sha256_16": wg.get("data_sha256_16"),
                                                          "pack": wg.get("pack")},
           "alpha": ALPHA, "per_league": {}}
    for lg in leagues:
        mine = {k: [g for g in v if g["league"] == lg] for k, v in sp.items()}
        if not mine["test"]:
            res["per_league"][lg] = {"note": "no test games"}
            continue
        va0, te0 = min(g["night"] for g in mine["valid"]), min(g["night"] for g in mine["test"])
        mv, mt = model_breaks(model, mine["valid"], rc["ctx"], dev), model_breaks(model, mine["test"], rc["ctx"], dev)
        c_m = conformal_shift(np.concatenate([quantile_bins(r["p"], ALPHA) for r in mv]),
                              np.concatenate([r["y"] for r in mv]), ALPHA)
        m_sum = summarize(mt, c_m)
        # trees, refit on the same nights: train < va0 <= calib < te0 <= test
        from redzone.noggin import timing_fit as TF
        from redzone.noggin.features import FEATURES
        seasons = W.SEASONS[lg][-1:] if a.smoke else W.SEASONS[lg]
        tw = tree_windows(lg, a.wm / "raw", seasons)
        if tw is None:
            res["per_league"][lg] = {"world_model": m_sum, "note": "no tree windows"}
            continue
        n_va, n_te = {g["night"] for g in mine["valid"]}, {g["night"] for g in mine["test"]}
        tw = tw[(tw.night < va0) | tw.night.isin(n_va | n_te)].reset_index(drop=True)   # the model's nights only
        tw["split"] = np.where(tw.night.isin(n_te), "test", np.where(tw.night.isin(n_va), "calib", "train"))
        tm = TF.fit(tw, lg)
        def rows_of(df):
            out = []
            for _, g in df.groupby("game_id"):
                p = tree_bins(tm, g[list(FEATURES)].to_numpy(np.float32))
                out.append({"p": p, "y": g.length_s.to_numpy(float)})
            return out
        brk = tw.kind.isin(TREE_BREAKS)
        tv, tt = rows_of(tw[(tw.split == "calib") & brk]), rows_of(tw[(tw.split == "test") & brk])
        c_t = conformal_shift(np.concatenate([quantile_bins(r["p"], ALPHA) for r in tv]),
                              np.concatenate([r["y"] for r in tv]), ALPHA)
        t_sum = summarize(tt, c_t)
        nights_m, nights_t = {g["night"] for g in mine["test"]}, set(tw[tw.split == "test"].night)
        res["per_league"][lg] = {
            "world_model": m_sum, "trees": t_sum, "verdict": verdict(m_sum, t_sum),
            "conformal_shift_log": {"world_model": round(c_m, 4), "trees": round(c_t, 4)},
            "test_nights": {"world_model": len(nights_m), "trees": len(nights_t), "shared": len(nights_m & nights_t),
                            "min_for_pass": 8},
            "trees_info": tm.info}
        if len(nights_m) < 8:
            res["per_league"][lg]["verdict"]["pass"] = "PENDING (fewer than 8 test nights)"
    res["wall_h"] = round((time.time() - t0) / 3600, 2)
    (a.out / "grades.json").write_text(json.dumps(res, indent=1, default=str))
    print("===== grades.json =====")
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
