"""GameValue v4 training: the ranking head, the trial search, and the one model for every sport.

Why: the fair test measures top-K capture across games, so the value head can be fit to that order; and a single model only works if its points mean
the same thing in each sport, which the cross-sport test measures against hindsight. FakeTrainer stands in for LightGBM except in one tiny real fit.
"""
import json

import numpy as np
import pandas as pd
import pytest

from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V
from scripts import kaggle_gamevalue as K
from tests.test_gamevalue import LEAK_OK, FakeBooster, FakeTrainer, _raw_dir, _shards, nba_summary, nfl_summary


@pytest.fixture(scope="module", autouse=True)
def v4():
    V.select_features("v4")
    yield
    V.select_features("v2")


class RawBooster(FakeBooster):
    def predict(self, X, raw_score=False):
        return np.array([sum(self._walk(t, r) for t in self.trees) for r in np.asarray(X, np.float32).astype(float)])


class KwTrainer(FakeTrainer):
    """The LightGBM trainer's whole signature; lambdarank returns a score with no link."""

    def __init__(self):
        super().__init__()
        self.kw = []

    def fit(self, X, y, objective, rounds=None, params=None, group=None, weight=None):
        self.kw.append({"objective": objective, "rounds": rounds, "params": params, "group": group, "weight": weight})
        if objective != "lambdarank":
            return super().fit(X, y, objective)
        return RawBooster(super().fit(X, np.maximum(np.asarray(y, float), 0.5), "tweedie").trees, "raw")


@pytest.fixture(scope="module")
def two_leagues(tmp_path_factory):
    root = tmp_path_factory.mktemp("two") / "shards"
    _shards(root, "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A", games=6)
    _shards(root, "nba", nba_summary, "hoopr_espn_nba", f"{G.PERSONAL_USE}; hoopR ESPN NBA", "B", games=6)
    return root


def _df(rows):
    return pd.DataFrame(rows, columns=["night", "game_id", "decision_t", "value_next_3m"])


def test_rank_queries_are_one_row_per_game_per_decision():
    df = _df([("n1", "a", 10.0, 2.0), ("n1", "a", 80.0, 5.0), ("n1", "b", 85.0, 0.0), ("n1", "c", 200.0, 1.0)])
    q = K.rank_queries(df)
    assert q.sizes.tolist() == [2] and sorted(q.idx.tolist()) == [1, 2]            # the row nearest the bucket's middle; the lone game is dropped


def test_rank_queries_weight_a_decision_by_what_it_holds_and_keep_whole_nights_apart():
    df = _df([("n1", "a", 90.0, 5.0), ("n1", "b", 90.0, 1.0), ("n2", "a", 90.0, 0.5), ("n2", "b", 90.0, 0.0)])
    q = K.rank_queries(df)
    assert q.sizes.tolist() == [2, 2] and q.weight.tolist() == [6.0, 6.0, 0.5, 0.5] and q.labels.tolist() == [10, 2, 1, 0]


def test_rank_knots_are_strict_even_when_isotonic_flattens():
    x, y = K.rank_knots(np.array([3.0, 2.0, 1.0, 0.0, -1.0, -2.0] * 10), np.array([0.0, 5.0, 0.0, 5.0, 0.0, 5.0] * 10))
    assert np.all(np.diff(x) > 0) and np.all(np.diff(y) > 0)


def _model(tiny, tr, ca, trial):
    return G.GameValueModel(*K.fit_v1(tr, ca, "nfl", tiny, LEAK_OK, trial))


def test_the_default_trial_calls_the_trainer_exactly_as_before(two_leagues):
    df = pd.read_parquet(two_leagues / "nfl" / "w0.parquet")
    trainer = FakeTrainer()
    _, meta = K.fit_v1(df[:800], df[800:1200], "nfl", trainer, LEAK_OK)
    assert [c[0] for c in trainer.calls] == ["tweedie", "binary"] and "value_link" not in meta and "trial" not in meta


