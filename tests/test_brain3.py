"""Brain v3 (redzone/scoring/brain3.py): live features match the training rows, the fused numpy
forest matches LightGBM (+ isotonic), the committed models stay inside the budget (<10 MB, <2 ms per
game per tick), bad inputs fall back safely, and the readings reach the break forecast and the
decider's entry timing without touching the cut gates."""
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from redzone.decider.cuts import DeadTracker
from redzone.models import GameState, League, Phase, Play, Situation, Stoppage, Team
from redzone.scoring import brain as B
from redzone.scoring import brain3 as B3
from redzone.scoring import breaks
from redzone.sources import espn

FIX = Path(__file__).parent / "fixtures"
LEAGUES = ("nfl", "nba", "nhl", "mlb")


def states(league: League, name: str):
    data = json.loads((FIX / name).read_text())
    plays = espn.flatten_plays(league, data)
    return [espn.parse_summary(league, data, upto=i, plays=plays) for i in range(len(plays))]


def live(gid="g", league=League.NBA, period=4, clock=100.0, stoppage=Stoppage.NONE, pid="1", wall=None):
    g = GameState(league=league, game_id=gid, home=Team("WSH", score=100), away=Team("BOS", score=98),
                  phase=Phase.LIVE, period=period, clock_s=clock, stoppage=stoppage, situation=Situation())
    g.last_play = Play(pid, "Full timeout", "full_timeout", wallclock=wall or 1_700_000_000.0 + float(pid),
                       type_text="Full Timeout")
    g.recent_plays = [g.last_play]
    return g


def test_kclass_reads_both_feeds_alike():
    k = B3.KC
    assert B3.kclass("none", "kick off") == B3.kclass("none", "Kickoff") == k["kickoff"]
    assert B3.kclass("none", "PUNT") == B3.kclass("none", "Punt") == k["punt"]
    assert B3.kclass("timeout", "Official Timeout") == B3.kclass("timeout", "timeout") == k["timeout"]
    assert B3.kclass("whistle", "Free Throw - 1 of 2") == k["free_throw"]
    assert B3.kclass("whistle", "Hooking") == B3.kclass("whistle", "penalty") == k["foul"]
    assert B3.kclass("none", "faceoff") == B3.kclass("none", "Face Off") == k["faceoff"]
    assert B3.kclass("score_break", "Passing Touchdown") == k["score"]
    assert B3.kclass("end_period", "End Period") == k["period_end"]


def test_training_builder_uses_the_live_definitions():
    pytest.importorskip("pandas")
    from redzone.lab import brain3data as D
    assert D.kclass is B3.kclass and D.V3_EXTRA == B3.V3_EXTRA and D.TYPES == B3.TYPES


@pytest.mark.parametrize("league,name", [(League.NBA, "espn_nba_summary_401909906.json"),
                                         (League.NHL, "espn_nhl_summary_401892626.json")])
def test_live_sequence_features_match_the_training_builder(tmp_path, league, name):
    """Tracker3 fed one live state per play rebuilds the v3 rows brain3data builds offline."""
    pytest.importorskip("pandas")
    from redzone.lab import brain3data as D
    from redzone.lab import sportsdata as sd

    src = FIX / name
    shutil.copy(src, tmp_path / f"summary_{src.stem.split('_')[-1]}.json")
    e = sd.norm_espn_archive(league.value, tmp_path)
    e = D.derive3(f"espn_{league.value}", sd.derive(league.value, sd.fill_wp(league.value, e)))
    tr = B3.Tracker3(league.value)
    rows = []
    for g in states(league, name):
        tr.observe(g)
        rows.append(dict(tr.extra))
    ok = total = 0
    for i, r in enumerate(rows[: len(e)]):
        for f in B3.V3_EXTRA:
            a, b = r.get(f, np.nan), float(e[f].iloc[i])
            total += 1
            ok += (np.isnan(a) and np.isnan(b)) or bool(np.isclose(a, b, atol=1e-3, rtol=1e-3))
    assert ok / total > 0.97


