"""Win-probability swing labels (suspense and surprise) for every play in the archive: derived numbers only.

Serves: Watch Value attention labels (Nick 11:30 AM Nov 5; crew G joins them to plays). Owner: Data Planner A.

    python scripts/pbp/wp_swings.py nba OUT_DIR --shards /mnt/project-files/data-a/pbp/nba_stats_pbp
    python scripts/pbp/wp_swings.py nfl OUT_DIR --seasons 1999-2024

NBA: home win probability from the score and time left, as a Brownian-motion model fitted on the archive itself
(margin drift mu and volatility sigma per second; Stern 1994): wp = Phi((m + mu*tau) / (sigma*sqrt(tau))), where
tau is the regulation seconds left (an overtime adds its own 300 s). NFL: nflverse's own per-play home_wp (its
public WP model, which uses down, distance and field position too).

Per play: wp (home), surprise = |wp - previous wp|, and suspense = the expected size of the next minute's move,
sqrt(E[dwp^2]) over 60 s, which for the Brownian model is phi(z) * sqrt(60 / tau) (Ely, Frankel and Kamenica 2015,
suspense = variance of the next belief). Per game: excitement = sum of surprise, and the max swing. No text is
kept. Locked nights are never in the inputs (nba shards stop before EVAL_FIRST_NIGHT; NFL stops at 2024).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

NFL_URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{y}.parquet"
NBA_LICENSE = ("Derived from NBA.com stats play-by-play (NBA.com ToS private non-commercial) via shufinskiy/nba_data; "
               "derived numbers only; plan B personal use")
NFL_LICENSE = ("Derived from nflverse play-by-play wp (CC-BY 4.0 nflverse data; underlying NFL data personal/"
               "non-commercial); plan B personal use")
PERSONAL = "personal-use, not for commercial release"
SUSPENSE_S = 60.0


def _phi(z):
    import numpy as np
    return np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def _Phi(z):
    """Normal CDF (Abramowitz-Stegun 7.1.26 erf, |error| < 1.5e-7): numpy only, no scipy."""
    import numpy as np
    x = np.abs(np.asarray(z, dtype=float)) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t \
        * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * y)


def nba_tau(period, clock_s):
    """Seconds left: regulation time left, or the overtime's own clock once in OT."""
    import numpy as np
    p = np.asarray(period, dtype=float)
    c = np.nan_to_num(np.asarray(clock_s, dtype=float), nan=0.0)
    return np.where(p <= 4, (4 - p) * 720 + c, c)


def fit_brownian(df) -> tuple[float, float]:
    """mu, sigma (points per second) from (final margin - margin now) over tau, pooled over every event."""
    import numpy as np
    m = (df.home_score - df.away_score).to_numpy(float)
    fin = df.groupby("game_id")[["home_score", "away_score"]].transform("last")
    d = (fin.home_score - fin.away_score).to_numpy(float) - m
    tau = df.tau.to_numpy(float)
    ok = tau > 60
    mu = float(np.sum(d[ok]) / np.sum(tau[ok]))
    sigma = float(math.sqrt(np.sum((d[ok] - mu * tau[ok]) ** 2) / np.sum(tau[ok])))
    return mu, sigma


def label_frame(df, wp):
    """Add wp / surprise / suspense columns; wp in [0, 1] per row, rows sorted by game, seq."""
    import numpy as np
    df = df.assign(wp=np.clip(wp, 0.0, 1.0).astype("float32"))
    prev = df.groupby("game_id").wp.shift(1)
    df["surprise"] = (df.wp - prev).abs().fillna(0.0).astype("float32")
    return df


def nba(shards: Path, out: Path) -> dict:
    import numpy as np
    import pandas as pd
    cols = ["game_id", "night", "season", "seq", "period", "clock_s", "home_score", "away_score"]
    files = sorted(Path(shards).glob("*.parquet"))
    parts = []
    for f in files:
        d = pd.read_parquet(f, columns=cols)
        d["playoffs"] = f.stem.endswith("_po")
        parts.append(d)
    df = pd.concat(parts, ignore_index=True).sort_values(["game_id", "seq"], kind="stable")
    df["tau"] = nba_tau(df.period, df.clock_s)
    mu, sigma = fit_brownian(df)
    tau = np.maximum(df.tau.to_numpy(float), 1.0)
    m = (df.home_score - df.away_score).to_numpy(float)
    z = (m + mu * tau) / (sigma * np.sqrt(tau))
    ended = (df.tau.to_numpy(float) <= 0) & (m != 0)
    wp = np.where(ended, (m > 0).astype(float), _Phi(z))
    df = label_frame(df, wp)
    df["suspense"] = np.where(ended, 0.0, _phi(z) * np.sqrt(np.minimum(SUSPENSE_S, tau) / tau)).astype("float32")
    return _write(df.drop(columns=["tau"]), out / "nba", "nba", NBA_LICENSE,
                  {"model": "brownian", "mu_pts_per_s": round(mu, 6), "sigma_pts_per_sqrt_s": round(sigma, 4)})


