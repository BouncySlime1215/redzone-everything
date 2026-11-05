"""[Watch Value v2 §2b] STAY or LEAVE from full game context (Nick, 9:45 AM Nov 5; EXP-1712 / EXP-1713).

The decision after each play of the game on screen: stay, or leave for the best alternative. It is made from
the WHOLE game, not the single play:

  state        clock left, period / overtime, |margin| in score-spread units, closeness, suspense (the WP move
               one score would cause), comeback odds, the high-leverage flag
  consequence  how the recent plays moved the game: |ΔWP| of the last play, over 5 min, WP volatility over
               10 min, the margin swing over 5 min (a run, only as measured WP movement)
  history      big / crunch plays per minute, dead-air share, the v1 past-mean reward
  stakes       your team (WSH / UNC). Standings and playoff odds join when B's table is wired.

A gradient-boosted regressor maps those to the game's expected reward over the next `LABEL_HORIZON` of its screen
time (label = the realized `oracle.OBJECTIVES["combined"]` reward from real play-by-play: never a Teacher
output, never Nick's viewing or buttons). That value replaces the windowed oracle's past-only placeholder
(`oracle.beyond_value`) at the end of what the buffer reveals, and the same exact DP compares staying with the
best alternative minus the switch cost (`solve_policy`). EXP-1713 adds a hysteresis band that grows with recent
switches.

HARD FLOORS, never learned: never join mid-play (the oracle's land table), never cut mid-play (its leave
table), and never leave a game whose last play on screen is high-leverage (`leverage.high_leverage`) or inside
the crunch table's app/graded window (`redzone/crunch.py`: NBA 3:00 within 7 is wider than CLOSE_LATE's 6).

Live path: `stay_or_leave(ctx, state)` is the same rule for one decision, for the shadow log (lane 2 logs `FIELDS`).
WP is the label-only Brownian model of `outcome_label.win_prob` (a fixed formula, no learned WP).
Offline only except `stay_or_leave`; flag `decider.stay_leave.learned` (off).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from redzone import crunch as CR
from redzone.lab import oracle as O
from redzone.lab import sportsgrade
from redzone.lab.backtest import SwitchLog, lag_of
from redzone.lab.leverage import CLOSE_LATE, _left_s, high_leverage
from redzone.lab.outcome_label import WP_SPREAD, game_left, win_prob
from redzone.models import PERIOD_S, REGULATION, League
from redzone.sources import espn

LABEL_HORIZON = 300.0                        # the label: realized reward over the next 5 min of screen time
HZ = int(LABEL_HORIZON // O.STEP_S)
MIN5, MIN10, MIN15 = 60, 120, 180        # steps
SCORE_UNIT = {League.NBA: 2.0, League.CBB: 2.0, League.NFL: 7.0, League.CFB: 7.0, League.NHL: 1.0,
              League.MLB: 1.0}
MINE = {"WSH", "UNC"}

GROUPS = {
    "state": ["left_frac", "overtime", "period_frac", "margin_z", "closeness", "suspense", "comeback", "high_lev",
              "started", "over"],
    "consequence": ["dwp_last", "dwp_5", "wp_vol_10", "swing_5"],
    "history": ["big_5", "big_15", "crunch_5", "dead_5", "past_mean_10"],
    "your_team": ["mine"],
    "league": ["lg_nba", "lg_nfl", "lg_nhl"],
}
CONTEXT_FEATURES = [f for g in GROUPS.values() for f in g]
# What the shadow log records per stay/leave decision (lane 2, tonight), so tomorrow's grade can replay it.
FIELDS = ["ts", "game_id", "league", "action", "value_stay", "value_alt", "alt_game_id", "margin", "floor",
          "reason", "recent_switches", *CONTEXT_FEATURES]
# What the label is built from (the tester asserts none of these is a Teacher output or Nick's own viewing).
LABEL_SOURCES = ["espn_play_by_play", "oracle_combined_reward"]
HP = {"learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 40, "l2_regularization": 0.1}
MAX_ITER_GRID = (100, 200, 400)
MARGIN_GRID = (0.25, 0.5, 1.0)


# ---------------------------------------------------------------------------------------------- features
def _roll_sum(x: np.ndarray, w: int) -> np.ndarray:
    """Sum over the last w steps INCLUDING k (k-w, k], along the last axis."""
    c = np.cumsum(np.pad(x, [(0, 0)] * (x.ndim - 1) + [(1, 0)]), axis=-1)
    k = np.arange(x.shape[-1]) + 1
    return c[..., k] - c[..., np.maximum(0, k - w)]


def _wp_at(league, margin, left):
    w = win_prob(league, margin, left)
    return 0.5 if w is None else w


def _game_rows(league: League, r, lag: float, t0: float, K: int, mine: bool):
    """Per step: a dict of feature arrays for one game, from the plays on screen at or before each step."""
    plen = sportsgrade.PLAY_LEN[league]
    reg_s = REGULATION.get(league, 4) * PERIOD_S.get(league, 720)
    n = len(r.plays)
    kp = np.array([int((w + lag - plen - t0) // O.STEP_S) for w in r.wall])
    margin = np.zeros(n)
    left = np.full(n, np.nan)
    per = np.zeros(n)
    hl = np.zeros(n, bool)
    last = 0
    for i, p in enumerate(r.plays):
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        last = hs - as_ if hs is not None and as_ is not None else last
        margin[i] = last
        gl = game_left(league, p)
        left[i] = np.nan if gl is None else gl
        per[i] = espn._int(espn._get(p, "period", "number")) or 0
        hl[i] = high_leverage(league, p)
    left = _ffill(left, reg_s)
    wp = np.array([_wp_at(league, margin[i], left[i]) for i in range(n)])
    su = SCORE_UNIT.get(league, 1.0)
    susp = np.array([abs(_wp_at(league, margin[i] + su, left[i]) - _wp_at(league, margin[i] - su, left[i])) / 2
                     for i in range(n)])
    dwp = np.abs(np.diff(wp, prepend=0.5))
    idx = np.searchsorted(kp, np.arange(K), side="right") - 1        # last play on screen at each step
    on = idx >= 0
    j = np.maximum(idx, 0)
    spread = WP_SPREAD.get(league, 1.0) * math.sqrt(reg_s)
    wps = np.where(on, wp[j], 0.5)
    ms = np.where(on, margin[j], 0.0)
    f = {
        "left_frac": np.where(on, np.clip(left[j] / reg_s, 0, 1), 1.0),
        "overtime": np.where(on, per[j] > REGULATION.get(league, 4), False).astype(float),
        "period_frac": np.where(on, per[j] / REGULATION.get(league, 4), 0.0),
        "margin_z": np.abs(ms) / spread,
        "closeness": 1.0 - np.abs(2 * wps - 1),
        "suspense": np.where(on, susp[j], susp[0] if n else 0.0),
        "comeback": np.minimum(wps, 1 - wps),
        "high_lev": np.where(on & (idx < n - 1), hl[j], False).astype(float),   # a finished game is not live
        "started": on.astype(float),
        "over": (idx == n - 1).astype(float),
        "dwp_last": np.where(on, dwp[j], 0.0),
        "dwp_5": np.abs(wps - _lagged(wps, MIN5, 0.5)),
        "wp_vol_10": _roll_std(wps, MIN10),
        "swing_5": np.abs(ms - _lagged(ms, MIN5, 0.0)) / spread,
        "mine": np.full(K, float(mine)),
        "lg_nba": np.full(K, float(league == League.NBA)),
        "lg_nfl": np.full(K, float(league == League.NFL)),
        "lg_nhl": np.full(K, float(league == League.NHL)),
    }
    return f, f["high_lev"] > 0


def _ffill(x: np.ndarray, first: float) -> np.ndarray:
    out = x.copy()
    prev = first
    for i, v in enumerate(out):
        if np.isnan(v):
            out[i] = prev
        else:
            prev = v
    return out


def _lagged(x: np.ndarray, w: int, fill: float) -> np.ndarray:
    return np.concatenate([np.full(min(w, len(x)), fill), x[:-w]]) if w < len(x) else np.full(len(x), fill)


def _roll_std(x: np.ndarray, w: int) -> np.ndarray:
    s1, s2 = _roll_sum(x[None], w)[0], _roll_sum((x * x)[None], w)[0]
    m = np.minimum(np.arange(len(x)) + 1, w)
    return np.sqrt(np.maximum(0.0, s2 / m - (s1 / m) ** 2))


def reward(tab: dict, objective: str = "combined") -> np.ndarray:
    wb, wc, wm, wd, _ = O.OBJECTIVES[objective]
    return wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]


def context(n: O.Night, tab: dict, objective: str = "combined") -> tuple[np.ndarray, np.ndarray]:
    """F[g, k, f] (CONTEXT_FEATURES order) and the high-leverage table hl[g, k], from what is on screen at step k."""
    R = reward(tab, objective)
    G, K = R.shape
    F = np.zeros((G, K, len(CONTEXT_FEATURES)))
    HL = np.zeros((G, K), bool)
    big5, big15 = _roll_sum(tab["big"], MIN5) / 5, _roll_sum(tab["big"], MIN15) / 15
    cr5, dead5 = _roll_sum(tab["crunch"], MIN5) / 5, _roll_sum(tab["dead"], MIN5) / MIN5
    pm = _roll_sum(R, MIN10) / np.minimum(np.arange(K) + 1, MIN10)
    for g, r in enumerate(n.replays):
        teams = {r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]}
        f, hl = _game_rows(r.league, r, lag_of(n.lag, r.gid), n.t0, K, bool(teams & n.mine))
        f.update(big_5=big5[g], big_15=big15[g], crunch_5=cr5[g], dead_5=dead5[g], past_mean_10=pm[g])
        F[g] = np.stack([f[c] for c in CONTEXT_FEATURES], axis=-1)
        HL[g] = hl
    return F, HL


def in_crunch(league, period, clock_s, margin) -> bool:
    """The crunch table's app window or graded window (redzone/crunch.py), e.g. NBA 3:00 left within 7."""
    return any(CR.inside(CR.row(kind, league), int(period or 0), clock_s, margin) for kind in ("window", "graded"))


