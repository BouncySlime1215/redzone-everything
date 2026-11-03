"""Brain v3: one compact model per league, numpy only at runtime.

One file per league (``brain_models/<league>.v3.npz``, trained by ``scripts/train_brain3.py``) holds
every head as LightGBM trees flattened into arrays, walked together in one pass, plus isotonic
calibration knots for the probabilities:

    stop15/30/60   P(a stoppage STARTS within 15 / 30 / 60 s of wall time)
    type           P(its type | a stoppage within 60 s): TV timeout, team timeout, review, free throws,
                   injury, score break, period end, other
    len10/50/90    stoppage length quantiles (s), for the stoppage that starts at this event
    entry10/50/90  seconds from this event until the next live snap / faceoff / inbound
    big60/180      P(a big play within 1 / 3 min): TD, turnover, goal (incl. power-play goals),
                   lead change late, clutch shot, big win-probability swing
    hot            0-100 game flow (the momentum graph)

Features are v2's (``brain.Tracker``) plus a short memory of the last ~20 plays (what kind of play
each was, how many stoppages / fouls / scores, wall-clock pace, breaks and timeouts this period),
rebuilt one event at a time exactly as ``redzone.lab.brain3data`` builds the training rows.

Consumers read ``Reading.v3`` (a ``V3`` below, or None): every number is already calibrated, and
times are seconds from ``V3.at`` (the wallclock of the event the reading is about).
"""
from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from redzone.models import GameState
from redzone.scoring import brain as B

TYPES = ("none", "tv_timeout", "team_timeout", "review", "free_throws", "injury", "score_break",
         "period_end", "other")
KCLASS = ("action", "score", "turnover", "punt", "kickoff", "foul", "timeout", "free_throw",
          "period_end", "review", "whistle", "shot", "faceoff", "pitch_change")
KC = {k: i for i, k in enumerate(KCLASS)}
NHL_PENALTIES = frozenset({"hooking", "holding", "tripping", "roughing", "interference", "slashing", "high-sticking",
                           "fighting", "unsportsmanlike conduct", "cross-checking", "delay of game", "too many men",
                           "penalty", "holding the stick", "boarding", "elbowing", "charging", "misconduct"})
SEQ = 20
V3_EXTRA = ["kc0", "kc1", "kc2", "wall_gap", "pace5", "n_stop20", "n_brk20", "n_foul20", "n_score20",
            "brk_period", "to_period", "fouls_period", "ft_pending"]
BINARY = ("stop15", "stop30", "stop60", "big60", "big180")
QUANT = ("len10", "len50", "len90", "entry10", "entry50", "entry90")


def kclass(stoppage: str, ttext: str, desc: str = "") -> int:
    """One event's class (the same function builds the training rows: redzone.lab.brain3data)."""
    t = (ttext or "").lower().replace("_", " ")
    d = (desc or "").lower()
    if stoppage in ("end_period", "halftime", "intermission", "game_end", "two_minute_warning"):
        return KC["period_end"]
    if stoppage == "timeout" or "timeout" in t:
        return KC["timeout"]
    if stoppage == "review":
        return KC["review"]
    if stoppage == "score_break":
        return KC["score"]
    if stoppage == "pitching_change" or "pitching change" in t:
        return KC["pitch_change"]
    if "free throw" in t or "freethrow" in t.replace(" ", ""):
        return KC["free_throw"]
    if "punt" in t:
        return KC["punt"]
    if "kickoff" in t or "kick off" in t:
        return KC["kickoff"]
    if ("intercept" in t or "fumble" in t or "turnover" in t or "giveaway" in t or "takeaway" in t
            or "steal" in t):
        return KC["turnover"]
    if "foul" in t or "penalty" in t or "penalty" in d[:40] or t in NHL_PENALTIES:
        return KC["foul"]
    if "faceoff" in t or "face off" in t:
        return KC["faceoff"]
    if stoppage in ("whistle", "injury", "unknown"):
        return KC["whistle"]
    if "shot" in t or "goal" in t or "layup" in t or "dunk" in t:
        return KC["shot"]
    return KC["action"]


