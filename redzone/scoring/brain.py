"""The learned brain: per-game break / key-moment / game-flow predictions from trained models.

Three small gradient-boosted tree models per league (trained by ``scripts/train_brain.py`` on
public play-by-play, see ``redzone.lab.datasets``), evaluated here with plain numpy:

    break_soon_p  chance a real break (timeout, TV timeout, end of period, ...) starts within
                  the next 60 s of game clock (MLB: wall clock)
    key_soon_p    chance of a key moment (TD, turnover, goal, big WP swing, crunch-time score,
                  late lead change, ...) in the next 2-5 minutes of game time
    hotness       0-100 game flow, like ESPN's game-flow chart (50 = a median moment)

Each game's features are rebuilt live from what the feed shows, exactly as the training rows
were built (``redzone.lab.sportsdata.derive``), one event per new play. Models load from
``redzone/scoring/brain_models/<league>.npz``; a league without a model (or a broken file) gets
``None`` and every caller falls back to its hand-written heuristics.
"""
from __future__ import annotations

import json
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from redzone.models import REAL_BREAKS, GameState, League, Phase
from redzone.scoring.gameflow import SPEC, game_elapsed, wp_baseball, wp_basketball, wp_hockey

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent / "brain_models"
TASKS = ("break", "key", "hot")
TIMELINE_MAX = 240
MAX_GAMES = 300  # per-game memory kept for at most this many games (oldest dropped first)

# Stoppage codes as in redzone.lab.datasets.STOPPAGES (kept in sync by tests/test_brain.py).
STOPPAGES = ("none", "whistle", "timeout", "two_minute_warning", "end_period", "halftime",
             "intermission", "review", "injury", "pitching_change", "score_break", "game_end",
             "unknown")
STOPPAGE_CODE = {s: i for i, s in enumerate(STOPPAGES)}
_BREAK_CODES = {STOPPAGE_CODE[s.value] for s in REAL_BREAKS}

# Features the live feed can supply, per pro league (college reads its pro league's row).
COMMON = ["period", "clock_s", "t_game", "frac_left", "is_ot", "wall_s",
          "score_home", "score_away", "score_diff", "abs_diff",
          "wp_home", "wp_lev", "wp_delta", "wp_vol_8", "wp_vol_w",
          "lead_changes", "ties", "run_home", "run_away", "pts_w",
          "stoppage", "in_break", "s_since_break", "ev_since_break", "wall_since_break", "crunch"]
LIVE_FEATURES = {
    "nfl": COMMON + ["possession_home", "yardline_100", "down", "ydstogo"],
    "nba": COMMON,
    "nhl": COMMON + ["home_skaters", "away_skaters"],
    "mlb": [c for c in COMMON if c != "clock_s"] + ["outs", "on_1b", "on_2b", "on_3b", "half_bottom"],
}
BASE = {"nfl": "nfl", "cfb": "nfl", "nba": "nba", "cbb": "nba", "wcbb": "nba", "nhl": "nhl", "mlb": "mlb"}


def features_for(league: str) -> list[str]:
    return LIVE_FEATURES[BASE[league.removeprefix("espn_")]]


# --- plain-numpy evaluation of a LightGBM model ------------------------------------------------

