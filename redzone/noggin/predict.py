"""Noggin Reflex Predict v1: P(a big play in the next 30 / 60 / 120 s of play) at a restart (numpy only).

The live path loads `models/predict_<league>.v1.npz`: one flattened LightGBM forest per horizon plus an
isotonic calibration table, read through the same batched numpy forest as Timing v1. Features are
`features.PREDICT_FEATURES`, computed by the one shared definition (`features.context` +
`features.game_state`). Behind `noggin.predict.leagues` (default [] = OFF): an L4 input only, never an L1
rule (redzone/noggin/priority.py).

Why: first added in ebfb9efb (2026-11-04) as "Noggin: NFL data factory + Timing v1 NFL, Predict v1 NBA (both OFF)"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from redzone.noggin.features import PREDICT_FEATURES, PREDICT_HORIZONS
from redzone.models_rt.forest import MODEL_DIRS, load_npz
from redzone.noggin.timing import Forest

MODEL_DIR = MODEL_DIRS["noggin"]
_ARRAYS = ("feat", "thr", "left", "right", "dleft", "miss", "value", "roots")


@dataclass
class PredictModel:
    league: str
    forests: dict                       # horizon -> Forest
    iso: dict                           # horizon -> (x knots, y knots): raw probability -> calibrated
    info: dict = field(default_factory=dict)

    def raw_p(self, hz: int, X: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.forests[hz].raw(X)))

    def proba(self, X: np.ndarray, hz: int) -> np.ndarray:
        """Calibrated P(big play within hz s of the restart), X (n, len(PREDICT_FEATURES))."""
        xk, yk = self.iso[hz]
        return np.interp(self.raw_p(hz, X), xk, yk)

    def reading(self, x: np.ndarray) -> dict:
        X = np.asarray(x, np.float32).reshape(1, -1)
        return {hz: float(self.proba(X, hz)[0]) for hz in self.forests}

    def save(self, path: Path) -> None:
        arrs, meta = {}, {"league": self.league, "info": self.info, "features": list(PREDICT_FEATURES),
                          "horizons": [], "depth": {}, "init": {}}
        for hz, f in self.forests.items():
            meta["horizons"].append(int(hz))
            meta["depth"][str(hz)], meta["init"][str(hz)] = f.depth, f.init
            for a in _ARRAYS:
                arrs[f"h{hz}_{a}"] = getattr(f, a)
            arrs[f"h{hz}_iso_x"], arrs[f"h{hz}_iso_y"] = self.iso[hz]
        arrs["meta"] = np.frombuffer(json.dumps(meta).encode(), np.uint8)
        np.savez_compressed(path, **arrs)


def load(path: Path) -> PredictModel:
    z, meta = load_npz(path, PREDICT_FEATURES)   # [one brain] the one loader (models_rt.forest)
    forests, iso = {}, {}
    for hz in meta["horizons"]:
        arrays = {a: z[f"h{hz}_{a}"] for a in _ARRAYS}
        forests[hz] = Forest(arrays, {"depth": meta["depth"][str(hz)], "init": meta["init"][str(hz)]})
        iso[hz] = (z[f"h{hz}_iso_x"], z[f"h{hz}_iso_y"])
    return PredictModel(meta["league"], forests, iso, meta.get("info", {}))


_CACHE: dict = {}


def model_for(league: str, model_dir: Path | None = None) -> PredictModel | None:
    p = (model_dir or MODEL_DIR) / f"predict_{league}.v1.npz"
    if p not in _CACHE:
        _CACHE[p] = load(p) if p.exists() else None
    return _CACHE[p]


HORIZONS = PREDICT_HORIZONS