def features_for(league: str) -> list[str]:
    return B.features_for(league) + V3_EXTRA


# ----------------------------------------------------------------------------- trees

class Forest3:
    """Every head's trees in one set of arrays. Each tree adds its leaf to one output slot
    (a binary / quantile head has one slot, the type head one per class)."""

    def __init__(self, arrays: dict, meta: dict):
        self.feat = arrays["feat"].astype(np.int64)
        self.thr = arrays["thr"].astype(np.float64)
        self.left = arrays["left"].astype(np.int64)
        self.right = arrays["right"].astype(np.int64)
        self.dleft = arrays["dleft"].astype(bool)
        self.miss = arrays["miss"].astype(np.int8)
        self.value = arrays["value"].astype(np.float64)
        self.roots = arrays["roots"].astype(np.int64)
        self.slot = arrays["slot"].astype(np.int64)
        self.depth = int(meta["depth"])
        self.nslots = int(meta["nslots"])
        self.miss_zero, self.miss_nan = self.miss == 1, self.miss == 2
        self.features = list(meta["features"])
        self.heads = meta["heads"]          # name -> {"slots": [..], "kind": ..., "iso": [[x], [y]] | None}
        self.info = meta.get("info", {})

    def raw(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32).astype(np.float64)
        idx = self.roots
        for _ in range(self.depth):
            f = self.feat[idx]
            v = x[f]
            nan = np.isnan(v)
            v = np.where(nan & ~self.miss_nan[idx], 0.0, v)
            to_default = (self.miss_zero[idx] & (np.abs(v) <= 1e-35)) | (self.miss_nan[idx] & nan)
            go_left = np.where(to_default, self.dleft[idx], v <= self.thr[idx])
            idx = np.where(go_left, self.left[idx], self.right[idx])
        return np.bincount(self.slot, weights=self.value[idx], minlength=self.nslots)

    def predict(self, x: np.ndarray) -> dict[str, np.ndarray | float]:
        out_raw = self.raw(x)
        out: dict[str, np.ndarray | float] = {}
        for name, h in self.heads.items():
            r = out_raw[h["slots"]]
            if h["kind"] == "binary":
                p = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, float(r[0])))))
                iso = h.get("iso")
                if iso:
                    p = float(np.interp(p, iso[0], iso[1]))
                out[name] = p
            elif h["kind"] == "multiclass":
                z = r - r.max()
                e = np.exp(z)
                out[name] = e / e.sum()
            else:
                out[name] = float(r[0])
        return out


def flatten(heads: dict[str, list[dict]], features: list[str]) -> tuple[dict, dict]:
    """{head: [LightGBM tree dicts, ...] with 'slot' set on each} -> arrays, meta."""
    feat, thr, left, right, dleft, miss, value, roots, slot = [], [], [], [], [], [], [], [], []
    depth = 0

    def walk(n: dict, d: int) -> int:
        nonlocal depth
        depth = max(depth, d)
        i = len(feat)
        feat.append(0), thr.append(0.0), left.append(i), right.append(i)
        dleft.append(False), miss.append(0)
        if "leaf_value" in n:
            value.append(float(n["leaf_value"]))
            return i
        value.append(0.0)
        if n.get("decision_type", "<=") != "<=":
            raise ValueError("categorical splits are not supported")
        feat[i] = int(n["split_feature"])
        thr[i] = float(n["threshold"])
        dleft[i] = bool(n.get("default_left", True))
        miss[i] = {"None": 0, "Zero": 1, "NaN": 2}[n.get("missing_type", "None")]
        left[i] = walk(n["left_child"], d + 1)
        right[i] = walk(n["right_child"], d + 1)
        return i

    for trees in heads.values():
        for t in trees:
            roots.append(walk(t["tree_structure"], 0))
            slot.append(t["slot"])
    arrays = dict(feat=np.array(feat, np.int16), thr=np.array(thr, np.float64), left=np.array(left, np.int32),
                  right=np.array(right, np.int32), dleft=np.array(dleft, bool), miss=np.array(miss, np.int8),
                  value=np.array(value, np.float32), roots=np.array(roots, np.int32), slot=np.array(slot, np.int16))
    return arrays, {"depth": depth, "features": features}