class Trees:
    """A LightGBM tree ensemble flattened into arrays (all trees walked at once).

    Node arrays: feat, thr, left, right (child node index), dleft (missing goes left), miss
    (0 None / 1 Zero / 2 NaN, LightGBM's missing_type), value (leaf value, or the node's
    expected output for an internal node: used for per-feature reasons)."""

    def __init__(self, arrays: dict, meta: dict):
        self.feat = arrays["feat"].astype(np.int64)
        self.thr = arrays["thr"].astype(np.float64)
        self.left = arrays["left"].astype(np.int64)
        self.right = arrays["right"].astype(np.int64)
        self.dleft = arrays["dleft"].astype(bool)
        self.miss = arrays["miss"].astype(np.int8)
        self.value = arrays["value"].astype(np.float64)
        self.roots = arrays["roots"].astype(np.int64)
        self.leaf = self.feat < 0
        self.depth = int(meta.get("depth", 32))
        self.objective = meta.get("objective", "binary")
        self.features = list(meta["features"])
        self.rate = meta.get("rate")  # training base rate of the label

    def raw(self, x: np.ndarray, explain: bool = False) -> tuple[float, np.ndarray | None]:
        """Sum of tree outputs for one feature row; with explain, Saabas per-feature contributions."""
        x = np.asarray(x, dtype=np.float32).astype(np.float64)
        idx = self.roots.copy()
        contrib = np.zeros(len(self.features)) if explain else None
        for _ in range(self.depth + 1):
            inner = ~self.leaf[idx]
            if not inner.any():
                break
            node = idx[inner]
            f = self.feat[node]
            v = x[f]
            nan = np.isnan(v)
            miss = self.miss[node]
            v = np.where(nan & (miss != 2), 0.0, v)
            to_default = ((miss == 1) & (np.abs(v) <= 1e-35)) | ((miss == 2) & nan)
            go_left = np.where(to_default, self.dleft[node], v <= self.thr[node])
            child = np.where(go_left, self.left[node], self.right[node])
            if explain:
                np.add.at(contrib, f, self.value[child] - self.value[node])
            idx[inner] = child
        return float(self.value[idx].sum()), contrib

    def predict(self, x: np.ndarray, explain: bool = False) -> tuple[float, np.ndarray | None]:
        r, c = self.raw(x, explain)
        if self.objective == "binary":
            return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, r)))), c
        return r, c


class Forest:
    """Several Trees (one per task, same features) walked together in a single pass: one set of
    numpy calls per tree level for all three models, so a game state costs well under 1 ms.
    Leaves loop to themselves, so every tree just takes `depth` steps."""

    def __init__(self, models: dict[str, Trees]):
        self.tasks = list(models)
        first = next(iter(models.values()))
        self.features = first.features
        feat, thr, left, right, dleft, miss, value, roots, task_of = [], [], [], [], [], [], [], [], []
        off = 0
        for k, (name, t) in enumerate(models.items()):
            if t.features != self.features:
                raise ValueError("every task in a forest needs the same features")
            n = len(t.feat)
            me = np.arange(n) + off
            feat.append(np.where(t.leaf, 0, t.feat))
            thr.append(t.thr)
            left.append(np.where(t.leaf, me, t.left + off))
            right.append(np.where(t.leaf, me, t.right + off))
            dleft.append(t.dleft), miss.append(t.miss), value.append(t.value)
            roots.append(t.roots + off)
            task_of.append(np.full(len(t.roots), k))
            off += n
        cat = np.concatenate
        self.feat, self.thr, self.left, self.right = cat(feat), cat(thr), cat(left), cat(right)
        self.dleft, self.miss, self.value = cat(dleft), cat(miss), cat(value)
        self.roots, self.task_of = cat(roots), cat(task_of)
        self.node_task = np.zeros(off, dtype=np.int64)
        starts = np.cumsum([0] + [len(t.feat) for t in models.values()])
        for k in range(len(self.tasks)):
            self.node_task[starts[k]:starts[k + 1]] = k
        self.depth = max(t.depth for t in models.values())
        self.binary = [models[n].objective == "binary" for n in self.tasks]
        self.rates = {n: models[n].rate for n in self.tasks}
        self.miss_zero, self.miss_nan = self.miss == 1, self.miss == 2

    def predict(self, x: np.ndarray, explain: bool = False) -> dict[str, tuple[float, np.ndarray | None]]:
        x = np.asarray(x, dtype=np.float32).astype(np.float64)
        nf = len(self.features)
        idx = self.roots
        contrib = np.zeros(len(self.tasks) * nf) if explain else None
        for _ in range(self.depth):
            f = self.feat[idx]
            v = x[f]
            nan = np.isnan(v)
            v = np.where(nan & ~self.miss_nan[idx], 0.0, v)
            to_default = (self.miss_zero[idx] & (np.abs(v) <= 1e-35)) | (self.miss_nan[idx] & nan)
            go_left = np.where(to_default, self.dleft[idx], v <= self.thr[idx])
            child = np.where(go_left, self.left[idx], self.right[idx])
            if explain:
                np.add.at(contrib, self.node_task[idx] * nf + f, self.value[child] - self.value[idx])
            idx = child
        sums = np.bincount(self.task_of, weights=self.value[idx], minlength=len(self.tasks))
        out = {}
        for k, name in enumerate(self.tasks):
            r = float(sums[k])
            if self.binary[k]:
                r = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, r))))
            out[name] = (r, contrib[k * nf:(k + 1) * nf] if explain else None)
        return out


