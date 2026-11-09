"""GameValue: train, export, reload and predict agree; bad input fails closed; a failed leak check trains nothing.

Why: WP-26's exit check. Training never runs here: FakeTrainer stands in for LightGBM and walks its own trees, so the
export parity check compares two independent walkers.
"""
import datetime as dt
import json
import math
import random
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V
from scripts import holdset
from scripts import kaggle_gamevalue as K
from scripts import kaggle_gamevalue_holdset as H
from scripts import value_leak_check as LC

KICK = dt.datetime(2023, 10, 8, 17, 0, tzinfo=dt.timezone.utc)
NIGHTS = [(KICK + dt.timedelta(days=7 * i)).strftime("%Y%m%d") for i in range(10)]
GOOD = {"nfl": "nflverse_pbp"}
LEAK_OK = {"passed": True, "shards": 1, "rows": 1, "prefix_rows": 1}


def _iso(sec):
    return (KICK + dt.timedelta(seconds=float(sec))).strftime("%Y-%m-%dT%H:%M:%SZ")


def nfl_summary(start_s, td_every):
    plays, home = [], 0
    n = 180
    for i in range(n):
        td = i % td_every == td_every - 1
        home += 7 if td else 0
        plays.append({"id": str(i), "type": {"text": "Passing Touchdown" if td else "Rush"}, "text": "",
                      "period": {"number": 1 + i * 4 // n}, "clock": {"displayValue": f"{14 - i % 15}:00"},
                      "wallclock": _iso(start_s + i * 60), "scoringPlay": td, "homeScore": home, "awayScore": 3,
                      "statYardage": 2, "start": {"down": 1 + i % 3, "distance": 10, "yardsToEndzone": 60 - i % 50},
                      "end": {"yardsToEndzone": 58 - i % 50}})
    return {"drives": {"previous": [{"plays": plays[i:i + 15]} for i in range(0, n, 15)]}}


def nba_summary(start_s, every):
    plays, home = [], 0
    for i in range(240):
        made = i % every == 0
        home += 2 if made else 0
        plays.append({"id": str(i), "type": {"text": "Jump Shot"}, "text": "", "period": {"number": 1 + i // 60},
                      "clock": {"displayValue": f"{11 - i % 12}:00"}, "wallclock": _iso(start_s + i * 40), "scoringPlay": made,
                      "shootingPlay": True, "scoreValue": 2 if made else 0, "homeScore": home, "awayScore": home - 1 + i % 5})
    return {"plays": plays}


def _write(out, league, night, i, summary, source, line, plan):
    gid = f"{league}{night}{i}"
    rows = V.build_rows(league, gid, summary, night=night, season=2023, source=source, license_line=line, plan=plan,
                        game_key=f"{league}:{night}:T{i}@H{i}")
    n, bad = V.prefix_check(league, gid, summary, rows)
    return rows, n, bad


def _shards(out, league, make, source, line, plan, games=6):
    for w, night in enumerate(NIGHTS):
        start = (dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - KICK).total_seconds()
        parts, checked, failed = [], 0, 0
        for i in range(games):
            rows, n, bad = _write(out, league, night, i, make(start + i * 60, 5 + (w + i) % 9 * 4), source, line, plan)
            parts.append(rows)
            checked += n
            failed += len(bad)
        V.write_shard(out, league, f"w{w}", pd.concat(parts, ignore_index=True),
                      {"source": source, "license": line, "plan": plan, "prefix_checked": checked, "prefix_failed": failed,
                       "prefix_examples": []})
    V.finalize(out)
    return out


@pytest.fixture(scope="module")
def shards(tmp_path_factory):
    return _shards(tmp_path_factory.mktemp("s") / "shards", "nfl", nfl_summary, "nflverse_pbp", "CC-BY 4.0", "A")


class FakeBooster:
    """LightGBM's dump layout (thresholds, default_left, leaf_value), walked here, independently of models_rt.forest."""

    def __init__(self, trees, link):
        self.trees, self.link = trees, link

    def dump_model(self):
        return {"tree_info": [{"tree_index": i, "tree_structure": t} for i, t in enumerate(self.trees)]}

    def _walk(self, node, row):
        while "leaf_value" not in node:
            node = node["left_child"] if float(row[node["split_feature"]]) <= node["threshold"] else node["right_child"]
        return node["leaf_value"]

    def predict(self, X, raw_score=False):
        raw = np.array([sum(self._walk(t, r) for t in self.trees) for r in np.asarray(X, np.float32).astype(float)])
        if raw_score:
            return raw
        return np.exp(raw) if self.link == "log" else 1.0 / (1.0 + np.exp(-raw))


class FakeTrainer:
    """Stumps on the residual. Round 0 carries the init score in its leaves, as boost_from_average does."""
    rounds = 6

    def __init__(self):
        self.calls = []

    def fit(self, X, y, objective):
        self.calls.append((objective, len(X)))
        link = "log" if objective == "tweedie" else "logit"
        mean = float(np.clip(np.mean(y), 1e-3, 1 - 1e-3 if link == "logit" else None))
        init = math.log(mean) if link == "log" else math.log(mean / (1 - mean))
        leaf = lambda v: {"leaf_value": v}   # noqa: E731
        trees, f = [leaf(init)], np.full(len(y), init)
        for r in range(self.rounds):
            j = [0, 1, 5, 11, 21, 6][r]
            thr = float(np.median(X[:, j]))
            left = X[:, j] <= thr
            pred = np.exp(f) if link == "log" else 1.0 / (1.0 + np.exp(-f))
            resid = (y - pred) / np.maximum(pred, 1e-3) if link == "log" else y - pred
            lv = 0.1 * float(resid[left].mean()) if left.any() else 0.0
            rv = 0.1 * float(resid[~left].mean()) if (~left).any() else 0.0
            trees.append({"split_feature": j, "threshold": thr, "decision_type": "<=", "default_left": True, "missing_type": "None",
                          "left_child": leaf(lv), "right_child": leaf(rv)})
            f += np.where(left, lv, rv)
        return FakeBooster(trees, link)


@pytest.fixture(scope="module")
def run(shards, tmp_path_factory):
    out = tmp_path_factory.mktemp("m") / "models"
    trainer = FakeTrainer()
    assert K.main(["--shards", str(shards), "--out", str(out), "--folds", "2"], trainer=trainer) == 0
    return out, trainer, json.loads((out / "manifest.json").read_text())


def good_row(**over):
    row = {f: 0.0 for f in G.GV_FEATURES}
    row.update(period=4.0, clock_left=200.0, game_left=200.0, abs_margin=3.0, total_points=30.0, since_head_s=20.0, since_start_s=9000.0,
               since_score_s=100.0, plays_5m=10.0, down=2.0, distance=7.0, yards_to_goal=12.0)
    row.update(over)
    return row


def _model(run, version="v1"):
    return G.load(G.model_path("nfl", version, run[0]), "nfl")


# --- exit check ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["v0", "v1"])
def test_train_export_reload_gives_the_same_numbers(shards, tmp_path, version):
    df, _ = K.load_rows(shards, "nfl")
    tr, ca = df[df.night.isin(NIGHTS[:6])].reset_index(drop=True), df[df.night.isin(NIGHTS[6:8])].reset_index(drop=True)
    arrays, meta = K.fit_v0(tr, "nfl", LEAK_OK) if version == "v0" else K.fit_v1(tr, ca, "nfl", FakeTrainer(), LEAK_OK)
    trained = G.GameValueModel(arrays, meta, "nfl")
    path = G.model_path("nfl", version, tmp_path)
    G.save(path, arrays, meta)
    X = df[list(G.GV_FEATURES)].to_numpy(float)[:500]
    a, b = trained.predict_matrix(X, max_rows=None), G.load(path, "nfl").predict_matrix(X, max_rows=None)
    assert a.ok.all() and np.array_equal(a.raw, b.raw) and np.array_equal(a.p, b.p) and np.array_equal(a.value, b.value)
    assert ((a.value >= 0) & (a.value < 100)).all() and ((a.p >= 0) & (a.p <= 1)).all()


def test_exported_trees_match_the_booster_on_real_rows(shards):
    df, _ = K.load_rows(shards, "nfl")
    tr, ca = df[df.night.isin(NIGHTS[:6])], df[df.night.isin(NIGHTS[6:8])]
    trainer = FakeTrainer()
    arrays, meta = K.fit_v1(tr.reset_index(drop=True), ca.reset_index(drop=True), "nfl", trainer, LEAK_OK)
    model = G.GameValueModel(arrays, meta, "nfl")
    X = K._X(ca)[:200]
    booster = trainer.fit(K._X(tr), tr.value_next_3m.to_numpy(float), "tweedie")
    got = model.predict_matrix(X.astype(float), max_rows=None).raw
    assert np.max(np.abs(np.log(got) - booster.predict(X, raw_score=True))) < 1e-4


def test_export_refused_when_the_numpy_walk_disagrees(shards):
    class Lying(FakeTrainer):
        def fit(self, X, y, objective):
            b = super().fit(X, y, objective)
            real = b.predict
            b.predict = lambda X, raw_score=False: real(X, raw_score) + 1.0
            return b
    df, _ = K.load_rows(shards, "nfl")
    tr, ca = df[df.night.isin(NIGHTS[:6])].reset_index(drop=True), df[df.night.isin(NIGHTS[6:8])].reset_index(drop=True)
    with pytest.raises(K.Refused, match="differs from the booster"):
        K.fit_v1(tr, ca, "nfl", Lying(), LEAK_OK)


def test_export_refused_when_the_link_is_not_the_runtimes(shards):
    class OtherLink(FakeTrainer):
        def fit(self, X, y, objective):
            b = super().fit(X, y, objective)
            b.link = "logit" if objective == "tweedie" else "log"
            return b
    df, _ = K.load_rows(shards, "nfl")
    tr, ca = df[df.night.isin(NIGHTS[:6])].reset_index(drop=True), df[df.night.isin(NIGHTS[6:8])].reset_index(drop=True)
    with pytest.raises(K.Refused, match="differs from the booster"):
        K.fit_v1(tr, ca, "nfl", OtherLink(), LEAK_OK)


def test_manifest_reports_mean_and_worst_fold_and_never_promotes(run):
    man = run[2]["leagues"]["nfl"]
    assert len(man["folds"]) == 2 and man["trials"] == 1
    for version in ("v0", "v1"):
        cv = man["models"][version]["cv"]
        assert len(cv["folds"]) == 2
        assert set(cv["k4_pct"]) == {"mean", "worst"} and cv["k4_pct"]["worst"] <= cv["k4_pct"]["mean"]
        assert man["models"][version]["test"]["k4_pct"] is not None
    assert man["promotion"]["may_promote"] is False and run[2]["status"] == "shadow_only"


def test_cleared_source_needs_no_personal_tag(run):
    assert run[2]["license_tag"] == "" and _model(run).meta["license_tag"] == ""


def test_forward_folds_are_whole_nights_and_only_look_back():
    nights = [f"2023{i:04d}" for i in range(101, 131)]
    folds = K.forward_folds(nights, 5)
    assert len(folds) == 5
    seen = []
    for fit, val in folds:
        assert max(fit) < min(val) and not set(fit) & set(val)
        seen += val
    assert seen == sorted(seen) and len(seen) == len(set(seen))


def test_v0_is_holdset_state_table():
    rng = random.Random(3)
    keys = [(rng.randint(0, 5), rng.randint(0, 3), rng.randint(0, 3), rng.randint(0, 3)) for _ in range(400)]
    y = [rng.random() * 8 for _ in keys]
    pred, _ = holdset.fit([[(None, k, None, v, None) for k, v in zip(keys, y)]])
    table = K.fit_table(np.array(keys), np.array(y))
    for k in [*keys[:50], (5, 3, 3, 3), (0, 0, 0, 0)]:
        assert table[k] == pytest.approx(pred(k), rel=1e-9)


def test_table_key_matches_holdset_key():
    cases = [(1, 800.0, 2, 15.0), (4, 100.0, 9, 45.0), (5, 250.0, 20, None), (2, None, 0, 30.0)]
    for per, c, m, y in cases:
        row = good_row(period=float(per), clock_left=-1.0 if c is None else c, abs_margin=float(m), yards_to_goal=-1.0 if y is None else y)
        X = np.array([[row[f] for f in G.GV_FEATURES]])
        assert tuple(G.table_key(X, "nfl")[0]) == holdset.key("nfl", (0, per, c, m, y), 0, 0, 0)


def test_capture_matches_holdset():
    rng = random.Random(5)
    decs = [[("g", (0,), 0, rng.random() * 9, 0, rng.random()) for _ in range(rng.randint(3, 8))] for _ in range(40)]
    g, t, _, _ = holdset.capture(decs, lambda x: x[5], 4, random.Random(0))
    slate = np.array([i for i, d in enumerate(decs) for _ in d])
    y = np.array([x[3] for d in decs for x in d])
    pred = np.array([x[5] for d in decs for x in d])
    assert K.capture(slate, pred, y, 4) == pytest.approx(g / t * 100, abs=0.01)


# --- the 0-100 scale -------------------------------------------------------------------------------------------


def test_scale_is_fixed_bounded_and_keeps_the_order():
    raw = np.array([0.0, 0.01, 1.0, 5.0, 10.0, 40.0, 500.0])
    v = G.to_scale(raw)
    assert v[0] == 0 and (np.diff(v[:-1]) > 0).all() and v[-1] <= 100
    assert v[3] == pytest.approx(63.21, abs=0.01) and v[4] == pytest.approx(86.47, abs=0.01)


# --- keep the two feature lists honest, keep the live process light ----------------------------------------------


def test_feature_list_matches_shards_and_leak_check():
    assert G.GV_FEATURES == V.VALUE_FEATURES == LC.FEATURES and G.GV_FEATURES_VERSION == V.FEATURES_VERSION


def test_bounds_cover_every_feature_and_every_shard_row(shards):
    assert set(G.GV_BOUNDS) == set(G.GV_FEATURES)
    _, info = K.load_rows(shards, "nfl")
    assert info["out_of_bounds_rows"] == 0


def test_runtime_imports_neither_pandas_nor_lightgbm():
    code = "import sys, redzone.noggin.gamevalue; print([m for m in ('pandas', 'lightgbm', 'torch') if m in sys.modules])"
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "[]", got.stderr


# --- leak check and other refusals ---------------------------------------------------------------------------------


def test_failed_leak_check_trains_nothing(shards, tmp_path):
    import shutil
    bad = tmp_path / "bad"
    shutil.copytree(shards, bad)
    rows = pd.read_parquet(bad / "nfl" / "w0.parquet")
    rows["feature_t_max"] = rows["feature_t_max"] + 1e6
    side = json.loads((bad / "nfl" / "w0.json").read_text())
    V.write_shard(bad, "nfl", "w0", rows, side)
    assert LC.check_dir(bad).fails
    out, trainer = tmp_path / "out", FakeTrainer()
    assert K.main(["--shards", str(bad), "--out", str(out)], trainer=trainer) == K.EXIT_REFUSED
    assert trainer.calls == [] and not out.exists()


def test_mac_refuses_the_real_trainer(shards, tmp_path, monkeypatch):
    monkeypatch.setattr(K.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "lightgbm", None)       # an import attempt would raise
    assert K.main(["--shards", str(shards), "--out", str(tmp_path / "o")]) == K.EXIT_MAC
    assert not (tmp_path / "o").exists()


def test_nba_needs_the_personal_flag_and_other_leagues_have_no_model(shards, tmp_path):
    trainer = FakeTrainer()
    for lg in ("nba", "nhl", ""):
        assert K.main(["--shards", str(shards), "--out", str(tmp_path / "o"), "--leagues", lg], trainer=trainer) == K.EXIT_REFUSED
    assert trainer.calls == []


def test_nba_with_flag_tags_model_and_manifest_personal_use(tmp_path):
    line = f"{G.PERSONAL_USE}; hoopR ESPN NBA"
    root = _shards(tmp_path / "s", "nba", nba_summary, "hoopr_espn_nba", line, "B", games=3)
    out = tmp_path / "out"
    assert K.main(["--shards", str(root), "--out", str(out), "--leagues", "nba", "--personal-nba", "--folds", "2"], trainer=FakeTrainer()) == 0
    man = json.loads((out / "manifest.json").read_text())
    assert man["license_tag"] == G.PERSONAL_USE and man["leagues"]["nba"]["license_tag"] == G.PERSONAL_USE
    for version in ("v0", "v1"):
        assert man["leagues"]["nba"]["models"][version]["license_tag"] == G.PERSONAL_USE
        assert G.load(G.model_path("nba", version, out), "nba").meta["license_tag"] == G.PERSONAL_USE


# --- hostile input ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("over", [
    {"period": float("nan")}, {"clock_left": float("inf")}, {"abs_margin": float("-inf")}, {"total_points": 1e39}, {"game_left": -5.0},
    {"since_head_s": 5000.0}, {"down": "2"}, {"distance": None}, {"period": 10 ** 400}, {"yards_to_goal": [1]}, {"is_ot": 2.0},
])
def test_a_bad_feature_gets_no_number_and_leaves_the_others_alone(run, over):
    model = _model(run)
    clean = model.estimate({"good": good_row()})["good"]
    got = model.estimate({"bad": good_row(**over), "good": good_row()})
    assert got["bad"] is None and got["good"] == clean and clean is not None


