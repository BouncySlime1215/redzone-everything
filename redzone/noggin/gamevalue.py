"""GameValue runtime: one game's causal features -> value_next_3m (0-100) and p_tier1_60s (0-1), numpy only.

Why: past the look-ahead the brain needs an estimate of what each game is worth, cheaply enough to run for every live
game on the 8 GB Mac, and a bad input must produce no number at all, never a confident wrong one (the-mind sections 3
and 8, build plan WP-26). Offline for now: nothing in the engine calls this; scripts/kaggle_gamevalue.py trains and
exports what it loads.

    model = load(path, "nfl")                               # ModelRejected on anything it cannot vouch for
    model.estimate({"401": features_dict, ...})             # {game id: Estimate, or None when that game failed closed}

Two model kinds in one file format: v0 is the shrunken state table (scripts/holdset.py's key, as a dense array), v1 is a
LightGBM export walked by models_rt.forest.Nodes, a Tweedie head for the value and a binary head with isotonic knots
for Tier 1. The feature list is value_shards.VALUE_FEATURES; it is restated here because importing value_shards would
pull pandas into the live process (a test keeps the two in step).

The 0-100 scale: value_shards' `value_next_3m` is raw points (Tier 1 = 5, Tier 2 = 2, Tier 3 = 0.5, summed over 180 s).
The brain contract and the queue want 0-100, so value = 100 * (1 - exp(-raw / 5)): one expected Tier 1 reads 63, two
read 86. One fixed curve for every league, because a Tier 1 is worth the same everywhere and a per-league percentile
would break that. Strictly increasing, so the top-K order is exactly the raw-points order, and bounded without a clip
that would tie the hottest games.

Fails closed: a row with a NaN, an infinity, a non-number, a missing or extra feature, or a value outside its physical
range gets no estimate; the others in the batch are unaffected. A model file that is corrupt, altered, for another
league or feature list, missing its personal-use tag, or marked anything but shadow-only does not load.

Promotion is not a flag here: `promotion` needs the real Planner, measured on the same nights, beaten on the hold-set test nights, then 7 of 7 shadow
nights, and the file status stays "shadow_only" until WP-28 changes this module on purpose.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NamedTuple, Sequence

import numpy as np

from redzone.context.tokens import PERSONAL_USE
from redzone.models_rt.forest import MODEL_DIRS, Nodes, read_meta

GV_SCHEMA = 1
GV_LEAGUES = ("nfl", "nba", "cbb")
_BASE_FEATURES = ("period", "clock_left", "game_left", "is_ot", "late", "abs_margin", "total_points", "pts_5m", "swing_5m",
                  "lead_changes", "plays_5m", "since_head_s", "since_score_s", "since_start_s", "t1_5m", "t2_5m", "t3_5m",
                  "t1_game", "value_10m", "down", "distance", "yards_to_goal", "playoff")
# v3 appends; the first 23 columns stay where v2 has them, so table_key reads both.
HISTORY_FEATURES = ("pts_last3", "pts_last5", "swing_last5", "run_pts", "run_by_trailing", "to_5m", "fouls_5m", "plays_2m",
                    "sec_per_play_10", "timeouts_period", "since_timeout_s", "timeout_recent")
GV_FEATURE_SETS = {"v2": _BASE_FEATURES, "v3": _BASE_FEATURES + HISTORY_FEATURES}
GV_FEATURES_ENV = "RZ_GV_FEATURES"
# Physical sanity, not the training range: -1 is the shards' "missing". since_head_s stops where the shards stop (a 900 s
# feed gap plus the 180 s lag cap): a feed staler than any training row gets no estimate.
GV_BOUNDS = {"period": (-1, 20), "clock_left": (-1, 3600), "game_left": (-1, 14400), "is_ot": (0, 1), "late": (0, 1),
             "abs_margin": (0, 300), "total_points": (0, 600), "pts_5m": (-300, 300), "swing_5m": (0, 600),
             "lead_changes": (0, 200), "plays_5m": (0, 2000), "since_head_s": (0, 1080), "since_score_s": (0, 86400),
             "since_start_s": (0, 86400), "t1_5m": (0, 500), "t2_5m": (0, 500), "t3_5m": (0, 500), "t1_game": (0, 1000),
             "value_10m": (0, 5000), "down": (-1, 5), "distance": (-100, 200), "yards_to_goal": (-100, 200),
             "playoff": (0, 1), "pts_last3": (-300, 300), "pts_last5": (-300, 300), "swing_last5": (0, 600), "run_pts": (0, 600),
             "run_by_trailing": (0, 1), "to_5m": (0, 500), "fouls_5m": (0, 500), "plays_2m": (0, 2000),
             "sec_per_play_10": (-1, 86400), "timeouts_period": (0, 100), "since_timeout_s": (-1, 86400), "timeout_recent": (0, 1)}
GV_MAX_ROWS = 64                     # more live games than this is a broken caller, not a big slate
GV_MAX_BYTES = 20_000_000
GV_SCALE_POINTS = 5.0                # raw points that read 63 on the 0-100 scale: one Tier 1
GV_TABLE_SHAPE = (6, 4, 4, 4)        # period, clock band, margin band, field-position band (scripts/holdset.py key)
GV_STATUS = "shadow_only"
GV_PERSONAL_SOURCES = ("hoopr_espn_nba", "hoopr_espn_cbb", "archive")
GV_SHADOW_NIGHTS = 7
GV_DIR = MODEL_DIRS["noggin"]

GV_FEATURES_VERSION, GV_FEATURES = "", ()
_COL: dict[str, int] = {}
_LO, _HI = np.zeros(0), np.zeros(0)


def select_features(version: str) -> None:
    """Make `version` the active feature list. v2 stays the default; RZ_GV_FEATURES=v3 picks the history features for a whole
    run (the shard builder and the trainer read it at import). An unknown name stops the import."""
    global GV_FEATURES_VERSION, GV_FEATURES, _COL, _LO, _HI
    if version not in GV_FEATURE_SETS:
        raise ValueError(f"{GV_FEATURES_ENV}={version!r}: known versions are {sorted(GV_FEATURE_SETS)}")
    GV_FEATURES_VERSION, GV_FEATURES = version, GV_FEATURE_SETS[version]
    _COL = {f: i for i, f in enumerate(GV_FEATURES)}
    _LO = np.array([GV_BOUNDS[f][0] for f in GV_FEATURES], float)
    _HI = np.array([GV_BOUNDS[f][1] for f in GV_FEATURES], float)


select_features(os.environ.get(GV_FEATURES_ENV, "v2"))
_NODE_ARRAYS = ("feat", "thr", "left", "right", "dleft", "miss", "value", "roots")


class ModelRejected(ValueError):
    """A model file this runtime will not vouch for."""


class BadFeatures(ValueError):
    """A feature matrix of the wrong shape or type: nothing in it can be scored."""


@dataclass(frozen=True)
class Estimate:
    value_next_3m: float             # 0-100, see the module docstring
    p_tier1_60s: float               # calibrated, 0-1
    raw_points: float                # the trained target: expected tier points in the next 180 s


class Batch(NamedTuple):
    raw: np.ndarray
    value: np.ndarray
    p: np.ndarray
    ok: np.ndarray                   # False: failed closed; raw, value and p are NaN there


# --- recent history (features v3) -----------------------------------------------------------------------------------
# One definition for the training rows and the live feed, numpy only, so a live caller never needs pandas. Everything is
# read from the plays up to the head: per-play arrays filled oldest first, then indexed at the head.

_HOOPS_VIOLATIONS = ("traveling", "shot clock", "backcourt", "palming", "double dribble", "kicked ball", "8 second")


def play_kind(league: str, play: Mapping) -> str:
    """'timeout', 'turnover', 'foul' or '' from ESPN's play type (and text, for hoops turnovers). Football's foul is a penalty."""
    kind = ((play.get("type") or {}).get("text") or "").lower()
    if "timeout" in kind:
        return "timeout"
    if league in ("nfl", "cfb"):
        if "interception" in kind or "fumble recovery (opponent)" in kind or "fumble return" in kind:
            return "turnover"
        return "foul" if "penalty" in kind else ""
    text = (play.get("text") or "").lower()
    if "turnover" in kind or "turnover" in text or any(v in kind for v in _HOOPS_VIOLATIONS):
        return "turnover"
    return "foul" if "foul" in kind else ""


@dataclass(frozen=True)
class History:
    """Per-play arrays, oldest first, of everything the history features read."""
    walls: np.ndarray
    total: np.ndarray
    margin: np.ndarray
    run_pts: np.ndarray
    run_trailing: np.ndarray
    turnover_t: np.ndarray
    foul_t: np.ndarray
    timeout_t: np.ndarray
    is_timeout: np.ndarray
    timeouts_in_period: np.ndarray    # timeouts so far in this play's period, this play included


def history_arrays(league: str, timed_plays: Sequence[tuple[float, Mapping]]) -> History:
    """(wall, play) in wall order -> History. A run is the unanswered points of the team that scored last."""
    n = len(timed_plays)
    total, margin, run_pts, run_trailing, is_timeout, in_period = (np.zeros(n) for _ in range(6))
    events: dict[str, list[float]] = {"turnover": [], "foul": [], "timeout": []}
    home = away = 0.0
    run, team, period, count = 0.0, 0, None, 0
    for i, (wall, p) in enumerate(timed_plays):
        h1, a1 = p.get("homeScore"), p.get("awayScore")
        nh, na = (home if h1 is None else float(h1)), (away if a1 is None else float(a1))
        dh, da = nh - home, na - away
        if dh < 0 or da < 0:              # a score correction: the run no longer means anything
            run, team = 0.0, 0
        elif dh > 0 or da > 0:
            scorer = 1 if dh >= da else -1
            run, team = (run + dh + da, scorer) if scorer == team else (dh + da, scorer)
        home, away = nh, na
        total[i], margin[i], run_pts[i] = home + away, home - away, run
        run_trailing[i] = float(team != 0 and team * (home - away) < 0)
        kind = play_kind(league, p)
        if kind:
            events[kind].append(wall)
        now = (p.get("period") or {}).get("number")
        if now != period:
            period, count = now, 0
        count += kind == "timeout"
        is_timeout[i], in_period[i] = float(kind == "timeout"), count
    return History(np.array([w for w, _ in timed_plays], float), total, margin, run_pts, run_trailing,
                   *(np.array(events[k], float) for k in ("turnover", "foul", "timeout")), is_timeout, in_period)


def _back(a: np.ndarray, head: np.ndarray, n: int) -> np.ndarray:
    """a[head - n], and 0 where the game has fewer than n earlier plays (the state before the first play)."""
    return np.where(head >= n, a[np.maximum(head - n, 0)], 0.0)


def _since(times: np.ndarray, wall: np.ndarray) -> np.ndarray:
    """Seconds since the last event at or before `wall`, -1 when there has been none."""
    i = np.searchsorted(times, wall, "right") - 1
    return np.where(i >= 0, wall - np.concatenate((times, [0.0]))[np.maximum(i, 0)], -1.0) if len(times) else np.full(len(wall), -1.0)


def history_features(h: History, head: np.ndarray) -> dict[str, np.ndarray]:
    """The twelve v3 features at each head index. Reads only plays at or before the head."""
    wall = h.walls[head]
    count = lambda times, span: (np.searchsorted(times, wall, "right") - np.searchsorted(times, wall - span, "right")).astype(float)  # noqa: E731
    return {
        "pts_last3": h.total[head] - _back(h.total, head, 3), "pts_last5": h.total[head] - _back(h.total, head, 5),
        "swing_last5": np.abs(h.margin[head] - _back(h.margin, head, 5)), "run_pts": h.run_pts[head],
        "run_by_trailing": h.run_trailing[head], "to_5m": count(h.turnover_t, 300.0), "fouls_5m": count(h.foul_t, 300.0),
        "plays_2m": (head - (np.searchsorted(h.walls, wall - 120.0, "right") - 1)).astype(float),
        "sec_per_play_10": np.where(head >= 10, (wall - h.walls[np.maximum(head - 10, 0)]) / 10.0, -1.0),
        "timeouts_period": h.timeouts_in_period[head], "since_timeout_s": _since(h.timeout_t, wall),
        "timeout_recent": np.maximum(h.is_timeout[head], np.where(head >= 1, h.is_timeout[np.maximum(head - 1, 0)], 0.0)),
    }


def history_now(league: str, timed_plays: Sequence[tuple[float, Mapping]]) -> dict[str, float]:
    """The live call: the feed's plays so far, (wall, play) in wall order -> the v3 features at the feed head. {} before any play."""
    if not timed_plays:
        return {}
    got = history_features(history_arrays(league, timed_plays), np.array([len(timed_plays) - 1]))
    return {k: float(v[0]) for k, v in got.items()}


