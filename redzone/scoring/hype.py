"""Hype v2: a learned "key moment coming" model at the core of the score.

The model (redzone/scoring/hype_model.json, written by scripts/train_hype.py) is one logistic
regression per base league over the variables in redzone.scoring.features. It answers:

    what is the chance this game has a key moment in the next couple of minutes?

That probability is comparable ACROSS leagues (each league is calibrated on its own games), which
is what the old hand-tuned score could not do (MLB extras sat at 90 while a one-possession NBA
fourth quarter sat at 40). It is then mapped onto the familiar 0-100 scale with a monotone table
fitted so the score's overall spread matches the old one: thresholds in the decider keep their
meaning, only the ORDER of games changes.

Pure Python at run time (no numpy): a dot product per game per poll.
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

from redzone.models import League
from redzone.scoring.features import PHRASES

MODEL_PATH = Path(__file__).with_name("hype_model.json")


class HypeModel:
    def __init__(self, data: dict):
        self.data = data
        self.horizon_s = data.get("horizon_s", 120)
        self.leagues = data["leagues"]
        sm = data["score_map"]
        self._px, self._sy = sm["p"], sm["s"]

    @classmethod
    def load(cls, path: Path | None = None) -> "HypeModel | None":
        try:
            return cls(json.loads((path or MODEL_PATH).read_text()))
        except (OSError, ValueError, KeyError):
            return None

    def predict(self, league: League, x: dict[str, float]) -> tuple[float, list[tuple[float, str]]]:
        """(probability, [(contribution in log-odds, feature name)] sorted biggest first)."""
        md = self.leagues.get(league.base.value)
        if md is None:
            return 0.0, []
        z = md["b"]
        contrib = []
        for name, mu, sd, w, keep in zip(md["names"], md["mu"], md["sd"], md["w"], md["keep"]):
            if not keep:
                continue
            c = w * ((x.get(name, mu) - mu) / sd)
            z += c
            contrib.append((c, name))
        contrib.sort(reverse=True)
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z)), contrib

    def to_score(self, p: float) -> float:
        """Probability -> 0-100 (piecewise-linear, monotone)."""
        px, sy = self._px, self._sy
        if p <= px[0]:
            return sy[0] * (p / px[0]) if px[0] > 0 else sy[0]
        if p >= px[-1]:
            # Beyond the top quantile: keep rising toward 100 so a near-certain moment still wins.
            return min(100.0, sy[-1] + (100.0 - sy[-1]) * (p - px[-1]) / max(1e-9, 1.0 - px[-1]))
        i = bisect.bisect_right(px, p) - 1
        f = (p - px[i]) / (px[i + 1] - px[i])
        return sy[i] + f * (sy[i + 1] - sy[i])


def top_phrases(contrib: list[tuple[float, str]], x: dict[str, float], n: int = 2) -> list[tuple[float, str]]:
    """The biggest positive, explainable drivers: [(points-ish weight, phrase)]."""
    out, seen = [], set()
    for c, name in contrib:
        if c <= 0.15:
            break
        phrase = PHRASES.get(name, "")
        if not phrase or phrase in seen or x.get(name, 0.0) <= 0:
            continue
        seen.add(phrase)
        out.append((c, phrase))
        if len(out) == n:
            break
    return out
