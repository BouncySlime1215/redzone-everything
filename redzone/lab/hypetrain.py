"""Hype v2 training lab: learn per-league weights from archived games.

    .venv/bin/python scripts/train_hype.py build      # replay data/archive into a feature table
    .venv/bin/python scripts/train_hype.py fit        # fit, report, write redzone/scoring/hype_model.json

Label: a KEY MOMENT happens in this game within the next H seconds (wallclock):
  NHL         every goal (shootout goals included)
  NFL / CFB   every score, turnover and 4th-down try
  basketball  every basket in the last 5:00 of regulation or OT with the margin within 5
  MLB         every run in the 7th or later within 3, every home run
  all         second-half lead changes and ties, overtime scores, win-probability swings >= 15%
Key moments in garbage time do not count (a goal in a 6-1 game is not worth a switch).

Features: redzone.scoring.features (the exact function the live scorer calls), computed while
the game is replayed poll by poll, so the model trains on what it will see live.

Model: L2 logistic regression per base league (basketball pools NBA + college), fit by Newton's
method in plain numpy on standardized features. Split by DATE: the last ~30% of each league's
days are the test set, never seen in fitting.
"""
from __future__ import annotations

import bisect
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from redzone.models import REGULATION, League, Phase
from redzone.scoring import garbage
from redzone.scoring.features import Tracker, features, time_left_frac
from redzone.sources import espn

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent.parent
HORIZONS = (60, 120, 180, 300)
BASES = {"nfl": "nfl", "nba": "nba", "cbb": "nba", "wcbb": "nba", "nhl": "nhl", "mlb": "mlb", "cfb": "nfl"}
_NOT_A_TRY = ("punt", "field goal", "timeout", "penalty", "kickoff", "extra point", "end of", "two-minute",
              "end period", "end quarter", "end half")


def _secs(clock: str | None) -> float | None:
    if clock is None:
        return None
    m = re.match(r"^(\d+):(\d+)", str(clock))
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return float(clock)
    except ValueError:
        return None


# Win-probability model for leagues/plays without ESPN's: margin vs a normal spread of the rest of
# the game (sudden death in OT makes any score decisive).
WP_SD = {League.NFL: 13.5, League.NBA: 12.0, League.NHL: 2.0, League.MLB: 3.6}