def nfl(seasons: list[int], out: Path, session=None) -> dict:
    import io

    import numpy as np
    import pandas as pd
    import requests
    s = session or requests.Session()
    s.headers.update({"User-Agent": "redzone-personal-research/1.0"})
    cols = ["game_id", "play_id", "game_date", "season", "season_type", "qtr", "game_seconds_remaining", "home_wp",
            "home_score", "away_score", "total_home_score", "total_away_score"]
    parts = []
    for y in seasons:
        if y >= 2025:
            continue                       # the locked NFL window starts 2025-09-05
        r = s.get(NFL_URL.format(y=y), timeout=600)
        if r.status_code != 200:
            continue
        import pyarrow.parquet as pq
        have = set(pq.read_schema(io.BytesIO(r.content)).names)
        d = pd.read_parquet(io.BytesIO(r.content), columns=[c for c in cols if c in have])
        d = d[d.home_wp.notna()]
        parts.append(d)
    df = pd.concat(parts, ignore_index=True)
    df["night"] = df.game_date.astype(str).str.replace("-", "")
    df = df.rename(columns={"play_id": "seq", "qtr": "period"}).sort_values(["game_id", "seq"], kind="stable")
    df = df.rename(columns={"total_home_score": "home_score_after", "total_away_score": "away_score_after"})
    df = label_frame(df, df.home_wp.to_numpy(float))
    # suspense: the Brownian-equivalent z of nflverse's wp, then the same next-minute formula as the NBA
    gs = df.game_seconds_remaining.to_numpy(float)
    tau = np.maximum(np.nan_to_num(gs, nan=1.0), 1.0)
    w = df.wp.to_numpy(float)
    z = np.clip(_ppf(np.clip(w, 1e-4, 1 - 1e-4)), -8, 8)
    df["suspense"] = (_phi(z) * np.sqrt(np.minimum(SUSPENSE_S, tau) / tau)).astype("float32")
    df["playoffs"] = df.season_type.astype(str).eq("POST")
    keep = ["game_id", "night", "season", "seq", "period", "game_seconds_remaining", "playoffs", "wp", "surprise",
            "suspense"]
    return _write(df[keep], out / "nfl", "nfl", NFL_LICENSE,
                  {"model": "nflverse home_wp; suspense from the Brownian-equivalent z of wp"})


def _ppf(p):
    """Inverse normal CDF (Acklam's rational approximation, relative error < 1.2e-9)."""
    import numpy as np
    a = (-39.69683028665376, 220.9460984245205, -275.9285104469687, 138.3577518672690, -30.66479806614716,
         2.506628277459239)
    b = (-54.47609879822406, 161.5858368580409, -155.6989798598866, 66.80131188771972, -13.28068155288572)
    c = (-0.007784894002430293, -0.3223964580411365, -2.400758277161838, -2.549732539343734, 4.374664141464968,
         2.938163982698783)
    d = (0.007784695709041462, 0.3224671290700398, 2.445134137142996, 3.754408661907416)
    p = np.asarray(p, dtype=float)
    lo, hi = p < 0.02425, p > 1 - 0.02425
    q = np.sqrt(-2 * np.log(np.where(lo, p, np.where(hi, 1 - p, 0.5))))
    tail = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    r = (p - 0.5) ** 2
    mid = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * (p - 0.5) / \
        (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    return np.where(lo, tail, np.where(hi, -tail, mid))


def _write(df, d: Path, league: str, license_: str, model: dict) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d / "plays.parquet", compression="zstd", index=False)
    g = df.groupby("game_id").agg(night=("night", "first"), plays=("wp", "size"), excitement=("surprise", "sum"),
                                  max_swing=("surprise", "max"), mean_suspense=("suspense", "mean"),
                                  final_wp=("wp", "last"))
    g.reset_index().to_parquet(d / "games.parquet", compression="zstd", index=False)
    meta = {"source": f"wp_swings_{league}", "league": league, "rows_are": "derived per-play labels (wp, surprise, "
            "suspense); join game_id + seq to the play table", "plays": int(len(df)), "games": int(len(g)),
            "seasons": f"{int(df.season.min())}-{int(df.season.max())}", "license": license_, "tag": PERSONAL,
            "data_plan": "B", "derived": True, **model,
            "surprise_p50_p90_p99": [round(float(x), 4) for x in df.surprise.quantile([.5, .9, .99])],
            "excitement_p50": round(float(g.excitement.median()), 3)}
    (d / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("league", choices=["nba", "nfl"])
    ap.add_argument("out", type=Path)
    ap.add_argument("--shards", type=Path)
    ap.add_argument("--seasons", default="1999-2024")
    a = ap.parse_args()
    if a.league == "nba":
        m = nba(a.shards, a.out)
    else:
        lo, _, hi = a.seasons.partition("-")
        m = nfl(list(range(int(lo), int(hi or lo) + 1)), a.out)
    print(json.dumps(m, indent=1))