def to_scale(raw: np.ndarray) -> np.ndarray:
    """Raw tier points -> the brain contract's 0-100."""
    return 100.0 * (1.0 - np.exp(-np.maximum(raw, 0.0) / GV_SCALE_POINTS))


def valid_rows(X: np.ndarray) -> np.ndarray:
    """True per row when every feature is finite and inside its physical range (NaN compares False)."""
    return ((X >= _LO) & (X <= _HI)).all(axis=1)


def table_key(X: np.ndarray, league: str) -> np.ndarray:
    """(n, 4) cell indices: scripts/holdset.py `key` on the shard features (-1 there is None here)."""
    clock, margin = X[:, _COL["clock_left"]], X[:, _COL["abs_margin"]]
    key = np.zeros((len(X), 4), np.int64)
    key[:, 0] = np.clip(X[:, _COL["period"]], 0, 5)
    key[:, 1] = np.select([clock < 0, clock <= 120, clock <= 300], [3, 0, 1], 2)
    key[:, 2] = np.select([margin <= 3, margin <= 8, margin <= 16], [0, 1, 2], 3)
    if league == "nfl":
        y = X[:, _COL["yards_to_goal"]]
        key[:, 3] = np.select([y < 0, y <= 20, y <= 40], [3, 0, 1], 2)
    return key


