"""Calibration chosen per league and reported apart from ranking, and a challenger model on the same shared-clock capture.

Why: the first honest NBA model's calibration and ranking were judged together, and garbage time flatters the extreme bins; a
challenger like TabPFN is only worth believing if it is graded on exactly the capture v1 is, and can never ship by accident.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V
from scripts import kaggle_gamevalue as K
from scripts import value_leak_check as LC
from tests.test_gamevalue import FakeTrainer, _raw_dir, _shards, nba_summary, nfl_summary


def _ca(p, y, game="g", gap=30.0):
    return pd.DataFrame({"p_tier1_60s": np.asarray(y, float), "game_id": game, "decision_t": np.arange(len(y)) * gap})


def test_consecutive_positive_rows_are_one_event():
    assert K.effective_events(_ca([0] * 8, [0, 1, 1, 1, 0, 1, 0, 1])) == 3
    assert K.effective_events(pd.concat([_ca([0, 0], [1, 1], "a"), _ca([0, 0], [1, 1], "b")])) == 2


def test_few_events_get_platt_and_the_curve_loads():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.02, 0.6, 2000)
    y = (rng.random(2000) < p).astype(float)
    kx, ky, info = K.calibrate(p, _ca(p, y))
    assert info["method"] == "platt" and info["effective_events"] < K.CAL_ISOTONIC_MIN_EVENTS and 0.7 < info["a"] < 1.3
    assert (np.diff(kx) >= 0).all() and (np.diff(ky) >= 0).all() and ky.min() >= 0 and ky.max() <= 1
    want = K._sigmoid(info["a"] * K._logit(np.array([0.1, 0.3])) + info["b"])
    assert np.interp([0.1, 0.3], kx, ky) == pytest.approx(want, abs=2e-3)


def test_plenty_of_events_get_isotonic(monkeypatch):
    monkeypatch.setattr(K, "CAL_ISOTONIC_MIN_EVENTS", 10)
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.9, 5000)
    y = (rng.random(5000) < p).astype(float)
    kx, ky, info = K.calibrate(p, _ca(p, y))
    assert info["method"] == "isotonic" and (np.diff(ky) >= 0).all()


def test_platt_never_inverts_the_ranking():
    p = np.linspace(0.01, 0.99, 400)
    a, _ = K.platt_fit(p, 1.0 - p > 0.5)    # anti-correlated on purpose
    assert a >= 0.0


def test_garbage_time_is_left_out_of_the_calibration_check_not_the_rows():
    p = np.full(100, 0.5)
    y = np.r_[np.ones(50), np.zeros(50)]
    planted = np.r_[np.zeros(80, bool), np.ones(20, bool)]
    ys = y.copy()
    ys[80:] = 1.0                                               # the garbage rows are wildly off
    got = K.calibration_check(p, ys, planted)
    clean = K.calibration_check(p[:80], ys[:80], np.zeros(80, bool))
    assert got["ece"] == clean["ece"] and got["garbage_rows_excluded"] == 20 and got["rows"] == 80


def test_garbage_time_rule_is_per_league():
    df = pd.DataFrame({"abs_margin": [25.0, 25.0, 5.0], "game_left": [120.0, 3000.0, 120.0]})
    assert K.garbage_time(df, "nba").tolist() == [True, False, False]


# --- the challenger ------------------------------------------------------------------------------------------------


class _Mean:
    def __init__(self, v):
        self.v = v

    def predict(self, X):
        return np.full(len(X), self.v) + np.asarray(X)[:, 5] * 1e-3


class GoodChallenger:
    name, license = "fake-tab", "test only"

    def __init__(self):
        self.fit_rows = []

    def fit(self, X, y, objective):
        self.fit_rows.append(len(X))
        return _Mean(float(np.mean(y)))


class BrokenChallenger(GoodChallenger):
    def fit(self, X, y, objective):
        raise RuntimeError("no GPU")


@pytest.fixture(scope="module")
def shards(tmp_path_factory):
    return _shards(tmp_path_factory.mktemp("s") / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A")


def _run(shards, out, challenger=None, rows=K.CHALLENGER_ROWS):
    leak = {"passed": True, "shards": 1, "rows": 1, "prefix_rows": 1}
    return K.train_league(shards, out, "nfl", FakeTrainer(), 2, leak, _raw_dir(shards), log=lambda *a, **k: None, challenger=challenger, challenger_rows=rows)


def test_challenger_reports_on_the_same_capture_as_v1_and_exports_nothing(shards, tmp_path):
    ch = GoodChallenger()
    rep = _run(shards, tmp_path / "m", ch, rows=500)
    entry = rep["challenger"]
    assert entry["name"] == "fake-tab" and entry["exported"] is False and rep["trials"] == 2
    assert entry["test"]["k4_pct"] is not None and entry["test"]["shared_clock"]["K4"]["hindsight_pct"] == rep["models"]["v1"]["test"]["shared_clock"]["K4"]["hindsight_pct"]
    assert len(entry["cv"]["folds"]) == 2 and entry["cv"]["k4_pct"]["worst"] is not None
    assert max(ch.fit_rows) <= 510                     # the sample cap holds
    assert sorted(p.name for p in (tmp_path / "m").glob("*.npz")) == ["gamevalue_nfl_v0.npz", "gamevalue_nfl_v1.npz"]


def test_a_failing_challenger_is_recorded_and_v1_still_stands(shards, tmp_path):
    rep = _run(shards, tmp_path / "m", BrokenChallenger())
    assert "no GPU" in rep["challenger"]["error"] and rep["challenger"].get("test") is None and rep["models"]["v1"]["test"]["k4_pct"] is not None


def test_no_challenger_means_the_run_is_unchanged(shards, tmp_path):
    rep = _run(shards, tmp_path / "m")
    assert "challenger" not in rep and rep["trials"] == 1
    assert rep["models"]["v1"]["p1_calibration"]["method"] in ("platt", "isotonic")
    block = rep["models"]["v1"]["test"]["calibration"]
    assert block["rows"] + block["garbage_rows_excluded"] == rep["models"]["v1"]["test"]["rows"] - rep["models"]["v1"]["test"]["failed_closed"]
    assert "k4_pct" in rep["models"]["v1"]["cv"] and "calibration_ece" in rep["models"]["v1"]["cv"]


def test_a_challenger_must_say_what_it_is():
    with pytest.raises(K.Refused, match="package.module"):
        K.load_challenger("nonsense")
    with pytest.raises(K.Refused, match="license"):
        K.load_challenger("tests.test_gamevalue:FakeTrainer")


def test_v2_shards_are_refused_on_a_v3_run(shards, tmp_path):
    V.select_features("v3")
    try:
        with pytest.raises(K.Refused, match="RZ_GV_FEATURES"):
            K.load_rows(shards, "nfl")
    finally:
        V.select_features("v2")


def test_sampling_keeps_every_night_and_repeats():
    df = pd.DataFrame({"night": [f"n{i % 40}" for i in range(4000)], "x": range(4000)})
    a, b = K.sample_rows(df, 400), K.sample_rows(df, 400)
    assert a.x.tolist() == b.x.tolist() and set(a.night) == set(df.night) and 300 <= len(a) <= 440


# --- the whole v3 path: scripts/value_leak_check.py knows v3 now, so these always run -------------------------------------------


def test_v3_shards_pass_the_leak_check_and_raw_audit_and_train(tmp_path):
    V.select_features("v3")
    try:
        root = _shards(tmp_path / "shards", "nba", nba_summary, "hoopr_espn_nba", f"{G.PERSONAL_USE}; hoopR ESPN NBA", "B")
        rep = LC.check_dir(root, allow_personal_nba=True, raw=_raw_dir(root))
        assert rep.fails == [] and rep.checked["raw_rows"] > 0
        out = tmp_path / "m"
        assert K.main(["--shards", str(root), "--out", str(out), "--raw", str(_raw_dir(root)), "--leagues", "nba", "--personal-nba", "--folds", "2"], trainer=FakeTrainer()) == 0
        assert G.load(G.model_path("nba", "v1", out), "nba").meta["features_version"] == "v3"
    finally:
        V.select_features("v2")


def test_a_v3_history_value_changed_after_the_build_fails_the_raw_audit(tmp_path):
    V.select_features("v3")
    try:
        root = _shards(tmp_path / "shards", "nba", nba_summary, "hoopr_espn_nba", f"{G.PERSONAL_USE}; hoopR ESPN NBA", "B")
        f = sorted((root / "nba").glob("*.parquet"))[0]
        df = pd.read_parquet(f)
        df["run_pts"] = df.run_pts + 4.0
        df.to_parquet(f, index=False)
        rep = LC.check_dir(root, allow_personal_nba=True, raw=_raw_dir(root))
        assert any("raw_audit" in x for x in rep.fails)
    finally:
        V.select_features("v2")


def test_v3_nfl_shards_pass_the_raw_audit(tmp_path):
    V.select_features("v3")
    try:
        root = _shards(tmp_path / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A")
        rep = LC.check_dir(root, raw=_raw_dir(root))
        assert rep.fails == [] and rep.checked["raw_rows"] > 0
    finally:
        V.select_features("v2")
