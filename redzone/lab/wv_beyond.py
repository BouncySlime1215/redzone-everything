"""[EXP-1723, WV loop cycle 3] Watch Value as the windowed oracle's beyond-window value.

The frozen windowed oracle (oracle_window.solve_window, grader v31) values the end of its look-ahead window with
`oracle_window.beyond_value` (v1 placeholder: past mean reward). This module never edits that file: it builds a
replacement with the same signature and swaps it in only for the duration of one solve (`patched_beyond`).

  WV grid     WV[g, k] = the Watch Value of game g's last play the feed has delivered by screen step k
              (play wall <= screen time - the game's screen lag), 0 before the first play and after the last.
  IsoMap      monotone map WV -> realized mean combined reward per step over the next `horizon` steps,
              fit on SEEN nights only (forward chain), refit after each night.
  beyond      at decision step k the window covers k .. e-1 (e = k + W, W as in solve_window), so the beyond
              value is IsoMap(WV[:, e-1]) * horizon: only what the feed has delivered, never the hidden future.

Before any night is seen there is no map and the v1 placeholder is used (B1/B2 fall back to B0).
personal-use, not for commercial release.
"""
from __future__ import annotations

import contextlib
from typing import Callable

import numpy as np

from redzone.lab import oracle_window as OW
from redzone.lab.oracle import lag_of

EVERY = 6          # iso training pairs: one live step in six (30 s), adjacent steps are near duplicates


def wv_grid(gids: list[str], plays: dict[str, tuple[np.ndarray, np.ndarray]], steps: np.ndarray, lag) -> np.ndarray:
    """plays[gid] = (wall times sorted, WV per play). Returns WV[G, K] as delivered at each screen step, with NaN
    before the game's first play and after its last (no value: the game has not started or is over)."""
    G, K = len(gids), len(steps)
    out = np.full((G, K), np.nan)
    for g, gid in enumerate(gids):
        if gid not in plays:
            continue
        wall, wv = plays[gid]
        if len(wall) == 0:
            continue
        real = steps - float(lag_of(lag, gid))                 # the real time each screen step shows
        j = np.searchsorted(wall, real, side="right") - 1      # last play that has happened by then
        ok = (j >= 0) & (real <= wall[-1])
        out[g, ok] = wv[j[ok]]
    return out


def future_mean(R: np.ndarray, horizon: int) -> np.ndarray:
    """F[g, k] = mean of R[g, k+1 .. k+horizon] (shorter at the night's end; 0 past it)."""
    G, K = R.shape
    c = np.concatenate([np.zeros((G, 1)), np.cumsum(R, axis=1)], axis=1)
    k = np.arange(K)
    hi = np.minimum(K, k + 1 + horizon)
    n = np.maximum(1, hi - (k + 1))
    return (c[:, hi] - c[:, np.minimum(K, k + 1)]) / n


def iso_pairs(WV: np.ndarray, R: np.ndarray, horizon: int, every: int = EVERY) -> tuple[np.ndarray, np.ndarray]:
    F = future_mean(R, horizon)
    m = np.isfinite(WV)
    m[:, np.arange(WV.shape[1]) % every != 0] = False
    return WV[m], F[m]


class IsoMap:
    """WV -> reward per step, monotone non-decreasing (sklearn isotonic), clipped outside the seen range."""

    def __init__(self):
        self.x: list[np.ndarray] = []
        self.y: list[np.ndarray] = []
        self.f = None

    def add(self, x: np.ndarray, y: np.ndarray) -> None:
        self.x.append(np.asarray(x, float))
        self.y.append(np.asarray(y, float))

    def refit(self) -> None:
        x, y = np.concatenate(self.x) if self.x else np.zeros(0), np.concatenate(self.y) if self.y else np.zeros(0)
        if len(x) < 20:
            self.f = None
            return
        self.f = pava(x, y)

    def __call__(self, wv: np.ndarray) -> np.ndarray:
        v = np.asarray(wv, float)
        out = np.zeros_like(v)
        ok = np.isfinite(v)
        if self.f is not None and ok.any():
            xs, ys = self.f
            out[ok] = np.interp(v[ok], xs, ys)          # linear between knots, clipped outside (like sklearn's clip)
        return out