def content_sha(arrays: Mapping[str, np.ndarray], meta: Mapping) -> str:
    """Hash of every array and every meta field but the hash itself."""
    h = hashlib.sha256(json.dumps({k: v for k, v in meta.items() if k != "sha256"}, sort_keys=True).encode())
    for name in sorted(arrays):
        a = np.asarray(arrays[name])
        h.update(f"{name}|{a.dtype.str}|{a.shape}".encode())
        h.update(a.tobytes())
    return h.hexdigest()


def _check_meta(meta: Mapping, league: str | None) -> None:
    if meta.get("schema") != GV_SCHEMA or meta.get("kind") not in ("table", "trees"):
        raise ModelRejected(f"unknown model format: schema {meta.get('schema')}, kind {meta.get('kind')}")
    if meta.get("league") not in GV_LEAGUES or (league is not None and meta["league"] != league):
        raise ModelRejected(f"model is for {meta.get('league')}, asked for {league}")
    if tuple(meta.get("features") or ()) != GV_FEATURES or meta.get("features_version") != GV_FEATURES_VERSION:
        raise ModelRejected("feature list or feature version differs from this runtime")
    if meta.get("status") != GV_STATUS:
        raise ModelRejected(f"status {meta.get('status')!r}: only {GV_STATUS!r} loads until WP-28")
    if meta.get("scale_points") != GV_SCALE_POINTS:
        raise ModelRejected("trained for a different 0-100 scale")
    if (meta.get("leak_check") or {}).get("passed") is not True:
        raise ModelRejected("no passed leak check on record")
    sources, lines = meta.get("sources"), meta.get("license_lines")
    if not sources or not lines:
        raise ModelRejected("no data sources or licence lines on record")
    personal = any(PERSONAL_USE in str(x) for x in lines) or any(s in GV_PERSONAL_SOURCES for s in sources)
    if personal and meta.get("license_tag") != PERSONAL_USE:
        raise ModelRejected("trained on personal-use sources without the personal-use tag")