def model_wp(league: League, diff: int, rem: float) -> float:
    z = diff / (WP_SD[league.base] * math.sqrt(max(rem, 1e-4)))
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def key_moments(rep) -> list[tuple[float, str, float]]:
    """(wallclock, kind, swing) for every key moment in a replayed game (garbage time excluded).
    swing = how much it moved the win probability (ESPN's when the feed has it, else model_wp),
    floored at 0.05: every key moment counts a little, decisive ones count a lot."""
    lg = rep.league
    base = lg.base
    reg = REGULATION[lg]
    wp = {w.get("playId"): w.get("homeWinPercentage") for w in rep.data.get("winprobability") or []}
    out = []
    prev = None
    for j, p in enumerate(rep.plays):
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        per = espn._int(espn._get(p, "period", "number")) or 0
        w = rep.wall[j]
        if hs is None or as_ is None or w is None:
            continue
        if prev is None:
            prev = (hs, as_, wp.get(p.get("id")), j)
            continue
        h0, a0, wp0, j0 = prev
        d0, d1 = h0 - a0, hs - as_
        scored = (hs + as_) != (h0 + a0)
        kinds = []
        t = (espn._get(p, "type", "text") or "").lower()
        ot = per > reg
        second_half = per > reg / 2 if base != League.MLB else per >= 5
        s0, s1 = (d0 > 0) - (d0 < 0), (d1 > 0) - (d1 < 0)
        if scored and s0 != s1 and second_half:
            kinds.append("tie" if s1 == 0 else "lead_change" if s0 != 0 else "go_ahead")
        if scored and ot:
            kinds.append("ot_score")
        if base == League.NHL and (p.get("scoringPlay") or t == "goal" or (t.startswith("shootout") and "goal" in (p.get("text") or "").lower())):
            kinds.append("goal")
        if base == League.NFL:
            if scored:
                kinds.append("score")
            if p.get("isTurnover") or "interception" in t or "fumble recovery (opponent)" in t:
                kinds.append("turnover")
            if (espn._get(p, "start", "down") == 4) and not any(x in t for x in _NOT_A_TRY):
                kinds.append("fourth_down_try")
        if base == League.NBA and scored:
            clk = _secs(espn._get(p, "clock", "displayValue"))
            if (per >= reg and clk is not None and clk <= 300 and abs(d0) <= 5) or ot:
                kinds.append("clutch_basket")
        if base == League.MLB:
            if scored and per >= 7 and abs(d0) <= 3:
                kinds.append("late_run")
            if "home run" in t:
                kinds.append("home_run")
        w0, w1 = _float(wp0), _float(wp.get(p.get("id")))
        if w0 is not None and w1 is not None and abs(w1 - w0) >= 0.15:
            kinds.append("wp_swing")
        prev = (hs, as_, wp.get(p.get("id")) or wp0, j)
        if not kinds:
            continue
        g0 = rep.state_at(j0)
        if g0 is not None and g0.phase == Phase.LIVE and garbage.is_garbage(g0) and "ot_score" not in kinds:
            continue
        if w0 is not None and w1 is not None:
            swing = abs(w1 - w0)
        else:
            g1 = rep.state_at(j)
            rem = time_left_frac(g1) if g1 is not None else 0.5
            swing = abs(model_wp(lg, d1, rem) - model_wp(lg, d0, rem))
        swing = max(0.05, min(1.0, swing))
        if out and w - out[-1][0] < 20:
            out[-1] = (out[-1][0], out[-1][1], max(out[-1][2], swing))  # the same moment (score + PAT)
            continue
        out.append((w, "+".join(kinds), swing))
    return out


def _float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _day(w: float) -> str:
    d = datetime.fromtimestamp(w, ET)
    if d.hour < 6:  # late games belong to the evening slate
        d = datetime.fromtimestamp(w - 6 * 3600, ET)
    return d.date().isoformat()


def replay_game(args) -> dict | None:
    """Poll one archived game on the global STEP grid; return feature rows + labels."""
    league_v, path, step = args
    from redzone.config import Config
    from redzone.lab.backtest import Replay
    from redzone.scoring.engine import Personal, Scorer, normalize
    lg = League(league_v)
    try:
        rep = Replay.load(lg, Path(path))
    except Exception:
        return None
    if not rep or rep.wall[0] is None:
        return None
    cfg = Config(user_path=Path("/nonexistent.yaml"))
    clk = [0.0]
    sc = Scorer(cfg, clock=lambda: clk[0])
    sc.v2 = None  # legacy score only: v2 is what we are fitting
    tr = Tracker()
    km = key_moments(rep)
    kw = [w for w, _, _ in km]
    ks = [v for _, _, v in km]
    t = math.ceil(rep.wall[0] / step) * step
    end = rep.wall[-1]
    rows = []
    while t <= end:
        clk[0] = t
        i = bisect.bisect_right(rep.wall, t) - 1
        if i >= 0:
            g = rep.state_at(i)
            if g is not None and g.phase == Phase.LIVE:
                s = sc.score(g, Personal())
                g = normalize(g)  # what the live scorer feeds features() (college halftime fix)
                tr.observe(g, t)
                h = sc.hist[g.game_id]
                x = features(g, tr, t, is_garbage=h.garbage, legacy=min(100.0, s.moment * s.stakes))
                k = bisect.bisect_right(kw, t)
                nxt = kw[k] - t if k < len(kw) else 1e9
                # Biggest win-probability swing among key moments in the next H seconds, per horizon.
                sw = []
                for H in HORIZONS:
                    e = bisect.bisect_right(kw, t + H)
                    sw.append(max(ks[k:e], default=0.0))
                rows.append((t, x, nxt, s.raw, s.score, bool(s.crunch), bool(s.garbage), bool(s.is_break),
                             tuple(sw)))
        t += step
    return dict(gid=rep.gid, league=league_v, base=BASES[league_v], day=_day(rep.wall[0]),
                matchup=rep.matchup, rows=rows, moments=km)


