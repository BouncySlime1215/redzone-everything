"""Features v5: the candidate generator, its leak proofs, the kept-column runtime and the selection that never sees a held night.

Why: 100,000+ generated columns are only worth anything if none can read the future, the shipped ones cost little on the 8 GB Mac and v2-v4 stay exactly what they were.
Each earlier leak in this project (the label window, feed order, clock rounding) has its own test here. Selection runs on a few blocks with a stand-in fitter: no training happens on the Mac.
"""
import datetime as dt
import hashlib
import json
import random
import time

import numpy as np
import pandas as pd
import pytest

from redzone.moments.detect import clock_s
from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V
from redzone.noggin import wide_features as W
from scripts import kaggle_gamevalue as K
from scripts import kaggle_gamevalue_select as S
from scripts import value_leak_check as LC
from tests.test_gamevalue import NIGHTS, _raw_dir, _shards, nba_summary, nfl_summary

T0 = dt.datetime(2023, 10, 8, 17, 0, tzinfo=dt.timezone.utc)
GOLDEN = {"v2/nfl": (359, "d36f29d322917c32ca4b86d3f6efa3911a87ba4356261a9e47aaffbfb0d0cf4d"), "v2/nba": (479, "d797a5502cf612122857fc741e13228f01f6e2bcc2f8ac696d3004a5a44aafef"),
          "v3/nfl": (359, "c59ff12a9544a5ef9ad5ca49bbb94605143edac30e606be01eec35dd5da1cc39"), "v3/nba": (479, "0dacf9ed44ec2c73e0baf0095a7703bf8549d8383fe63908251dfaba4f2ec2d3"),
          "v4/nfl": (359, "c0858288539fa414b1bfbdbc1e9834876cbf4469cb31570bee13fb4d8569227b"), "v4/nba": (479, "51e8047006fd4b873ccd78998ae6fb5b763cc7539c938136660b068c9ca0b651")}
KEPT = ["gv5__plays__all__s600__sum__raw__none", "gv5__pts__all__p10__sum__raw__none", "gv5__pts__away__s300__sum__raw__none", "gv5__pts__home__p5__sum__raw__none",
        "gv5__pts__lead__p10__slope__raw__late", "gv5__pts__trail__s300__rate__raw__close", "gv5__scoring__home__p5__sum__raw__none", "gv5__pts__all__game__cum__log__margin",
        "gv5__pts__home__game__since_s__raw__none", "gv5__plays__all__p200__nz__raw__none"]
KEPT = sorted(set(KEPT))


@pytest.fixture
def kept_file(tmp_path, monkeypatch):
    path = tmp_path / "kept.json"
    W.write_kept(path, {"nfl": {"selection_nights": NIGHTS[:5], "kept_columns": KEPT}, "nba": {"selection_nights": NIGHTS[:5], "kept_columns": KEPT}}, complete=True, shard_id="test")
    monkeypatch.setenv(W.KEPT_ENV, str(path))
    return path


@pytest.fixture
def v5(kept_file):
    V.select_features("v5")
    yield kept_file
    V.select_features("v2")


def _flat(league, summary):
    plays = V.flat_plays(league, V.wall_ordered(league, V.strip_published_wp(summary)))
    return [x for x in plays if G.feed_row(league, x[1])]


# --- v2, v3 and v4 are not touched ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["v2", "v3", "v4"])
@pytest.mark.parametrize("league,make,a,b", [("nfl", nfl_summary, 0, 13), ("nba", nba_summary, 0, 5)])
def test_older_feature_versions_are_byte_identical(version, league, make, a, b):
    V.select_features(version)
    try:
        rows = V.build_rows(league, "g1", make(a, b), night="20231008", season=2023, source="nflverse_pbp", license_line="x", plan="A")
        cols = list(V.VALUE_FEATURES + V.VALUE_LABELS)
        got = (len(rows), hashlib.sha256(rows[cols].to_numpy(np.float32).tobytes()).hexdigest())
    finally:
        V.select_features("v2")
    assert got == GOLDEN[f"{version}/{league}"]


def test_v5_is_opt_in_and_needs_a_kept_list(tmp_path, monkeypatch):
    assert G.GV_FEATURES_VERSION == "v2" and V.FEATURES_VERSION == "v2"            # the default did not move
    monkeypatch.setenv(W.KEPT_ENV, str(tmp_path / "missing.json"))
    with pytest.raises(ValueError, match="kept-column list"):
        G.select_features("v5")
    assert G.GV_FEATURES_VERSION == "v2"
    with pytest.raises(ValueError, match="known versions"):
        G.select_features("v6")


def test_v5_columns_are_v4_then_the_kept_list_and_v4_values_do_not_move(v5):
    assert V.VALUE_FEATURES == G.GV_FEATURE_SETS["v4"] + tuple(KEPT) and G.GV_FEATURES == V.VALUE_FEATURES
    rows5 = V.build_rows("nba", "g1", nba_summary(0, 5), night="20231008", season=2023, source="x", license_line="x", plan="A")
    V.select_features("v4")
    rows4 = V.build_rows("nba", "g1", nba_summary(0, 5), night="20231008", season=2023, source="x", license_line="x", plan="A")
    cols = list(G.GV_FEATURE_SETS["v4"])
    assert rows5[cols].to_numpy().tobytes() == rows4[cols].to_numpy().tobytes()


