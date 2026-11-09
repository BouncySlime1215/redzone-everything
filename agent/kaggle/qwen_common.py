"""What the fine-tune and its grader share: the prompt, the answer, the number buckets, and the brain-contract output.

Why: Nick wants a 1-2 B parameter live brain, and qwen_finetune.py and qwen_eval.py must read and write one exact format,
or the model is graded on something it was not trained on. Pure: no torch, no clock, no network.

The prompt holds only GameValue's 23 causal features and the league: no label, game id, night or team name. A greedy
decode of a target that is about 45% zero says "0" nearly every time, and a list of zeros cannot rank games. So the
number is read from the probabilities over ten bucket digits, E[points] = sum p(bucket) x mean(bucket), never from the text.

The output is numbers and words only (redzone/contracts/brain.py). Nothing here can reach a stick; code schedules and cuts.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from redzone.contracts import brain
from redzone.noggin import gamevalue as G

SCHEMA = 1
STATUS = "shadow_only"       # nothing loads this live until it beats the bars in qwen_eval.py
DIGITS = tuple("0123456789")
YES, NO = "yes", "no"
BUCKET_EDGES = (0.0, 0.5, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 25.0)   # upper edges; above the last is bucket 9
TIER1_PREFIX = "\ntier1="
WHY_PREFIX = "\nwhy="
WHY_MAX = 80
MIND_TTL_S = 90.0            # brain.BrainOutput: Reflex 5 s, Mind 90 s
TIER_POINTS = {1: 5.0, 2: 2.0, 3: 0.5}   # value_shards.TIER_WEIGHTS, the scale of the labels


@dataclass(frozen=True)
class Base:
    """One open checkpoint. Revisions are pinned: a moved upstream file must not change what is trained."""
    repo: str
    revision: str
    license: str
    dequantize: bool         # MLX 4-bit weights to rebuild with agent/kaggle/dequant.py
    has_mlx_4bit: bool       # False: the adapter cannot load on the Mac until someone makes a 4-bit copy


BASES = {
    # Checked 2026-11-09: Apache-2.0, 968 MB, converted from Qwen/Qwen3-1.7B (the instruct model). The Mac's own weights.
    "mac-4bit": Base("mlx-community/Qwen3-1.7B-4bit", "3b1b1768f8f8cf8351c712464f906e86c2b8269e", "Apache-2.0", True, True),
    # Checked 2026-11-09: Apache-2.0, 1.7 B, 28 layers, not gated. No MLX 4-bit of it on the hub that day.
    "hf-base": Base("Qwen/Qwen3-1.7B-Base", "ea980cb0a6c2ae4b936e82123acc929f1cec04c1", "Apache-2.0", False, False),
}
DEFAULT_BASE = "mac-4bit"


@dataclass(frozen=True)
class Prediction:
    raw_points: float        # expected tier points in the next 180 s
    p_tier1_60s: float       # calibrated
    why: str = ""


def _num(v) -> str:
    x = round(float(v), 1)
    return "0" if x == 0 else f"{x:.1f}".rstrip("0").rstrip(".")


def prompt_of(league: str, row: Mapping) -> str:
    """Causal features only; the answer starts right after the last character."""
    pairs = " ".join(f"{name}={_num(row[name])}" for name in G.GV_FEATURES)
    return f"Rate the next three minutes of this game.\nleague={league} {pairs}\nvalue="


def row_of(x: Sequence[float]) -> dict:
    """One matrix row in GV_FEATURES order -> the mapping prompt_of reads."""
    return {name: float(v) for name, v in zip(G.GV_FEATURES, x)}


def bucket_of(points: float) -> int:
    return int(np.searchsorted(BUCKET_EDGES, float(points), "left"))


def bucket_means(points: np.ndarray) -> list[float]:
    """Mean raw points per bucket on the training nights. An empty bucket falls back to its neighbouring edges' middle."""
    points = np.asarray(points, float)
    which = np.searchsorted(BUCKET_EDGES, points, "left")
    out = []
    for k in range(len(DIGITS)):
        hit = points[which == k]
        if len(hit):
            out.append(round(float(hit.mean()), 4))
        elif k == 0:
            out.append(0.0)
        elif k == len(DIGITS) - 1:
            out.append(BUCKET_EDGES[-1])
        else:
            out.append((BUCKET_EDGES[k - 1] + BUCKET_EDGES[k]) / 2.0)
    return out


def expected_points(probs: np.ndarray, means: Sequence[float]) -> np.ndarray:
    """(n, 10) bucket probabilities -> expected raw points. Rows are renormalised; a bad row becomes NaN and fails closed."""
    probs = np.asarray(probs, float)
    total = probs.sum(axis=1)
    bad = ~np.isfinite(total) | (total <= 0)
    safe = np.where(bad, 1.0, total)
    out = (probs / safe[:, None]) @ np.asarray(means, float)
    out[bad] = np.nan
    return out


def clean_words(text: str) -> str:
    """Model words to a short printable line. Whatever the model says, the contract gets at most WHY_MAX plain characters."""
    line = (text or "").split("\n", 1)[0]
    return re.sub(r"[^\x20-\x7e]", "", line).strip()[:WHY_MAX]


