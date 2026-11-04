"""The learned brain (redzone/scoring/brain.py): live features match the training rows, the numpy
tree walker matches LightGBM, missing models fall back to the heuristics, and the outputs feed the
scorer, the break forecast and the phone's momentum graph."""
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from conftest import FakeClock

from redzone.lab import datasets
from redzone.models import GameState, League, Phase, Play, Situation, Stoppage, Team
from redzone.scoring import brain as B
from redzone.scoring import breaks
from redzone.scoring.engine import Scorer
from redzone.sources import espn
from tests.conftest import assert_speed

FIX = Path(__file__).parent / "fixtures"


def states(league: League, name: str):
    data = json.loads((FIX / name).read_text())
    plays = espn.flatten_plays(league, data)
    return [espn.parse_summary(league, data, upto=i, plays=plays) for i in range(len(plays))]


def live(gid="g", league=League.NBA, period=4, clock=100.0, hs=100, as_=98, stoppage=Stoppage.NONE, pid="1"):
    g = GameState(league=league, game_id=gid, home=Team("WSH", score=hs), away=Team("BOS", score=as_),
                  phase=Phase.LIVE, period=period, clock_s=clock, stoppage=stoppage, situation=Situation())
    g.last_play = Play(pid, "Jump shot", "jump_shot", wallclock=1_700_000_000.0 + float(pid))
    g.recent_plays = [g.last_play]
    return g


def test_stoppage_codes_match_the_training_tables():
    assert B.STOPPAGES == datasets.STOPPAGES


def test_every_league_has_live_features_the_training_tables_carry():
    for lg in datasets.LEAGUES:
        assert set(B.features_for(lg)) <= set(datasets.FEATURES)


@pytest.mark.parametrize("league,name", [(League.CBB, "espn_cbb_summary_401820788.json"),
                                         (League.NBA, "espn_nba_summary_401909906.json"),
                                         (League.NHL, "espn_nhl_summary_401892626.json")])
def test_live_features_match_the_training_builder(tmp_path, league, name):
    """The tracker, fed one live state per play, rebuilds the same feature rows that
    sportsdata.derive builds offline from the same ESPN summary."""
    pytest.importorskip("pandas")
    from redzone.lab import sportsdata as sd

    src = FIX / name
    shutil.copy(src, tmp_path / f"summary_{src.stem.split('_')[-1]}.json")
    e = sd.norm_espn_archive(league.value, tmp_path)
    e = sd.derive(league.value, sd.fill_wp(league.value, e))
    tr = B.Tracker(league.value)
    rows = []
    for g in states(league, name):
        tr.observe(g)
        rows.append(dict(tr.row))
    feats = [f for f in B.COMMON if f in e.columns]
    ok = 0
    total = 0
    for i, r in enumerate(rows):
        if e.stoppage.iloc[i] == datasets.STOPPAGE_CODE["game_end"]:
            continue  # the builder relabels the final whistle; live can't know it's final yet
        for f in feats:
            a, b = r[f], float(e[f].iloc[i])
            total += 1
            ok += (np.isnan(a) and np.isnan(b)) or bool(np.isclose(a, b, atol=1e-3, rtol=1e-3))
    assert ok / total > 0.995


def test_numpy_trees_match_lightgbm_including_missing_values(tmp_path):
    lgb = pytest.importorskip("lightgbm")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(3000, 4)).astype(np.float32)
    X[rng.random(3000) < 0.2, 1] = np.nan
    X[rng.random(3000) < 0.2, 2] = 0.0
    y = ((X[:, 0] + np.nan_to_num(X[:, 1], nan=1.0) - X[:, 2]) > 0.3).astype(float)
    feats = ["a", "b", "c", "d"]
    bst = lgb.train(dict(objective="binary", num_leaves=8, verbose=-1, min_data_in_leaf=20, zero_as_missing=False),
                    lgb.Dataset(X, y), 30)
    reg = lgb.train(dict(objective="regression", num_leaves=8, verbose=-1), lgb.Dataset(X, y * 100), 20)
    models = {"break": B.trees_from_lightgbm(bst.dump_model(), feats),
              "hot": B.trees_from_lightgbm(reg.dump_model(), feats)}
    B.save(tmp_path / "nba.npz", models, {"league": "nba"})
    trees, info = B.load(tmp_path / "nba.npz")
    forest = B.Forest(trees)
    Xt = X[:200]
    want_b, want_h = bst.predict(Xt), reg.predict(Xt)
    for i in range(200):
        got = forest.predict(Xt[i], explain=True)
        assert got["break"][0] == pytest.approx(want_b[i], abs=1e-7)
        assert got["hot"][0] == pytest.approx(want_h[i], abs=1e-4)
        assert trees["break"].predict(Xt[i])[0] == pytest.approx(want_b[i], abs=1e-7)
    assert info["league"] == "nba"


def test_no_model_means_no_reading_and_unchanged_scores(cfg, tmp_path):
    empty = B.Brain(model_dir=tmp_path, version=2)
    assert empty.read(live()) is B.EMPTY
    cfg.update({"brain": {"enabled": True}}, record=False)
    clock = FakeClock(1000.0)
    with_brain = Scorer(cfg, clock=clock, brain=empty).score(live())
    cfg.update({"brain": {"enabled": False}}, record=False)
    without = Scorer(cfg, clock=clock).score(live())
    assert with_brain.score == without.score and with_brain.brain == {}


