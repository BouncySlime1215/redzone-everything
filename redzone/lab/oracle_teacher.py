"""[time-shift buffer, T3] The windowed-hindsight oracle TEACHER (Nick 9:10 AM Nov 5, part C; owner T3).

`oracle_window.solve_window` (lane 1) plans with exact DP over only what the buffer reveals and values the end of
the window with the past mean (a placeholder). This module adds the two parts Nick allowed to be learned and
keeps everything inside the window exact:

1. **Learned beyond-window value** (`ValueModel`): per game, the expected reward of the next H steps after the
   window, from the game's state at the window's last revealed step (clock, period, margin, close/late,
   high leverage, pre/post game, recent reward rates, your team). One ridge regression per reward component
   (big, crunch, your-team crunch, dead air) and league, so one fit serves every objective in
   `oracle.OBJECTIVES`. Trained on TRAIN nights only, its horizon picked on VALID nights; locked nights are
   graded, never fitted on. Pure numpy, coefficients only (no raw data) in its JSON.
2. **Play-start backoff** (`solve_window_cue`): a switch may land on the target's last dead ball up to
   `backoff_s` behind (the hidden stick seeks back), so a big play is shown from its start instead of waiting
   for the next break. State = (game, offset), as `oracle.solve_cue`; the window is never widened by the
   offset (conservative: it never reads what the buffer has not revealed).

Taste (Watch Value v2 on the real attention labels) plugs in through the same `value_fn(k, e)` hook once T8's
model lands; it is not in this first pass.

Rule #1 holds by construction: every switch leaves on a clean leave and lands on a dead ball (tab["land"]
or the cue offset of `oracle.cue_steps`), graded by the app's own `sportsgrade.grade`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from redzone.grading import RESUME_LEAD
from redzone.lab import leverage, oracle as O
from redzone.lab import oracle_window as OW
from redzone.lab.backtest import SwitchLog, lag_of
from redzone.models import PERIOD_S, League
from redzone.sources import espn

COMPONENTS = ("big", "crunch", "mine", "dead")
PERIODS = {League.NBA: 4, League.NFL: 4, League.NHL: 3, League.CBB: 2}
MARGIN_SCALE = {League.NBA: 10.0, League.NFL: 8.0, League.NHL: 1.5, League.CBB: 10.0}
FEATURES = ("bias", "pre", "post", "frac", "frac2", "late", "margin", "close", "late_close", "high_lev",
            "big24", "crunch24", "dead24", "big120", "crunch120", "dead120", "mine")


# ------------------------------------------------------------------------------------------------ features
def _play_state(league: League, p: dict) -> tuple[float, float, float]:
    """(fraction of regulation elapsed, |margin| in league units, high leverage) of one ESPN play."""
    per = espn._int(espn._get(p, "period", "number")) or 1
    ps = PERIOD_S.get(league) or 900
    npers = PERIODS.get(league, 4)
    left = leverage._left_s(league, p)
    left = ps / 2 if left is None else min(max(left, 0.0), ps)
    frac = min(1.25, ((per - 1) * ps + (ps - left)) / (npers * ps))
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    m = abs((hs or 0) - (as_ or 0)) / MARGIN_SCALE.get(league, 8.0)
    return frac, min(m, 3.0), float(leverage.high_leverage(league, p))


def state_features(n: O.Night, tab: dict) -> np.ndarray:
    """F[g, k, f]: the game state SHOWN at screen step k (the last play whose start is on screen by then) and
    the reward rates of the 24 / 120 steps before k. Everything at step k is known once step k is revealed."""
    G, K = tab["big"].shape
    steps = n.t0 + np.arange(K) * O.STEP_S
    F = np.zeros((G, K, len(FEATURES)))
    F[:, :, 0] = 1.0
    for g, r in enumerate(n.replays):
        lg, lag = r.league, lag_of(n.lag, r.gid)
        st = steps - lag                                         # real time shown at each step
        wall = np.asarray(r.wall, float)
        F[g, :, 1] = st < wall[0]
        F[g, :, 2] = st > wall[-1]
        ps = np.array([_play_state(lg, p) for p in r.plays]) if r.plays else np.zeros((1, 3))
        idx = np.clip(np.searchsorted(wall, st, side="right") - 1, 0, len(ps) - 1)
        frac, marg, hl = ps[idx, 0], ps[idx, 1], ps[idx, 2]
        frac = np.where(st < wall[0], 0.0, np.where(st > wall[-1], 1.0, frac))
        F[g, :, 3], F[g, :, 4] = frac, frac ** 2
        F[g, :, 5] = frac > 0.75
        F[g, :, 6] = marg
        thr = leverage.CLOSE_LATE.get(lg, (0, 0, 1))[2] / MARGIN_SCALE.get(lg, 8.0)
        F[g, :, 7] = marg <= thr
        F[g, :, 8] = F[g, :, 5] * F[g, :, 7]
        F[g, :, 9] = hl * (1 - F[g, :, 2])
        mine = bool({r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]} & n.mine) if hasattr(r, "matchup") else False
        F[g, :, 16] = float(mine)
    for j, (comp, w) in enumerate((("big", 24), ("crunch", 24), ("dead", 24), ("big", 120), ("crunch", 120),
                                   ("dead", 120))):
        c = np.concatenate([np.zeros((G, 1)), np.cumsum(tab[comp], axis=1)], axis=1)     # c[:, k] = sum[:k]
        lo = np.maximum(0, np.arange(K) - w)
        F[:, :, 10 + j] = (c[:, np.arange(K)] - c[:, lo]) / w
    return F


def future_sums(tab: dict, H: int) -> np.ndarray:
    """Y[g, k, c]: component c summed over steps k .. k+H-1 (0 past the night's end)."""
    G, K = tab["big"].shape
    Y = np.zeros((G, K, len(COMPONENTS)))
    for j, comp in enumerate(COMPONENTS):
        c = np.concatenate([np.zeros((G, 1)), np.cumsum(tab[comp], axis=1)], axis=1)
        hi = np.minimum(K, np.arange(K) + H)
        Y[:, :, j] = c[:, hi] - c[:, np.arange(K)]
    return Y


# ------------------------------------------------------------------------------------------------ the model
@dataclass
class ValueModel:
    """Per league: ridge weights W[f, c] predicting the next-H-step sum of each reward component."""
    horizon: int                                   # H, steps
    weights: dict = field(default_factory=dict)    # league -> list[list[float]] (F x C)
    meta: dict = field(default_factory=dict)


    def predict(self, league: League, X: np.ndarray) -> np.ndarray:
        W = np.asarray(self.weights.get(league.value) or self.weights["_all"])
        Y = X @ W
        Y[..., :3] = np.clip(Y[..., :3], 0.0, None)
        Y[..., 3] = np.clip(Y[..., 3], 0.0, float(self.horizon))
        return Y

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps({"horizon": self.horizon, "features": FEATURES,
                                          "components": COMPONENTS, "weights": self.weights,
                                          "meta": self.meta}, indent=1))

    @classmethod
    def load(cls, path: Path) -> "ValueModel":
        d = json.loads(Path(path).read_text())
        assert tuple(d["features"]) == FEATURES, "feature set changed: refit"
        return cls(d["horizon"], d["weights"], d.get("meta", {}))


