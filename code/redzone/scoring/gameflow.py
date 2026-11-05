"""Game-flow math shared by the training-data builder and the live brain (numpy only, no pandas).

SPEC holds each league's clock shape and thresholds; the win-probability models are the analytic
ones used wherever a feed has no win probability of its own.
"""
from __future__ import annotations

import math

import numpy as np

# Per-league clock shape and thresholds.
#   period_s / n_reg / ot_s : regulation period length, number of regulation periods, OT length
#   vol_w   : trailing/upcoming window (game s) for win-probability volatility and hotness
#   wp_key  : a single event that moves home WP by at least this much is a key moment
#   crunch  : (final-period seconds left, max margin) that counts as crunch time
#   late    : fraction of regulation left under which a lead change is a key moment
SPEC = {
    "nfl": dict(period_s=900, n_reg=4, ot_s=600, vol_w=420, wp_key=0.10, crunch=(300, 8), late=0.25),
    "cfb": dict(period_s=900, n_reg=4, ot_s=0, vol_w=420, wp_key=0.15, crunch=(300, 8), late=0.25),
    "nba": dict(period_s=720, n_reg=4, ot_s=300, vol_w=180, wp_key=0.10, crunch=(300, 5), late=0.25),
    "cbb": dict(period_s=1200, n_reg=2, ot_s=300, vol_w=180, wp_key=0.10, crunch=(300, 6), late=0.25),
    "wcbb": dict(period_s=600, n_reg=4, ot_s=300, vol_w=180, wp_key=0.10, crunch=(300, 6), late=0.25),
    "nhl": dict(period_s=1200, n_reg=3, ot_s=300, vol_w=420, wp_key=0.10, crunch=(300, 1), late=0.33),
    "mlb": dict(period_s=0, n_reg=9, ot_s=0, vol_w=900, wp_key=0.10, crunch=None, late=0.25),
}


def phi(x):
    """Standard normal CDF (vectorised, Abramowitz-Stegun 7.1.26 erf; |err| < 1.5e-7)."""
    x = np.asarray(x, dtype="float64")
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
               + 0.254829592) * t * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * y)


def game_elapsed(league: str, period: np.ndarray, clock_s: np.ndarray, ot_s=None) -> np.ndarray:
    sp = SPEC[league]
    P, n = sp["period_s"], sp["n_reg"]
    ot = np.full(len(period), float(sp["ot_s"])) if ot_s is None else np.asarray(ot_s, dtype=float)
    reg = (np.minimum(period, n) - 1) * P + (P - clock_s)
    extra = n * P + (period - n - 1) * ot + (ot - clock_s)
    return np.where(period <= n, reg, extra)


def wp_basketball(league: str, diff, t_rem, exp_margin) -> np.ndarray:
    """Normal model: final margin ~ N(diff + exp_margin * frac_left, sigma^2 * frac_left)."""
    sigma = {"nba": 16.5, "cbb": 14.5, "wcbb": 14.5}[league]  # nba fit to ESPN WP on data/archive
    total = SPEC[league]["period_s"] * SPEC[league]["n_reg"]
    frac = np.clip(np.asarray(t_rem, float) / total, 0, None)
    m = np.asarray(diff, float) + np.nan_to_num(exp_margin) * frac
    s = sigma * np.sqrt(frac)
    p = phi(m / np.maximum(s, 1e-6))
    over = frac <= 0
    return np.where(over, np.where(diff > 0, 1.0, np.where(diff < 0, 0.0, 0.5)), p)


def _poisson_pmf(lam: np.ndarray, kmax: int) -> np.ndarray:
    k = np.arange(kmax + 1)
    logf = np.array([math.lgamma(i + 1) for i in k])
    lam = np.maximum(lam, 1e-9)[:, None]
    return np.exp(k[None, :] * np.log(lam) - lam - logf[None, :])


def wp_hockey(diff, t_rem, home_skaters=None, away_skaters=None) -> np.ndarray:
    """Independent Poisson scoring for the rest of regulation; ties -> OT at 52/48."""
    rate = 3.05 / 3600.0
    t_rem = np.clip(np.asarray(t_rem, float), 0, None)
    lh, la = rate * 1.03 * t_rem, rate * 0.97 * t_rem
    if home_skaters is not None:
        # A man advantage roughly triples the scoring rate for ~the next minute.
        adv = np.nan_to_num(np.asarray(home_skaters, float) - np.asarray(away_skaters, float))
        boost = np.minimum(t_rem, 90.0) * rate * 2.0
        lh = lh + np.where(adv > 0, boost, 0)
        la = la + np.where(adv < 0, boost, 0)
    K = 12
    ph, pa = _poisson_pmf(lh, K), _poisson_pmf(la, K)
    d = np.asarray(diff, float)
    win = np.zeros(len(d))
    tie = np.zeros(len(d))
    for j in range(K + 1):  # away scores j more
        # home final - away final = d + i - j ; win if > 0, tie if == 0
        need = j - d  # i > need
        i = np.arange(K + 1)[None, :]
        win += pa[:, j] * (ph * (i > need[:, None])).sum(1)
        tie += pa[:, j] * (ph * (i == need[:, None])).sum(1)
    return np.clip(win + 0.52 * tie, 0, 1)


# Run expectancy by (bases bitmask 1b=1,2b=2,3b=4, outs) -- MLB 2015-2019 averages.
_RE24 = {0: (.481, .254, .098), 1: (.859, .509, .224), 2: (1.100, .664, .319), 4: (1.357, .950, .353),
         3: (1.437, .884, .429), 5: (1.784, 1.130, .478), 6: (1.964, 1.376, .580), 7: (2.292, 1.541, .752)}


def wp_baseball(diff, inning, half_bottom, outs, bases) -> np.ndarray:
    """Normal approx: remaining runs ~ half-innings * (0.48, var 1.0) + current base-out RE."""
    diff = np.asarray(diff, float)
    inning = np.asarray(inning, float)
    hb = np.asarray(half_bottom, float)
    outs = np.asarray(outs, float)
    bases = np.nan_to_num(np.asarray(bases, float)).astype(int)
    over = outs >= 3
    re_now = np.array([_RE24[b][int(min(o, 2))] for b, o in zip(bases, np.nan_to_num(outs))])
    re_now = np.where(over, 0.0, re_now)
    last = np.maximum(inning, 9)
    # full half-innings still to come after the current one
    away_full = np.where(hb == 0, last - inning, last - inning)
    home_full = np.where(hb == 0, last - inning + 1, last - inning)
    e_home = home_full * 0.48 + np.where(hb == 1, re_now, 0)
    e_away = away_full * 0.48 + np.where(hb == 0, re_now, 0)
    var = (home_full + away_full) * 1.0 + re_now * 1.6 + 1e-6
    p = phi((diff + e_home - e_away) / np.sqrt(var))
    # Walk-off / game over states.
    done_bottom = (hb == 1) & (inning >= 9) & (diff > 0)
    done_top = (hb == 0) & over & (inning >= 9) & (diff > 0)
    done_away = (hb == 1) & over & (inning >= 9) & (diff < 0)
    p = np.where(done_bottom | done_top, 1.0, p)
    return np.where(done_away, 0.0, p)