def floor_play(league: League, p: dict) -> bool:
    """The no-leave floor for one play on screen: high leverage (close and late, OT) OR inside crunch."""
    if high_leverage(league, p):
        return True
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    if hs is None or as_ is None:
        return False
    per = espn._int(espn._get(p, "period", "number")) or 0
    return in_crunch(league, per, _left_s(league, p), hs - as_)


def floor_table(n: O.Night, K: int) -> np.ndarray:
    """FLOOR[g, k]: the game's last play on screen at step k is under the no-leave floor (a finished game never)."""
    out = np.zeros((len(n.replays), K), bool)
    for g, r in enumerate(n.replays):
        lag, plen = lag_of(n.lag, r.gid), sportsgrade.PLAY_LEN[r.league]
        kp = np.array([int((w + lag - plen - n.t0) // O.STEP_S) for w in r.wall])
        fl = np.array([floor_play(r.league, p) for p in r.plays])
        idx = np.searchsorted(kp, np.arange(K), side="right") - 1
        out[g] = (idx >= 0) & (idx < len(r.plays) - 1) & fl[np.maximum(idx, 0)]
    return out


def label(R: np.ndarray, hz: int = HZ) -> np.ndarray:
    """Y[g, k] = realized reward of game g over screen steps k .. k + hz - 1 (the game's next 5 min)."""
    return _roll_sum(R[:, ::-1], hz)[:, ::-1]


# ---------------------------------------------------------------------------------------------- model
@dataclass
class Model:
    """Seeds-averaged regressors over CONTEXT_FEATURES; `drop` zeroes feature groups (ablations)."""
    regs: list = field(default_factory=list)
    drop: tuple = ()

    def _x(self, X: np.ndarray) -> np.ndarray:
        if not self.drop:
            return X
        X = X.copy()
        for gname in self.drop:
            for c in GROUPS[gname]:
                X[..., CONTEXT_FEATURES.index(c)] = 0.0
        return X

    def predict(self, X: np.ndarray) -> np.ndarray:
        sh = X.shape[:-1]
        X2 = self._x(X).reshape(-1, X.shape[-1])
        return np.mean([r.predict(X2) for r in self.regs], axis=0).reshape(sh)


@dataclass
class TwoHead:
    """[EXP-1714] value = head 1 (reward without the your-team term) + 100 x your-team x head 2 (crunch count)."""
    base: Model
    crunch: Model
    w_mine: float = O.OBJECTIVES["combined"][2]

    def predict(self, X: np.ndarray) -> np.ndarray:
        mine = X[..., CONTEXT_FEATURES.index("mine")]
        return self.base.predict(X) + self.w_mine * mine * np.maximum(0.0, self.crunch.predict(X))


def reward_nomine(tab: dict, objective: str = "combined") -> np.ndarray:
    wb, wc, _wm, wd, _ = O.OBJECTIVES[objective]
    return wb * tab["big"] + wc * tab["crunch"] - wd * tab["dead"]


def floored(tab: dict, HL: np.ndarray) -> dict:
    """The oracle's tables with the no-leave floor applied: `oracle.solve` on it is the floored ceiling."""
    return {**tab, "leave": tab["leave"] & ~HL}


def fit(X: np.ndarray, y: np.ndarray, max_iter: int, seeds=(0, 1, 2), drop: tuple = ()) -> Model:
    from sklearn.ensemble import HistGradientBoostingRegressor
    m = Model(drop=drop)
    Xd = m._x(X)
    for s in seeds:
        m.regs.append(HistGradientBoostingRegressor(max_iter=max_iter, random_state=s, early_stopping=False,
                                                    **HP).fit(Xd, y))
    return m


def rows(F: np.ndarray, Y: np.ndarray, every: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """Training rows: every 30 s of screen time per game while it is live (started, not over)."""
    G, K, _ = F.shape
    ks = np.arange(0, K - HZ, every)
    X, y = F[:, ks].reshape(-1, F.shape[-1]), Y[:, ks].reshape(-1)
    live = (X[:, CONTEXT_FEATURES.index("started")] > 0) & (X[:, CONTEXT_FEATURES.index("over")] == 0)
    return X[live], y[live]


def rows_relative(F: np.ndarray, Y: np.ndarray, every: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """[EXP-126] Rows as `rows`, target demeaned across the games live at the same step (relative value)."""
    G, K, _ = F.shape
    ks = np.arange(0, K - HZ, every)
    Xk, Yk = F[:, ks], Y[:, ks]                                     # [G, S, f], [G, S]
    live = (Xk[..., CONTEXT_FEATURES.index("started")] > 0) & (Xk[..., CONTEXT_FEATURES.index("over")] == 0)
    cnt = live.sum(0)
    mean = np.where(cnt > 0, (Yk * live).sum(0) / np.maximum(1, cnt), 0.0)
    keep = live & (cnt >= 2)[None]
    return Xk[keep], (Yk - mean[None])[keep]


# ---------------------------------------------------------------------------------------------- policy
def solve_policy(n: O.Night, feed_lag: float, tab: dict, F: np.ndarray, HL: np.ndarray, model: Model | None,
                 objective: str = "combined", margin: float = 0.0, floor: bool = True,
                 horizon_s: float = LABEL_HORIZON) -> list[SwitchLog]:
    """`oracle.solve_window` with (a) the beyond-window value from `model` (None = the v1 past mean),
    (b) the high-leverage floor in the DP and at the decision, (c) a hysteresis band at the decision:
    leave only if best alternative > stay + margin * (1 + switches in the last 15 min)."""
    R = reward(tab, objective)
    ws = O.OBJECTIVES[objective][4]
    G, K = R.shape
    blk = max(1, int(math.ceil(n.tune_s / O.STEP_S)))
    lags = [lag_of(n.lag, r.gid) for r in n.replays]
    W = max(blk + 1, min(O.window_steps(lg, feed_lag, n.tune_s) for lg in lags))
    hz = int(horizon_s // O.STEP_S)
    leave = tab["leave"] & ~HL if floor else tab["leave"]
    NEG = -1e18
    gi = np.arange(G)
    Vb = model.predict(F) if model is not None else None      # [G, K]: value from step k + 1 on, known at k

    def beyond(k: int, e: int) -> np.ndarray:
        if e >= K:
            return np.zeros(G)
        if Vb is None:
            return O.beyond_value(R, k, hz)
        return Vb[:, e - 1]                                     # features of the window's last known step

    def plan(k: int) -> np.ndarray:
        e = min(K, k + W)
        V = np.zeros((e - k + blk + 1, G))
        V[e - k:] = beyond(k, e)
        for j in range(e - k - 1, -1, -1):
            kk = k + j
            v = R[:, kk] + V[j + 1]
            tgt = np.where(tab["land"][:, kk], V[j + blk] - ws, NEG)
            order = np.argsort(-tgt)
            b1, b2 = tgt[order[0]], (tgt[order[1]] if G > 1 else NEG)
            alt = np.where(gi == order[0], b2, b1)
            V[j] = np.where(leave[:, kk] & (alt > v), alt, v)
        return V

    steps = np.arange(n.t0, n.t1, O.STEP_S)
    out: list[SwitchLog] = []
    k = blk
    V = plan(k)
    first = [g for g in range(G) if tab["land"][g, 0]] or list(range(G))
    cur = max(first, key=lambda g: V[0, g])
    out.append(SwitchLog(float(n.t0), None, n.replays[cur].gid, "start", objective))
    recent: list[int] = []
    while k < K:
        V = plan(k)
        stay = R[cur, k] + V[1, cur]
        cand = np.where(tab["land"][:, k] & (gi != cur), V[blk] - ws, NEG)
        b = int(np.argmax(cand))
        recent = [x for x in recent if k - x < MIN15]
        band = margin * (1 + len(recent))
        if not (leave[cur, k] and k + blk <= K and cand[b] > stay + band):
            k += 1
            continue
        out.append(SwitchLog(float(steps[k]), n.replays[cur].gid, n.replays[b].gid, "stay_leave", objective))
        recent.append(k)
        cur = b
        k += blk
    return out


# ---------------------------------------------------------------------------------------------- grading
def night_metrics(n: O.Night, tab: dict, HL: np.ndarray, switches, objective: str = "combined") -> dict:
    """The row's metrics for one schedule on one night (the oracle's own grader for the card lines)."""
    card = O.grade(n, switches)
    c = O.counts(card)
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    hot = _roll_sum(tab["crunch"], MIN5) > 0                      # crunch play in the 5 min before
    pay = label(tab["big"] + tab["crunch"], MIN5)                 # big/crunch in the next 5 min
    K = tab["big"].shape[1]
    hl_leaves, exits, exits_ok = 0, 0, 0
    for s in switches:
        if s.frm is None:
            continue
        k = min(K - 1, max(0, int(round((s.t - n.t0) / O.STEP_S))))
        g = gi[s.frm]
        hl_leaves += bool(HL[g, k])
        if hot[g, k]:
            exits += 1
            exits_ok += int(pay[g, min(K - 1, k + 1)] == 0)
    hours = max(1e-9, (n.t1 - n.t0) / 3600)
    return {"value": O.schedule_value(n, tab, objective, switches), "hl_leaves": hl_leaves,
            "crunch_exits": exits, "crunch_exits_ok": exits_ok, "switches": c["switches"],
            "switches_per_h": round(c["switches"] / hours, 2), "entries_mid_play": c["entries_mid_play"],
            "mid_play_cuts": c["mid_play_cuts"], "big": c["big"], "big_live": c["big_live"], "crunch": c["crunch"],
            "crunch_live": c["crunch_live"], "dead_s": c["dead_s"], "watched_s": c["watched_s"]}


def _shares(a: dict, f: dict) -> dict:
    """North Star `pct_of_oracle_<lg>` shape (scripts/northstar.py): the worst of big live, crunch live and live
    air (100 - dead air) as a share of the full-hindsight oracle's, capped at 100."""
    def r(x, y):
        return 100 * x / y if y else None
    bl, fb = r(a["big_live"], a["big"]), r(f["big_live"], f["big"])
    cl, fc = r(a["crunch_live"], a["crunch"]), r(f["crunch_live"], f["crunch"])
    la, fl = 100 - r(a["dead_s"], max(1, a["watched_s"])), 100 - r(f["dead_s"], max(1, f["watched_s"]))
    sh = {"big_live": min(100.0, 100 * bl / fb) if bl is not None and fb else None,
          "crunch_live": min(100.0, 100 * cl / fc) if cl is not None and fc else None,
          "live_air": min(100.0, 100 * la / fl) if fl else None}
    v = [x for x in sh.values() if x is not None]
    sh["pct_of_oracle"] = min(v) if v else None
    return sh


KEYS = ("big", "big_live", "crunch", "crunch_live", "dead_s", "watched_s", "value")


def pct_ci(rows_arm: list[dict], rows_full: list[dict], rows_base: list[dict] | None = None, B: int = 2000,
           seed: int = 0) -> dict:
    """% of oracle (North Star shape, pooled over nights) with a night-cluster bootstrap 95 % CI, the delta vs a
    base arm with its CI, and regret = full - arm in the DP's own objective units per night."""
    if not rows_arm:
        return {"pct": None, "ci": None, "nights": 0}
    A = np.array([[r[k] for k in KEYS] for r in rows_arm], float)
    Fu = np.array([[r[k] for k in KEYS] for r in rows_full], float)
    Bs = np.array([[r[k] for k in KEYS] for r in rows_base], float) if rows_base else None

    def pct(M, idx):
        return _shares(dict(zip(KEYS, M[idx].sum(0))), dict(zip(KEYS, Fu[idx].sum(0))))["pct_of_oracle"]

    allx = np.arange(len(A))
    sh = _shares(dict(zip(KEYS, A.sum(0))), dict(zip(KEYS, Fu.sum(0))))
    rng = np.random.default_rng(seed)
    boots = [rng.integers(0, len(A), len(A)) for _ in range(B)]
    pa = np.array([pct(A, i) for i in boots], float)
    out = {"pct": round(sh["pct_of_oracle"], 2) if sh["pct_of_oracle"] is not None else None,
           "ci": [round(float(np.nanpercentile(pa, q)), 2) for q in (2.5, 97.5)],
           "shares": {k: (round(v, 2) if v is not None else None) for k, v in sh.items() if k != "pct_of_oracle"},
           "nights": len(A), "regret_per_night": round(float((Fu[:, -1] - A[:, -1]).mean()), 3)}
    if Bs is not None:
        pb = np.array([pct(Bs, i) for i in boots], float)
        out["delta_vs_base"] = round(out["pct"] - pct(Bs, allx), 2)
        out["delta_ci"] = [round(float(np.nanpercentile(pa - pb, q)), 2) for q in (2.5, 97.5)]
        out["value_gain_per_night"] = round(float((A[:, -1] - Bs[:, -1]).mean()), 3)    # > 0: less regret than base
        out["value_gain_ci"] = [round(float(np.percentile([(A[i, -1] - Bs[i, -1]).mean() for i in boots[:500]], q)), 3)
                                  for q in (2.5, 97.5)]
    return out


# ---------------------------------------------------------------------------------------------- live path
@dataclass
class Decision:
    action: str                 # "stay" | "leave"
    value_stay: float
    value_alt: float
    alt_game_id: str | None
    margin: float               # the hysteresis band used
    floor: bool                 # True when the high-leverage floor forced the stay
    reason: str


@dataclass
class State:
    switches: list = field(default_factory=list)      # timestamps (s) of our recent switches
    m: float = 0.5                                    # band per recent switch (EXP-1713 picks it on valid)
    dwell_s: float = 60.0                             # no voluntary leave within this long of our last switch


def high_leverage_ctx(league, period, clock_s, home, away) -> bool:
    """`leverage.high_leverage` for a context dict (clock_s = seconds LEFT in the period, every league)."""
    lg = League(league) if not isinstance(league, League) else league
    if period is None or home is None or away is None:
        return False
    d = abs(int(home) - int(away))
    if lg == League.MLB:
        return int(period) >= 7 and d <= 2
    rule = CLOSE_LATE.get(lg)
    if rule is None:
        return False
    last, secs, mg = rule
    if int(period) > last:
        return True
    return int(period) == last and clock_s is not None and float(clock_s) <= secs and d <= mg


def band(state: State, ts: float) -> float:
    """Hysteresis: grows with our switches in the last 15 min (never shrinks as k grows)."""
    k = sum(1 for t in state.switches if ts - t < 15 * 60)
    return state.m * (1 + k)


def dwell(state: State, ts: float) -> float:
    """Minimum time on a game after our last switch: dwell_s x (1 + switches in the last 15 min before it),
    so whiplash between two games that trade the lead backs off (0, 60, 180, 420 s ...)."""
    if not state.switches:
        return 0.0
    last = state.switches[-1]
    k = sum(1 for t in state.switches[:-1] if last - t < 15 * 60)
    return state.dwell_s * (1 + k)


def stay_or_leave(ctx: dict, state: State | None = None) -> tuple[Decision, State]:
    """One stay/leave decision for the game on screen. ctx: league, period, clock_s (left in period),
    home_score, away_score, value (this game's predicted value; `value_stay` also read), alts [{game_id, value}],
    ts, optionally high_leverage (True forces the floor too). The floor is checked first and always wins."""
    st = state or State()
    ts = float(ctx.get("ts") or 0.0)
    v_stay = float(ctx.get("value", ctx.get("value_stay", 0.0)) or 0.0)
    alts = [a for a in (ctx.get("alts") or []) if a.get("value") is not None]
    best = max(alts, key=lambda a: a["value"]) if alts else None
    v_alt = float(best["value"]) if best else float("-inf")
    m = band(st, ts)
    hl = bool(ctx.get("high_leverage")) or high_leverage_ctx(ctx.get("league"), ctx.get("period"), ctx.get("clock_s"),
                                                              ctx.get("home_score"), ctx.get("away_score"))
    if not hl and ctx.get("home_score") is not None and ctx.get("away_score") is not None and ctx.get("league"):
        hl = in_crunch(ctx["league"], ctx.get("period"), ctx.get("clock_s"),
                       int(ctx["home_score"]) - int(ctx["away_score"]))
    gid = best["game_id"] if best else None
    if hl:
        return Decision("stay", v_stay, v_alt, gid, m, True, "high-leverage floor"), st
    if st.switches and ts - st.switches[-1] < dwell(st, ts):
        return Decision("stay", v_stay, v_alt, gid, m, False, "minimum dwell after our last switch"), st
    if best is None or not v_alt > v_stay + m:
        return Decision("stay", v_stay, v_alt, gid, m, False, "alternative inside the hysteresis band"), st
    st.switches = [t for t in st.switches if ts - t < 15 * 60] + [ts]
    return Decision("leave", v_stay, v_alt, gid, m, False, "alternative beats staying by more than the band"), st


def manifest(splits_by_league: dict) -> dict:
    """{league: {train_nights, valid_nights}, label_sources}: what a fit read (the tester checks no locked night)."""
    out = {lg: {"train_nights": list(s.train), "valid_nights": list(s.valid)} for lg, s in splits_by_league.items()}
    out["label_sources"] = list(LABEL_SOURCES)
    return out