def test_a_rank_model_exports_reloads_and_keeps_its_order(two_leagues, tmp_path):
    df = pd.read_parquet(two_leagues / "nfl" / "w0.parquet")
    trainer = KwTrainer()
    arrays, meta = K.fit_v1(df[:1000], df[1000:1500], "nfl", trainer, LEAK_OK, K.TRIALS["rank"])
    assert meta["value_link"] == "rank" and trainer.kw[0]["group"] is not None and trainer.kw[0]["weight"] is not None
    path = tmp_path / "m.npz"
    G.save(path, arrays, meta)
    again = G.load(path, "nfl")
    X = df[list(G.GV_FEATURES)].to_numpy(float)[:200]
    b = again.predict_matrix(X, max_rows=None)
    score = again._value.value[again._value.leaves_batch(X.astype(np.float32))].sum(axis=1)
    order = np.argsort(score, kind="stable")
    assert b.ok.all() and np.all(np.diff(b.raw[order]) >= -1e-9) and np.all((np.diff(score[order]) <= 0) | (np.diff(b.raw[order]) > 0))


def test_a_rank_file_without_its_knots_does_not_load(two_leagues):
    df = pd.read_parquet(two_leagues / "nfl" / "w0.parquet")
    arrays, meta = K.fit_v1(df[:1000], df[1000:1500], "nfl", KwTrainer(), LEAK_OK, K.TRIALS["rank"])
    arrays.pop("value_cal_x")
    with pytest.raises(G.ModelRejected):
        G.GameValueModel(arrays, meta)


def test_a_real_lambdarank_fit_exports_with_parity(two_leagues):
    """The one real fit: a few hundred rows, a dozen trees, so the LightGBM dump layout of a ranker is really checked."""
    pytest.importorskip("lightgbm")
    df = pd.read_parquet(two_leagues / "nfl" / "w0.parquet")
    trainer = K.LightGBMTrainer(rounds=12)
    trial = K.Trial("tiny", "lambdarank", 12, {"min_data_in_leaf": 5})
    arrays, meta = K.fit_v1(df[:1500], df[1500:1800], "nfl", trainer, LEAK_OK, trial)
    assert G.GameValueModel(arrays, meta, "nfl").predict_matrix(df[list(G.GV_FEATURES)].to_numpy(float)[:50]).ok.all()


def test_the_best_cv_trial_wins_and_an_unfinished_one_cannot():
    cv = {"base": [{"k4_pct": 80.0}, {"k4_pct": 80.0}], "rank": [{"k4_pct": 90.0}, {"k4_pct": 84.0}], "deep": [{"k4_pct": 99.0}]}
    assert K.pick_trial(cv, ["base", "rank", "deep"]) == "rank"


def test_only_the_winning_trial_reaches_the_test_nights(two_leagues, tmp_path):
    out, trainer = tmp_path / "out", KwTrainer()
    rc = K.main(["--shards", str(two_leagues), "--out", str(out), "--raw", str(_raw_dir(two_leagues)), "--leagues", "nfl", "--personal-nba", "--folds", "2", "--trials", "base,rank"], trainer=trainer)
    man = json.loads((out / "manifest.json").read_text())["leagues"]["nfl"]
    assert rc == 0 and man["trials"] == 2 and man["trial_search"]["chosen"] in ("base", "rank")
    assert set(man["trial_search"]["cv"]) == {"base", "rank"} and "test" in man["models"]["v1"]


def test_a_spent_budget_runs_only_the_first_trial(two_leagues, tmp_path):
    out, trainer = tmp_path / "out", KwTrainer()
    rc = K.main(["--shards", str(two_leagues), "--out", str(out), "--raw", str(_raw_dir(two_leagues)), "--leagues", "nfl", "--personal-nba", "--folds", "2", "--trials", "base,rank", "--budget-hours", "0.000000001"], trainer=trainer)
    search = json.loads((out / "manifest.json").read_text())["leagues"]["nfl"]["trial_search"]
    assert rc == 0 and search["skipped_out_of_time"] == ["rank"] and search["chosen"] == "base"


def test_a_ranking_trial_needs_v4(two_leagues, tmp_path):
    V.select_features("v3")
    try:
        assert K.main(["--shards", str(two_leagues), "--out", str(tmp_path / "o"), "--trials", "rank"], trainer=KwTrainer()) == K.EXIT_REFUSED
    finally:
        V.select_features("v4")