def test_missing_and_extra_features_fail_closed(run):
    model = _model(run)
    short = good_row()
    short.pop("down")
    assert model.estimate({"a": short, "b": good_row(surprise=1.0), "c": [1, 2, 3], "d": None}) == {"a": None, "b": None, "c": None, "d": None}


def test_a_bad_batch_fails_closed(run):
    model = _model(run)
    assert model.estimate(["not", "a", "mapping"]) == {}
    assert model.estimate({str(i): good_row() for i in range(G.GV_MAX_ROWS + 1)}) == {}
    good = np.array([[good_row()[f] for f in G.GV_FEATURES]])
    for bad in (good[:, :-1], good[0], np.array([["x"] * 23]), np.array([[object()] * 23], dtype=object)):
        with pytest.raises(G.BadFeatures):
            model.predict_matrix(bad)
    with pytest.raises(G.BadFeatures):
        model.predict_matrix(np.repeat(good, G.GV_MAX_ROWS + 1, axis=0))


def test_a_nan_row_in_a_matrix_is_marked_not_guessed(run):
    good = [good_row()[f] for f in G.GV_FEATURES]
    bad = list(good)
    bad[3] = float("nan")
    b = _model(run).predict_matrix(np.array([good, bad]))
    assert list(b.ok) == [True, False] and math.isnan(b.value[1]) and math.isnan(b.p[1]) and not math.isnan(b.value[0])


