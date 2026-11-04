"""Noggin Reflex PICK v1: which game should be on screen, learned by imitating the hindsight oracle.

The oracle (redzone/lab/oracle.py) knows every play of the night. PICK only knows what the app knows at
real time t:

- every play the feed has delivered, i.e. wall <= t - feed_lag (the measured per-league feed lag, taken
  at its 90th percentile so the model never leans on a play that might not have arrived yet);
- each screen runs `lag` behind real life, so the app already knows the next (lag - feed_lag) s of every
  screen (the time-shift buffer). Leaving / landing on a dead ball is judged inside that known stretch.

Features are per (game, 5 s step) and numpy only (`features_for_night`), so the live path and training
share one definition. The policy (`rollout`) keeps the app's hard rules: leave and land only on dead
balls (the oracle's `leave` / `land` tables), the picture black for tune_s, and your team's crunch first
(an L1 rule outside the model). Switches go to the best-scoring landable game when its score beats the
current game's by `margin`.

Offline only for now: scripts/train_noggin_pick.py builds the labels on whole factory nights, trains, and
grades rollouts with the app's own grader. No decider hook yet: PICK reaches the app only after it beats the
app's own schedule on dev nights.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from redzone.lab import cutgrade, sportsgrade
from redzone.lab.backtest import SwitchLog, lag_of
from redzone.models import League
from redzone.noggin.features import REG
from redzone.sources import espn

PICK_FEATURES = ("leave_ok", "land_ok", "dead_screen", "ahead_big", "ahead_crunch", "ahead_plays",
                 "ahead_stops", "screen_crunch", "head_crunch", "head_break", "head_stop", "since_head",
                 "period", "game_left", "margin", "mine", "pre", "post", "n_live", "crunch_games",
                 "margin_rank", "left_rank")
# p90 of each league's measured detection lag (redzone/lab/feed_lag_profile.json, Nov 2-3 2026)
FEED_LAG_P90 = {League.NBA: 70.0, League.NHL: 75.0, League.NFL: 62.0, League.CBB: 46.0}
MODEL_DIR = Path(__file__).resolve().parent / "models"


def _secs(clock) -> float:
    try:
        if clock is None:
            return math.nan
        s = str(clock)
        if ":" in s:
            m, sec = s.split(":", 1)
            return float(m) * 60 + float(sec)
        return float(s)
    except ValueError:
        return math.nan


def _per_play(league: League, plays: list[dict]) -> dict:
    """Per-play arrays the step features index into."""
    n = len(plays)
    big, crunch, action, stop, brk = (np.zeros(n) for _ in range(5))
    per, clock, margin = np.zeros(n), np.full(n, math.nan), np.zeros(n)
    prev = None
    hs = as_ = 0.0
    for i, p in enumerate(plays):
        big[i] = sportsgrade.is_big(league, p, prev)
        marker = sportsgrade._marker(sportsgrade._type(p))
        crunch[i] = sportsgrade.is_crunch(league, p) and not marker
        st = espn.stoppage_for(league, p)
        stop[i] = st != espn.Stoppage.NONE
        brk[i] = st in sportsgrade.REAL_BREAKS
        action[i] = not stop[i] and not marker
        per[i] = espn._int(espn._get(p, "period", "number")) or 0
        clock[i] = _secs(espn._get(p, "clock", "displayValue"))
        h, a = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        hs, as_ = (float(h) if h is not None else hs), (float(a) if a is not None else as_)
        margin[i] = abs(hs - as_)
        prev = p
    c = lambda x: np.concatenate([[0.0], np.cumsum(x)])          # noqa: E731
    return dict(cbig=c(big), ccrunch=c(crunch), caction=c(action), cstop=c(stop), crunch=crunch, stop=stop,
                brk=brk, per=per, clock=clock, margin=margin)


def features_for_night(replays, tab: dict, steps: np.ndarray, lag, mine: set, feed_lag: float | None = None
                       ) -> np.ndarray:
    """(G, K, F) float32 features, using only plays the feed has delivered by each step."""
    G, K = len(replays), len(steps)
    X = np.zeros((G, K, len(PICK_FEATURES)), np.float32)
    col = {f: i for i, f in enumerate(PICK_FEATURES)}
    for g, r in enumerate(replays):
        lg = r.league
        fl = FEED_LAG_P90.get(lg, 70.0) if feed_lag is None else feed_lag
        lag_g = lag_of(lag, r.gid)
        W = np.asarray(r.wall, float)
        pp = _per_play(lg, r.plays)
        known = steps - fl
        screen = steps - lag_g
        ik = np.searchsorted(W, known, side="right") - 1      # last delivered play
        isc = np.searchsorted(W, screen, side="right") - 1    # last play on screen
        ikc, isc_c = np.clip(ik, 0, None), np.clip(isc, 0, None)
        ahead = lambda cs: np.where(ik > isc, cs[ik + 1] - cs[isc + 1], 0.0)   # noqa: E731
        nper, plen = REG.get(lg, (4, 720.0))
        per, clk = pp["per"][ikc], np.nan_to_num(pp["clock"][ikc], nan=plen)
        game_left = np.where(per <= nper, (nper - per) * plen + clk, 0.0)
        teams = {r.matchup.split(" @ ")[0], r.matchup.split(" @ ")[-1]}
        pre, post = known < W[0], known > W[-1]
        X[g, :, col["leave_ok"]] = tab["leave"][g]
        X[g, :, col["land_ok"]] = tab["land"][g]
        X[g, :, col["dead_screen"]] = tab["dead"][g]
        X[g, :, col["ahead_big"]] = ahead(pp["cbig"])
        X[g, :, col["ahead_crunch"]] = ahead(pp["ccrunch"])
        X[g, :, col["ahead_plays"]] = ahead(pp["caction"])
        X[g, :, col["ahead_stops"]] = ahead(pp["cstop"])
        X[g, :, col["screen_crunch"]] = np.where(isc >= 0, pp["crunch"][isc_c], 0.0)
        X[g, :, col["head_crunch"]] = np.where(ik >= 0, pp["crunch"][ikc], 0.0)
        X[g, :, col["head_break"]] = np.where(ik >= 0, pp["brk"][ikc], 0.0)
        X[g, :, col["head_stop"]] = np.where(ik >= 0, pp["stop"][ikc], 0.0)
        X[g, :, col["since_head"]] = np.where(ik >= 0, np.minimum(known - W[ikc], 900.0), 900.0)
        X[g, :, col["period"]] = np.where(ik >= 0, per, 0.0)
        X[g, :, col["game_left"]] = np.where(pre, nper * plen, game_left)
        X[g, :, col["margin"]] = np.where(ik >= 0, pp["margin"][ikc], 0.0)
        X[g, :, col["mine"]] = bool(teams & mine)
        X[g, :, col["pre"]] = pre
        X[g, :, col["post"]] = post
    live = (X[:, :, col["pre"]] == 0) & (X[:, :, col["post"]] == 0)
    X[:, :, col["n_live"]] = live.sum(0)[None, :]
    X[:, :, col["crunch_games"]] = (X[:, :, col["head_crunch"]] * live).sum(0)[None, :]
    for f, src in (("margin_rank", "margin"), ("left_rank", "game_left")):
        v = np.where(live, X[:, :, col[src]], np.inf)
        X[:, :, col[f]] = np.where(live, v.argsort(0).argsort(0), G)
    return X


CUE_FEATURES = ("cue_s", "cue_ahead_big", "cue_ahead_crunch", "cue_ahead_plays")


def cue_features(replays, cue: np.ndarray, steps: np.ndarray, lag, feed_lag: float | None = None) -> np.ndarray:
    """[possession-start entry] (G, K, 4): what landing on each game now, cued cue[g, k] steps behind live,
    would replay before the live picture: the offset and the big / crunch / action plays between the cue point
    and the feed head. Only delivered plays (wall <= t - feed lag p90), as for PICK_FEATURES."""
    G, K = len(replays), len(steps)
    E = np.zeros((G, K, len(CUE_FEATURES)), np.float32)
    for g, r in enumerate(replays):
        if not getattr(r, "wall", None):
            continue
        fl = FEED_LAG_P90.get(r.league, 70.0) if feed_lag is None else feed_lag
        W = np.asarray(r.wall, float)
        pp = _per_play(r.league, r.plays)
        o = np.where(cue[g] >= 0, cue[g], 0) * 5.0
        ik = np.searchsorted(W, steps - fl, side="right") - 1
        isc = np.searchsorted(W, steps - lag_of(lag, r.gid) - o, side="right") - 1
        ahead = lambda cs: np.where(ik > isc, cs[ik + 1] - cs[isc + 1], 0.0)   # noqa: E731
        E[g, :, 0] = np.where(cue[g] >= 0, o, -5.0)
        E[g, :, 1] = ahead(pp["cbig"])
        E[g, :, 2] = ahead(pp["ccrunch"])
        E[g, :, 3] = ahead(pp["caction"])
    return E


def oracle_on(switches: list[SwitchLog], replays, steps: np.ndarray, tune_s: float) -> np.ndarray:
    """(K,) index of the game the oracle has on screen (or picked, while black) at each step."""
    gi = {r.gid: i for i, r in enumerate(replays)}
    on = np.full(len(steps), -1, np.int64)
    for s in switches:
        on[steps >= s.t] = gi.get(s.to, -1)
    return on


def my_hot(X: np.ndarray) -> np.ndarray:
    """(G, K) your team's game where crunch can start soon: crunch now, or the last period with <= 5 min
    left and a <= 10 point / 2 goal game (or overtime). L1: go there first and never leave it."""
    col = {f: i for i, f in enumerate(PICK_FEATURES)}
    mine, post = X[:, :, col["mine"]] > 0, X[:, :, col["post"]] > 0
    late = (X[:, :, col["game_left"]] <= 300) & (X[:, :, col["margin"]] <= 10)
    return mine & ~post & ((X[:, :, col["head_crunch"]] > 0) | late)


def rollout(score: np.ndarray, X: np.ndarray, replays, steps: np.ndarray, tune_s: float, margin: float = 0.1,
            objective: str = "pick", cue: np.ndarray | None = None) -> list[SwitchLog]:
    """Run the policy over one night: score (G, K) from the model, the rules from X. SwitchLog in time order.

    cue (oracle.cue_steps, [possession-start entry]): a switch lands on the target's last dead ball, cue[b, k]
    steps behind live (-1 = none in reach), and the picture then leaves on a dead ball of what it shows."""
    col = {f: i for i, f in enumerate(PICK_FEATURES)}
    G, K = score.shape
    blk = max(1, int(math.ceil(tune_s / 5.0)))
    leave, land = X[:, :, col["leave_ok"]] > 0, X[:, :, col["land_ok"]] > 0
    if cue is not None:
        land = cue >= 0
    my_crunch = my_hot(X)
    k = 0
    first = np.where(land[:, 0], score[:, 0], -np.inf)
    a = int(np.argmax(first))
    out = [SwitchLog(float(steps[0]), None, replays[a].gid, "start", objective)]
    k, off = blk, 0
    while k < K:
        if k - off >= 0 and leave[a, k - off] and not my_crunch[a, k]:
            cand = land[:, k].copy()
            cand[a] = False
            if cand.any():
                forced = cand & my_crunch[:, k]
                s = np.where(forced if forced.any() else cand, score[:, k], -np.inf)
                b = int(np.argmax(s))
                if forced.any() or s[b] - score[a, k] > margin:
                    o = int(cue[b, k]) if cue is not None else 0
                    out.append(SwitchLog(float(steps[k]), replays[a].gid, replays[b].gid, "pick", objective,
                                         offset=float(o * 5.0)))
                    a, off = b, o
                    k += blk
                    continue
        k += 1
    return out


def load_booster(path: Path):
    """The LightGBM PICK model (offline: scripts only; the live path gets a numpy export with the decider hook)."""
    import gzip

    import lightgbm as lgb
    path = Path(path)
    if path.suffix == ".gz":
        return lgb.Booster(model_str=gzip.decompress(path.read_bytes()).decode())
    return lgb.Booster(model_file=str(path))


# ---------------------------------------------------------------- live: numpy forest + incremental per-game book

class PickModel:
    """The PICK forest in numpy (no LightGBM at runtime): proba(X) = sigmoid(raw margin)."""

    def __init__(self, arrays: dict, meta: dict):
        from redzone.noggin.timing import Forest
        if meta.get("features") != list(PICK_FEATURES):
            raise ValueError(f"PICK features {meta.get('features')} != {list(PICK_FEATURES)}")
        self.forest = Forest(arrays, meta)
        self.meta = meta
        self.margin = float(meta.get("margin", 0.1))

    def proba(self, X: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(self.forest.raw(X), -40, 40)))

    @classmethod
    def from_booster(cls, booster, margin: float, info: dict | None = None) -> "PickModel":
        from redzone.scoring.brain3 import flatten
        dump = booster.dump_model()
        arrays, meta = flatten({"h": [dict(t, slot=0) for t in dump["tree_info"]]}, list(PICK_FEATURES))
        arrays.pop("slot", None)
        meta.update(init=0.0, margin=margin, info=info or {})
        return cls(arrays, meta)

    def save(self, path: Path) -> None:
        import json
        flat = {a: getattr(self.forest, a) for a in ("feat", "thr", "left", "right", "dleft", "miss", "value", "roots")}
        flat["meta"] = np.frombuffer(json.dumps(self.meta).encode(), np.uint8)
        np.savez_compressed(path, **flat)

    @classmethod
    def load(cls, path: Path) -> "PickModel":
        import json
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(bytes(z["meta"]).decode())
            arrays = {k: z[k] for k in z.files if k != "meta"}
        return cls(arrays, meta)


_MODELS: dict = {}


def model_for(league: str, model_dir: Path | None = None, name: str = "v1") -> PickModel | None:
    """models/pick_<league>.<name>.npz: v1 = the live oracle's imitation, cue90 = the possession-start one."""
    p = (model_dir or MODEL_DIR) / f"pick_{league}.{name}.npz"
    if p not in _MODELS:
        _MODELS[p] = PickModel.load(p) if p.exists() else None
    return _MODELS[p]


