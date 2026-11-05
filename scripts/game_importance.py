#!/usr/bin/env python3
"""Game Importance v1 (Nick 12:00 PM Nov 5: "which game to watch, when"). One row per game, PRE-GAME numbers only
(Data B shards: stakes, rest, national TV, NFL injuries/weather/primetime), the target is how much the game turned
out to matter to watch. Labels and evaluation only: the target is never an input.

    python scripts/game_importance.py nba nfl --out /mnt/project-files/attention-g/gi   # build + train + grade

Target: within-night rank of Data A's game excitement (sum of |dWP|, Ely-Frankel-Kamenica). A night ranks its
games, so the model learns "which of tonight's games", never "was this an exciting season". Pre-game features
can't see the outcome, so the label isn't the rule that made it (context-only check reported anyway).

Split: by whole SEASON, forward in time (train <= valid < test). The eval-data locked nights are 2025-26 and are
not in Data A's history, so this script never reads them. Grade = mean within-night pairwise rank agreement (the
pick-the-better-game rate) vs three baselines: national TV, closeness (|elo diff|), playoff-odds swing.

Group columns (for EXP-1405's L5/L6 ablations) are in GROUPS. Outputs: <out>/<lg>_games.parquet (private),
<out>/<lg>_model.txt, <out>/report.json (derived numbers only).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

B = Path("/mnt/project-files/data-b")
A = Path("/mnt/project-files/data-a/wp_swings")
TAG = "personal-use, not for commercial release"
SHARDS = {"nba": ["nba-stakes", "nba-rest", "nba-tv"], "nfl": ["nfl-stakes", "nfl", "nfl-weather"]}
GROUPS = {   # field -> group (EXP-1405 ablations name groups, not columns)
    "stakes": ["clinch_status", "div_rank", "elimination_game", "elo", "elo_diff_abs", "elo_win_prob", "games_back",
               "win_pct", "playin_odds", "playoff_odds", "playoff_odds_swing", "stakes_swing_max", "stakes_swing_sum",
               "top10_odds", "top10_odds_swing"],
    "schedule": ["back_to_back", "games_in_last_7", "rest_days", "rest_diff", "travel_km", "tz_shift_h",
                 "home_stand_game", "road_trip_game", "kickoff_hour_et", "weekday", "div_game"],
    "broadcast": ["national_tv", "primetime"],
    "availability": ["players_out", "players_doubtful", "players_questionable", "qb_out"],
    "weather": ["temp_f", "wind_mph", "precip_mm", "snow", "roof_open"],
    "season": ["month_frac", "playoffs"],
}
SEED = 1700
HALF_LIFE_W = 0.5   # per-season weight decay (Nick 12:10 PM: recent context carries the most weight)


def _shards(lg: str) -> pd.DataFrame:
    fs = [f for d in SHARDS[lg] for f in sorted(glob.glob(str(B / "shards" / d / "*.parquet")))]
    x = pd.concat([pd.read_parquet(f, columns=["game_id", "team", "asof_utc", "field", "value"]) for f in fs])
    keep = {c for cs in GROUPS.values() for c in cs}
    x = x[x.field.isin(keep)].dropna(subset=["value"])
    return x.sort_values("asof_utc").drop_duplicates(["game_id", "team", "field"], keep="last")


def features(lg: str) -> pd.DataFrame:
    sch = pd.read_csv(B / "schedules" / f"{lg}.csv", dtype=str)
    sch["start"] = pd.to_datetime(sch.start_utc, utc=True)
    x = _shards(lg)
    x = x.merge(sch[["game_id", "home", "away", "start"]], on="game_id", how="inner")
    x = x[pd.to_datetime(x.asof_utc, utc=True) <= x.start]          # pre-game only, hard
    side = np.where(x.team == x.home, "h", np.where(x.team == x.away, "a", "g"))
    x = x.assign(col=[f"{s}_{f}" for s, f in zip(side, x.field)])
    w = x.pivot_table(index="game_id", columns="col", values="value", aggfunc="last")
    out = sch.set_index("game_id")[["season", "home", "away", "start", "game_type"]
                                   + (["nba_game_id"] if lg == "nba" else [])].join(w, how="inner")
    for f in {c for cs in GROUPS.values() for c in cs}:               # game-level summaries: both, max, sum
        h, a = out.get(f"h_{f}"), out.get(f"a_{f}")
        if h is not None and a is not None:
            out[f"max_{f}"] = np.fmax(h, a)
            out[f"sum_{f}"] = h.fillna(0) + a.fillna(0)
        elif out.get(f"g_{f}") is None and (h is not None or a is not None):
            out[f"g_{f}"] = h if h is not None else a
    out["playoffs"] = (out.game_type.fillna("REG") != "REG").astype(float)
    m = out.start.dt.month
    out["month_frac"] = ((m - (10 if lg == "nba" else 9)) % 12) / 9.0
    return out


def target(lg: str, feats: pd.DataFrame) -> pd.DataFrame:
    g = pd.read_parquet(A / lg / "games.parquet")
    g["game_id"] = g.game_id.astype(str)
    key = "nba_game_id" if lg == "nba" else None
    f = feats.reset_index()
    if key:
        f = f[f[key].notna()].copy()
        f["join"] = f[key].str.zfill(10)
    else:
        f["join"] = f.game_id
    t = f.merge(g.rename(columns={"game_id": "join"})[["join", "night", "excitement", "max_swing"]], on="join")
    t = t.groupby("night").filter(lambda d: len(d) >= 2)
    t["truth"] = t.excitement
    t["y"] = t.groupby("night").truth.rank(pct=True)
    return t


MKT = Path("/mnt/project-files/attention-g/mkt")   # crew D's per-game Polymarket volume (markets-attn-out/*)


def target_volume(lg: str, feats: pd.DataFrame) -> pd.DataFrame:
    """Human attention per game = prediction-market volume (Nick 9:05 AM named market VOLUME as a label; prices
    stay inputs only). Within-night rank of log volume."""
    m = pd.concat([pd.read_csv(f) for f in sorted(MKT.glob(f"{lg}-*_games.csv"))]).drop_duplicates("game_key")
    k = m.game_key.str.split(":", expand=True)
    m["date"], m["away"], m["home"] = k[1], k[2].str.split("@").str[0], k[2].str.split("@").str[1]
    f = feats.reset_index()
    f["date"] = f.start.dt.tz_convert("America/New_York").dt.strftime("%Y%m%d")
    t = f.merge(m[["date", "home", "away", "volume_usd"]], on=["date", "home", "away"])
    t = t[t.volume_usd > 0].rename(columns={"date": "night"})
    t["join"] = t.game_id
    t["truth"] = np.log1p(t.volume_usd)
    t = t.groupby("night").filter(lambda d: len(d) >= 2)
    t["y"] = t.groupby("night").truth.rank(pct=True)
    return t


LOCKED = Path("/mnt/project-files/attention-g/locked_nights.json")   # eval-data locked nights (league_split test)


def drop_locked(lg: str, t: pd.DataFrame) -> pd.DataFrame:
    """Locked nights never train or select anything, however recent (Nick; grading.md)."""
    ln = set(json.loads(LOCKED.read_text()).get(lg, [])) if LOCKED.exists() else set()
    if not LOCKED.exists():
        raise SystemExit(f"{LOCKED} missing: refusing to train without the locked-night list")
    return t[~t.night.astype(str).isin(ln)]


def feat_cols(t: pd.DataFrame, drop_groups: tuple = ()) -> list[str]:
    bad = {"game_id", "join", "night", "excitement", "max_swing", "y", "truth", "volume_usd", "date", "season", "home", "away", "start",
           "game_type", "nba_game_id"}
    gone = {c for g in drop_groups for c in GROUPS[g]}
    cols = [c for c in t.columns if c not in bad and pd.api.types.is_numeric_dtype(t[c])]
    return [c for c in cols if c.split("_", 1)[-1] not in gone]


def pair_agree(t: pd.DataFrame, score: np.ndarray) -> float:
    """Mean over nights of the rate the score orders a pair of that night's games the way excitement did."""
    t = t.assign(s=score)
    acc, n = 0.0, 0
    for _, d in t.groupby("night"):
        y, s = d.truth.to_numpy(), d.s.to_numpy()
        i, j = np.triu_indices(len(d), 1)
        ok = y[i] != y[j]
        if not ok.any():
            continue
        c = np.sign(s[i] - s[j])[ok] == np.sign(y[i] - y[j])[ok]
        acc += (c + 0.5 * (s[i] == s[j])[ok]).mean()
        n += 1
    return round(100 * acc / max(1, n), 2)