def save(path: Path, arrays: dict, meta: dict) -> None:
    flat = dict(arrays)
    flat["meta"] = np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **flat)


def load(path: Path) -> Forest3:
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(bytes(z["meta"]).decode())
        arrays = {k: z[k] for k in z.files if k != "meta"}
    # thresholds were stored as float32: compare in float32 space like LightGBM's float features
    return Forest3(arrays, meta)


# ----------------------------------------------------------------------------- live features

class Tracker3(B.Tracker):
    """v2's running state plus the last-20-plays memory."""

    def __init__(self, league: str):
        super().__init__(league)
        self.kc: deque = deque([-1, -1, -1], maxlen=3)
        self.walls: deque = deque(maxlen=6)
        self.win20: deque = deque(maxlen=SEQ)   # (stop, brk, foul, score)
        self.per_key = None
        self.brk_period = self.to_period = self.fouls_period = 0
        self.last_wall_abs: float | None = None
        self.at: float | None = None             # wallclock of the newest event
        self.extra: dict = {}

    def _event(self, g: GameState, play, final: bool) -> None:
        prev_score = (self.sh or 0.0) + (self.sa or 0.0)
        super()._event(g, play, final)
        row = self.row
        stop = B.STOPPAGES[int(row["stoppage"])] if row else "none"
        ttext = (getattr(play, "type_text", "") or getattr(play, "kind", "") or "") if play is not None else ""
        text = getattr(play, "text", "") if play is not None else ""
        k = kclass(stop, ttext, text)
        self.kc.appendleft(k)
        w = self.wall
        gap = float("nan")
        if self.walls and not math.isnan(w) and not math.isnan(self.walls[-1]):
            gap = min(3600.0, max(0.0, w - self.walls[-1]))
        self.walls.append(w)
        gaps = [min(600.0, max(0.0, b - a)) for a, b in zip(list(self.walls)[:-1], list(self.walls)[1:])
                if not (math.isnan(a) or math.isnan(b))][-5:]
        score_now = (self.sh or 0.0) + (self.sa or 0.0)
        is_score = k == KC["score"] or score_now > prev_score
        self.win20.append((stop != "none", stop in _BREAK_NAMES, k == KC["foul"], is_score))
        if self.per_key != g.period:
            self.per_key = g.period
            self.brk_period = self.to_period = self.fouls_period = 0
        if k == KC["timeout"] or stop == "two_minute_warning":
            self.brk_period += 1
        if k == KC["timeout"]:
            self.to_period += 1
        if k == KC["foul"]:
            self.fouls_period += 1
        if play is not None and play.wallclock:
            self.at = float(play.wallclock)
        kcl = list(self.kc)
        self.extra = dict(
            kc0=float(kcl[0]), kc1=float(kcl[1]), kc2=float(kcl[2]), wall_gap=gap,
            pace5=float(np.mean(gaps)) if gaps else float("nan"),
            n_stop20=float(sum(x[0] for x in self.win20)), n_brk20=float(sum(x[1] for x in self.win20)),
            n_foul20=float(sum(x[2] for x in self.win20)), n_score20=float(sum(x[3] for x in self.win20)),
            brk_period=float(self.brk_period), to_period=float(self.to_period),
            fouls_period=float(self.fouls_period),
            ft_pending=float(stop == "whistle" and k == KC["free_throw"]))

    def vector(self, features: list[str]) -> np.ndarray:
        r = dict(self.row or {})
        r.update(self.extra)
        return np.array([r.get(f, float("nan")) for f in features], dtype=np.float32)