def _head(arrays: Mapping[str, np.ndarray], prefix: str, depth) -> Nodes:
    try:
        part = {k: np.asarray(arrays[f"{prefix}_{k}"]) for k in _NODE_ARRAYS}
    except KeyError as e:
        raise ModelRejected(f"missing array {e}") from e
    if not isinstance(depth, int) or isinstance(depth, bool) or not 0 <= depth <= 64:
        raise ModelRejected(f"{prefix}: bad depth {depth!r}")
    n = len(part["feat"])
    if n == 0 or len(part["roots"]) == 0 or any(len(part[k]) != n for k in _NODE_ARRAYS if k != "roots"):
        raise ModelRejected(f"{prefix}: node arrays disagree in length")
    if any(part[k].dtype.kind not in "iuf" for k in ("feat", "left", "right", "roots")):
        raise ModelRejected(f"{prefix}: non-numeric node arrays")
    if not (np.isfinite(part["value"]).all() and np.isfinite(part["thr"]).all()):
        raise ModelRejected(f"{prefix}: a non-finite threshold or leaf")
    nodes = Nodes(part, depth)
    in_range = [(nodes.left, n), (nodes.right, n), (nodes.roots, n), (nodes.feat, len(GV_FEATURES))]
    if any(a.size and (a.min() < 0 or a.max() >= top) for a, top in in_range):
        raise ModelRejected(f"{prefix}: a node points outside the model")
    return nodes