def trees_from_lightgbm(dump: dict, features: list[str]) -> tuple[dict, dict]:
    """booster.dump_model() -> (arrays, meta) for Trees / save()."""
    feat, thr, left, right, dleft, miss, value, roots = [], [], [], [], [], [], [], []
    depth = 0

    def walk(n: dict, d: int) -> int:
        nonlocal depth
        depth = max(depth, d)
        i = len(feat)
        feat.append(-1), thr.append(0.0), left.append(-1), right.append(-1)
        dleft.append(False), miss.append(0)
        if "leaf_value" in n:
            value.append(float(n["leaf_value"]))
            return i
        value.append(float(n.get("internal_value", 0.0)))
        if n.get("decision_type", "<=") != "<=":
            raise ValueError("categorical splits are not supported")
        feat[i] = int(n["split_feature"])
        thr[i] = float(n["threshold"])
        dleft[i] = bool(n.get("default_left", True))
        miss[i] = {"None": 0, "Zero": 1, "NaN": 2}[n.get("missing_type", "None")]
        left[i] = walk(n["left_child"], d + 1)
        right[i] = walk(n["right_child"], d + 1)
        return i

    for t in dump["tree_info"]:
        roots.append(walk(t["tree_structure"], 0))
    arrays = dict(feat=np.array(feat, np.int16), thr=np.array(thr, np.float64), left=np.array(left, np.int32),
                  right=np.array(right, np.int32), dleft=np.array(dleft, bool), miss=np.array(miss, np.int8),
                  value=np.array(value, np.float32), roots=np.array(roots, np.int32))
    obj = str(dump.get("objective", "binary")).split()[0]
    meta = dict(features=features, depth=depth, objective="binary" if obj == "binary" else "regression")
    return arrays, meta


