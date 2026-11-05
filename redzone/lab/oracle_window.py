"""[time-shift buffer] The windowed-hindsight oracle (Nick 9:10 AM Nov 5): exact DP over only what the buffer
reveals, past-only value beyond the window. Lives next to the frozen grader `oracle.py` (integrator-owned,
docs/grading.md rule 1) and reuses its tables, objectives and moves without changing them.

Owner: lane 1 (solve_window); the learned beyond-window value is the oracle-teacher thread's (EXP-120..124).
"""
from __future__ import annotations

import math

import numpy as np

from redzone.grading import RESUME_LEAD
from redzone.lab.backtest import SwitchLog, lag_of
from redzone.lab.oracle import OBJECTIVES, STEP_S, Night, tables


# ---------------------------------------------------------------------------------- [time-shift buffer] windowed
def window_steps(lag: float, feed_lag: float, tune_s: float, lookahead_s: float = 0.0) -> int:
    """How many 5 s steps of the screen's future a decision can see: the screen runs `lag` s behind real life,
    a play is known `feed_lag` s after it happened, and a switch needs `tune_s` to land. The buffer's look-ahead
    (Nick 9:10 AM Nov 5): buffer - feed lag - tune/seek. `lookahead_s` is reserved for what the grader's
    leave/land rules themselves peek at past a cell (grading.RESUME_LEAD: NBA 26 s, NHL 5 s; found by #328), so a
    cell is only used once everything it reads has been delivered."""
    return max(0, int((lag - feed_lag - tune_s - lookahead_s) // STEP_S))


def beyond_value(R: np.ndarray, k: int, horizon: int, memory: int = 120) -> np.ndarray:
    """[learned part, v1 placeholder] Value per game beyond the window: its mean reward per step over the last
    `memory` KNOWN steps (10 min), times `horizon` steps. Uses only the past, never the hidden future; the
    Watch Value v2 model replaces it."""
    lo = max(0, k - memory)
    if k <= lo:
        return np.zeros(R.shape[0])
    return R[:, lo:k].mean(axis=1) * horizon


def solve_window(n: Night, feed_lag: float, objective: str = "combined", tab: dict | None = None,
                 horizon_s: float = 120.0, value_fn=None) -> list[SwitchLog]:
    """[time-shift buffer] The windowed-hindsight oracle: at every 5 s step it plans with exact DP over only
    what the buffer has revealed (W = window_steps(lag, feed_lag, tune_s) steps of the screen's future, plays
    the feed has delivered), values the end of the window with `beyond_value` (the past only), takes the first
    move and replans. Same moves, rules and grader as `solve` (switch on a clean leave, land on a dead ball,
    black for tune_s): the ceiling a real buffered app could reach with a perfect in-window optimizer.
    `solve` (full hindsight) is the ceiling of everything; % oracle = this / that.
    `value_fn(k, e)` -> per-game value past the window's end e, from what is revealed by then (T3's learned value,
    `oracle_teacher.value_fn`); default `beyond_value`, the past-mean placeholder. 0 after the night ends."""
    wb, wc, wm, wd, ws = OBJECTIVES[objective]
    steps = np.arange(n.t0, n.t1, STEP_S)
    tab = tab or tables(n, steps)
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    G, K = R.shape
    blk = max(1, int(math.ceil(n.tune_s / STEP_S)))
    W = min(window_steps(lag_of(n.lag, r.gid), feed_lag, n.tune_s, RESUME_LEAD.get(getattr(r, "league", None), 0.0))
            for r in n.replays)
    k0 = 0                                         # never start mid-play: wait for a step where some game is dead
    while k0 < K and not tab["land"][:, k0].any():
        k0 += 1
    if k0 >= K:
        return []
    if W < blk + 1:
        # The feed is too slow for this buffer: nothing is revealed in time to verify a clean leave and a dead-ball
        # landing ahead, so the honest oracle never switches (the B=0 control): it starts on the first game listed
        # that is landable at the first dead step and stays there.
        g0 = int(np.flatnonzero(tab["land"][:, k0])[0])
        return [SwitchLog(float(steps[k0]), None, n.replays[g0].gid, "start", objective)]
    hz = int(horizon_s // STEP_S)
    NEG = -1e18
    gi = np.arange(G)

    def plan(k: int) -> np.ndarray:
        """V[j, g]: the best value from step k + j on game g (deciding then), known steps only."""
        e = min(K, k + W)                          # steps k .. e-1 are known
        Vn = (value_fn(k, e) if value_fn else beyond_value(R, k, hz)) if e < K else np.zeros(G)  # 0 after the night
        V = np.zeros((e - k + blk + 1, G))
        V[e - k:] = Vn                             # past the window: the beyond value (also while black)
        for j in range(e - k - 1, -1, -1):         # backward DP over the window, j = step k + j
            kk = k + j
            v = R[:, kk] + V[j + 1]
            tgt = np.where(tab["land"][:, kk], V[j + blk] - ws, NEG)
            order = np.argsort(-tgt)
            b1, b2 = tgt[order[0]], (tgt[order[1]] if G > 1 else NEG)
            alt = np.where(gi == order[0], b2, b1)
            v = np.where(tab["leave"][:, kk] & (alt > v), alt, v)
            V[j] = v
        return V

    out: list[SwitchLog] = []
    k = k0 + blk
    V = plan(k)
    first = [g for g in range(G) if tab["land"][g, k0]]
    cur = max(first, key=lambda g: V[0, g])        # the first pick lands at step k0 + blk, like `solve`
    out.append(SwitchLog(float(steps[k0]), None, n.replays[cur].gid, "start", objective))
    while k < K:
        V = plan(k)
        stay = R[cur, k] + V[1, cur]
        cand = np.where(tab["land"][:, k] & (gi != cur), V[blk] - ws, NEG)
        b = int(np.argmax(cand))
        if not (tab["leave"][cur, k] and k + blk <= K and cand[b] > stay):
            k += 1
            continue
        out.append(SwitchLog(float(steps[k]), n.replays[cur].gid, n.replays[b].gid, "oracle_window", objective))
        cur = b
        k += blk
    return out