def pava(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Isotonic (non-decreasing) least-squares fit by pool-adjacent-violators, numpy only (CI has no sklearn).
    Returns knots (x per unique value, fitted y)."""
    o = np.argsort(x, kind="stable")
    xs, inv = np.unique(x[o], return_inverse=True)
    w = np.bincount(inv).astype(float)
    m = np.bincount(inv, weights=y[o]) / w
    val, wt, cnt = [], [], []
    for mi, wi in zip(m, w):
        val.append(mi); wt.append(wi); cnt.append(1)
        while len(val) > 1 and val[-2] > val[-1]:
            v2, w2, c2 = val.pop(), wt.pop(), cnt.pop()
            val[-1] = (val[-1] * wt[-1] + v2 * w2) / (wt[-1] + w2)
            wt[-1] += w2
            cnt[-1] += c2
    return xs, np.repeat(val, cnt)


def window_of(n, feed_lag: float) -> int:
    """W exactly as solve_window computes it (min over the night's games)."""
    return min(OW.window_steps(lag_of(n.lag, r.gid), feed_lag, n.tune_s,
                               OW.RESUME_LEAD.get(getattr(r, "league", None), 0.0)) for r in n.replays)


def beyond_from_wv(WV: np.ndarray, iso: IsoMap, W: int) -> Callable:
    """A drop-in for oracle_window.beyond_value(R, k, horizon, memory): WV at the window's last known step."""
    K = WV.shape[1]

    def beyond(R: np.ndarray, k: int, horizon: int, memory: int = 120) -> np.ndarray:
        e = min(K, k + W)
        return iso(WV[:, max(0, e - 1)]) * horizon
    return beyond


@contextlib.contextmanager
def patched_beyond(fn: Callable | None):
    """Swap oracle_window.beyond_value for one solve (None keeps the v1 placeholder). The frozen file is never
    edited; solve_window looks the name up at call time, so the swap is exactly the beyond value and nothing else."""
    if fn is None:
        yield
        return
    old = OW.beyond_value
    OW.beyond_value = fn
    try:
        yield
    finally:
        OW.beyond_value = old


def solve_with(n, feed_lag: float, tab: dict, fn: Callable | None, objective: str = "combined"):
    with patched_beyond(fn):
        return OW.solve_window(n, feed_lag, objective, tab)


def step_weights(ledger_w: np.ndarray, t0: float, step_s: float, wall_screen: np.ndarray) -> np.ndarray:
    """Per-play training weight from mistake_ledger.sample_weights (per screen step): the weight of the step the
    play is on screen at (outside the night's steps: 1)."""
    k = np.round((np.asarray(wall_screen, float) - t0) / step_s).astype(int)
    w = np.ones(len(k))
    ok = (k >= 0) & (k < len(ledger_w))
    w[ok] = ledger_w[k[ok]]
    return w


def pooled_pct(arm: np.ndarray, full: np.ndarray, seed: int = 1723, draws: int = 2000) -> dict:
    """Pooled % of full hindsight (sum arm / sum full) with a night-bootstrap 95% CI."""
    arm, full = np.asarray(arm, float), np.asarray(full, float)
    pct = 100.0 * arm.sum() / full.sum() if full.sum() > 0 else float("nan")
    rng = np.random.default_rng(seed)
    n = len(arm)
    bs = []
    for _ in range(draws if n > 1 else 0):
        i = rng.integers(0, n, n)
        if full[i].sum() > 0:
            bs.append(100.0 * arm[i].sum() / full[i].sum())
    ci = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))] if bs else [pct, pct]
    return {"pct": round(pct, 2), "ci": [round(c, 2) for c in ci], "nights": n}


def delta_ci(a: np.ndarray, b: np.ndarray, full: np.ndarray, seed: int = 1723, draws: int = 2000) -> dict:
    """(a - b) in points of % of full, same bootstrap draws for both arms."""
    a, b, full = (np.asarray(x, float) for x in (a, b, full))
    d = 100.0 * (a.sum() - b.sum()) / full.sum() if full.sum() > 0 else float("nan")
    rng = np.random.default_rng(seed)
    n = len(a)
    bs = []
    for _ in range(draws if n > 1 else 0):
        i = rng.integers(0, n, n)
        if full[i].sum() > 0:
            bs.append(100.0 * (a[i].sum() - b[i].sum()) / full[i].sum())
    ci = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))] if bs else [d, d]
    return {"delta": round(d, 2), "ci": [round(c, 2) for c in ci]}
