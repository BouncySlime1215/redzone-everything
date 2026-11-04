"""HEAT 2.0 watch value: self-taught labels, the calibrated 0-100 scale, the loader, and the Scorer hook (off by
default; on, it only ADDS fields: crunch / lock / score are untouched, so the crunch=stay floor above it holds)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.contracts.watch import WatchModel, features_for  # noqa: E402
from redzone.noggin import watch as W  # noqa: E402


def test_labels_are_the_realized_swing_ahead():
    import train_watch_value as TW
    wps = [0.5, 0.52, 0.5, 0.75, 0.74, 0.74]
    rows = [{"wall": 60.0 * i, "wp_home": w} for i, w in enumerate(wps)]
    wv, pb = TW.labels(rows, 120.0)
    assert wv[0] == pytest.approx(min(1, 0.02 / TW.SWING_FULL))       # 2 min ahead: 0.52, 0.50
    assert wv[1] == pytest.approx(0.23 / TW.SWING_FULL)               # the jump to 0.75 is inside its window
    assert pb[1] == 1.0 and pb[0] == 0.0 and wv[-1] == 0.0            # nothing after the last play


def test_scale_is_50_at_the_median_and_90_at_p95():
    qs = list(np.linspace(0, 1, 101))
    pcts = list(np.linspace(0, 100, 101))
    assert W.calibrate(0.5, qs, pcts) == pytest.approx(50)
    assert W.calibrate(0.95, qs, pcts) == pytest.approx(90)
    assert W.calibrate(1.0, qs, pcts) == pytest.approx(100) and W.calibrate(-1, qs, pcts) == 0
    v = [W.calibrate(x, qs, pcts) for x in np.linspace(0, 1, 50)]
    assert v == sorted(v)


def test_crunch_state_has_an_exit_and_hysteresis():
    import train_watch_value as TW
    # late game: WP still swings until play 3, then the game is decided (no more movement)
    rows = [{"wall": 10.0 * i, "wp_home": w, "facts": {"frac_left": 0.05}} for i, w in
            enumerate([0.5, 0.6, 0.4, 0.9, 0.97, 0.97, 0.97])]
    y = TW.crunch_labels(rows, "nba")
    assert list(y) == [1, 1, 1, 0, 0, 0, 0]              # it EXITS once the rest of the game can't swing
    early = [dict(r, facts={"frac_left": 0.6}) for r in rows]
    assert not TW.crunch_labels(early, "nba").any()     # not late: never crunch
    st = TW.hysteresis(np.array([0.2, 0.65, 0.5, 0.4, 0.3, 0.5, 0.62]))
    assert list(st) == [False, True, True, True, False, False, True]   # no flicker between 0.35 and 0.6
    games = [{"gid": "a", "wall": float(i * 30)} for i in range(6)]
    c = TW.crunch_card(games, np.array([0.1, 0.7, 0.7, 0.7, 0.1, 0.1]), np.array([0, 1, 1, 1, 0, 0.0]))
    assert c == {"games_in_crunch": 1, "crunch_entry_acc_pct": 100.0, "crunch_exit_acc_pct": 100.0, "flicker_per_game": 0.0}


def test_metrics_shape_and_sanity():
    import train_watch_value as TW
    rows = [{"night": "n", "gid": g, "wall": 10.0 * t} for t in range(5) for g in ("a", "b")]
    y = np.array([0.1, 0.9] * 5)
    assert TW.rank_agree(rows, y.copy(), y) == (100.0, 1)            # one minute, two games, the right pick
    assert TW.rank_agree(rows, -y, y) == (0.0, 1)
    assert TW.calibration_err(np.full(10, 50.0), np.full(10, 50.0)) == 0.0


@pytest.fixture
def model_dir(tmp_path):
    lgb = pytest.importorskip("lightgbm")
    from redzone.scoring import brain as B
    feats = features_for("nba")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(600, len(feats))).astype(np.float32)
    lev = feats.index("wp_lev")
    y = 1 / (1 + np.exp(-2 * X[:, lev]))
    m = lgb.train(dict(objective="regression", verbose=-1, num_leaves=7), lgb.Dataset(X, y, feature_name=feats), 30)
    mb = lgb.train(dict(objective="binary", verbose=-1, num_leaves=7), lgb.Dataset(X, (y > 0.7).astype(float), feature_name=feats), 30)
    a, meta = B.trees_from_lightgbm(m.dump_model(), feats)
    meta["objective"] = "regression"
    ab, metab = B.trees_from_lightgbm(mb.dump_model(), feats)
    qs = list(np.percentile(m.predict(X), np.linspace(0, 100, 101)))
    B.save(tmp_path / "watch_nba.v1.npz", {"wv": (a, meta), "pb": (ab, metab)},
           {"cal_q": qs, "cal_pct": list(np.linspace(0, 100, 101)), "version": "v1"})
    return tmp_path


def test_reader_scores_explains_and_tracks_rising(model_dir):
    wvm = W.WatchValue(model_dir)
    assert isinstance(wvm, WatchModel)
    lo = wvm.read("g", "nba", {"wp_lev": -2.0, "abs_diff": 20}, now=100.0)
    hi = wvm.read("g", "nba", {"wp_lev": 2.0, "abs_diff": 1}, now=160.0)
    assert 0 <= lo.watch_value < hi.watch_value <= 100
    assert hi.watch_rising == pytest.approx(hi.watch_value - lo.watch_value, abs=0.2)   # per minute
    assert "close game" in hi.watch_why
    assert set(hi.watch_terms) == {"p_big", "impact", "leverage", "stakes_mult", "story", "garbage", "crunch_p"}
    boosted = W.WatchValue(model_dir).read("h", "nba", {"wp_lev": 0.0}, now=1.0, stakes_mult=1.5)
    plain = W.WatchValue(model_dir).read("h", "nba", {"wp_lev": 0.0}, now=1.0)
    assert boosted.watch_value >= plain.watch_value and boosted.watch_terms["stakes_mult"] == 1.5
    assert W.WatchValue(model_dir).read("g", "nhl", {}, now=1.0).watch_value is None       # no model: None
    assert W.WatchValue(model_dir).read("g", "cbb", {"wp_lev": 1.0}, now=1.0).watch_value is not None  # college -> pro


def _cfg(**over):
    from redzone.config import Config

    class C(Config):
        def get(self, key, default=None):
            return over[key] if key in over else super().get(key, default)
    return C()


def test_crunch_state_reading_enters_stays_and_leaves(model_dir, monkeypatch):
    from redzone.contracts.watch import CRUNCH_STATES, SCORED_FIELDS
    from redzone.scoring.engine import Scored
    assert set(SCORED_FIELDS) <= {f for f in Scored.__dataclass_fields__}
    wvm = W.WatchValue(model_dir)
    seq = iter([0.2, 0.7, 0.5, 0.3, 0.3])

    class Fake:
        features = ["wp_lev"]

        def predict(self, x, explain=False):
            return next(seq), None
    wv, pb, _, info = wvm.model("nba")
    wvm._m["nba"] = (wv, pb, Fake(), info)
    states = [wvm.read("g", "nba", {"wp_lev": 0.0}, now=float(i)).crunch_state for i in range(5)]
    assert states == [None, "entering", "in", "leaving", None]
    assert all(s is None or s in CRUNCH_STATES for s in states)


def test_scorer_hook_is_off_by_default_and_only_adds_fields(model_dir, monkeypatch):
    from redzone.models import GameState, League, Phase, Team
    from redzone.scoring.engine import Scorer
    g = GameState(League.NBA, "g1", Team("BOS", score=99), Team("NYK", score=98), phase=Phase.LIVE, period=4, clock_s=60)
    off = Scorer(_cfg(), clock=lambda: 1000.0).score(g)
    assert off.watch_value is None and off.watch_why == "" and off.watch_terms == {} and off.crunch_state is None
    monkeypatch.setattr(W, "MODEL_DIR", model_dir)
    on = Scorer(_cfg(**{"scoring.watch_value": True}), clock=lambda: 1000.0).score(g)
    assert on.watch_value is not None and 0 <= on.watch_value <= 100 and on.watch_why
    # WV is an extra reading: the floors the decider reads (crunch, lock, the score) are exactly as before
    for k in ("score", "raw", "crunch", "crunch_now", "lock", "late", "garbage", "is_break"):
        assert getattr(on, k) == getattr(off, k), k


def test_postseason_stakes_raise_wv_only_behind_their_flag(model_dir, monkeypatch):
    """#163: a Game 7 multiplies WV (after the model, never a label) and fills stakes_line, only when
    score.postseason.enabled; off, the reading is exactly the regular-season one."""
    import dataclasses

    from redzone.models import GameState, League, Phase, Team
    from redzone.scoring.engine import Scorer
    from redzone.scoring.postseason import Stakes
    monkeypatch.setattr(W, "MODEL_DIR", model_dir)
    g = GameState(League.NBA, "g7", Team("BOS", score=60), Team("NYK", score=58), phase=Phase.LIVE, period=3, clock_s=400)
    g7 = dataclasses.replace(g, stakes=Stakes(postseason=True, rounds_left=0, best_of=7, game_no=7, elimination=True,
                                              decider=True, label="NBA Finals - Game 7"))
    base = Scorer(_cfg(**{"scoring.watch_value": True}), clock=lambda: 1000.0).score(g)
    flag_off = Scorer(_cfg(**{"scoring.watch_value": True}), clock=lambda: 1000.0).score(g7)
    on = Scorer(_cfg(**{"scoring.watch_value": True, "score.postseason.enabled": True}), clock=lambda: 1000.0).score(g7)
    assert flag_off.watch_value == base.watch_value and flag_off.stakes_line is None
    assert on.watch_terms["stakes_mult"] > 1.0 and on.watch_value >= base.watch_value
    assert on.stakes_line == "NBA Finals - Game 7 · winner takes it"