def test_a_broken_model_file_is_ignored(tmp_path):
    (tmp_path / "nba.npz").write_bytes(b"not a model")
    assert B.Brain(model_dir=tmp_path, version=2).read(live()) is B.EMPTY


def test_committed_models_read_a_real_game_fast_and_keep_a_timeline():
    b = B.Brain(version=2)
    if not b.available("nba"):
        pytest.skip("no trained NBA model")
    seq = [g for g in states(League.NBA, "espn_nba_summary_401909906.json") if g and g.phase == Phase.LIVE]
    t0 = time.perf_counter()
    out = [b.read(g) for g in seq]
    per = (time.perf_counter() - t0) / len(seq)
    assert_speed(per, 0.005, "brain read")  # ~0.3 ms on an M2; generous for slow CI
    r = out[-1]
    assert 0 <= r.break_soon_p <= 1 and 0 <= r.key_soon_p <= 1 and 0 <= r.hotness <= 100
    tl = b.timeline(seq[-1].game_id)
    assert 2 <= len(tl) <= B.TIMELINE_MAX + 50
    assert all(0 <= p["hot"] <= 100 for p in tl)
    assert tl[-1]["frac"] >= tl[0]["frac"]
    # the same state twice costs nothing new
    assert b.read(seq[-1]) is r


def test_scorer_carries_the_brain_reading_when_enabled(cfg, monkeypatch):
    monkeypatch.setattr(B, "WARMUP_EVENTS", 0)   # single late-game states: no warm-up
    if not B.Brain(version=2).available("nba"):
        pytest.skip("no trained NBA model")
    clock = FakeClock(1000.0)
    cfg.update({"brain": {"enabled": True}}, record=False)
    s = Scorer(cfg, clock=clock).score(live(period=4, clock=60, hs=100, as_=99))
    assert set(s.brain) >= {"hotness", "key_soon_p", "break_soon_p", "reasons"}
    assert "brain" in s.factors
    cfg.update({"brain": {"enabled": False}}, record=False)
    assert Scorer(cfg, clock=clock).score(live()).brain == {}


def test_blend_never_overrides_a_break_already_in_the_data():
    g = live(stoppage=Stoppage.TIMEOUT)
    now = breaks.Forecast(1.0, 188.0, "long timeout", True)
    assert breaks.blend(now, 0.0, g) is now
    rules = breaks.Forecast(0.7, 188.0, "mandatory timeout due")
    mixed = breaks.blend(rules, 0.3, g, 0.5)
    assert mixed.p == pytest.approx(0.5) and mixed.expect_s == 188.0 and mixed.why == rules.why
    alone = breaks.blend(breaks.NONE, 0.8, g, 0.5, ["a while since the last break"])
    assert alone.p == pytest.approx(0.8) and alone.visit and "model" in alone.why
    assert breaks.blend(breaks.NONE, None, g) is breaks.NONE


def test_break_book_blends_only_when_the_brain_is_on():
    g = live(period=2, clock=500)
    off = breaks.BreakBook(clock=lambda: 0.0, brain=lambda: (None, 0.0))
    assert off.forecast(g).p == breaks.forecast(g).p

    class Fixed:
        def read(self, _g):
            return B.Reading(break_soon_p=0.9, key_soon_p=0.1, hotness=50.0)

    on = breaks.BreakBook(clock=lambda: 0.0, brain=lambda: (Fixed(), 1.0))
    assert on.forecast(live(period=2, clock=500, gid="h")).p == pytest.approx(0.9)


def test_brain_settings_are_validated(cfg):
    assert cfg.get("brain.break_weight") is not None
    with pytest.raises(Exception):
        cfg.update({"brain": {"break_weight": 3}}, record=False)


def test_a_model_that_fails_to_predict_is_logged_once_then_skipped(monkeypatch):
    """A loadable but broken model used to log a traceback on every poll of every game."""
    monkeypatch.setattr(B, "WARMUP_EVENTS", 0)   # single late-game states: no warm-up
    b = B.Brain(version=2)
    b.enabled = True
    calls = {"n": 0}

    class Bad:
        features, rates = [], {}

        def predict(self, *a, **k):
            calls["n"] += 1
            raise ValueError("bad model")
    b.models = lambda lg: Bad()
    for i in range(4):
        assert b.read(live(pid=str(i), clock=100.0 - i)) is B.EMPTY
    assert calls["n"] == 1


def test_a_game_joined_late_stays_quiet_until_warmed_up(monkeypatch):
    """After a restart mid-game the run/lead-change/break-gap features start from zero (biased)."""
    b = B.Brain(version=2)
    b.enabled = True

    class Ok:
        features, rates = [], {}

        def predict(self, *a, **k):
            return {"key": (0.2, None)}
    b.models = lambda lg: Ok()
    first = b.read(live(pid="0", period=4, clock=300.0))
    assert first is B.EMPTY
    monkeypatch.setattr(B, "WARMUP_EVENTS", 1)
    assert b.read(live(pid="1", period=4, clock=290.0)) is not B.EMPTY