_BREAK_NAMES = frozenset({"timeout", "two_minute_warning", "end_period", "halftime", "intermission",
                          "review", "injury", "pitching_change", "score_break"})


# ----------------------------------------------------------------------------- reading

@dataclass
class V3:
    """Brain v3's calibrated outputs for one game state. Times are seconds after `at`."""
    at: float | None                      # wallclock of the event this is about (None: unknown)
    stop: dict[int, float]                # {15: p, 30: p, 60: p} a stoppage starts within h s
    types: dict[str, float]               # P(type | a stoppage within 60 s)
    long_p: float                         # P(a commercial-length stoppage within 60 s)
    length: tuple[float, float, float]    # (p10, p50, p90) s of a stoppage starting now
    entry: tuple[float, float, float]     # (p10, p50, p90) s until play resumes (the entry point)
    big: dict[int, float]                 # {60: p, 180: p} a big play within h s
    hot: float | None = None
    clock: str = "wall"                   # "game" for leagues trained on the game clock
    type_len: dict[str, float] = field(default_factory=dict)   # median length (s) of each type

    def expected_len(self, long_only: bool = True) -> float | None:
        """Expected length of the stoppage coming within 60 s, weighted by its likely type."""
        items = [(p, self.type_len.get(t)) for t, p in self.types.items() if self.type_len.get(t) is not None
                 and (not long_only or t in self.long)]
        w = sum(p for p, _ in items)
        return sum(p * v for p, v in items) / w if w > 0 else None

    long: tuple = ("tv_timeout", "period_end")

    def as_dict(self) -> dict:
        r = lambda x: None if x is None else round(float(x), 3)  # noqa: E731
        return {"at": self.at, "stop": {str(k): r(v) for k, v in self.stop.items()},
                "types": {k: r(v) for k, v in self.types.items()}, "long_p": r(self.long_p),
                "len_q": [round(x, 1) for x in self.length], "entry_q": [round(x, 1) for x in self.entry],
                "big": {str(k): r(v) for k, v in self.big.items()}, "hot": r(self.hot),
                "expect_len": r(self.expected_len())}

    def resume_at(self, q: int = 50) -> float | None:
        """Wallclock play is expected to resume (q = 10 / 50 / 90 quantile), None if unknown."""
        if self.at is None:
            return None
        return self.at + self.entry[{10: 0, 50: 1, 90: 2}[q]]


def reading_from(pred: dict, forest: Forest3, at: float | None) -> V3:
    types = pred.get("type")
    names = forest.info.get("type_names", list(TYPES[1:]))
    tp = {n: float(p) for n, p in zip(names, types)} if types is not None else {}
    s15, s30, s60 = (float(pred.get(k, 0.0)) for k in ("stop15", "stop30", "stop60"))
    s30, s60 = max(s30, s15), max(s60, s30, s15)       # nested horizons can't shrink
    long_types = forest.info.get("long_types", ["tv_timeout", "period_end"])
    long_p = s60 * sum(tp.get(t, 0.0) for t in long_types)
    q = lambda a, b, c: tuple(sorted(max(0.0, float(pred.get(k, 0.0))) for k in (a, b, c)))  # noqa: E731
    b60, b180 = float(pred.get("big60", 0.0)), float(pred.get("big180", 0.0))
    return V3(at=at, stop={15: s15, 30: s30, 60: s60}, types=tp, long_p=long_p,
              length=q("len10", "len50", "len90"), entry=q("entry10", "entry50", "entry90"),
              big={60: b60, 180: max(b180, b60)},
              hot=max(0.0, min(100.0, float(pred["hot"]))) if "hot" in pred else None,
              clock=forest.info.get("clock", "wall"), type_len=dict(forest.info.get("type_len", {})),
              long=tuple(long_types))