def build(out: Path, step: float = 20.0, leagues=("nfl", "nba", "cbb", "wcbb", "nhl", "mlb"), procs: int = 8,
          archive: Path | None = None) -> dict:
    import pickle
    from multiprocessing import Pool
    archive = archive or ROOT / "data/archive"
    jobs = [(lg, str(f), step) for lg in leagues for f in sorted((archive / lg).glob("summary_*.json"))]
    games = []
    with Pool(procs) as pool:
        for r in pool.imap_unordered(replay_game, jobs, chunksize=2):
            if r and r["rows"]:
                games.append(r)
    games.sort(key=lambda r: (r["day"], r["gid"]))
    with open(out, "wb") as fh:
        pickle.dump(dict(step=step, games=games), fh)
    return dict(games=len(games), rows=sum(len(g["rows"]) for g in games))


# --- fitting -----------------------------------------------------------------------------------

def logit_fit(X, y, l2: float = 3.0, it: int = 50, w0=None):
    import numpy as np
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1]) if w0 is None else w0.copy()
    reg = l2 * np.r_[0.0, np.ones(Xb.shape[1] - 1)]
    for _ in range(it):
        z = np.clip(Xb @ w, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = Xb.T @ (p - y) + reg * w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(reg) + 1e-9 * np.eye(len(w))
        stepv = np.linalg.solve(H, g)
        w -= stepv
        if np.abs(stepv).max() < 1e-7:
            break
    return w


def auc(s, y) -> float:
    import numpy as np
    s, y = np.asarray(s, float), np.asarray(y)
    order = np.argsort(s, kind="mergesort")
    s, y = s[order], y[order]
    # average ranks for ties
    ranks = np.empty(len(s))
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[i: j + 1] = (i + j) / 2 + 1
        i = j + 1
    npos = y.sum()
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def load_table(path: Path, horizon: int = 120):
    """Feature table -> numpy arrays. Returns dict with X, names, y, base, day, t, legacy fields."""
    import pickle

    import numpy as np
    with open(path, "rb") as fh:
        data = pickle.load(fh)
    names = sorted({k for g in data["games"] for r in g["rows"][:50] for k in r[1]})
    rows = []
    for gi, g in enumerate(data["games"]):
        for (t, x, nxt, raw, shown, crunch, garb, brk, sw) in g["rows"]:
            rows.append((gi, t, [x.get(n, 0.0) for n in names], nxt, raw, shown, crunch, garb, brk,
                         sw[HORIZONS.index(horizon)] if horizon in HORIZONS else 0.0))
    X = np.array([r[2] for r in rows], float)
    nxt = np.array([r[3] for r in rows], float)
    games = data["games"]
    return dict(
        names=names, X=X, nxt=nxt, y=(nxt <= horizon).astype(float),
        swing=np.array([r[9] for r in rows], float),
        gi=np.array([r[0] for r in rows]), t=np.array([r[1] for r in rows], float),
        raw=np.array([r[4] for r in rows], float), shown=np.array([r[5] for r in rows], float),
        crunch=np.array([r[6] for r in rows], bool), garbage=np.array([r[7] for r in rows], bool),
        brk=np.array([r[8] for r in rows], bool),
        base=np.array([games[r[0]]["base"] for r in rows]), league=np.array([games[r[0]]["league"] for r in rows]),
        day=np.array([games[r[0]]["day"] for r in rows]), games=games, step=data["step"])


def split_by_date(T, test_frac: float = 0.3):
    """Per base league: the last `test_frac` of its days are the test set."""
    import numpy as np
    test = np.zeros(len(T["day"]), bool)
    for b in set(T["base"]):
        m = T["base"] == b
        days = sorted(set(T["day"][m]))
        cut = days[int(len(days) * (1 - test_frac))] if len(days) > 3 else days[-1]
        test |= m & (T["day"] >= cut)
    return ~test, test


def fit_models(T, train, names=None, l2: float = 3.0, drop=(), target: str = "y"):
    """target "y": did a key moment happen (binary). "swing": the biggest win-probability swing
    among them (soft label 0..1): the model then predicts how much this game is about to change."""
    import numpy as np
    names = [n for n in (names or T["names"]) if n not in drop]
    cols = [T["names"].index(n) for n in names]
    models = {}
    for b in sorted(set(T["base"])):
        m = train & (T["base"] == b)
        X = T["X"][m][:, cols]
        sd = X.std(0)
        keep = sd > 1e-6
        mu = X.mean(0)
        sd = np.where(keep, sd, 1.0)
        Z = (X - mu) / sd
        Z[:, ~keep] = 0.0
        w = logit_fit(Z, T[target][m], l2=l2)
        models[b] = dict(names=names, mu=mu.tolist(), sd=sd.tolist(), w=w[1:].tolist(), b=float(w[0]),
                         keep=keep.tolist(), n=int(m.sum()), pos=float(T[target][m].mean()))
    return models


def predict(models, T, mask=None):
    import numpy as np
    p = np.zeros(len(T["y"]))
    for b, md in models.items():
        m = T["base"] == b
        if mask is not None:
            m &= mask
        cols = [T["names"].index(n) for n in md["names"]]
        Z = (T["X"][m][:, cols] - np.array(md["mu"])) / np.array(md["sd"])
        Z[:, ~np.array(md["keep"])] = 0.0
        p[m] = 1 / (1 + np.exp(-np.clip(Z @ np.array(md["w"]) + md["b"], -30, 30)))
    return p


def ranking(T, score, mask, y=None):
    """Cross-league: at each poll (same night, same instant) with 2+ live games and at least one
    about to have a key moment, did the top-scored game have it? Returns (top1 rate, polls, pairwise)."""
    import numpy as np
    idx = np.where(mask)[0]
    key = np.array([f"{T['day'][i]}|{int(T['t'][i])}" for i in idx])
    order = np.argsort(key, kind="stable")
    idx, key = idx[order], key[order]
    bounds = np.flatnonzero(key[1:] != key[:-1]) + 1
    hit = polls = 0
    conc = tot = 0.0
    Y = T["y"] if y is None else y
    for grp in np.split(idx, bounds):
        y = Y[grp]
        if len(grp) < 2 or y.sum() == 0 or y.sum() == len(y):
            continue
        s = score[grp]
        polls += 1
        hit += y[np.argmax(s)] == 1
        sp, sn = s[y == 1], s[y == 0]
        conc += (sp[:, None] > sn[None, :]).sum() + 0.5 * (sp[:, None] == sn[None, :]).sum()
        tot += len(sp) * len(sn)
    return hit / max(1, polls), polls, conc / max(1, tot)


def calibration(p, y, bins=(0, .05, .1, .2, .3, .4, .5, .6, .8, 1.01)):
    import numpy as np
    out = []
    for lo, hi in zip(bins, bins[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum():
            out.append((lo, hi, int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return out


def score_map(p_train, legacy_train, n: int = 41):
    """Monotone map from model probability to the 0-100 hype scale: probability quantiles are
    matched to the legacy score's quantiles, so the decider's thresholds keep their meaning
    (a 25 is still 'worth a break visit', 80+ is still rare) while the ORDER is the model's."""
    import numpy as np
    qs = np.linspace(0, 1, n)
    px = np.quantile(p_train, qs)
    sy = np.quantile(legacy_train, qs)
    # strictly increasing x for interpolation
    for i in range(1, n):
        if px[i] <= px[i - 1]:
            px[i] = px[i - 1] + 1e-9
        if sy[i] < sy[i - 1]:
            sy[i] = sy[i - 1]
    return dict(p=px.round(6).tolist(), s=sy.round(3).tolist())