def _toy_forest(tmp_path):
    lgb = pytest.importorskip("lightgbm")
    rng = np.random.default_rng(0)
    X = rng.normal(size=(3000, 4)).astype(np.float32)
    X[rng.random(3000) < 0.2, 1] = np.nan
    y = ((X[:, 0] + np.nan_to_num(X[:, 1], nan=1.0)) > 0.3).astype(float)
    cls = (np.digitize(X[:, 0], [-0.5, 0.5])).astype(float)
    binm = lgb.train(dict(objective="binary", num_leaves=8, verbose=-1), lgb.Dataset(X, y), 25)
    mc = lgb.train(dict(objective="multiclass", num_class=3, num_leaves=8, verbose=-1), lgb.Dataset(X, cls), 10)
    qm = lgb.train(dict(objective="quantile", alpha=0.5, num_leaves=8, verbose=-1), lgb.Dataset(X, y * 50 + X[:, 2]), 20)
    heads, meta_heads, slot = {}, {}, 0
    for name, bst, k, kind in (("stop60", binm, 1, "binary"), ("type", mc, 3, "multiclass"), ("entry50", qm, 1, "quantile")):
        trees = bst.dump_model()["tree_info"]
        slots = list(range(slot, slot + k))
        for i, t in enumerate(trees):
            t["slot"] = slots[i % k]
        heads[name] = trees
        meta_heads[name] = {"kind": kind, "slots": slots, "iso": [[0.0, 0.5, 1.0], [0.0, 0.4, 1.0]] if kind == "binary" else None}
        slot += k
    arrays, meta = B3.flatten(heads, ["a", "b", "c", "d"])
    meta.update(nslots=slot, heads=meta_heads, info={})
    B3.save(tmp_path / "toy.v3.npz", arrays, meta)
    return B3.load(tmp_path / "toy.v3.npz"), X, binm, mc, qm


def test_fused_forest_matches_lightgbm_with_calibration(tmp_path):
    forest, X, binm, mc, qm = _toy_forest(tmp_path)
    xt = X[:200]
    pb, pm, pq = binm.predict(xt), mc.predict(xt), qm.predict(xt)
    for i in range(200):
        got = forest.predict(xt[i])
        assert got["stop60"] == pytest.approx(np.interp(pb[i], [0, 0.5, 1], [0, 0.4, 1]), abs=1e-6)
        assert np.allclose(got["type"], pm[i], atol=1e-6)
        assert got["entry50"] == pytest.approx(pq[i], abs=1e-4)


@pytest.mark.parametrize("league", LEAGUES)
def test_committed_models_load_with_the_live_features(league):
    p = B.MODEL_DIR / f"{league}.v3.npz"
    if not p.exists():
        pytest.skip("no v3 model")
    f = B3.load(p)
    assert f.features == B3.features_for(league)
    for h in ("stop15", "stop30", "stop60", "type", "len10", "len50", "len90", "entry10", "entry50",
              "entry90", "big60", "big180", "hot"):
        assert h in f.heads


def test_committed_models_fit_the_budget():
    files = list(B.MODEL_DIR.glob("*.v3.npz"))
    if not files:
        pytest.skip("no v3 models")
    assert sum(p.stat().st_size for p in files) < 10 * 1024 * 1024


def test_a_real_game_reads_fast_calibrated_and_ordered():
    b = B.Brain(version=3)
    if b.model3("nba") is None:
        pytest.skip("no v3 NBA model")
    seq = [g for g in states(League.NBA, "espn_nba_summary_401909906.json") if g and g.phase == Phase.LIVE]
    t0 = time.perf_counter()
    out = [b.read(g) for g in seq]
    per = (time.perf_counter() - t0) / len(seq)
    assert per < 0.004   # the budget is 2 ms per game per tick on an M2; generous for slow CI
    vs = [r.v3 for r in out if r.v3 is not None]
    assert len(vs) > len(seq) // 2
    for v in vs:
        assert 0 <= v.stop[15] <= v.stop[30] <= v.stop[60] <= 1
        assert 0 <= v.big[60] <= v.big[180] <= 1
        assert v.length[0] <= v.length[1] <= v.length[2]
        assert v.entry[0] <= v.entry[1] <= v.entry[2]
        assert abs(sum(v.types.values()) - 1) < 1e-6
        assert 0 <= v.long_p <= v.stop[60] + 1e-9
    assert out[-1].as_dict()["v3"]["entry_q"]