def season_split(t: pd.DataFrame) -> dict:
    ss = sorted(t.season.astype(int).unique())
    # Nick 12:10 PM: newest-first. Valid = the newest season Data A has; everything older trains, recency-weighted.
    # (The eval-data locked nights are 2025-26+ and are not in Data A, so nothing here is locked.)
    return {"train": ss[:-1], "valid": ss[-1:], "test": []}


def train(lg: str, t: pd.DataFrame, drop_groups: tuple = ()) -> tuple[object, dict]:
    import lightgbm as lgb
    sp = season_split(t)
    part = {s: k for k, ss in sp.items() for s in ss}
    t = t.assign(part=t.season.astype(int).map(part))
    cols = feat_cols(t, drop_groups)
    tr, va = t[t.part == "train"], t[t.part == "valid"]
    age = max(sp["train"]) - tr.season.astype(int)
    wt = HALF_LIFE_W ** age                              # last season 1.0, two back 0.5, ... (low weight for old)
    p = dict(objective="regression", learning_rate=0.05, num_leaves=31, min_data_in_leaf=50,
             feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, seed=SEED, verbose=-1)
    m = lgb.train(p, lgb.Dataset(tr[cols], tr.y, weight=wt), 2000, valid_sets=[lgb.Dataset(va[cols], va.y)],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    rep = {"seasons": {k: [int(v[0]), int(v[-1])] if v else [] for k, v in sp.items()}, "features": len(cols),
           "drop_groups": list(drop_groups), "best_iter": m.best_iteration}
    for k in ("train", "valid"):
        d = t[t.part == k]
        rep[f"{k}_games"] = len(d)
        rep[f"{k}_pair_agree"] = pair_agree(d, m.predict(d[cols], num_iteration=m.best_iteration))
    d = t[t.part == "valid"]

    def col(*names, fill=0.0):
        for n in names:
            if n in d:
                return d[n].fillna(fill).to_numpy()
        return np.full(len(d), fill)
    rep["valid_baselines"] = {
        "national_tv": pair_agree(d, col("g_national_tv", "g_primetime", "max_primetime")),
        "closeness": pair_agree(d, -col("g_elo_diff_abs", "max_elo_diff_abs", fill=999.0)),
        "stakes_swing": pair_agree(d, col("sum_playoff_odds_swing")),
        "coin": 50.0}
    return m, rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="+")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--target", choices=("excitement", "volume"), default="excitement")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    report = {"tag": TAG, "seed": SEED, "groups": GROUPS,
              "code_sha1": hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]}
    for lg in a.leagues:
        t = drop_locked(lg, (target_volume if a.target == "volume" else target)(lg, features(lg)))
        t.to_parquet(a.out / f"{lg}_{a.target}_games.parquet")
        report[lg] = {"games": len(t), "nights": int(t.night.nunique()),
                      "data_sha1": hashlib.sha1(pd.util.hash_pandas_object(t[["join", "y"]]).values.tobytes())
                      .hexdigest()[:12]}
        m, rep = train(lg, t)
        m.save_model(str(a.out / f"{lg}_{a.target}_model.txt"), num_iteration=m.best_iteration)
        report[lg]["full"] = rep
        for g in GROUPS:
            report[lg][f"minus_{g}"] = train(lg, t, (g,))[1]["valid_pair_agree"]
        print(lg, json.dumps(report[lg])[:1500], flush=True)
    report["target"] = a.target
    (a.out / f"report_{a.target}.json").write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