def _knots(arrays: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    try:
        x, y = np.asarray(arrays["p1_cal_x"], float), np.asarray(arrays["p1_cal_y"], float)
    except (KeyError, TypeError, ValueError) as e:
        raise ModelRejected(f"bad calibration knots: {e}") from e
    if x.ndim != 1 or x.shape != y.shape or x.size == 0 or not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ModelRejected("bad calibration knots")
    if np.any(np.diff(x) < 0) or np.any(np.diff(y) < 0) or y.min() < 0 or y.max() > 1:
        raise ModelRejected("calibration knots are not monotone inside 0-1")
    return x, y


def _table(arrays: Mapping[str, np.ndarray], name: str, top: float) -> np.ndarray:
    t = np.asarray(arrays.get(name, np.zeros(0)), float)
    if t.shape != GV_TABLE_SHAPE or not np.isfinite(t).all() or t.min() < 0 or t.max() > top:
        raise ModelRejected(f"bad table {name}")
    return t.ravel()


class GameValueModel:
    """One league's two heads. Construction validates; `load` adds the file checks."""

    def __init__(self, arrays: Mapping[str, np.ndarray], meta: Mapping, league: str | None = None):
        _check_meta(meta, league)
        self.meta, self.league, self.kind, self.version = dict(meta), meta["league"], meta["kind"], meta.get("version", "")
        if self.kind == "table":
            self._value_table, self._p_table = _table(arrays, "value_table", 1e6), _table(arrays, "p1_table", 1.0)
        else:
            self._value, self._p1 = _head(arrays, "value", meta.get("value_depth")), _head(arrays, "p1", meta.get("p1_depth"))
            self._cal_x, self._cal_y = _knots(arrays)

    def _predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Already validated float32 rows -> (raw points, probability)."""
        if self.kind == "table":
            cell = np.ravel_multi_index(table_key(X, self.league).T, GV_TABLE_SHAPE)
            return self._value_table[cell], self._p_table[cell]
        with np.errstate(over="ignore"):
            raw = np.exp(self._value.value[self._value.leaves_batch(X)].sum(axis=1))
            margin = self._p1.value[self._p1.leaves_batch(X)].sum(axis=1)
            p = 1.0 / (1.0 + np.exp(-margin))
        return raw, np.interp(p, self._cal_x, self._cal_y)

    def predict_matrix(self, X, max_rows: int | None = GV_MAX_ROWS) -> Batch:
        """(n, len(GV_FEATURES)) features in GV_FEATURES order. BadFeatures for a wrong shape or type; a bad row is ok=False."""
        X = np.asarray(X)
        if X.dtype.kind not in "fiub" or X.ndim != 2 or X.shape[1] != len(GV_FEATURES):
            raise BadFeatures(f"expected (n, {len(GV_FEATURES)}) numbers, got {X.dtype} {X.shape}")
        if max_rows is not None and len(X) > max_rows:
            raise BadFeatures(f"{len(X)} rows is more than {max_rows}")
        X = X.astype(np.float64)
        ok = valid_rows(X)
        raw, p = np.full(len(X), np.nan), np.full(len(X), np.nan)
        if ok.any():
            raw[ok], p[ok] = self._predict(X[ok].astype(np.float32))
        ok &= np.isfinite(raw) & np.isfinite(p)
        raw[~ok], p[~ok] = np.nan, np.nan
        return Batch(raw, to_scale(raw), p, ok)

    def estimate(self, games: Mapping[str, Mapping]) -> dict[str, Estimate | None]:
        """{game id: features} -> {game id: Estimate or None}. {} when the batch itself is not usable."""
        if not isinstance(games, Mapping) or len(games) > GV_MAX_ROWS:
            return {}
        ids, rows, out = [], [], {}
        for gid, feats in games.items():
            out[gid] = None
            row = _row(feats)
            if row is not None:
                ids.append(gid)
                rows.append(row)
        if rows:
            b = self.predict_matrix(np.array(rows, float))
            for i, gid in enumerate(ids):
                if b.ok[i]:
                    out[gid] = Estimate(float(b.value[i]), float(b.p[i]), float(b.raw[i]))
        return out


def _row(feats) -> list[float] | None:
    """One game's dict -> a row in GV_FEATURES order, or None. Numbers only: "5" is not a feature value."""
    if not isinstance(feats, Mapping) or len(feats) != len(GV_FEATURES):
        return None
    row = []
    try:
        for name in GV_FEATURES:
            v = feats[name]
            if not isinstance(v, (int, float, np.integer, np.floating, np.bool_)):
                return None
            row.append(float(v))
    except (KeyError, OverflowError):
        return None
    return row


def save(path: Path, arrays: Mapping[str, np.ndarray], meta: Mapping) -> dict:
    """Validate, stamp the content hash, write atomically. Returns the stamped meta."""
    GameValueModel(arrays, meta)
    stamped = {**meta, "sha256": content_sha(arrays, meta)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        np.savez_compressed(f, meta=np.frombuffer(json.dumps(stamped).encode(), dtype=np.uint8), **arrays)
    tmp.replace(path)
    return stamped


def load(path: Path, league: str | None = None) -> GameValueModel:
    """A model file, or ModelRejected. No pickle, no LightGBM."""
    path = Path(path)
    try:
        if path.stat().st_size > GV_MAX_BYTES:
            raise ModelRejected(f"{path.name}: larger than {GV_MAX_BYTES} bytes")
        with np.load(path, allow_pickle=False) as z:
            meta = read_meta(z)
            arrays = {k: z[k] for k in z.files if k != "meta"}
    except (OSError, ValueError, KeyError, EOFError, TypeError, zipfile.BadZipFile) as e:
        raise ModelRejected(f"{path.name}: unreadable ({type(e).__name__}: {e})") from e
    if not isinstance(meta, dict) or meta.get("sha256") != content_sha(arrays, meta):
        raise ModelRejected(f"{path.name}: content does not match its hash")
    return GameValueModel(arrays, meta, league)


def model_path(league: str, version: str = "v1", directory: Path = GV_DIR) -> Path:
    return Path(directory) / f"gamevalue_{league}_{version}.npz"


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def promotion(league: str, model_k4_pct, shadow_nights: Sequence[bool], planner_k4_pct=None) -> tuple[bool, str]:
    """(may promote, why). The model's K=4 capture must beat the real Planner's, strictly, and the last 7 shadow nights
    must all have passed. `planner_k4_pct` is the Planner measured on the same nights as the model
    (scripts/kaggle_gamevalue_holdset.py): a stored number from other nights is not a bar, so without it nothing promotes."""
    if league not in GV_LEAGUES:
        return False, f"{league}: no GameValue model for this league"
    if not _number(planner_k4_pct):
        return False, f"{league}: no real-Planner K=4 measured on the same nights"
    bar = float(planner_k4_pct)
    if not _number(model_k4_pct):
        return False, f"{league}: no K=4 result"
    if model_k4_pct <= bar:
        return False, f"{league}: K=4 {model_k4_pct:.1f} does not beat the real Planner {bar:.1f}"
    last = list(shadow_nights)[-GV_SHADOW_NIGHTS:]
    passed = sum(1 for n in last if n is True)
    if len(last) < GV_SHADOW_NIGHTS or passed < GV_SHADOW_NIGHTS:
        return False, f"{league}: {passed} of {GV_SHADOW_NIGHTS} shadow nights passed"
    return True, f"{league}: K=4 {model_k4_pct:.1f} beats {bar:.1f}, {GV_SHADOW_NIGHTS} of {GV_SHADOW_NIGHTS} shadow nights"
