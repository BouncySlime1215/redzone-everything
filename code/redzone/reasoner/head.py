"""The reasoner's fast head: lock_in odds from the brief's numbers (numpy only, < 1 ms).

One small LightGBM model per league (trained by scripts/train_reasoner.py from the archive,
redzone/lab/reasoner_data.py), stored like the brain's models and walked with the same plain-
numpy tree code (redzone.scoring.brain.Trees). It answers one question: how likely is a scorecard
big play or crunch play in this game in the next few minutes (NFL/NHL 3 min, NBA 2 min)?

The file's meta holds the league's two cut points, picked on held-out nights:
  watch     odds at least this: worth keeping an eye on
  must_see  odds at least this: lock in
A league without a model (or a broken file) gives None and nothing downstream changes.
"""
from __future__ import annotations

import logging
from pathlib import Path

from redzone.reasoner import brief
from redzone.models_rt.forest import MODEL_DIRS
from redzone.scoring.brain import Trees, load

log = logging.getLogger(__name__)

MODEL_DIR = MODEL_DIRS["reasoner"]
LEVELS = ("none", "watch", "must_see")


class Head:
    def __init__(self, model_dir: Path | None = None):
        self.model_dir = model_dir or MODEL_DIR
        self._m: dict[str, tuple[Trees, dict] | None] = {}
        self._broken: set[str] = set()

    def model(self, league: str) -> tuple[Trees, dict] | None:
        key = brief.BASE.get(league, league)
        if league not in self._m:
            m = None
            for name in (league, key):      # college reads its own file first, then the pro league's
                p = self.model_dir / f"{name}.npz"
                if p.exists():
                    try:
                        trees, info = load(p)
                        m = (trees["lock"], info)
                    except Exception:  # noqa: BLE001 - a bad file means "no head", never a crash
                        log.exception("reasoner: could not load %s", p)
                        m = None
                    break
            self._m[league] = m
        return self._m[league]

    def available(self, league: str) -> bool:
        return league not in self._broken and self.model(league) is not None

    def odds(self, league: str, facts: dict) -> float | None:
        m = None if league in self._broken else self.model(league)
        if m is None:
            return None
        trees, _ = m
        try:
            p, _ = trees.predict(brief.vector(facts, trees.features))
        except Exception:  # noqa: BLE001
            log.exception("reasoner: head failed for %s; off for this league from now on", league)
            self._broken.add(league)
            return None
        return max(0.0, min(1.0, float(p)))

    def cuts(self, league: str) -> tuple[float, float]:
        m = self.model(league)
        info = m[1] if m else {}
        return float(info.get("watch", 0.35)), float(info.get("must_see", 0.6))

    def level(self, league: str, p: float | None, *, garbage: bool = False, crunch: bool = False) -> str:
        """lock_in from odds and the hard facts: garbage time is never a lock-in; crunch always is."""
        if garbage:
            return "none"
        if crunch:
            return "must_see"
        if p is None:
            return "none"
        watch, must = self.cuts(league)
        return "must_see" if p >= must else "watch" if p >= watch else "none"