def ridge(X: np.ndarray, Y: np.ndarray, alpha: float = 1.0) -> np.ndarray:
    A = X.T @ X + alpha * np.eye(X.shape[1])
    A[0, 0] -= alpha                               # no penalty on the bias
    return np.linalg.solve(A, X.T @ Y)


def fit(samples: dict[str, tuple[list, list]], horizon: int, alpha: float = 1.0) -> ValueModel:
    """samples: league -> (list of X blocks, list of Y blocks). One ridge per league plus a pooled one."""
    w, meta = {}, {"rows": {}}
    allx, ally = [], []
    for lg, (xs, ys) in samples.items():
        X, Y = np.concatenate(xs), np.concatenate(ys)
        w[lg] = ridge(X, Y, alpha).tolist()
        meta["rows"][lg] = int(len(X))
        allx.append(X)
        ally.append(Y)
    w["_all"] = ridge(np.concatenate(allx), np.concatenate(ally), alpha).tolist()
    return ValueModel(horizon, w, meta)


def training_rows(n: O.Night, tab: dict, H: int, every: int = 3) -> tuple[np.ndarray, np.ndarray]:
    """(X, Y) for one night: every `every`-th step of every game, features at k-1, target from k (the
    window's end e: state at the last revealed step e-1, value from e on)."""
    F = state_features(n, tab)
    Y = future_sums(tab, H)
    ks = np.arange(1, F.shape[1], every)
    return F[:, ks - 1].reshape(-1, F.shape[2]), Y[:, ks].reshape(-1, Y.shape[2])


def value_fn(model: ValueModel, n: O.Night, tab: dict, objective: str = "combined"):
    """The `value_fn(k, e)` hook for `oracle_window.solve_window` / `solve_window_cue`: per game, the learned
    value of the H steps after the window's end e, from the state at e-1 (revealed). 0 once the night is over.
    The your-team component is NOT used beyond the window (selected on valid nights, EXP-120 amendment: a learned
    your-team prior parks the screen on WSH/UNC waiting for crunch that may never come; stakes are T8's taste
    term). Your-team crunch inside the window stays exact."""
    wb, wc, _wm, wd, _ws = O.OBJECTIVES[objective]
    F = state_features(n, tab)
    K = F.shape[1]
    comb = np.array([wb, wc, 0.0, -wd])
    leagues = [r.league for r in n.replays]
    pred = np.zeros((len(leagues), K))
    for g, lg in enumerate(leagues):
        pred[g] = model.predict(lg, F[g]) @ comb

    def f(k: int, e: int) -> np.ndarray:
        if e >= K:
            return np.zeros(len(leagues))
        return pred[:, max(0, e - 1)]
    return f