def _file(run, version="v1"):
    path = G.model_path("nfl", version, run[0])
    with np.load(path, allow_pickle=False) as z:
        return {k: z[k] for k in z.files if k != "meta"}, json.loads(bytes(z["meta"]).decode())


def _resave(path, arrays, meta, stamp=True):
    meta = {**meta, "sha256": G.content_sha(arrays, meta) if stamp else meta.get("sha256")}
    with open(path, "wb") as f:
        np.savez_compressed(f, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), **arrays)
    return path


@pytest.mark.parametrize("edit,match", [
    (lambda a, m: m.update(status="promoted"), "status"), (lambda a, m: m.update(league="nba"), "is for"),
    (lambda a, m: m.update(features=m["features"][::-1]), "feature list"), (lambda a, m: m.update(leak_check={"passed": False}), "leak check"),
    (lambda a, m: m.update(scale_points=9.0), "scale"), (lambda a, m: m.update(sources=[]), "sources"),
    (lambda a, m: m.update(license_lines=[f"{G.PERSONAL_USE}; x"], license_tag=""), "personal-use"),
    (lambda a, m: m.update(sources=["archive"], license_tag=""), "personal-use"),
    (lambda a, m: a.update(value_left=a["value_left"] + 10 ** 6), "outside the model"),
    (lambda a, m: a.update(value_feat=a["value_feat"] + 100), "outside the model"),
    (lambda a, m: a.update(p1_value=a["p1_value"] * np.nan), "non-finite"), (lambda a, m: a.pop("p1_thr"), "missing array"),
    (lambda a, m: a.update(p1_cal_y=a["p1_cal_y"][::-1] * 3), "calibration"),
])
def test_bad_model_files_do_not_load(run, tmp_path, edit, match):
    arrays, meta = _file(run)
    edit(arrays, meta)
    with pytest.raises(G.ModelRejected, match=match):
        G.load(_resave(tmp_path / "m.npz", arrays, meta), "nfl")