def save(path: Path, models: dict[str, tuple[dict, dict]], info: dict) -> None:
    """One file per league: every task's arrays (prefixed '<task>.') plus a JSON meta blob."""
    flat = {}
    meta = {"info": info, "tasks": {}}
    for task, (arrays, m) in models.items():
        meta["tasks"][task] = m
        for k, v in arrays.items():
            flat[f"{task}.{k}"] = v
    flat["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **flat)


def load(path: Path) -> tuple[dict[str, Trees], dict]:
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(bytes(z["meta"]).decode())
        out = {}
        for task, m in meta["tasks"].items():
            arrays = {k.split(".", 1)[1]: z[k] for k in z.files if k.startswith(task + ".")}
            out[task] = Trees(arrays, m)
    return out, meta.get("info", {})


# --- live features: the training rows, rebuilt one event at a time ---------------------------

def _f(x) -> float:
    return float("nan") if x is None else float(x)


def analytic_wp(league: str, g: GameState, t_game: float, diff: float) -> float:
    """Win probability when the feed has none (same models the training builder used)."""
    base = league if league in SPEC else BASE.get(league, league)
    sp = SPEC[base]
    try:
        if BASE[base] == "nba":
            total = sp["period_s"] * sp["n_reg"]
            left = max(0.0, total - t_game)
            if (g.period or 0) > sp["n_reg"]:
                left += g.clock_s or 0.0
            exp = -(g.spread or 0.0)
            return float(wp_basketball(base, np.array([diff]), np.array([left]), np.array([exp]))[0])
        if base == "nhl":
            if (g.period or 0) > 3:
                return 1.0 if diff > 0 else 0.0 if diff < 0 else 0.52
            hs, as_ = _skaters(g)
            return wp_hockey1(diff, max(0.0, 3600 - t_game), hs, as_)
        if base == "mlb":
            r = g.situation.runners or (False, False, False)
            bases = int(r[0]) + 2 * int(r[1]) + 4 * int(r[2])
            hb = 1.0 if g.half == "bottom" else 0.0
            return float(wp_baseball(np.array([diff]), np.array([float(g.period or 1)]), np.array([hb]),
                                     np.array([float(g.situation.outs or 0)]), np.array([bases]))[0])
        left = max(0.0, 3600 - t_game)  # football: normal model, sigma ~13.5
        return float(wp_basketball("nba", np.array([diff]), np.array([left * 2880 / 3600]), np.array([0.0]))[0])
    except Exception:  # noqa: BLE001 - a WP guess must never break scoring
        return 0.5


def wp_hockey1(diff: float, t_rem: float, home_skaters: float = float("nan"),
               away_skaters: float = float("nan")) -> float:
    """gameflow.wp_hockey for one state in plain Python (same numbers, ~20x faster)."""
    rate = 3.05 / 3600.0
    t_rem = max(0.0, t_rem)
    lh, la = rate * 1.03 * t_rem, rate * 0.97 * t_rem
    adv = home_skaters - away_skaters
    if not math.isnan(adv):
        boost = min(t_rem, 90.0) * rate * 2.0
        lh += boost if adv > 0 else 0.0
        la += boost if adv < 0 else 0.0
    K = 12

    def pmf(lam: float) -> list[float]:
        lam = max(lam, 1e-9)
        return [math.exp(k * math.log(lam) - lam - math.lgamma(k + 1)) for k in range(K + 1)]

    ph, pa = pmf(lh), pmf(la)
    suf = [0.0] * (K + 2)
    for i in range(K, -1, -1):
        suf[i] = suf[i + 1] + ph[i]
    win = tie = 0.0
    for j in range(K + 1):
        need = j - diff  # home scores i more: win if i > need, tie if i == need
        lo = math.floor(need) + 1
        win += pa[j] * suf[min(K + 1, max(0, lo))]
        if need == int(need) and 0 <= need <= K:
            tie += pa[j] * ph[int(need)]
    return min(1.0, max(0.0, win + 0.52 * tie))


def _skaters(g: GameState) -> tuple[float, float]:
    s = g.situation
    if not s.strength:
        return float("nan"), float("nan")
    try:
        a, b = (int(x) for x in s.strength.lower().split("v")[:2])
    except ValueError:
        return float("nan"), float("nan")
    if s.power_play == g.home.abbr:
        return float(max(a, b)), float(min(a, b))
    if s.power_play == g.away.abbr:
        return float(min(a, b)), float(max(a, b))
    return float(a), float(b)


JOINED_LATE_S = 180.0   # a tracker whose first play is this far into the game joined it late
WARMUP_EVENTS = 40      # ...and stays quiet for this many plays


class Tracker:
    """One game's running feature state (mirrors sportsdata.derive for the newest row)."""

    def __init__(self, league: str):
        self.league = league
        self.base = league if league in SPEC else BASE[league]
        self.sp = SPEC[self.base]
        self.n = 0
        self.seen: set[str] = set()
        self.last_key: tuple | None = None
        self.t = 0.0
        self.t0: float | None = None
        self.clock: float | None = None
        self.wall0: float | None = None
        self.wall = float("nan")
        self.wp: float | None = None
        self.vol8: deque = deque(maxlen=8)
        self.win: deque = deque()          # (t, |dwp|, points) inside the trailing window
        self.sh = self.sa = None
        self.sign_nz = 0
        self.prev_sd: float | None = None
        self.lead_changes = self.ties = 0
        self.run_home = self.run_away = 0.0
        self.lb_t: float | None = None
        self.lb_i: int | None = None
        self.lb_w: float | None = None
        self.first_t: float | None = None
        self.first_w: float | None = None
        self.row: dict | None = None

    # One event = one logged play (or one change of state, for feeds without plays).
    def observe(self, g: GameState) -> bool:
        """Add the events this state shows that we haven't seen. True if anything changed."""
        if g.phase != Phase.LIVE:
            return False
        plays = [p for p in (g.recent_plays or ([g.last_play] if g.last_play else [])) if p is not None]
        new = [p for p in plays if p.id not in self.seen]
        if g.last_play is not None and new and new[-1].id != g.last_play.id and g.last_play.id not in self.seen:
            new.append(g.last_play)
        if not plays:
            key = (g.period, g.clock_s, g.home.score, g.away.score, g.stoppage)
            if key == self.last_key:
                return False
            self.last_key = key
            self._event(g, None, final=True)
            return True
        if not new:
            return False
        for p in new[:-1]:
            self.seen.add(p.id)
            self._event(g, p, final=False)
        self.seen.add(new[-1].id)
        self._event(g, new[-1], final=True)
        return True

    def _event(self, g: GameState, play, final: bool) -> None:
        base, sp = self.base, self.sp
        # Scores: the state's for the newest play, the play's own (when the feed says) before it.
        if final or play is None:
            sh, sa = g.home.score, g.away.score
        elif play.score is not None:
            sh, sa = play.score
        else:
            sh, sa = self.sh, self.sa
        sh = float(sh if sh is not None else (self.sh or 0.0))
        sa = float(sa if sa is not None else (self.sa or 0.0))
        sh, sa = max(sh, self.sh or 0.0), max(sa, self.sa or 0.0)   # scores only go up (cummax)
        period = float(g.period or 0)
        wall_abs = play.wallclock if play is not None and play.wallclock else None
        if wall_abs is not None:
            if self.wall0 is None:
                self.wall0 = wall_abs
            w = wall_abs - self.wall0
            self.wall = w if math.isnan(self.wall) else max(self.wall, w)
        if base == "mlb":
            clock = float("nan")
            t = self.wall if not math.isnan(self.wall) else self.t
        else:
            if final and g.clock_s is not None:
                self.clock = float(g.clock_s)
            clock = _f(self.clock)
            if self.clock is None:
                t = self.t
            else:
                t = float(game_elapsed(base, np.array([period]), np.array([self.clock]))[0])
        t = max(self.t, t if not math.isnan(t) else self.t)
        self.t = t
        diff = sh - sa
        if final:
            wp = g.home_win_prob if g.home_win_prob is not None else analytic_wp(self.league, g, t, diff)
        else:
            wp = self.wp if self.wp is not None else 0.5
        wp = min(1.0, max(0.0, float(wp)))
        d_wp = 0.0 if self.wp is None else wp - self.wp
        self.wp = wp
        dh = 0.0 if self.sh is None else max(0.0, sh - self.sh)
        da = 0.0 if self.sa is None else max(0.0, sa - self.sa)
        self.sh, self.sa = sh, sa
        stop = STOPPAGE_CODE.get(g.stoppage.value, STOPPAGE_CODE["unknown"]) if final else STOPPAGE_CODE["none"]
        i = self.n
        self.n += 1
        if self.first_t is None:
            self.first_t, self.first_w = t, self.wall
        # win-probability movement
        self.vol8.append(abs(d_wp))
        self.win.append((t, abs(d_wp), dh + da))
        while self.win and self.win[0][0] <= t - sp["vol_w"]:
            self.win.popleft()
        # lead changes, ties, runs
        sgn = (diff > 0) - (diff < 0)
        if sgn != 0 and self.sign_nz != 0 and sgn != self.sign_nz:
            self.lead_changes += 1
        if diff == 0 and self.prev_sd is not None and self.prev_sd != 0:
            self.ties += 1
        self.prev_sd = diff
        if sgn != 0:
            self.sign_nz = sgn
        self.run_home = 0.0 if da > 0 else self.run_home + dh
        self.run_away = 0.0 if dh > 0 else self.run_away + da
        # breaks
        in_break = stop in _BREAK_CODES
        if in_break:
            self.lb_t, self.lb_i, self.lb_w = t, i, self.wall
        ref_t = self.lb_t if self.lb_t is not None else self.first_t
        ref_w = self.lb_w if self.lb_i is not None else self.first_w
        # time shape
        if base == "mlb":
            hb = 1.0 if g.half == "bottom" else 0.0
            frac_left = min(1.0, max(0.0, 1 - (period - 1 + 0.5 * hb) / 9))
            is_ot = period > 9
            crunch = period >= 8 and abs(diff) <= 2
        else:
            total = sp["period_s"] * sp["n_reg"]
            frac_left = min(1.0, max(0.0, 1 - t / total))
            is_ot = period > sp["n_reg"]
            cs, cm = sp["crunch"]
            crunch = is_ot or (period == sp["n_reg"] and not math.isnan(clock) and clock <= cs and abs(diff) <= cm)
        row = dict(
            period=period, clock_s=clock, t_game=t, frac_left=frac_left, is_ot=float(is_ot), wall_s=self.wall,
            score_home=sh, score_away=sa, score_diff=diff, abs_diff=abs(diff),
            wp_home=wp, wp_lev=4 * wp * (1 - wp), wp_delta=d_wp, wp_vol_8=sum(self.vol8),
            wp_vol_w=sum(x[1] for x in self.win), lead_changes=float(self.lead_changes), ties=float(self.ties),
            run_home=self.run_home, run_away=self.run_away, pts_w=sum(x[2] for x in self.win),
            stoppage=float(stop), in_break=float(in_break), s_since_break=t - ref_t,
            ev_since_break=float(i - self.lb_i if self.lb_i is not None else i),
            wall_since_break=(self.wall - ref_w) if ref_w is not None else float("nan"),
            crunch=float(crunch))
        s = g.situation
        if BASE[base] == "nfl":
            row.update(possession_home=float("nan") if not s.possession else float(s.possession == g.home.abbr),
                       yardline_100=_f(s.yards_to_goal), down=_f(s.down), ydstogo=_f(s.distance))
        elif base == "nhl":
            row["home_skaters"], row["away_skaters"] = _skaters(g)
        elif base == "mlb":
            r = s.runners
            row.update(outs=_f(s.outs), half_bottom=1.0 if g.half == "bottom" else 0.0 if g.half else float("nan"),
                       on_1b=_f(r[0]) if r else float("nan"), on_2b=_f(r[1]) if r else float("nan"),
                       on_3b=_f(r[2]) if r else float("nan"))
        self.row = row

    def vector(self, features: list[str]) -> np.ndarray:
        r = self.row or {}
        return np.array([r.get(f, float("nan")) for f in features], dtype=np.float32)


# --- reasons -------------------------------------------------------------------------------------

_WHY = {
    "wp_vol_w": "win probability swinging", "wp_vol_8": "win probability swinging", "wp_delta": "a big swing just now",
    "wp_lev": "close game", "abs_diff": "close game", "score_diff": "close game", "wp_home": "close game",
    "frac_left": "late in the game", "t_game": "late in the game", "period": "late in the game",
    "clock_s": "the clock", "is_ot": "overtime", "crunch": "crunch time",
    "s_since_break": "a while since the last break", "ev_since_break": "a while since the last break",
    "wall_since_break": "a while since the last break", "stoppage": "a stoppage", "in_break": "a stoppage",
    "lead_changes": "back-and-forth game", "ties": "back-and-forth game",
    "run_home": "a scoring run", "run_away": "a scoring run", "pts_w": "lots of scoring",
    "yardline_100": "field position", "down": "the down", "ydstogo": "yards to go", "possession_home": "possession",
    "home_skaters": "a power play", "away_skaters": "a power play",
    "outs": "the outs", "on_1b": "runners on", "on_2b": "runners on", "on_3b": "runners on",
    "half_bottom": "the inning", "wall_s": "time of game", "score_home": "the score", "score_away": "the score",
}


def reasons_from(contrib: np.ndarray | None, features: list[str], k: int = 3) -> list[str]:
    if contrib is None:
        return []
    out: list[str] = []
    for i in np.argsort(-contrib):
        if contrib[i] <= 0 or len(out) >= k:
            break
        w = _WHY.get(features[i], features[i])
        if w not in out:
            out.append(w)
    return out


# --- the brain ---------------------------------------------------------------------------------

@dataclass
class Reading:
    break_soon_p: float | None = None
    key_soon_p: float | None = None
    hotness: float | None = None
    reasons: list[str] = field(default_factory=list)       # why the game is hot / a key moment is near
    break_reasons: list[str] = field(default_factory=list)  # why a break looks near
    events: int = 0
    key_rate: float | None = None   # the league's usual key_soon rate (what "likely" is measured against)

    def as_dict(self) -> dict:
        r = lambda x, n=3: None if x is None else round(x, n)  # noqa: E731
        return {"break_soon_p": r(self.break_soon_p), "key_soon_p": r(self.key_soon_p),
                "hotness": r(self.hotness, 1), "reasons": self.reasons, "break_reasons": self.break_reasons}


EMPTY = Reading()


class Brain:
    """Loads per-league models; keeps one Tracker and a hotness timeline per game."""

    def __init__(self, model_dir: Path | None = None, enabled: bool = True):
        self.model_dir = model_dir or MODEL_DIR
        self.enabled = enabled
        self._models: dict[str, Forest | None] = {}
        self.info: dict[str, dict] = {}
        self._track: dict[str, Tracker] = {}
        self._broken: set[str] = set()     # leagues whose model failed to predict: rules only from then on
        self._cache: dict[str, tuple[int, Reading]] = {}
        self._timeline: dict[str, list] = {}

    @classmethod
    def from_config(cls, cfg) -> "Brain":
        return cls(enabled=bool(cfg.get("brain.enabled", False)) if cfg is not None else False)

    def models(self, league: str) -> Forest | None:
        if league not in self._models:
            p = self.model_dir / f"{league}.npz"
            m = None
            if p.exists():
                try:
                    trees, self.info[league] = load(p)
                    m = Forest(trees)
                except Exception:  # noqa: BLE001 - a bad model file means heuristics, never a crash
                    log.exception("brain: could not load %s", p)
                    m = None
            self._models[league] = m
        return self._models[league]

    def available(self, league: League | str) -> bool:
        return self.enabled and self.models(getattr(league, "value", league)) is not None

    def read(self, g: GameState | None) -> Reading:
        """Predictions for this game's current state (EMPTY when off, unmodeled or not live)."""
        if g is None or not self.enabled or g.phase != Phase.LIVE:
            return EMPTY
        lg = g.league.value
        if lg in self._broken:
            return EMPTY
        ms = self.models(lg)
        if ms is None:
            return EMPTY
        tr = self._track.get(g.game_id)
        if tr is None:
            tr = self._track[g.game_id] = Tracker(lg)
            while len(self._track) > MAX_GAMES:
                self.forget(next(iter(self._track)))
        try:
            tr.observe(g)
        except Exception:  # noqa: BLE001
            log.exception("brain: feature update failed for %s", g.game_id)
            return EMPTY
        if tr.row is None:
            return EMPTY
        if tr.first_t is not None and tr.first_t > JOINED_LATE_S and tr.n < WARMUP_EVENTS:
            # Joined mid-game (a service restart): run, lead-change and time-since-break features
            # start from zero, which biased break odds up by ~0.2 and game flow by 10-20 points for
            # the first plays (review, Nov 3). The rules carry on until the tracker has caught up.
            return EMPTY
        hit = self._cache.get(g.game_id)
        if hit is not None and hit[0] == tr.n:
            return hit[1]
        out = Reading(events=tr.n, key_rate=ms.rates.get("key"))
        try:
            pred = ms.predict(tr.vector(ms.features), explain=True)
        except Exception:  # noqa: BLE001
            # A loadable but broken model fails the same way on every poll of every game: log it once
            # per league, then drop that league's model so the hand-written rules carry on.
            log.exception("brain: prediction failed for %s; using the rules for %s from now on", g.game_id, lg)
            self._broken.add(lg)
            return EMPTY
        if "break" in pred:
            out.break_soon_p = pred["break"][0]
            out.break_reasons = reasons_from(pred["break"][1], ms.features, 2)
        if "key" in pred:
            out.key_soon_p = pred["key"][0]
        if "hot" in pred:
            out.hotness = max(0.0, min(100.0, pred["hot"][0]))
            out.reasons = reasons_from(pred["hot"][1], ms.features)
        self._cache[g.game_id] = (tr.n, out)
        self._note(g, tr, out)
        return out

    def _note(self, g: GameState, tr: Tracker, r: Reading) -> None:
        tl = self._timeline.setdefault(g.game_id, [])
        row = tr.row or {}
        lp = g.last_play
        big = bool(lp and (lp.kind in ("td", "turnover", "goal", "hr") or
                           abs(row.get("wp_delta", 0.0)) >= SPEC[tr.base]["wp_key"]))
        tl.append({"t": round(row.get("t_game", 0.0), 1), "frac": round(1 - row.get("frac_left", 1.0), 4),
                   "hot": round(r.hotness or 0.0, 1), "wp": round(row.get("wp_home", 0.5), 3),
                   "key": round(r.key_soon_p or 0.0, 3), "big": big,
                   "label": (lp.text[:60] if (big and lp) else "")})
        if len(tl) > TIMELINE_MAX * 2:
            # Thin the older half, keep every big moment.
            old, recent = tl[:-TIMELINE_MAX], tl[-TIMELINE_MAX:]
            tl[:] = [x for i, x in enumerate(old) if x["big"] or i % 2 == 0] + recent

    def timeline(self, game_id: str, n: int = TIMELINE_MAX) -> list[dict]:
        """The game's momentum graph: hotness (0-100) per observed event, thinned to about n points."""
        tl = self._timeline.get(game_id, [])
        if len(tl) <= n:
            return list(tl)
        step = len(tl) / n
        keep = {int(i * step) for i in range(n)} | {i for i, x in enumerate(tl) if x["big"]} | {len(tl) - 1}
        return [tl[i] for i in sorted(keep)]

    def forget(self, game_id: str) -> None:
        for d in (self._track, self._cache, self._timeline):
            d.pop(game_id, None)
