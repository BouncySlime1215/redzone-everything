"""Hindsight oracle: the best schedule anyone could have run on an archived night (offline only).

Knowing every play of every game in advance, which game should have been on screen when? A dynamic program
over 5 s steps, state = the game on screen, under the same hard rules the app lives by:

- every switch leaves on a dead ball (no mid-play cut, `sportsgrade._clean_cut` and `cutgrade._dead_at`) and
  lands on one (no mid-play entry), with the picture black for `tune_s`;
- screens run `lag` s behind real life per game (the sim's own `rep.lag_true`, buffer included);
- no rewinds and no replays (v1: the live-only ceiling).

Its switches are graded by the SAME `sportsgrade.grade` as the app, so every scorecard line gets a ceiling
and `oracle_gap` = ceiling - what the app did on the same night. One objective per North Star line
(`OBJECTIVES`): the ceiling of a line is the oracle that maximizes that line.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from redzone.lab import cutgrade, sportsgrade
from redzone.lab.backtest import SwitchLog, lag_of
from redzone.models import League
from redzone.sources import espn
from redzone.sources.espn import Stoppage

STEP_S = 5.0
SWITCH_PENALTY = 1e-3      # break ties toward fewer switches

# weights: big play live, crunch play live, your-team crunch play live (on top of crunch: an L1 rule, so it
# dominates every objective), dead-air step, and a cost per switch (the combined schedule must stay
# watchable: without one it flips every 5 s to dodge breaks)
OBJECTIVES = {
    "big_live": (1.0, 0.01, 100.0, 0.0, SWITCH_PENALTY),
    "crunch_live": (0.01, 1.0, 100.0, 0.0, SWITCH_PENALTY),
    "dead_air": (0.001, 0.001, 100.0, 0.1, 0.5),   # a switch costs 5 dead steps (25 s of breaks)
    "combined": (3.0, 1.0, 100.0, 0.02, 0.5),
}


@dataclass
class Night:
    replays: list
    mine: set
    lag: object            # float or backtest.Lags
    tune_s: float
    t0: float
    t1: float


def _clean_leave(league: League, wall: list[float], plays: list[dict], st: float) -> bool:
    """sportsgrade._clean_cut with a binary search (same rule)."""
    i = cutgrade._idx(wall, st)
    if i == len(wall) - 1 or (i >= 0 and st > wall[-1]):
        return True
    if league in sportsgrade.CONTINUOUS:
        if i < 0:
            return True
        last = plays[i]
        nxt = next((wall[j] for j in range(i + 1, len(wall))
                    if espn.stoppage_for(league, plays[j]) == Stoppage.NONE
                    and not sportsgrade._is_marker(espn._kind(league, plays[j]), league)), math.inf)
        dead = espn.stoppage_for(league, last) != Stoppage.NONE or (
            bool(last.get("scoringPlay")) and st - wall[i] <= 4.0)
        return bool(dead and st < nxt - sportsgrade.RESUME_LEAD[league])
    plen = sportsgrade.PLAY_LEN[league]
    lo = max(0, i - 3)
    for w, p in zip(wall[lo:i + 4], plays[lo:i + 4]):
        if sportsgrade._marker(sportsgrade._type(p)) and not p.get("scoringPlay"):
            continue
        if w - plen <= st <= w:
            return False
    return True


def tables(n: Night, steps: np.ndarray) -> dict:
    """Per game x step: rewards and where a switch may leave / land."""
    G, K = len(n.replays), len(steps)
    big, crunch, mine_c = np.zeros((G, K)), np.zeros((G, K)), np.zeros((G, K))
    dead = np.zeros((G, K))
    leave_ok, land_ok = np.zeros((G, K), bool), np.zeros((G, K), bool)
    for g, r in enumerate(n.replays):
        lg, lag = r.league, lag_of(n.lag, r.gid)
        mine = bool({r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]} & n.mine)
        prev = None
        plen = sportsgrade.PLAY_LEN[lg]
        for w, p in zip(r.wall, r.plays):
            k = int((w + lag - plen - n.t0) // STEP_S)
            if 0 <= k < K:
                if sportsgrade.is_big(lg, p, prev):
                    big[g, k] += 1
                if sportsgrade.is_crunch(lg, p) and not sportsgrade._marker(sportsgrade._type(p)):
                    crunch[g, k] += 1
                    mine_c[g, k] += mine
            prev = p
        for k, t in enumerate(steps):
            st = t - lag
            leave_ok[g, k] = _clean_leave(lg, r.wall, r.plays, st) and cutgrade._dead_at(lg, r.wall, r.plays, st)[0]
            sl = t + n.tune_s - lag
            land_ok[g, k] = (sl < r.wall[0] or sl > r.wall[-1]) or cutgrade._dead_at(lg, r.wall, r.plays, sl)[0]
            if r.wall[0] <= st <= r.wall[-1]:
                i = cutgrade._idx(r.wall, st)
                dead[g, k] = espn.stoppage_for(lg, r.plays[max(0, i)]) in sportsgrade.REAL_BREAKS
            else:
                dead[g, k] = 1.0        # nothing on (before the start / after the end): no hiding from dead air
    return dict(big=big, crunch=crunch, mine=mine_c, dead=dead, leave=leave_ok, land=land_ok)


def solve(n: Night, objective: str = "combined", tab: dict | None = None) -> list[SwitchLog]:
    """The oracle's switches for one night (SwitchLog, time order), maximizing OBJECTIVES[objective]."""
    wb, wc, wm, wd, ws = OBJECTIVES[objective]
    steps = np.arange(n.t0, n.t1, STEP_S)
    tab = tab or tables(n, steps)
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    G, K = R.shape
    blk = max(1, int(math.ceil(n.tune_s / STEP_S)))       # steps the picture is black after a switch
    NEG = -1e18
    V = np.full((K + blk + 1, G), NEG)
    back = np.full((K + blk + 1, G), -2, np.int64)        # -1: stayed, >=0: switched from that game at k-blk
    # the night's first pick lands at step blk (black before), where it lands clean
    for g in range(G):
        if tab["land"][g, 0]:
            V[blk, g] = 0.0
            back[blk, g] = -3
    for k in range(blk, K):
        v = V[k]
        if not np.isfinite(v).any() or v.max() <= NEG / 2:
            continue
        # stay: collect this step's reward
        stay = v + R[:, k]
        better = stay > V[k + 1]
        V[k + 1] = np.where(better, stay, V[k + 1])
        back[k + 1] = np.where(better, -1, back[k + 1])
        # switch at step k from a (clean leave) to b (clean land): black for blk steps, no reward
        if k + blk <= K:
            can_leave = tab["leave"][:, k] & (v > NEG / 2)
            if can_leave.any():
                a = int(np.argmax(np.where(can_leave, v, NEG)))
                best_from = v[a] - ws
                tgt = tab["land"][:, k]
                for b in np.nonzero(tgt)[0]:
                    src = a if b != a else None
                    if src is None:
                        alt = np.where(can_leave, v, NEG)
                        alt[b] = NEG
                        if alt.max() <= NEG / 2:
                            continue
                        src = int(np.argmax(alt))
                        val = v[src] - ws
                    else:
                        val = best_from
                    if val > V[k + blk, b]:
                        V[k + blk, b] = val
                        back[k + blk, b] = src
    # backtrack from the best end state
    k, g = K, int(np.argmax(V[K]))
    out = []
    while k > blk or (k == blk and back[k, g] != -3):
        b = back[k, g]
        if b == -1:
            k -= 1
        elif b >= 0:
            out.append(SwitchLog(float(n.t0 + (k - blk) * STEP_S), n.replays[b].gid, n.replays[g].gid, "oracle", objective))
            k, g = k - blk, int(b)
        else:
            break
    out.append(SwitchLog(float(n.t0), None, n.replays[g].gid, "start", objective))
    return out[::-1]


def grade(n: Night, switches) -> sportsgrade.Card:
    return sportsgrade.grade(n.replays, switches, n.mine, n.lag, n.tune_s, n.t0, n.t1, set())