def play_dict(p) -> dict:
    """models.Play -> the ESPN play dict the PICK features read (same fields the factory writes)."""
    c = p.clock
    clock = f"{int(c // 60)}:{c % 60:04.1f}" if c is not None else ""
    d = {"id": p.id, "type": {"text": p.type_text or p.kind}, "text": p.text or "", "period": {"number": p.period},
         "clock": {"displayValue": clock}, "scoringPlay": bool(p.scoring)}
    if p.score is not None:
        d["homeScore"], d["awayScore"] = p.score
    return d


class LiveGame:
    """One game's delivered plays (wallclock order), shaped like a backtest Replay for the shared features."""

    def __init__(self, league: League, gid: str, matchup: str):
        self.league, self.gid, self.matchup = league, gid, matchup
        self.plays: list[dict] = []
        self.wall: list[float] = []
        self._ids: set = set()

    def add(self, plays) -> None:
        for p in plays:
            if not p.id or p.id in self._ids or p.wallclock is None:
                continue
            self._ids.add(p.id)
            d, w = play_dict(p), float(p.wallclock)
            j = len(self.wall)
            while j and self.wall[j - 1] > w:     # rare out-of-order delivery
                j -= 1
            self.plays.insert(j, d)
            self.wall.insert(j, w)


def live_choice(games: list[LiveGame], current: str | None, now: float, lags: dict, mine: set, tune_s: float,
                model: PickModel, cue_s: float | None = None) -> tuple[str | None, dict]:
    """PICK at one tick: the game to switch to now (None = stay), and the scores. Same features and rule as
    `rollout`, from the plays delivered so far (the feed lag p90 cut keeps it to what training saw)."""
    from types import SimpleNamespace

    from redzone.lab import oracle as O
    steps = np.array([now])
    G = len(games)
    leave, land, dead = np.zeros((G, 1), bool), np.zeros((G, 1), bool), np.zeros((G, 1))
    for g, r in enumerate(games):
        if not r.wall:
            land[g, 0], dead[g, 0] = True, 1.0
            continue
        lag = float(lags.get(r.gid, 0.0))
        st, sl = now - lag, now + tune_s - lag
        leave[g, 0] = O._clean_leave(r.league, r.wall, r.plays, st) and cutgrade._dead_at(r.league, r.wall, r.plays, st)[0]
        land[g, 0] = (sl < r.wall[0] or sl > r.wall[-1]) or cutgrade._dead_at(r.league, r.wall, r.plays, sl)[0]
        if cue_s and not land[g, 0]:
            # [possession start] a cue model lands on the last dead ball up to cue_s behind live (as trained)
            land[g, 0] = any(cutgrade._dead_at(r.league, r.wall, r.plays, sl - 5.0 * j)[0]
                             for j in range(1, int(cue_s // 5.0) + 1))
        if r.wall[0] <= st <= r.wall[-1]:
            i = cutgrade._idx(r.wall, st)
            dead[g, 0] = espn.stoppage_for(r.league, r.plays[max(0, i)]) in sportsgrade.REAL_BREAKS
        else:
            dead[g, 0] = 1.0
    reps = [r if r.wall else SimpleNamespace(league=r.league, gid=r.gid, matchup=r.matchup, wall=[math.inf],
                                             plays=[{}]) for r in games]
    from redzone.lab.backtest import Lags
    lagmap = Lags(0.0, {k: float(v) for k, v in lags.items()})
    X = features_for_night(reps, dict(leave=leave, land=land, dead=dead), steps, lagmap, mine)
    sc = model.proba(X[:, 0, :])
    gi = {r.gid: i for i, r in enumerate(games)}
    a = gi.get(current)
    hot = my_hot(X)[:, 0]
    scores = {r.gid: float(sc[i]) for i, r in enumerate(games)}
    if a is None:
        cand = land[:, 0]
        return (games[int(np.argmax(np.where(cand, sc, -np.inf)))].gid if cand.any() else None), scores
    if not leave[a, 0] or hot[a]:
        return None, scores
    cand = land[:, 0].copy()
    cand[a] = False
    if not cand.any():
        return None, scores
    forced = cand & hot
    s = np.where(forced if forced.any() else cand, sc, -np.inf)
    b = int(np.argmax(s))
    if forced.any() or s[b] - sc[a] > model.margin:
        return games[b].gid, scores
    return None, scores

