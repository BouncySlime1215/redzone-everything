"""Cut data equals stored data (task F-09, TRAINING-PLAN rule 8): the model features rebuilt from a feed
prefix equal the features computed from the stored full-game summary at the same play.

For every summary fixture and a few moments k, the brain Tracker row (the feature row every tree model
reads) is built twice: from the stored full game replayed up to play k (redzone.lab.backtest.Replay), and
from a cut feed holding only plays 0..k with no final score, records, box score or leader board
(fence.cut_feed). Any difference means a feature reads the future. A planted leaky feature proves the
check can fail.
"""
import json
import math
from pathlib import Path

import pytest

from redzone.lab import fence
from redzone.lab.backtest import Replay
from redzone.models import League
from redzone.scoring.brain import Tracker

FIX = Path(__file__).resolve().parent / "fixtures"
SUMMARIES = sorted(FIX.glob("espn_*_summary_*.json"))


def _league(f: Path):
    try:
        return League(f.name.split("_")[1])
    except ValueError:
        return None


def _rows_at(replay: Replay, moments: set[int]) -> dict[int, dict]:
    """Tracker rows at each moment, one pass over the replay."""
    tr, out = Tracker(replay.league.value), {}
    for i in range(max(moments) + 1):
        g = replay.state_at(i)
        if g is not None:
            tr.observe(g)
        if i in moments:
            out[i] = dict(tr.row or {})
    return out


def _diff(a: dict, b: dict) -> dict:
    def same(x, y):
        return x == y or (isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y))
    return {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if not same(a.get(k), b.get(k))}


def _moments(n: int) -> set[int]:
    return {n // 3, n - 2}


@pytest.mark.parametrize("f", [f for f in SUMMARIES if _league(f)], ids=lambda f: f.stem)
def test_features_from_a_feed_prefix_equal_the_stored_full_game_features(f, tmp_path):
    league = _league(f)
    full = Replay.load(league, f)
    assert full is not None and len(full.plays) > 10
    moments = _moments(len(full.plays))
    stored = _rows_at(full, moments)
    for k in sorted(moments):
        cut = tmp_path / f"cut_{k}_{f.name}"
        cut.write_text(json.dumps(fence.cut_feed(league, full.data, k)))
        prefix = Replay.load(league, cut)
        assert prefix is not None and len(prefix.plays) == k + 1
        rebuilt = _rows_at(prefix, {k})[k]
        assert stored[k], f"{f.name}: no feature row at play {k}"
        assert _diff(stored[k], rebuilt) == {}, f"{f.name} play {k}: features read past the cut"


def test_the_causal_check_catches_a_feature_that_reads_the_final_score():
    """Control: a feature built from the summary header (the final score in a stored game) differs
    between the stored game and the cut feed, so the equality above is not vacuous."""
    f = next(x for x in SUMMARIES if x.name.startswith("espn_nba_"))
    league = _league(f)
    full = Replay.load(league, f)
    k = len(full.plays) // 3

    def leaky(data):   # reads the header the way a careless feature builder would
        comps = data["header"]["competitions"][0]["competitors"]
        return {"final_margin": sum(float(c.get("score") or "nan") * (1 if c["homeAway"] == "home" else -1) for c in comps)}

    assert _diff(leaky(full.data), leaky(fence.cut_feed(league, full.data, k))) != {}