def test_version_2_ignores_v3_models():
    b = B.Brain(version=2)
    assert b.model3("nba") is None


def test_a_broken_v3_file_falls_back_to_v2(tmp_path):
    for f in B.MODEL_DIR.glob("nba.npz"):
        shutil.copy(f, tmp_path / "nba.npz")
    (tmp_path / "nba.v3.npz").write_bytes(b"junk")
    b = B.Brain(model_dir=tmp_path, version=3)
    assert b.model3("nba") is None
    r = b.read(live())
    assert r.v3 is None


def test_a_v3_prediction_error_switches_that_league_to_v2(monkeypatch):
    b = B.Brain(version=3)
    m = b.model3("nba")
    if m is None:
        pytest.skip("no v3 NBA model")
    monkeypatch.setattr(B, "WARMUP_EVENTS", 0)
    monkeypatch.setattr(m, "predict", lambda x: (_ for _ in ()).throw(RuntimeError("boom")))
    assert b.read(live(pid="5")) is B.EMPTY
    r = b.read(live(pid="6"))
    assert r.v3 is None   # v2 from now on (or EMPTY if no v2 file)


# ----------------------------------------------------------------------------- consumers

def _v3(at, entry=(20.0, 60.0, 150.0), long_p=0.5):
    return B3.V3(at=at, stop={15: 0.1, 30: 0.3, 60: 0.6}, types={"tv_timeout": 0.7, "team_timeout": 0.3},
                 long_p=long_p, length=(40.0, 120.0, 200.0), entry=entry, big={60: 0.1, 180: 0.2},
                 type_len={"tv_timeout": 150.0, "team_timeout": 50.0}, long=("tv_timeout",))


def test_break_in_the_data_takes_the_learned_length_but_keeps_its_odds():
    g = live(league=League.NBA, stoppage=Stoppage.TIMEOUT, pid="9")
    f = breaks.forecast(g, ())
    assert f.now
    got = breaks.blend(f, 0.9, g, 0.5, v3=_v3(g.last_play.wallclock, entry=(30.0, 77.0, 200.0)))
    assert got.now and got.p == f.p and got.expect_s == 77.0
    # an old reading (another play) or the knob off keeps the table
    assert breaks.blend(f, 0.9, g, 0.5, v3=_v3(g.last_play.wallclock - 40)).expect_s == f.expect_s
    assert breaks.blend(f, 0.9, g, 0.5, v3=_v3(g.last_play.wallclock), lengths=False).expect_s == f.expect_s


def test_halftime_keeps_the_table_length():
    g = live(league=League.NBA, period=2, clock=0.0, stoppage=Stoppage.HALFTIME, pid="3")
    f = breaks.forecast(g, ())
    got = breaks.blend(f, 0.9, g, 0.5, v3=_v3(g.last_play.wallclock, entry=(100.0, 300.0, 900.0)))
    assert got.expect_s == f.expect_s >= 600


def test_brain_raised_break_uses_the_type_weighted_length():
    g = live(league=League.NBA, period=1, clock=600.0, pid="4")
    g.last_play.type_text, g.last_play.kind = "Jump Shot", "jump_shot"
    f = breaks.forecast(g, ())
    assert f.p == 0
    got = breaks.blend(f, 0.8, g, 0.5, v3=_v3(g.last_play.wallclock))
    assert got.p == pytest.approx(0.4) and got.expect_s == pytest.approx(150.0)