def test_altered_or_corrupt_files_do_not_load(run, tmp_path):
    arrays, meta = _file(run)
    arrays["value_value"] = arrays["value_value"] + 1
    with pytest.raises(G.ModelRejected, match="hash"):
        G.load(_resave(tmp_path / "a.npz", arrays, meta, stamp=False), "nfl")
    junk = tmp_path / "j.npz"
    junk.write_bytes(b"not a zip")
    with pytest.raises(G.ModelRejected, match="unreadable"):
        G.load(junk, "nfl")
    with pytest.raises(G.ModelRejected, match="unreadable"):
        G.load(tmp_path / "missing.npz", "nfl")
    real = G.model_path("nfl", "v1", run[0]).read_bytes()
    cut = tmp_path / "cut.npz"
    cut.write_bytes(real[: len(real) // 2])
    with pytest.raises(G.ModelRejected, match="unreadable"):
        G.load(cut, "nfl")
    pickled = tmp_path / "p.npz"
    with zipfile.ZipFile(pickled, "w") as z:
        import io
        buf = io.BytesIO()
        np.save(buf, np.array([{"evil": 1}], dtype=object), allow_pickle=True)
        z.writestr("meta.npy", buf.getvalue())
    with pytest.raises(G.ModelRejected, match="unreadable"):
        G.load(pickled, "nfl")


def test_wrong_league_request_is_refused(run):
    with pytest.raises(G.ModelRejected, match="asked for nba"):
        G.load(G.model_path("nfl", "v1", run[0]), "nba")


# --- promotion -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("league,k4,nights,ok", [
    ("nfl", 72.5, [True] * 7, True), ("nfl", 72.4, [True] * 7, False), ("nfl", 90.0, [True] * 6, False), ("nfl", 90.0, [True] * 6 + [False], False),
    ("nfl", None, [True] * 7, False), ("nfl", float("nan"), [True] * 7, False), ("nba", 79.9, [True] * 7, True), ("nba", 79.8, [True] * 7, False),
    ("nhl", 99.0, [True] * 7, False), ("nfl", 90.0, [False] + [True] * 7, True), ("nfl", 90.0, [1] * 7, False),
])
def test_promotion_needs_the_real_planner_beaten_and_seven_of_seven(league, k4, nights, ok):
    assert G.promotion(league, k4, nights)[0] is ok