@pytest.fixture(scope="module")
def pooled_run(two_leagues, tmp_path_factory):
    out = tmp_path_factory.mktemp("p") / "out"
    rc = K.main(["--shards", str(two_leagues), "--out", str(out), "--raw", str(_raw_dir(two_leagues)), "--leagues", "nfl,nba", "--personal-nba", "--folds", "2", "--pooled"], trainer=FakeTrainer())
    return rc, out, json.loads((out / "manifest.json").read_text())


def test_pooled_model_saves_loads_for_any_of_its_leagues_and_refuses_others(pooled_run):
    rc, out, man = pooled_run
    assert rc == 0 and man["pooled"]["leagues"] == ["nfl", "nba"]
    path = G.model_path(G.GV_POOLED, "v1", out)
    assert G.load(path, "nfl").meta["league"] == "all" and G.load(path, "nba").league == "all"
    with pytest.raises(G.ModelRejected):
        G.load(path, "cbb")


def test_pooled_never_learns_from_a_night_at_or_after_the_earliest_test_night(pooled_run, two_leagues):
    _, _, man = pooled_run
    splits = json.loads((two_leagues / "splits.json").read_text())
    cut = min(min(s["test"]) for s in splits.values())
    assert man["pooled"]["fit_cut_before"] == cut
    for fold in man["pooled"]["folds"]:
        assert fold["fit"][1] < fold["validate"][0] <= fold["validate"][1] < cut


def test_pooled_reports_each_league_beside_its_own_model_on_the_same_nights(pooled_run, two_leagues):
    _, _, man = pooled_run
    splits = json.loads((two_leagues / "splits.json").read_text())
    for lg in ("nfl", "nba"):
        row = man["pooled"]["test"][lg]
        assert row["nights_test"] == len(splits[lg]["test"]) and row["per_league"] is not None and "k4_pct" in row["pooled"]
    assert man["license_tag"] == G.PERSONAL_USE


def test_cross_sport_scores_decisions_with_two_leagues_and_the_scale_of_each(pooled_run):
    _, _, man = pooled_run
    got = man["pooled"]["cross_sport"]
    assert got["pooled"]["decisions"] > 0 and got["per_league"]["decisions"] == got["pooled"]["decisions"]
    assert got["pooled"]["across_league_pairs"] > 0 and set(got["pooled"]["league_scale"]) == {"nfl", "nba"}


def test_a_pair_is_scored_only_across_leagues_and_a_tied_score_is_half():
    dec = [("a", None, 0, 5.0, 0, 3.0), ("b", None, 0, 0.5, 0, 1.0), ("c", None, 0, 2.0, 0, 3.0), ("d", None, 0, 9.0, 0, 3.0)]
    leagues = {"a": "nfl", "b": "nfl", "c": "nba", "d": "nba"}
    good, n = K._pairs(dec, leagues, lambda x: x[5])
    assert (good, n) == (3.0, 4)              # a-c and a-d tie for 0.5 each, b-c and b-d are ordered as hindsight orders them


def test_pooled_needs_two_leagues(two_leagues, tmp_path):
    assert K.main(["--shards", str(two_leagues), "--out", str(tmp_path / "o"), "--raw", str(_raw_dir(two_leagues)), "--leagues", "nfl", "--pooled-only"], trainer=FakeTrainer()) == K.EXIT_REFUSED


def test_a_pooled_file_of_the_wrong_kind_or_leagues_does_not_load():
    meta = {"schema": G.GV_SCHEMA, "kind": "table", "league": "all", "leagues": ["nfl"], "features": list(G.GV_FEATURES), "features_version": G.GV_FEATURES_VERSION,
            "status": G.GV_STATUS, "scale_points": G.GV_SCALE_POINTS, "leak_check": {"passed": True}, "sources": ["x"], "license_lines": ["y"]}
    with pytest.raises(G.ModelRejected, match="pooled"):
        G._check_meta(meta, None)
    with pytest.raises(G.ModelRejected, match="pooled"):
        G._check_meta({**meta, "kind": "trees", "leagues": ["nfl", "xfl"]}, None)
