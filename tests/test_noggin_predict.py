"""Noggin Predict v1: numpy == LightGBM, calibration is monotone, save/load round trip, fast, honest grade."""
import time

import numpy as np
import pytest

from redzone.noggin import features as F
from redzone.noggin import predict as P


def test_game_state_one_definition():
    walls = np.array([0.0, 100.0, 200.0, 400.0, 500.0])
    home = np.array([0.0, 2.0, 2.0, 5.0, 5.0])
    away = np.array([0.0, 0.0, 3.0, 3.0, 6.0])
    g = F.game_state(walls, home, away, 4)
    assert g["score_diff"] == 1 and g["total_pts"] == 11
    assert g["pts_5m"] == 11 - 5 and g["swing_5m"] == 0     # 5 min back = the entry at 200 s (2-3)
    assert g["lead_changes"] == 3          # +2, -1, +2, -1
    assert F.game_state(walls, home, away, -1)["total_pts"] == 0


def test_isotonic_is_monotone_and_calibrates():
    pf = pytest.importorskip("redzone.noggin.predict_fit")
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 20000)
    y = (rng.uniform(0, 1, len(p)) < p ** 2).astype(int)      # raw p overconfident: truth is p^2
    kx, ky = pf.isotonic(p, y)
    assert np.all(np.diff(ky) >= -1e-12)
    q = np.interp(p, kx, ky)
    assert pf.ece(q, y) < 0.02 < pf.ece(p, y)


def test_fit_save_load_matches_lightgbm(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("lightgbm")
    pf = pytest.importorskip("redzone.noggin.predict_fit")
    rng = np.random.default_rng(1)
    n = 6000
    t = pd.DataFrame({f: rng.normal(size=n) for f in F.PREDICT_FEATURES})
    t["period"] = rng.integers(1, 5, n)
    t["clock_left"] = rng.uniform(0, 720, n)
    t["score_diff"] = rng.integers(0, 20, n)
    for hz in F.PREDICT_HORIZONS:
        t[f"big_next{hz}"] = (rng.uniform(size=n) < 0.2 + 0.1 * (t.clock_left < 120)).astype(int)
    t["split"] = np.where(np.arange(n) < 4000, "train", np.where(np.arange(n) < 5000, "calib", "test"))
    t["night"] = (np.arange(n) // 100).astype(str)
    t["season"] = 2020
    m = pf.fit(t, "nba", rounds=20, log=lambda *_: None)
    path = tmp_path / "predict_nba.v1.npz"
    m.save(path)
    m2 = P.load(path)
    X = t[list(F.PREDICT_FEATURES)].to_numpy(np.float32)
    for hz in F.PREDICT_HORIZONS:
        assert np.allclose(m.proba(X, hz), m2.proba(X, hz))
        p = m2.proba(X, hz)
        assert ((p >= 0) & (p <= 1)).all()
    rep = pf.grade(m2, t[t.split == "test"], t[t.split == "train"])
    assert rep["windows"] == 1000 and set(rep) >= {60}
    lat = []
    for _ in range(50):
        s = time.perf_counter()
        for hz in F.PREDICT_HORIZONS:
            m2.proba(X[:8], hz)
        lat.append(time.perf_counter() - s)
    assert np.percentile(lat, 99) < 0.005        # 8 games, all horizons, < 5 ms


def test_no_model_means_off(tmp_path):
    assert P.model_for("nba", tmp_path) is None


def test_hockey_big_play_is_every_goal_basketball_unchanged():
    from redzone.models import League
    rows = [(1, 900.0, 0, 0, ""), (1, 800.0, 1, 0, "goal"), (1, 700.0, 2, 0, "goal"), (1, 600.0, 2, 0, "shot")]
    assert F._big_marks(rows, League.NHL).tolist() == [False, True, True, False]
    # basketball: a 2-pt make that doesn't swap the lead is not big; a three is
    b = [(1, 700.0, 0, 0, ""), (1, 690.0, 2, 0, "jump shot"), (1, 680.0, 5, 0, "three point")]
    assert F._big_marks(b).tolist() == [False, False, True]
    assert F.REG[League.NHL] == (3, 1200.0)