# ------------------------------------------------------------------------------------------ backoff solver
def solve_window_cue(n: O.Night, feed_lag: float, objective: str = "combined", tab: dict | None = None,
                     horizon_s: float = 120.0, value_fn=None, backoff_s: float = 15.0) -> list[SwitchLog]:
    """`oracle_window.solve_window` plus play-start backoff: a switch at step k lands on target b's last dead ball up
    to `backoff_s` back (offset o = cue_steps), and b then runs o steps further behind until the next switch.
    Exact DP over the revealed window on state (game, offset); `value_fn(k, e)` (default: the past-mean
    placeholder) values the window's end. backoff_s = 0 is solve_window."""
    wb, wc, wm, wd, ws = O.OBJECTIVES[objective]
    steps = np.arange(n.t0, n.t1, O.STEP_S)
    tab = tab or O.tables(n, steps)
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    G, K = R.shape
    blk = max(1, int(math.ceil(n.tune_s / O.STEP_S)))
    W = min(OW.window_steps(lag_of(n.lag, r.gid), feed_lag, n.tune_s, RESUME_LEAD.get(getattr(r, "league", None), 0.0))
            for r in n.replays)
    k0 = 0                                         # never start mid-play (as oracle_window.solve_window)
    while k0 < K and not tab["land"][:, k0].any():
        k0 += 1
    if k0 >= K:
        return []
    if W < blk + 1:                                # nothing revealed in time: no switches (oracle_window rule)
        g0 = int(np.flatnonzero(tab["land"][:, k0])[0])
        return [SwitchLog(float(steps[k0]), None, n.replays[g0].gid, "start", objective)]
    hz = int(horizon_s // O.STEP_S)
    vf = value_fn or (lambda k, e: OW.beyond_value(R, k, hz))
    M = int(backoff_s // O.STEP_S)
    Oo = M + 1
    cue = O.cue_steps(tab, backoff_s)
    Rs = np.zeros((G, Oo, K))
    Ls = np.zeros((G, Oo, K), bool)
    for o in range(Oo):
        Rs[:, o, o:] = R[:, :K - o] if o else R
        Ls[:, o, o:] = tab["leave"][:, :K - o] if o else tab["leave"]
    NEG = -1e18
    gi = np.arange(G)
    cue_c = np.clip(cue, 0, None)

    def plan(k: int) -> np.ndarray:
        e = min(K, k + W)
        Vn = vf(k, e) if e < K else np.zeros(G)
        V = np.zeros((e - k + blk + 1, G, Oo))
        V[e - k:] = Vn[None, :, None]
        for j in range(e - k - 1, -1, -1):
            kk = k + j
            v = Rs[:, :, kk] + V[j + 1]
            tgt = np.where(cue[:, kk] >= 0, V[j + blk, gi, cue_c[:, kk]] - ws, NEG)
            order = np.argsort(-tgt)
            b1, b2 = tgt[order[0]], (tgt[order[1]] if G > 1 else NEG)
            alt = np.where(gi == order[0], b2, b1)
            V[j] = np.where(Ls[:, :, kk] & (alt[:, None] > v), alt[:, None], v)
        return V

    out: list[SwitchLog] = []
    k = k0 + blk
    V = plan(k)
    first = [g for g in range(G) if tab["land"][g, k0]]
    cur, o = max(first, key=lambda g: V[0, g, 0]), 0
    out.append(SwitchLog(float(steps[k0]), None, n.replays[cur].gid, "start", objective))
    while k < K:
        V = plan(k)
        stay = Rs[cur, o, k] + V[1, cur, o]
        cand = np.where((cue[:, k] >= 0) & (gi != cur), V[blk, gi, cue_c[:, k]] - ws, NEG) if k + blk <= K \
            else np.full(G, NEG)
        b = int(np.argmax(cand))
        if not (Ls[cur, o, k] and cand[b] > stay):
            k += 1
            continue
        ob = int(cue[b, k])
        out.append(SwitchLog(float(steps[k]), n.replays[cur].gid, n.replays[b].gid, "oracle_window_cue", objective,
                             offset=float(ob * O.STEP_S)))
        cur, o = b, ob
        k += blk
    return out
