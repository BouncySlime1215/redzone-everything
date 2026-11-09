#!/usr/bin/env python3
"""Grade an exported GameValue model on scripts/holdset.py's own test nights, against the real Planner on those nights.

    nice -n 15 python scripts/kaggle_gamevalue_holdset.py --archive data/archive --models <dir> --out report.json [--leagues nfl]

Why: a model may only be promoted by beating what the app does today on the same decisions, so this plugs the model and
then the real standby.Planner into holdset's ranker slot, on the same rows, rather than comparing against a stored number
(build plan WP-26). Numpy prediction only, no training, dev nights only (holdset reads through DevOnly). The feed is read
at the decision with no lag, the Planner column's rule. Published win probability is removed from the rows first, so the
wp5 / wp10 events are out of y for every method, as they are out of the training labels.

holdset calls whatever the ranker returns its "planner" column; the model's run and the Planner's run are kept apart.
"""
from __future__ import annotations

import argparse
import collections
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
from scripts.holdset_public import PlannerRanker  # noqa: E402


class ModelRanker:
    """ranker(l, night, night_rows) -> {decision time: {game id: value_next_3m}}. A game that fails closed gets no rank."""

    def __init__(self, model: G.GameValueModel, games=None):
        self.model, self.games = model, games     # games(league, night) -> [(night, gid, summary)]; default: the archive

    def __call__(self, league, night, night_rows):
        times = np.array(holdset.decision_times(night_rows), float)
        out = {float(t): {} for t in times}
        want = {g[0] for g in night_rows}
        for _, gid, summary in (self.games(league, night) if self.games else holdset.games(league, only={night})):
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


def build_rows(league: str) -> dict:
    """holdset.build with published win probability removed, so y counts what the training labels count."""
    rows = collections.defaultdict(list)
    for night, gid, summary in holdset.games(league):
        row = holdset.game_row(league, gid, V.strip_published_wp(summary))
        if row:
            rows[night].append(row)
    return rows


def verdict(model_results: dict, planner_results: dict) -> dict:
    """holdset's report per league, model and real Planner, -> the promotion bar. Shadow nights are not run here, so this
    never says promote. A league whose two runs did not score the same decisions gets no bar."""
    out = {}
    for league, res in model_results.items():
        base = planner_results.get(league) or {}
        same = all(res.get(k) == base.get(k) for k in ("nights_test", "decisions_test"))
        k4, bar = res["K4"]["planner_pct"], (base.get("K4") or {}).get("planner_pct") if same else None
        out[league] = {"model_k4_pct": k4, "real_planner_k4_pct": bar, "beats_planner": bool(k4 is not None and bar is not None and k4 > bar),
                       "test_nights": res["nights_test"], "same_decisions": same, "promotion": G.promotion(league, k4, [], bar)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--config", type=Path, default=None, help="default: <archive>/../eval_config.yaml when it exists")
    ap.add_argument("--poll-s", type=float, default=30.0)
    a = ap.parse_args(argv)
    holdset.A = a.archive
    config = a.config or (a.archive.parent / "eval_config.yaml")
    config = config if config.exists() else None
    model_results, planner_results = {}, {}
    for lg in a.leagues.split(","):
        try:
            rows = build_rows(lg)
        except holdset.LockedNightError as e:
            print(lg, "REFUSED", e)
            continue
        model_results[lg] = holdset.report(lg, rows, ModelRanker(G.load(G.model_path(lg, a.version, a.models), lg)))
        planner_results[lg] = holdset.report(lg, rows, PlannerRanker(config, a.poll_s))
    a.out.with_suffix(".holdset.json").write_text(json.dumps({"model": model_results, "planner": planner_results}, indent=1))
    report = verdict(model_results, planner_results)
    a.out.write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
