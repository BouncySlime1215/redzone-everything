#!/usr/bin/env python3
"""Grade an exported GameValue model on scripts/holdset.py's own test nights, against the real Planner's bar.

    nice -n 15 python scripts/kaggle_gamevalue_holdset.py --archive data/archive --models <dir> --out report.json [--leagues nfl]

Why: a model may only be promoted by beating what the app does today on the same decisions, so this plugs the model into
holdset's ranker slot rather than computing a look-alike number (build plan WP-26). Numpy prediction only, no training,
dev nights only (holdset reads through DevOnly). The feed is read at the decision with no lag, the Planner column's rule.

holdset calls whatever the ranker returns its "planner" column; here that column is the model.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from redzone.moments.detect import detect  # noqa: E402
from redzone.noggin import gamevalue as G  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from scripts import holdset  # noqa: E402


class ModelRanker:
    """ranker(l, night, night_rows) -> {decision time: {game id: value_next_3m}}. A game that fails closed gets no rank."""

    def __init__(self, model: G.GameValueModel):
        self.model = model

    def __call__(self, league, night, night_rows):
        times = np.array(holdset.decision_times(night_rows), float)
        out = {float(t): {} for t in times}
        want = {g[0] for g in night_rows}
        for _, gid, summary in holdset.games(league, only={night}):
            if gid not in want:
                continue
            clean = V.strip_published_wp(summary)
            playoff = int(((summary.get("header") or {}).get("season") or {}).get("type") == 3)
            view = V.make_view(league, clean, detect(league, clean, gid), playoff)
            head = np.searchsorted(view.walls, times, "right") - 1
            seen = head >= 0
            if not seen.any():
                continue
            feats = V.features(view, head[seen], times[seen])
            b = self.model.predict_matrix(np.column_stack([feats[f] for f in G.GV_FEATURES]), max_rows=None)
            for t, value, ok in zip(times[seen], b.value, b.ok):
                if ok:
                    out[float(t)][gid] = float(value)
        return out


def verdict(results: dict) -> dict:
    """holdset's report per league -> the promotion bar. Shadow nights are not run here, so this never says promote."""
    out = {}
    for league, res in results.items():
        k4 = res["K4"]["planner_pct"]
        bar = G.GV_PLANNER_K4.get(league)
        out[league] = {"model_k4_pct": k4, "real_planner_k4_pct": bar, "beats_planner": bool(k4 is not None and bar is not None and k4 > bar),
                       "test_nights": res["nights_test"], "promotion": G.promotion(league, k4, [])}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--version", default="v1")
    a = ap.parse_args(argv)
    models = {lg: G.load(G.model_path(lg, a.version, a.models), lg) for lg in a.leagues.split(",")}
    raw = a.out.with_suffix(".holdset.json")
    results = holdset.main(["--archive", str(a.archive), "--out", str(raw), "--leagues", a.leagues],
                           ranker_factory=lambda lg: ModelRanker(models[lg]))
    report = verdict(results)
    a.out.write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