def test_a_kept_list_from_another_grid_will_not_load(tmp_path, monkeypatch):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema": 1, "generator": "gv5-other", "complete": True, "columns": KEPT}))
    with pytest.raises(ValueError, match="generates"):
        W.read_kept(path)
    path.write_text(json.dumps({"schema": 1, "generator": W.GENERATOR, "complete": True, "columns": ["gv5__pts__all__p7__sum__raw__none"]}))
    with pytest.raises(ValueError, match="not a wide column"):
        W.read_kept(path)


# --- the generator ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("league", W.LEAGUES)
def test_at_least_100k_candidates_per_league_with_systematic_names(league):
    names = W.candidate_names(league)
    assert len(names) == W.candidate_count(league) >= 100_000 and len(set(names)) == len(names)
    assert names == W.candidate_names(league)                                       # the same list every run
    assert all(W.parse(n) for n in names[:: len(names) // 500])
    assert all(LC.WIDE_RE.match(n) for n in names[:: len(names) // 500])
    print(f"\ncandidates generated, {league}: {len(names):,}")


def test_the_named_columns_agree_with_the_leak_checks_grammar_and_tables():
    assert LC.WIDE_RING == W.RING and W.REGULATION == {k: V.REGULATION_LEN[k] for k in W.REGULATION}
    bad = ["gv5__pts__all__p10__sum__raw__none__x", "gv5__plays__home__p10__sum__raw__none", "x__pts__all__p10__sum__raw__none", "gv5__pts__all__p10__sum__log__late", "gv5__pts__all__p10__sum__lg__none"]
    for n in bad:
        with pytest.raises(ValueError):
            W.parse(n)
    assert all(LC.WIDE_RE.match(n) for lg in W.LEAGUES for n in W.candidate_names(lg)[::2000])


def test_block_values_line_up_with_block_names():
    s = W.track("nba", _flat("nba", nba_summary(0, 5)))
    head = np.array([200, 239])
    b = next(x for x in W.blocks("nba") if x.stat == "pts" and x.window == "p10")
    names, vals = W.block_names(b), W.block_values(s, head, b)
    assert vals.shape == (2, len(names))
    i = names.index("gv5__pts__all__p10__sum__raw__none")
    pts = s.x[:, W.series_names("nba").index("pts@all")]
    assert vals[0, i] == pytest.approx(pts[191:201].sum()) and vals[1, i] == pytest.approx(pts[230:240].sum())


def test_every_family_has_the_value_a_person_would_compute():
    plays = _flat("nba", nba_summary(0, 5))
    s = W.track("nba", plays)
    idx = {n: i for i, n in enumerate(W.series_names("nba"))}
    head = np.array([100])
    cols = {"gv5__pts__all__s300__sum__raw__none": s.x[(s.walls > s.walls[100] - 300) & (s.walls <= s.walls[100]), idx["pts@all"]].sum(),
            "gv5__pts__all__p10__mean__raw__none": s.x[91:101, idx["pts@all"]].mean(), "gv5__pts__all__p10__max__raw__none": s.x[91:101, idx["pts@all"]].max(),
            "gv5__pts__all__p10__nz__raw__none": (s.x[91:101, idx["pts@all"]] != 0).sum(), "gv5__pts__all__p10__sum__lg__margin": s.x[91:101, idx["pts@all"]].sum() / W._unit("nba", "pts") * s.cross[100, W.crosses_of("nba").index("margin")],
            "gv5__pts__all__game__cum__raw__none": s.x[:101, idx["pts@all"]].sum(), "gv5__plays__all__game__cum__raw__none": 101.0}
    X = W.run_plan(s, head, W.compile_plan("nba", tuple(cols)))
    assert X[0] == pytest.approx(np.array(list(cols.values()), np.float32), abs=1e-3)


# --- no column may read the future ----------------------------------------------------------------------------------------------------


def _all_columns(league, summary, heads):
    """Every candidate at the given heads: one matrix, in candidate order."""
    s = W.track(league, _flat(league, summary))
    return np.hstack([W.block_values(s, heads, b) for b in W.blocks(league)])


@pytest.mark.parametrize("league,make", [("nba", lambda: nba_summary(0, 5)), ("nfl", lambda: nfl_summary(0, 13))])
def test_changing_a_future_play_changes_no_generated_column_of_an_earlier_row(league, make):
    """Every candidate column, all 100,000+, at early heads: rewriting everything after the head changes nothing."""
    base = make()
    n = len(_flat(league, base))
    heads = np.array([10, n // 3, n // 2])
    a = _all_columns(league, base, heads)
    cut = heads[-1]
    changed = make()
    plays = changed["plays"] if league == "nba" else [p for d in changed["drives"]["previous"] for p in d["plays"]]
    plays = sorted(plays, key=lambda p: p["wallclock"])
    for p in plays[cut + 1:]:                     # later plays: different type, score, clock, period, yards
        p["type"]["text"] = "Three Point Jumper" if league == "nba" else "Passing Touchdown"
        p["homeScore"], p["awayScore"] = p["homeScore"] + 40, p["awayScore"] + 40
        p["clock"]["displayValue"] = "0:05"
        p["period"]["number"] = 9
        p["statYardage"] = 90
        if league == "nfl":
            p["start"] = {"down": 4, "distance": 1, "yardsToEndzone": 1}
    b = _all_columns(league, changed, heads)
    assert a.shape[1] == W.candidate_count(league) and np.array_equal(a, b)


def test_a_track_of_a_cut_feed_is_a_true_prefix():
    summary = nba_summary(0, 5)
    plays = _flat("nba", summary)
    full = W.track("nba", plays)
    for cut in (1, 40, 150):
        part = W.track("nba", plays[:cut])
        assert np.array_equal(full.x[:cut], part.x) and np.array_equal(full.regs[:cut], part.regs) and np.array_equal(full.cross[:cut], part.cross)


def test_no_column_reads_the_decision_or_the_label_window(v5, monkeypatch):
    """The old since_head_s leak: a v5 column must be the same whatever the decision time. The lag moves the decision; every kept column at a given head stays put."""
    summary = nba_summary(0, 5)
    kw = dict(night="20231008", season=2023, source="x", license_line="x", plan="A")
    a = V.build_rows("nba", "g1", summary, **kw)
    monkeypatch.setattr(V, "sample_lags", lambda league, game_id, n: np.full(n, 150.0))
    b = V.build_rows("nba", "g1", summary, **kw)
    assert (a.lag_s != b.lag_s).any() and (a.since_head_s != b.since_head_s).any()   # the lag really moved what since_head_s reads
    a, b = a.sort_values(["feed_cut_t"]).reset_index(drop=True), b.sort_values(["feed_cut_t"]).reset_index(drop=True)
    assert np.array_equal(a.feed_head_t, b.feed_head_t) and np.array_equal(a[KEPT].to_numpy(), b[KEPT].to_numpy())


def test_rows_on_the_grid_and_on_a_play_read_the_same_columns_at_the_same_head(v5):
    """Clock rounding: windows anchor on the head play's wall clock, not on a rounded grid second, so every row at one head has identical columns."""
    rows = V.build_rows("nba", "g1", nba_summary(0, 5), night="20231008", season=2023, source="x", license_line="x", plan="A")
    groups = rows.groupby("feed_head_t")[KEPT].nunique()
    assert (groups.to_numpy() == 1).all()


def test_feed_order_of_tied_or_out_of_order_stamps_does_not_reach_earlier_rows():
    """hoopR has plays stamped later than the plays after them. A future play delivered out of feed order must not change what a cut feed saw."""
    summary = nba_summary(0, 5)
    plays = summary["plays"]
    shuffled = {**summary, "plays": list(plays)}
    late = shuffled["plays"].pop(120)                     # a play whose stamp is later than plays 121+...
    shuffled["plays"].insert(30, late)                    # ...delivered 90 positions early in the feed: still future for a cut before its wall
    a = W.track("nba", _flat("nba", summary))
    b = W.track("nba", _flat("nba", shuffled))
    assert np.array_equal(a.x, b.x) and np.array_equal(a.walls, b.walls)


def test_feed_rows_one_source_only_has_never_become_a_head():
    """v4's rule: official timeouts and quarter ends are one source only. The generator never sees them, offline or live."""
    base = nfl_summary(0, 13)
    flat = [p for d in base["drives"]["previous"] for p in d["plays"]]
    extra = {**flat[10], "id": "x1", "type": {"text": "Official Timeout"}, "text": "official timeout", "wallclock": flat[10]["wallclock"]}
    plays_full = V.flat_plays("nfl", base)
    with_row = sorted(plays_full + [(plays_full[10][0], extra)], key=lambda x: x[0])
    filtered = [x for x in with_row if G.feed_row("nfl", x[1])]
    s = W.track("nfl", filtered)
    assert np.array_equal(s.x, W.track("nfl", [x for x in plays_full if G.feed_row("nfl", x[1])]).x)
    game = W.WideGame("nfl", KEPT)
    game.update(with_row)
    online = game.matrix()
    assert online is not None and game._kept == len(filtered)


def test_clock_comes_from_the_one_parser_so_rounding_cannot_differ():
    """The earlier rounding leak: two parsers rounded the clock differently. This file reads the clock only through redzone.moments.detect.clock_s."""
    text = open(W.__file__).read()
    assert text.count("clock_s(") == 1 and 'displayValue' not in text
    t = W.Tracker("nba")
    p = {"period": {"number": 2}, "clock": {"displayValue": "1:05.3"}, "homeScore": 0, "awayScore": 0}
    _, _, cross, _ = t.push(1000.0, p)
    frac = cross[W.crosses_of("nba").index("left")]
    want = (t.regs - 2) * t.length + float(clock_s(p))
    assert frac == pytest.approx(want / t.scale)


def test_a_score_correction_does_not_reach_back_or_count_as_a_score():
    plays = [(float(i), {"id": str(i), "type": {"text": "Jump Shot"}, "text": "", "period": {"number": 1}, "clock": {"displayValue": "5:00"}, "homeScore": h, "awayScore": 0}) for i, h in enumerate([2, 4, 2, 5])]
    s = W.track("nba", plays)
    pts = s.x[:, W.series_names("nba").index("pts@all")]
    assert list(pts) == [2.0, 2.0, 0.0, 3.0]             # the drop from 4 to 2 is no negative score, and earlier rows are unchanged


def test_the_prefix_rebuild_catches_a_future_reading_column(v5):
    """Same check as scripts/kaggle_value_shards: every kept column rebuilt from a cut-off feed must match. A planted peek must trip it."""
    summary = nba_summary(0, 5)
    rows = V.build_rows("nba", "g1", summary, night="20231008", season=2023, source="x", license_line="x", plan="A")
    n, bad = V.prefix_check("nba", "g1", summary, rows)
    assert n > 0 and not bad

    def peeking(view, head, decision):
        out = V.features(view, head, decision)
        out[KEPT[0]] = np.full(len(head), view.total[-1])              # reads the last play of whatever it was given
        return out

    planted = V.build_rows("nba", "g1", summary, night="20231008", season=2023, source="x", license_line="x", plan="A", feature_fn=peeking)
    n2, bad2 = V.prefix_check("nba", "g1", summary, planted, feature_fn=peeking)
    assert n2 > 0 and any(KEPT[0] in b for b in bad2)


def test_the_leak_check_passes_v5_shards_and_flags_a_bad_column(tmp_path, v5):
    out = _shards(tmp_path / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A", games=2)
    rep = LC.check_dir(out, None, False, _raw_dir(out))
    assert rep.fails == [] and rep.checked["raw_rows"] > 0 and rep.checked["prefix_rows"] > 0 and rep.checked["raw_wide_columns"] == sum(bool(LC.WIDE_RAW_RE.match(n)) for n in KEPT) > 0
    side = sorted((out / "nfl").glob("*.json"))[0]
    info = json.loads(side.read_text())
    info["v5_columns"] = info["v5_columns"] + ["not_a_generated_name"]
    side.write_text(json.dumps(info))
    rep = LC.check_dir(out, None, False, _raw_dir(out))
    assert any("not generated names" in f or "missing columns" in f for f in rep.fails)


def test_shards_from_another_kept_list_are_not_finished(tmp_path, v5, monkeypatch):
    rows = V.build_rows("nba", "g1", nba_summary(0, 5), night="20231008", season=2023, source="x", license_line="x", plan="A")
    V.write_shard(tmp_path, "nba", "2023", rows, {"source": "x", "license": "x", "plan": "A"})
    assert V.shard_done(tmp_path, "nba", "2023")
    other = tmp_path / "other.json"
    W.write_kept(other, {"nba": {"selection_nights": NIGHTS[:5], "kept_columns": KEPT[:3]}}, complete=True, shard_id="test")
    monkeypatch.setenv(W.KEPT_ENV, str(other))
    V.select_features("v5")
    assert not V.shard_done(tmp_path, "nba", "2023")


# --- the runtime: only the kept columns, online, bounded ------------------------------------------------------------------------------


@pytest.mark.parametrize("league,make", [("nba", lambda: nba_summary(0, 5)), ("nfl", lambda: nfl_summary(0, 13))])
def test_online_equals_offline_at_every_head(league, make):
    plays = _flat(league, make())
    pick = sorted(random.Random(1).sample(W.candidate_names(league), 400))
    full = W.track(league, plays)
    offline = W.run_plan(full, np.arange(len(plays)), W.compile_plan(league, tuple(pick)))
    game = W.WideGame(league, pick)
    for i in range(1, len(plays) + 1):
        game.update(plays[:i])
        np.testing.assert_array_equal(game.matrix()[0], offline[i - 1])


def test_runtime_state_is_bounded_however_long_the_game():
    names = list(KEPT)
    short, long_game = W.WideGame("nba", names), W.WideGame("nba", names)
    plays = [(float(i), {"id": str(i), "type": {"text": "Jump Shot"}, "text": "", "period": {"number": 1}, "clock": {"displayValue": "5:00"}, "homeScore": i, "awayScore": 0}) for i in range(3000)]
    short.update(plays[:300])
    long_game.update(plays)
    assert short.state_bytes() == long_game.state_bytes() < 300_000
    print(f"\nstate per game: {long_game.state_bytes():,} bytes (bounded by RING = {W.RING} plays)")


def test_cost_per_tick_is_small_for_a_full_slate(capsys):
    """The per-tick cost, measured and printed: a realistic kept list (200 columns) on a 32-game slate, every game ticking. No model call here; forest time is measured in test_gamevalue."""
    names = sorted(random.Random(7).sample(W.candidate_names("nba"), 200))
    plays = _flat("nba", nba_summary(0, 5))
    games = [W.WideGame("nba", names) for _ in range(32)]
    for g in games:
        g.update(plays[:180])
    start = time.perf_counter()
    for g in games:
        g.update(plays[:181])
    update_s = time.perf_counter() - start
    start = time.perf_counter()
    out = [g.matrix() for g in games]
    compute_s = time.perf_counter() - start
    per_game = (update_s + compute_s) / len(games)
    with capsys.disabled():
        print(f"\nv5 per-tick cost (200 kept columns, 32 games): {update_s * 1e3 / 32:.3f} ms update + {compute_s * 1e3 / 32:.3f} ms compute per game, {(update_s + compute_s) * 1e3:.1f} ms for the slate; "
              f"{games[0].state_bytes() / 1024:.0f} KB per game")
    assert all(x.shape == (1, 200) for x in out) and per_game < 0.05          # a loaded Mac runs this; the bar is ordinary, not a benchmark


# --- selection: training nights only, kept list, Kaggle guards ------------------------------------------------------------------------------


class FakeFitter:
    """Stands in for LightGBM: 'importance' is correlation with the target on the rows it was given; prediction is the fit's best feature, so only those rows taught it."""

    def __init__(self):
        self.calls = 0

    def fit(self, X, y, names, seed=7):
        self.calls += 1
        y0 = (y - y.mean()) / (y.std() or 1.0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        z = (X - X.mean(axis=0)) / std
        gains = np.abs((z * y0[:, None]).mean(axis=0))
        return gains, int(np.argmax(gains)), float(np.polyfit(X[:, int(np.argmax(gains))], y, 1)[0]), std

    @staticmethod
    def gain(model):
        return model[0]

    @staticmethod
    def predict(model, X):
        return X[:, model[1]] * model[2]


def test_selection_nights_are_train_only_and_refuses_a_held_or_eval_night():
    splits = {"nfl": {"train": ["20231008", "20231015"], "calib": ["20231022"], "test": ["20231029"]}}
    assert S.selection_nights(splits, "nfl") == ["20231008", "20231015"]
    with pytest.raises(S.Refused, match="also held out"):
        S.selection_nights({"nfl": {"train": ["20231008", "20231029"], "test": ["20231029"]}}, "nfl")
    from redzone.noggin import factory
    cut = factory.EVAL_FIRST_NIGHT["nfl"]
    with pytest.raises(S.Refused, match="eval window"):
        S.selection_nights({"nfl": {"train": [cut]}}, "nfl")


def _games(nights):
    games = []
    for w, night in enumerate(nights):
        start = (dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - T0).total_seconds()
        for i in range(2):
            games.append((f"g{w}{i}", nba_summary(start + i * 60, 5 + (w + i) % 9 * 4), night, 2023, 0))
    return games


WANTED = tuple(b for b in W.blocks("nba") if b.stat in ("pts", "plays") and b.window in ("p5", "p10", "s300", "game"))


def _factory(games, seen=None):
    def factory(wanted):
        for g in games:
            if g[2] in wanted:
                if seen is not None:
                    seen.append(g[2])
                yield g
    return factory


@pytest.fixture
def v4():
    V.select_features("v4")
    yield
    V.select_features("v2")


def test_selection_splits_nights_and_reports_both_counts(tmp_path, v4):
    nights, seen = NIGHTS[:10], []
    result = S.select_league("nba", tmp_path, nights, FakeFitter(), keep_filter=60, sizes=(5, 20), folds=2, blocks=WANTED, games_factory=_factory(_games(nights), seen), log=lambda *a, **k: None)
    assert result.filter_nights == nights[:6] and result.forward_nights == nights[6:] and set(seen) == set(nights) and len(seen) == len(set(g[0] for g in _games(nights)))   # each game built once
    assert result.counts["candidates_generated"] == sum(len(W.block_names(b)) for b in WANTED) and result.counts["boosted_columns"] <= 60
    assert result.folds_planned == result.folds_done == 2 and result.complete and all(W.parse(c) for c in result.kept) and result.verdict


def test_the_filter_cannot_see_a_value_changed_only_on_forward_nights(tmp_path, v4, monkeypatch):
    """Nesting: rewrite every game of the forward nights. The filter's column order must not move, because it never reads those nights."""
    nights = NIGHTS[:10]
    recorded = []
    real = S.filter_candidates

    def spying(*a, **k):
        got = real(*a, **k)
        recorded.append(got[0])
        return got

    monkeypatch.setattr(S, "filter_candidates", spying)
    games = _games(nights)
    S.select_league("nba", tmp_path, nights, FakeFitter(), keep_filter=60, sizes=(5,), folds=2, blocks=WANTED, games_factory=_factory(games), log=lambda *a, **k: None)
    changed = []
    for gid, summary, night, season, playoff in games:
        if night in nights[6:]:
            for p in summary["plays"]:
                p["homeScore"] += 50
                p["awayScore"] += 90
                p["type"]["text"] = "Three Point Jumper"
        changed.append((gid, summary, night, season, playoff))
    S.select_league("nba", tmp_path, nights, FakeFitter(), keep_filter=60, sizes=(5,), folds=2, blocks=WANTED, games_factory=_factory(changed), log=lambda *a, **k: None)
    assert recorded[0] == recorded[1] and len(recorded[0]) > 0


def test_a_deadline_stops_cleanly_and_keeps_nothing(tmp_path, v4):
    nights = NIGHTS[:10]
    result = S.select_league("nba", tmp_path, nights, FakeFitter(), keep_filter=60, sizes=(5,), folds=2, blocks=WANTED, deadline=time.monotonic() - 1.0, games_factory=_factory(_games(nights)), log=lambda *a, **k: None)
    assert not result.complete and result.kept == [] and "out of time" in result.verdict and result.counts["filter_stopped_early"]


def test_the_command_reads_only_train_nights_from_the_raw_files_and_writes_a_list_the_runtime_loads(tmp_path, monkeypatch, v4):
    """The Kaggle entry end to end, through the real raw-file reader. Only a few blocks, so it stays light; the fitter is a stand-in."""
    out = _shards(tmp_path / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A", games=2)
    few = tuple(b for b in W.blocks("nfl") if b.stat == "pts" and b.window in ("p5", "p10", "game"))
    monkeypatch.setattr(S.W, "blocks", lambda league: few)
    splits = json.loads((out / "splits.json").read_text())["nfl"]
    built = []
    real = S.game_rows
    monkeypatch.setattr(S, "game_rows", lambda league, gid, summary, night, season, playoff, step=1: (built.append(night), real(league, gid, summary, night, season, playoff, step))[1])
    kept_path = tmp_path / "v5_kept.json"
    rc = S.main(["--shards", str(out), "--raw", str(_raw_dir(out)), "--out", str(kept_path), "--leagues", "nfl", "--keep-filter", "12", "--sizes", "3", "--folds", "2"], fitter=FakeFitter())
    assert rc == 0 and set(built) <= set(splits["train"]) and built
    info = W.read_kept(kept_path)
    assert info["leagues"]["nfl"]["candidates_generated"] == sum(len(W.block_names(b)) for b in few) and info["leagues"]["nfl"]["complete"] and info["shard_id"] == V.shards_id(out)
    assert set(info["leagues"]["nfl"]["selection_nights"]) == set(splits["train"])
    monkeypatch.setenv(W.KEPT_ENV, str(kept_path))
    K.check_kept_list(out, ["nfl"])
    partial = tmp_path / "partial.json"
    rc = S.main(["--shards", str(out), "--raw", str(_raw_dir(out)), "--out", str(partial), "--leagues", "nfl", "--budget-hours", "0.0000001"], fitter=FakeFitter())
    data = json.loads(partial.read_text())
    assert rc == 0 and data["complete"] is False and data["leagues"]["nfl"]["complete"] is False
    with pytest.raises(ValueError, match="not a finished"):
        W.read_kept(partial)


def test_the_probe_times_every_step_and_writes_nothing(tmp_path, monkeypatch, v4, capsys):
    out = _shards(tmp_path / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A", games=2)
    few = tuple(b for b in W.blocks("nfl") if b.stat == "pts" and b.window in ("p5", "game"))
    monkeypatch.setattr(S.W, "blocks", lambda league: few)
    kept_path = tmp_path / "v5_kept.json"
    assert S.main(["--shards", str(out), "--raw", str(_raw_dir(out)), "--out", str(kept_path), "--leagues", "nfl", "--keep-filter", "12", "--probe-games", "3"], fitter=FakeFitter()) == 0
    printed = capsys.readouterr().out
    assert "PROBE nfl" in printed and "extrapolated_hours" in printed and not kept_path.exists()


def test_selection_stops_when_a_night_outside_its_list_reaches_it(tmp_path, v4):
    nights = NIGHTS[:3]
    leak = _games(NIGHTS[3:4])
    wanted = tuple(b for b in W.blocks("nba") if b.stat == "pts" and b.window == "p5")
    with pytest.raises(S.Refused, match="not a training night"):
        S.select_league("nba", tmp_path, nights, FakeFitter(), keep_filter=10, sizes=(5,), folds=2, blocks=wanted, games_factory=lambda _wanted: iter(leak), log=lambda *a, **k: None)


def _res(base, got, noise=0.01, done=5, planned=5):
    return S.ForwardResult([], {0: base, 5: got}, [noise] * len(base), done, planned)


def test_a_size_must_beat_the_measured_noise_on_most_folds_and_every_fold_must_have_run():
    base = [50.0] * 5
    assert S.pick_size(_res(base, [50.5, 50.6, 50.5, 50.7, 50.4]), (5,))[0] == 5
    assert S.pick_size(_res(base, [50.5, 50.6, 50.5, 50.7, 50.4], noise=2.0), (5,))[0] == 0             # the measured noise is bigger than the gain
    assert S.pick_size(_res(base, [51.0, 51.0, 51.0, 49.9, 49.9]), (5,))[0] == 0                        # 3 of 5 folds positive
    assert S.pick_size(_res([50.0], [55.0], done=1, planned=5), (5,))[1].startswith("refused")          # a deadline after fold 1 picks nothing
    assert S.pick_size(_res(base, [51.0] * 4), (5,))[0] == 0                                            # a size missing a fold cannot win
    assert S.pick_size(S.ForwardResult([], {0: [], 5: []}, [], 0, 5), (5,))[0] == 0


def test_the_filter_keeps_a_useful_column_and_drops_constants_and_label_copies():
    f = S.CorrFilter(3)
    rng = np.random.default_rng(0)
    y = rng.normal(size=500)
    good = y * 0.6 + rng.normal(size=500) * 0.8
    X = np.column_stack((good, y, np.ones(500)))
    f.add_labels(y, y)
    f.update(0, X, y, y)
    r, var = f.correlations()
    assert var[2] == 0 and r[1] >= S.COPY_R and 0.4 < r[0] < 0.9


def test_thinning_keeps_whole_decisions():
    t = np.array([0.0, 30.0, 170.0, 200.0, 360.0, 400.0, 540.0])
    keep = S.bucket_keep(t, 2)
    assert keep.tolist() == [True, True, True, False, True, True, False]
    assert S.bucket_keep(t, 1).all()


def test_order_clones_are_duplicates_and_negative_zero_is_zero():
    rng = np.random.default_rng(1)
    x = rng.normal(size=500).astype(np.float32)
    negative = x.copy()
    negative[x < 0] = -0.0
    zero = negative.copy()
    zero[x < 0] = 0.0
    X = np.column_stack((x, x * 3.5, np.sign(x) * np.log1p(np.abs(x)), rng.normal(size=500).astype(np.float32), negative, zero))
    assert S.drop_duplicates(X) == [0, 3, 4]


def test_the_generator_id_tracks_logic_not_comments():
    src = open(W.__file__).read()
    gv, det = open(G.__file__).read(), open(W.clock_s.__code__.co_filename).read()
    base = W.logic_digest(src, gv, det)
    assert base == W.GENERATOR.removeprefix("gv5-")
    assert W.logic_digest(src.replace("RING = 256  ", "RING = 257  "), gv, det) != base
    assert W.logic_digest(src.replace("Tracker therefore counts", "Tracker so counts"), gv, det) == base           # a docstring edit is not a new generator
    assert W.logic_digest(src, gv.replace('"timeout" in kind', '"time out" in kind'), det) != base                 # nor is the borrowed play_kind free to change


def test_a_football_touchdown_reads_the_same_from_either_source():
    """Checked against six dev-night ESPN archive games: ESPN's touchdown row is usually +7, the nflverse adapter drops the conversion row and shows +6, then +1 on the next row."""
    def plays(scores):
        return [(float(i * 30), {"id": str(i), "type": {"text": t}, "text": "", "period": {"number": 1}, "clock": {"displayValue": "9:00"}, "homeScore": h, "awayScore": 0, "start": {"down": 1, "distance": 10, "yardsToEndzone": 50},
                                 "statYardage": 3}) for i, (t, h) in enumerate(scores)]
    espn = W.track("nfl", plays([("Rush", 0), ("Passing Touchdown", 7), ("Kickoff", 7), ("Rush", 7)]))
    nflverse = W.track("nfl", plays([("Rush", 0), ("Passing Touchdown", 6), ("Kickoff", 7), ("Rush", 7)]))
    names = W.series_names("nfl")
    i, j = names.index("pts@all"), names.index("scoring@all")
    assert espn.x[:, i].tolist() == nflverse.x[:, i].tolist() == [0.0, 7.0, 0.0, 0.0] and espn.x[:, j].tolist() == nflverse.x[:, j].tolist()
    safety = W.track("nfl", plays([("Rush", 0), ("Safety", 2)]))
    assert safety.x[1, i] == 2.0


def test_the_live_game_equals_the_shards_for_the_kept_columns(v5):
    """WideGame is not wired into the engine yet; this pins its output to what training saw, every kept column at every sampled row."""
    for league, make in (("nba", nba_summary(0, 5)), ("nfl", nfl_summary(0, 13))):
        rows = V.build_rows(league, "g1", make, night="20231008", season=2023, source="x", license_line="x", plan="A")
        plays = V.flat_plays(league, V.wall_ordered(league, V.strip_published_wp(make)))
        walls = np.array([w for w, _ in plays])
        for r in rows.iloc[:: len(rows) // 12].itertuples(index=False):
            game = W.game_for(league)
            game.update(plays[: int(np.searchsorted(walls, r.feed_cut_t, "right"))])
            got = game.values()
            for name in KEPT:
                assert got[name] == pytest.approx(float(getattr(r, name)), rel=1e-6, abs=1e-6)


def test_each_league_reads_only_its_own_columns(tmp_path, monkeypatch):
    path = tmp_path / "k.json"
    W.write_kept(path, {"nba": {"selection_nights": NIGHTS[:5], "kept_columns": KEPT[:5]}, "nfl": {"selection_nights": NIGHTS[:5], "kept_columns": KEPT[5:]}}, complete=True, shard_id="t")
    monkeypatch.setenv(W.KEPT_ENV, str(path))
    V.select_features("v5")
    try:
        assert V.VALUE_FEATURES[-len(KEPT):] == tuple(KEPT) and G.GV_V5_OWN["nba"] == frozenset(KEPT[:5])
        nba = V.build_rows("nba", "g1", nba_summary(0, 5), night="20231008", season=2023, source="x", license_line="x", plan="A")
        assert (nba[KEPT[5:]].to_numpy() == 0).all() and (nba[KEPT[:5]].to_numpy() != 0).any()
        game = W.game_for("nba")
        game.update(V.flat_plays("nba", nba_summary(0, 5))[:100])
        assert all(game.values()[n] == 0.0 for n in KEPT[5:])
        out = _shards(tmp_path / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A", games=2)
        rep = LC.check_dir(out, None, False, _raw_dir(out))
        assert rep.fails == []
        f = sorted((out / "nfl").glob("*.parquet"))[0]
        df = pd.read_parquet(f)
        df[KEPT[0]] = 1.0                                     # a masked column that is not zero
        df.to_parquet(f, index=False)
        assert any("own list" in x for x in LC.check_dir(out, None, False, _raw_dir(out)).fails)
    finally:
        V.select_features("v2")


def test_pooled_is_refused_under_v5(tmp_path, v5, capsys):
    assert K.main(["--shards", str(tmp_path), "--out", str(tmp_path / "o"), "--raw", str(tmp_path), "--leagues", "nfl,nba", "--pooled-only", "--personal-nba", "--trials", "base"], trainer=object()) == K.EXIT_REFUSED
    assert "not validated" in capsys.readouterr().out


def test_paired_report_needs_the_interval_to_exclude_zero():
    from scripts import gamevalue_v5_report as R
    nights = [f"2023{i:04d}" for i in range(100, 120)]
    v4 = {n: {"got": 50.0, "total": 100.0, "decisions": 10} for n in nights}
    better = {n: {"got": 55.0, "total": 100.0, "decisions": 10} for n in nights}
    assert R.compare(v4, better)["replaces_v4"] is True and R.compare(v4, better)["delta_pct"] == pytest.approx(5.0)
    mixed = {n: {"got": 50.0 + (10.0 if i % 2 else -9.0), "total": 100.0, "decisions": 10} for i, n in enumerate(nights)}
    assert R.compare(v4, mixed)["replaces_v4"] is False
    lost = {n: dict(v, total=99.0) for n, v in better.items()}
    assert R.compare(v4, lost)["nights_paired"] == 0 and R.compare(v4, lost)["replaces_v4"] is False


def test_the_report_reads_the_trainers_manifest(tmp_path, capsys):
    from scripts import gamevalue_v5_report as R
    from tests.test_gamevalue import LEAK_OK, FakeTrainer
    shards = _shards(tmp_path / "s", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A")
    rep = K.train_league(shards, tmp_path / "m", "nfl", FakeTrainer(), 2, LEAK_OK, _raw_dir(shards), log=lambda *a, **k: None)
    nights = rep["models"]["v1"]["test_by_night"]
    assert nights and all(x["got"] <= x["total"] and x["decisions"] > 0 for x in nights.values())
    (tmp_path / "a.json").write_text(json.dumps({"leagues": {"nfl": rep}}))
    (tmp_path / "b.json").write_text(json.dumps({"leagues": {"nfl": rep}}))
    assert R.main([str(tmp_path / "a.json"), str(tmp_path / "b.json")]) == 0
    assert "v5 replaces v4: no" in capsys.readouterr().out


def test_capture_is_the_share_of_the_best_k_a_ranking_got():
    frame = pd.DataFrame({"night": ["n"] * 4, "game_id": ["a", "b", "c", "d"], "decision_t": [90.0] * 4, "value_next_3m": [4.0, 3.0, 2.0, 1.0]})
    assert S.capture_pct(frame, np.array([4.0, 3.0, 2.0, 1.0]), k=2) == 100.0
    assert S.capture_pct(frame, np.array([1.0, 2.0, 3.0, 4.0]), k=2) == pytest.approx(100.0 * 3.0 / 7.0)


def test_a_kept_list_picked_on_held_nights_is_refused_by_the_trainer(tmp_path, kept_file, monkeypatch):
    splits = {"nfl": {"train": NIGHTS[:5], "calib": NIGHTS[5:7], "test": NIGHTS[7:]}}
    (tmp_path / "splits.json").write_text(json.dumps(splits))
    K.check_kept_list(tmp_path, ["nfl"])                                    # fine: picked on train nights
    bad = tmp_path / "bad.json"
    W.write_kept(bad, {"nfl": {"selection_nights": NIGHTS[:6], "kept_columns": KEPT}}, complete=True, shard_id="t")
    monkeypatch.setenv(W.KEPT_ENV, str(bad))
    with pytest.raises(K.Refused, match="calibration or test"):
        K.check_kept_list(tmp_path, ["nfl"])
    empty = tmp_path / "empty.json"
    W.write_kept(empty, {"nfl": {"kept_columns": KEPT}}, complete=True, shard_id="t")
    monkeypatch.setenv(W.KEPT_ENV, str(empty))
    with pytest.raises(K.Refused, match="which nights"):
        K.check_kept_list(tmp_path, ["nfl"])


def test_the_trainer_exports_a_v5_model_that_loads_and_scores(tmp_path, v5):
    from tests.test_gamevalue import LEAK_OK, FakeTrainer
    rows = V.build_rows("nba", "g1", nba_summary(0, 5), night="20231008", season=2023, source="hoopr_espn_nba", license_line=G.PERSONAL_USE, plan="B")
    half = len(rows) // 2
    arrays, meta = K.fit_v1(rows[:half], rows[half:], "nba", FakeTrainer(), LEAK_OK)
    assert meta["features_version"] == "v5" and meta["features"] == list(G.GV_FEATURES) and len(meta["features"]) == len(G.GV_FEATURE_SETS["v4"]) + len(KEPT)
    path = G.model_path("nba", "v1", tmp_path)
    G.save(path, arrays, meta)
    back = G.load(path, "nba")
    b = back.predict_matrix(rows[list(G.GV_FEATURES)].to_numpy(float), max_rows=None)
    assert b.ok.all() and np.isfinite(b.value).all()


def test_promotion_is_not_changed_by_v5():
    assert G.promotion("nba", 90.0, [True] * 7, 80.0)[0] is True
    assert G.promotion("nba", 90.0, [True] * 6, 80.0)[0] is False and G.promotion("nba", 70.0, [True] * 7, 80.0)[0] is False
