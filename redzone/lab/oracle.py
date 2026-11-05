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
from redzone.grading import clean_leave as _clean_leave  # noqa: F401  [one brain] moved, step 3
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


def cue_steps(tab: dict, max_cue_s: float) -> np.ndarray:
    """[possession-start entry] Per game x step: how many steps behind live a switch made at that step lands
    when the hidden stick cues to the target's LAST dead ball (0 = it is dead at the live edge; -1 = no dead
    ball within max_cue_s). Landing o steps back shows what the live landing at step k - o would have shown."""
    land = tab["land"]
    G, K = land.shape
    M = int(max_cue_s // STEP_S)
    out = np.full((G, K), -1, np.int64)
    for o in range(M, -1, -1):              # smallest offset wins: overwrite from the largest down
        ok = np.zeros((G, K), bool)
        ok[:, o:] = land[:, :K - o] if o else land
        out[ok] = o
    return out


def solve_cue(n: Night, objective: str = "combined", tab: dict | None = None,
              max_cue_s: float = 120.0) -> list[SwitchLog]:
    """[possession-start entry] The oracle when every switch lands on the target's last dead ball: the
    picture comes up `offset` s behind live (SwitchLog.offset), never mid-play, and never waits for the next
    break. State = (game on screen, how far behind live it runs); watching keeps the offset, a switch leaves
    on a dead ball of the picture as shown (offset included) and resets the offset to the new cue. With
    max_cue_s = 0 it is `solve`."""
    wb, wc, wm, wd, ws = OBJECTIVES[objective]
    steps = np.arange(n.t0, n.t1, STEP_S)
    tab = tab or tables(n, steps)
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    G, K = R.shape
    M = int(max_cue_s // STEP_S)
    O = M + 1
    cue = cue_steps(tab, max_cue_s)
    blk = max(1, int(math.ceil(n.tune_s / STEP_S)))
    NEG = -1e18
    # shifted tables: Rs[g, o, k] = R[g, k - o] (the picture o steps behind), Ls likewise for leave
    Rs = np.zeros((G, O, K))
    Ls = np.zeros((G, O, K), bool)
    for o in range(O):
        Rs[:, o, o:] = R[:, :K - o] if o else R
        Ls[:, o, o:] = tab["leave"][:, :K - o] if o else tab["leave"]
    V = np.full((K + blk + 1, G, O), NEG)
    back = np.full((K + blk + 1, G, O), -2, np.int64)     # -1 stayed, -3 start, >=0: src game * O + src offset
    for g in range(G):
        if tab["land"][g, 0]:
            V[blk, g, 0] = 0.0
            back[blk, g, 0] = -3
    for k in range(blk, K):
        v = V[k]
        if v.max() <= NEG / 2:
            continue
        stay = v + Rs[:, :, k]
        better = stay > V[k + 1]
        V[k + 1] = np.where(better, stay, V[k + 1])
        back[k + 1] = np.where(better, -1, back[k + 1])
        if k + blk > K:
            continue
        lv = np.where(Ls[:, :, k] & (v > NEG / 2), v, NEG)    # leavable states
        per_game = lv.max(axis=1)                            # best leavable value per source game
        if per_game.max() <= NEG / 2:
            continue
        arg_o = lv.argmax(axis=1)
        order = np.argsort(-per_game)
        a1 = int(order[0])
        a2 = int(order[1]) if G > 1 else -1
        for b in range(G):
            ob = int(cue[b, k])
            if ob < 0:
                continue
            a = a1 if a1 != b else a2
            if a < 0 or per_game[a] <= NEG / 2:
                continue
            val = per_game[a] - ws
            if val > V[k + blk, b, ob]:
                V[k + blk, b, ob] = val
                back[k + blk, b, ob] = a * O + int(arg_o[a])
    k = K
    g, o = np.unravel_index(int(np.argmax(V[K])), (G, O))
    g, o = int(g), int(o)
    out = []
    while k >= blk:
        b = back[k, g, o]
        if b == -1:
            k -= 1
        elif b >= 0:
            src_g, src_o = divmod(int(b), O)
            out.append(SwitchLog(float(n.t0 + (k - blk) * STEP_S), n.replays[src_g].gid, n.replays[g].gid,
                                 "oracle", objective, offset=float(o * STEP_S)))
            k, g, o = k - blk, src_g, src_o
        else:
            break
    out.append(SwitchLog(float(n.t0), None, n.replays[g].gid, "start", objective))
    return out[::-1]


def value_to_go_cue(n: Night, objective: str = "combined", tab: dict | None = None, max_cue_s: float = 120.0):
    """[possession-start entry, DAgger] The backward form of solve_cue: V[k, g, o] = the best value from
    step k on, watching game g o steps behind live (deciding now), and the action values at any state, so an
    imitation policy can be labelled on the states IT visits. Returns (V, R shifted Rs, leave Ls, cue, blk, ws)."""
    wb, wc, wm, wd, ws = OBJECTIVES[objective]
    steps = np.arange(n.t0, n.t1, STEP_S)
    tab = tab or tables(n, steps)
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    G, K = R.shape
    O = int(max_cue_s // STEP_S) + 1
    cue = cue_steps(tab, max_cue_s)
    blk = max(1, int(math.ceil(n.tune_s / STEP_S)))
    Rs = np.zeros((G, O, K))
    Ls = np.zeros((G, O, K), bool)
    for o in range(O):
        Rs[:, o, o:] = R[:, :K - o] if o else R
        Ls[:, o, o:] = tab["leave"][:, :K - o] if o else tab["leave"]
    NEG = -1e18
    V = np.zeros((K + blk + 1, G, O))
    gi = np.arange(G)
    for k in range(K - 1, -1, -1):
        v = Rs[:, :, k] + V[k + 1]
        if k + blk <= K:
            tgt = np.where(cue[:, k] >= 0, V[k + blk, gi, np.clip(cue[:, k], 0, None)] - ws, NEG)
            order = np.argsort(-tgt)
            best1, best2 = tgt[order[0]], (tgt[order[1]] if G > 1 else NEG)
            alt = np.where(gi == order[0], best2, best1)                 # best switch to another game
            v = np.where(Ls[:, :, k] & (alt[:, None] > v), alt[:, None], v)
        V[k] = v
    return V, Rs, Ls, cue, blk, ws


def cue_action_values(V, Rs, Ls, cue, blk, ws, k: int, a: int, o: int) -> np.ndarray:
    """Per game b: the value of being on b after deciding at step k on (a, o): stay for b == a, else a
    switch (NEG where not allowed). The DAgger label is its argmax."""
    G = Rs.shape[0]
    K = Rs.shape[2]
    out = np.full(G, -1e18)
    out[a] = Rs[a, o, k] + V[k + 1, a, o]
    if Ls[a, o, k] and k + blk <= K:
        for b in range(G):
            if b != a and cue[b, k] >= 0:
                out[b] = V[k + blk, b, int(cue[b, k])] - ws
    return out


def schedule_value(n: Night, tab: dict, objective: str, switches) -> float:
    """The DP's own objective for a schedule (reward on the steps a game is on screen, black for tune_s
    after each switch, minus the switch cost): what `solve` maximizes, for checks and brute force."""
    wb, wc, wm, wd, ws = OBJECTIVES[objective]
    R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
    K = R.shape[1]
    blk = max(1, int(math.ceil(n.tune_s / STEP_S)))
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    on, off = np.full(K, -1), np.zeros(K, np.int64)
    sw = sorted(switches, key=lambda s: s.t)
    v = 0.0
    for j, s in enumerate(sw):
        k0 = int(round((s.t - n.t0) / STEP_S)) + blk
        k1 = int(round((sw[j + 1].t - n.t0) / STEP_S)) if j + 1 < len(sw) else K
        on[k0:k1] = gi[s.to]
        off[k0:k1] = int(round((getattr(s, "offset", 0.0) or 0.0) / STEP_S))
        if s.frm is not None:
            v -= ws
    return float(v + sum(R[g, k - off[k]] for k, g in enumerate(on) if g >= 0 and k - off[k] >= 0))


def grade(n: Night, switches) -> sportsgrade.Card:
    return sportsgrade.grade(n.replays, switches, n.mine, n.lag, n.tune_s, n.t0, n.t1, set())


def counts(card) -> dict:
    """A card's per-night counts, the shape the scorecards aggregate (eval_sports / eval --oracle)."""
    cs = card.cut.summary()
    return {"big": card.big, "big_live": card.big_live, "crunch": card.crunch, "crunch_live": card.crunch_live,
            "my_crunch": card.my_crunch, "my_crunch_live": card.my_crunch_live, "mid_play_cuts": len(card.cuts),
            "entries": cs["entries"], "entries_mid_play": cs["entries_mid_play"],
            "dead_s": round(card.dead, 1), "watched_s": round(card.watched, 1), "switches": card.switches}


def aggregate(rows: list[dict]) -> dict:
    """Sum per-night counts and add the three percentages."""
    tot: dict = {}
    for x in rows:
        for k, v in x.items():
            tot[k] = tot.get(k, 0) + v
    tot["big_live_pct"] = round(100 * tot.get("big_live", 0) / max(1, tot.get("big", 0)), 1)
    tot["crunch_live_pct"] = round(100 * tot.get("crunch_live", 0) / max(1, tot.get("crunch", 0)), 1)
    tot["dead_air_pct"] = round(100 * tot.get("dead_s", 0) / max(1, tot.get("watched_s", 0)), 1)
    return tot