def _tracker_with_open_window(t0=1000.0):
    dt = DeadTracker(League.NFL)
    dt.add("p1", t0 - 40, t0 - 40, "rush", "J.Doe left tackle for 3 yards", "Rush", 600.0, 1)
    dt.add("p2", t0, t0, "td", "J.Doe 1 yard rush, TOUCHDOWN", "Rushing Touchdown", 560.0, 1)
    return dt


def test_learned_entry_moves_only_the_expected_restart():
    dt = _tracker_with_open_window()
    base_eta = dt.resume_eta(1010.0)
    base_until = dt.dead_until(1010.0)
    base_landing = dt.landing(1005.0, 1015.0)
    dt.learn(1000.0, (60.0, 120.0, 240.0))
    assert dt.resume_eta(1010.0) == pytest.approx(1120.0) != base_eta
    # the cut / landing gates never read it
    assert dt.dead_until(1010.0) == base_until
    assert dt.landing(1005.0, 1015.0) == base_landing


def test_learned_entry_ignored_when_stale_or_broken():
    dt = _tracker_with_open_window()
    base = dt.resume_eta(1010.0)
    dt.learn(900.0, (60.0, 120.0, 240.0))       # about an older play
    assert dt.resume_eta(1010.0) == base
    for bad in [(float("nan"), 1, 2), (-5, 1, 2), (1, 2), None, ("x", 1, 2), (1, 2, 99999)]:
        dt2 = _tracker_with_open_window()
        dt2.learn(1000.0, bad)
        assert dt2.learned is None and dt2.resume_eta(1010.0) == base
    dt.learn(None, (1, 2, 3))
    assert dt.resume_eta(1010.0) == base


def test_contract_adapters_are_the_contract_types():
    from redzone import contracts as c
    from redzone.scoring.breaks import BreakBook
    m = B3.MomentV3(B.Brain(version=3))
    assert isinstance(m, c.MomentModel)
    b = B3.BreaksV3(BreakBook(clock=lambda: 0.0), B.Brain(version=3))
    assert isinstance(b, c.BreakModel)
    assert B3.MomentV3(None).predict(live(), 1.0).available is False


def test_contract_break_in_the_data_carries_v3_quantiles_and_restart(monkeypatch):
    from redzone.scoring.breaks import BreakBook
    g = live(league=League.NBA, stoppage=Stoppage.TIMEOUT, pid="9")
    brain = B.Brain(version=3)
    v = _v3(g.last_play.wallclock, entry=(40.0, 95.0, 210.0))
    monkeypatch.setattr(brain, "available", lambda lg: True)
    monkeypatch.setattr(brain, "read", lambda g: B.Reading(break_soon_p=0.5, v3=v))
    f = B3.BreaksV3(BreakBook(clock=lambda: 0.0), brain).forecast(g, 5.0)
    assert f.in_data_now and f.p_start == 1.0
    assert (f.length_q10_s, f.length_q50_s, f.length_q90_s) == (40.0, 95.0, 210.0)
    assert f.next_restart_at == pytest.approx(g.last_play.wallclock + 95.0)


def test_contract_break_coming_uses_type_quantiles(monkeypatch):
    from redzone.scoring.breaks import BreakBook
    g = live(league=League.NBA, period=1, clock=600.0, pid="4")
    g.last_play.type_text, g.last_play.kind = "Jump Shot", "jump_shot"
    brain = B.Brain(version=3)
    v = _v3(g.last_play.wallclock)
    v.type_q.update({"tv_timeout": [120.0, 150.0, 200.0]})
    monkeypatch.setattr(brain, "available", lambda lg: True)
    monkeypatch.setattr(brain, "read", lambda g: B.Reading(break_soon_p=0.8, v3=v, break_reasons=[]))
    book = BreakBook(clock=lambda: 0.0, brain=lambda: (brain, 0.5, True))
    f = B3.BreaksV3(book, brain).forecast(g, 5.0)
    assert not f.in_data_now and f.p_start == pytest.approx(0.4)
    assert (f.length_q10_s, f.length_q50_s, f.length_q90_s) == pytest.approx((120.0, 150.0, 200.0))
    assert f.kind.value == "commercial"