def test_holdset_ranker_scores_each_decision_and_feeds_the_bar(run, monkeypatch):
    gid = "g1"
    summary = nfl_summary(0.0, 9)
    rows = holdset.game_row("nfl", gid, summary)
    night = [rows]
    monkeypatch.setattr(H.holdset, "games", lambda league, only=None: [("n", gid, summary), ("n", "other", summary)])
    ranks = H.ModelRanker(_model(run))("nfl", "n", night)
    assert set(ranks) == set(holdset.decision_times(night))
    assert all(set(v) == {gid} for v in ranks.values()) and all(0 <= s < 100 for v in ranks.values() for s in v.values())
    report = H.verdict({"nfl": {"K4": {"planner_pct": 73.0}, "nights_test": 23}})["nfl"]
    assert report["beats_planner"] is True and report["promotion"][0] is False    # no shadow nights yet


# --- latency ---------------------------------------------------------------------------------------------------


def big_model():
    """150 full depth-6 trees per head, the export's largest shape, with thresholds that make every level branch."""
    rng = np.random.default_rng(11)
    F, nodes = len(G.GV_FEATURES), 127
    arrays = {}
    for head in ("value", "p1"):
        feat, left, right, thr, value, roots = [], [], [], [], [], []
        for t in range(150):
            base = len(feat)
            roots.append(base)
            for i in range(nodes):
                inner = 2 * i + 2 < nodes
                feat.append(int(rng.integers(0, F)) if inner else 0)
                left.append(base + 2 * i + 1 if inner else base + i)
                right.append(base + 2 * i + 2 if inner else base + i)
                thr.append(float(rng.uniform(0, 300)) if inner else 0.0)
                value.append(0.0 if inner else float(rng.normal(0, 0.01)))
        n = len(feat)
        arrays.update({f"{head}_feat": np.array(feat, np.int16), f"{head}_left": np.array(left, np.int32), f"{head}_right": np.array(right, np.int32),
                       f"{head}_thr": np.array(thr, float), f"{head}_dleft": np.zeros(n, bool), f"{head}_miss": np.zeros(n, np.int8),
                       f"{head}_value": np.array(value, np.float32), f"{head}_roots": np.array(roots, np.int32)})
    arrays.update(p1_cal_x=np.array([0.0, 1.0]), p1_cal_y=np.array([0.0, 1.0]))
    meta = {"schema": G.GV_SCHEMA, "kind": "trees", "version": "v1", "league": "nfl", "features": list(G.GV_FEATURES), "features_version": G.GV_FEATURES_VERSION,
            "status": G.GV_STATUS, "scale_points": G.GV_SCALE_POINTS, "leak_check": {"passed": True}, "sources": ["nflverse_pbp"],
            "license_lines": ["CC-BY"], "license_tag": "", "value_depth": 6, "p1_depth": 6}
    return arrays, meta


def test_latency_32_games_under_target():
    model = G.GameValueModel(*big_model(), "nfl")
    games = {str(i): good_row(total_points=float(i), plays_5m=float(i % 20)) for i in range(32)}
    took = []
    for _ in range(40):
        t0 = time.perf_counter()
        got = model.estimate(games)
        took.append((time.perf_counter() - t0) * 1000)
    assert all(v is not None for v in got.values())
    print(f"\ngamevalue latency 32 games, 2 heads, 150 depth-6 trees: best {min(took):.2f} ms, median {np.median(took):.2f} ms")
    assert min(took) < 5.0      # the best of 40 measures the code, not whatever else the Mac is doing