def why_of(row: Mapping) -> str:
    """The training words. Features only, so the words cannot carry a label."""
    bits = []
    if float(row["is_ot"]) > 0:
        bits.append("overtime")
    elif float(row["late"]) > 0:
        bits.append("late")
    margin = int(round(float(row["abs_margin"])))
    bits.append("tied" if margin == 0 else f"{margin} point game")
    left = float(row["game_left"])
    if left >= 0:
        bits.append(f"{int(left) // 60}:{int(left) % 60:02d} left")
    if float(row["t1_5m"]) > 0:
        bits.append("big play in the last 5 min")
    return clean_words(", ".join(bits))


def answer_parts(points: float, tier1: float, why: str) -> tuple[str, str, str]:
    """(bucket digit, yes|no, words): the three pieces qwen_finetune weights separately."""
    return DIGITS[bucket_of(points)], (YES if float(tier1) >= 0.5 else NO), why


def after_digit(prompt: str, digit: str) -> str:
    """The text the tier-1 yes/no is read after."""
    return f"{prompt}{digit}{TIER1_PREFIX}"


def after_tier1(prompt: str, digit: str, yes_no: str) -> str:
    """The text the words are generated after."""
    return f"{after_digit(prompt, digit)}{yes_no}{WHY_PREFIX}"


def calibrate(p: np.ndarray, knots_x: Sequence[float] | None, knots_y: Sequence[float] | None) -> np.ndarray:
    p = np.asarray(p, float)
    if knots_x is None or knots_y is None or not len(knots_x):
        return np.clip(p, 0.0, 1.0)
    return np.clip(np.interp(p, np.asarray(knots_x, float), np.asarray(knots_y, float)), 0.0, 1.0)


def known_now(inp: brain.BrainInput, game_id: str) -> float:
    """value_now is code, not model (the-mind section 3): tier value already known inside this game's look-ahead."""
    look = float(inp.lookahead_s.get(game_id, 0.0))
    points = sum(TIER_POINTS.get(m.tier, 0.0) for m in inp.moments
                 if m.game_id == game_id and m.is_fact and inp.now <= m.air_at <= inp.now + look)
    return float(G.to_scale(np.array([points]))[0])


def to_brain_output(inp: brain.BrainInput, preds: Mapping[str, Prediction], model_id: str) -> brain.BrainOutput | None:
    """Contract output for one tick, or None when `valid()` refuses it (the caller then runs the baseline)."""
    values, order = [], []
    for g in inp.games:
        pred = preds.get(g.game_id)
        if pred is None or not (math.isfinite(pred.raw_points) and math.isfinite(pred.p_tier1_60s)):
            continue                 # a game that failed closed gets no value, and the others still count
        scaled = float(G.to_scale(np.array([pred.raw_points]))[0])
        values.append(brain.GameValue(game_id=g.game_id, value_now=known_now(inp, g.game_id), value_next_3m=scaled,
                                      p_tier1_60s=float(np.clip(pred.p_tier1_60s, 0.0, 1.0)), why=clean_words(pred.why)))
        order.append((-scaled, g.game_id))
    by_game = {v.game_id: v.value_next_3m for v in values}
    out = brain.BrainOutput(tick=inp.tick, model_id=model_id, made_at=inp.now, ttl_s=MIND_TTL_S, values=tuple(values),
                            scores={o.id: by_game[o.game_id] for o in inp.options if o.game_id in by_game},
                            preload=tuple(gid for _, gid in sorted(order)))
    return out if brain.valid(out, inp) else None


class Predictor:
    """GameValueModel's predict_matrix over a fine-tuned model, so holdset's ranker code runs on it unchanged.

    backend.digit_probs(prompts) -> (n, 10); backend.yes_probs(prompts) -> (n,). `league` goes into the prompt."""

    def __init__(self, backend, manifest: Mapping, league: str, keep: int = 32):
        self.backend, self.manifest, self.league, self.keep = backend, manifest, league, keep
        self.recent: list[tuple[str, str, float, float]] = []    # (prompt, digit, raw, p): rows the contract check re-reads

    def predict_matrix(self, X, max_rows: int | None = None) -> G.Batch:
        X = np.asarray(X, float)
        if X.ndim != 2 or X.shape[1] != len(G.GV_FEATURES):
            raise G.BadFeatures(f"expected (n, {len(G.GV_FEATURES)}) numbers, got {X.shape}")
        if max_rows is not None and len(X) > max_rows:
            raise G.BadFeatures(f"{len(X)} rows is more than {max_rows}")
        ok = G.valid_rows(X)
        raw, p = np.full(len(X), np.nan), np.full(len(X), np.nan)
        if ok.any():
            prompts = [prompt_of(self.league, row_of(x)) for x in X[ok]]
            probs = np.asarray(self.backend.digit_probs(prompts), float)
            digits = [DIGITS[int(i)] for i in probs.argmax(axis=1)]
            raw[ok] = expected_points(probs, self.manifest["bucket_means"])
            yes = np.asarray(self.backend.yes_probs([after_digit(q, d) for q, d in zip(prompts, digits)]), float)
            p[ok] = calibrate(yes, self.manifest.get("p_cal_x"), self.manifest.get("p_cal_y"))
            where = np.flatnonzero(ok)
            for i, (q, d) in list(enumerate(zip(prompts, digits)))[-self.keep:]:
                self.recent.append((q, d, float(raw[where[i]]), float(p[where[i]])))
            del self.recent[:-self.keep]
        ok &= np.isfinite(raw) & np.isfinite(p)
        raw[~ok], p[~ok] = np.nan, np.nan
        return G.Batch(raw, G.to_scale(raw), p, ok)
