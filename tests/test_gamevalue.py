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
from scripts import holdset_public as HP
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


def _raw_nfl(gid, summary):
    """The nflverse row each fixture play would have come from, so shards can be built through the real adapter."""
    out = []
    for drive, d in enumerate(summary["drives"]["previous"]):
        for p in d["plays"]:
            td = "Touchdown" in p["type"]["text"]
            start, end = p["start"]["yardsToEndzone"], p["end"]["yardsToEndzone"]
            out.append({"game_id": gid, "play_id": int(p["id"]) + 1, "time_of_day": p["wallclock"], "time": p["clock"]["displayValue"],
                        "qtr": p["period"]["number"], "play_type": "pass" if td else "run", "desc": "x", "timeout": 0, "touchdown": float(td),
                        "field_goal_result": None, "extra_point_result": None, "interception": 0, "fumble_lost": 0, "sack": 0, "complete_pass": 1,
                        "safety": 0, "sp": float(td), "total_home_score": float(p["homeScore"]), "total_away_score": float(p["awayScore"]),
                        "season_type": "REG", "two_point_attempt": 0, "drive": drive, "down": float(p["start"]["down"]),
                        "ydstogo": float(p["start"]["distance"]), "yardline_100": float(start), "yards_gained": float(start - end)})
    return pd.DataFrame(out)


def _raw_nba(gid, summary):
    return pd.DataFrame([{"id": str(gid) + p["id"], "sequence_number": i, "type_text": p["type"]["text"], "text": "x", "period_number": p["period"]["number"],
                          "clock_display_value": p["clock"]["displayValue"], "wallclock": p["wallclock"], "scoring_play": p["scoringPlay"],
                          "score_value": p["scoreValue"], "home_score": p["homeScore"], "away_score": p["awayScore"], "game_id": gid,
                          "season": 2024, "season_type": 2, "shooting_play": True} for i, p in enumerate(summary["plays"])])


def _write(out, league, night, i, summary, source, line, plan):
    gid = f"{league}{night}{i}"
    frame = (_raw_nfl if league == "nfl" else _raw_nba)(gid, summary)
    summary = (HP.nfl_summary if league == "nfl" else HP.hoops_summary)(frame)
    rows = V.build_rows(league, gid, summary, night=night, season=2023, source=source, license_line=line, plan=plan,
                        game_key=f"{league}:{night}:T{i}@H{i}")
    n, bad = V.prefix_check(league, gid, summary, rows)
    return rows, n, bad, frame


def _raw_dir(shards):
    return shards.parent / "raw"


def _shards(out, league, make, source, line, plan, games=6):
    for w, night in enumerate(NIGHTS):
        start = (dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - KICK).total_seconds()
        parts, checked, failed, frames = [], 0, 0, []
        for i in range(games):
            rows, n, bad, frame = _write(out, league, night, i, make(start + i * 60, 5 + (w + i) % 9 * 4), source, line, plan)
            parts.append(rows)
            frames.append(frame)
            checked += n
            failed += len(bad)
        V.write_shard(out, league, f"w{w}", pd.concat(parts, ignore_index=True),
                      {"source": source, "license": line, "plan": plan, "prefix_checked": checked, "prefix_failed": failed,
                       "prefix_examples": []})
        (_raw_dir(out) / league).mkdir(parents=True, exist_ok=True)
        pd.concat(frames, ignore_index=True).to_parquet(_raw_dir(out) / league / f"pbp_w{w}.parquet", index=False)
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
    assert K.main(["--shards", str(shards), "--out", str(out), "--raw", str(_raw_dir(shards)), "--folds", "2"], trainer=trainer) == 0
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


@pytest.mark.parametrize("version", ["v0", "v1"])
def test_manifest_shows_the_model_beside_every_baseline_on_the_same_nights(run, version):
    test = run[2]["leagues"]["nfl"]["models"][version]["test"]
    for k in ("K2", "K4"):
        cell = test["shared_clock"][k]
        assert set(cell) == {"decisions", "model_pct", "state_table_pct", "closest_score_pct", "random_pct", "hindsight_pct"}
        assert cell["random_pct"] <= cell["hindsight_pct"] and cell["model_pct"] <= cell["hindsight_pct"]
    assert test["k4_pct"] == test["shared_clock"]["K4"]["model_pct"]


def test_trainer_and_holdset_give_the_same_number_on_the_same_nights(run, shards):
    """The exit check: one fixture, the trainer's report and holdset.report, every column."""
    raw, league = _raw_dir(shards), "nfl"
    rows, games = HP.public_rows(league, HP.read_public(league, sorted((raw / league).glob("*.parquet"))), keep_games=set(NIGHTS))
    nights = sorted(rows)
    cut = int(len(nights) * 0.6)
    model = _model(run)
    ours = K.SharedClock(league, rows, games).report(model, nights[:cut], nights[cut:])
    ranker = H.ModelRanker(model, games=lambda _l, n: games.get(n, []))
    theirs = holdset.report(league, rows, ranker)
    for k in ("K2", "K4"):
        assert ours[k]["model_pct"] == theirs[k]["planner_pct"], k
        for col in ("state_table_pct", "closest_score_pct", "random_pct", "hindsight_pct"):
            assert ours[k][col] == theirs[k][col], (k, col)


def test_shared_clock_never_reads_a_night_on_or_after_the_eval_cut(run):
    frame = _raw_nfl("2025_01_AAA_BBB", nfl_summary((dt.datetime(2025, 9, 7, 17, tzinfo=dt.timezone.utc) - KICK).total_seconds(), 9))
    rows, games = HP.public_rows("nfl", frame, keep_games={"20250907"})
    assert not rows and not games


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


def _noise_game(rng, t0, gid):
    """Every play outcome is iid noise; the only structure is the breaks between plays (the review's reproduction)."""
    iso = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    t, h, a, drives, cur = t0, 0, 0, [], []
    for q in range(1, 5):
        clock = 900.0
        while clock > 0:
            r = rng.random()
            ty, sc, gain = "Rush", False, int(rng.integers(0, 9))
            if r < .04:
                ty, sc, gain = "Rushing Touchdown", True, 5
                h, a = (h + 7, a) if rng.random() < .5 else (h, a + 7)
            elif r < .07:
                ty, gain = "Interception Return", 0
            elif r < .21:
                ty, gain = "Pass Reception", 15
            yl = float(rng.integers(1, 99))
            cur.append({"id": f"{gid}:{len(cur)}:{t}", "type": {"text": ty}, "text": ty.lower(), "period": {"number": q},
                        "clock": {"displayValue": f"{int(clock) // 60}:{int(clock) % 60:02d}"}, "wallclock": iso(t), "scoringPlay": sc,
                        "homeScore": h, "awayScore": a, "statYardage": gain, "start": {"down": int(rng.integers(1, 5)), "yardsToEndzone": yl, "distance": 10},
                        "end": {"yardsToEndzone": max(0.0, yl - gain)}})
            gap = float(np.clip(rng.lognormal(3.6, .25), 15, 70))
            if sc or ty == "Interception Return" or rng.random() < .14:
                drives.append({"plays": cur})
                cur = []
                gap += float(rng.uniform(110, 190)) if rng.random() < .65 else 30.0
            t += gap
            clock -= min(gap, 40.0)
        if q == 2:
            t += 1000.0
    if cur:
        drives.append({"plays": cur})
    return {"drives": {"previous": drives}}


def test_noise_game_ranked_on_the_feed_gap_scores_near_random_on_the_trainer_metric():
    """The exit check. Before the fix a noise game ranked on since_head_s alone scored 71.3. Halftime is real in this
    game (a long silence does mean little is coming), so the gap may earn what the state table earns and no more."""
    rng = np.random.default_rng(3)
    bins = np.array([0, 15, 30, 45, 60, 75, 90, 110, 130, 150, 180, 240, 400, 1e9])
    nights, rows = {}, []
    for n in range(30):
        t0 = 1696784400.0 + 7 * 86400 * n
        night = pd.Timestamp(t0, unit="s").strftime("%Y%m%d")
        nights[night] = {}
        for g in range(6):
            gid = f"{n:03d}{g}"
            nights[night][gid] = _noise_game(rng, t0 + float(rng.uniform(0, 240)), gid)
            rows.append(V.build_rows("nfl", gid, nights[night][gid], night=night, season=2023, source="nflverse_pbp", license_line="x", plan="A"))
    df = pd.concat(rows, ignore_index=True)
    df = df[df.label_full_window == 1]
    names = sorted(nights)
    fit_nights, test_nights = names[:18], names[18:]
    tr = df[df.night.isin(fit_nights)]
    binned = np.digitize(tr.since_head_s.to_numpy(float), bins)
    table = np.full(len(bins) + 2, float(tr.value_next_3m.mean()))
    for b in np.unique(binned):
        table[b] = tr.value_next_3m.to_numpy(float)[binned == b].mean()
    held = {n: [r for r in (holdset.game_row("nfl", g, s) for g, s in nights[n].items()) if r] for n in names}

    def gap_only(league, night, night_rows):
        out = {}
        for t in holdset.decision_times(night_rows):
            out[t] = {}
            for g in night_rows:
                i = np.searchsorted([x[0] for x in g[3]], t) - 1
                if i >= 0:
                    out[t][g[0]] = float(table[np.digitize(t - g[3][i][0], bins)])
        return out
    got = HP.fold_report("nfl", held, fit_nights, test_nights, gap_only)
    for k in ("K2", "K4"):
        cell = got[k]
        assert cell["planner_pct"] - cell["random_pct"] < 8.0, cell
        assert cell["planner_pct"] < cell["hindsight_pct"] - 15.0, cell


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
    assert set(G.GV_BOUNDS) == set(G.GV_FEATURE_SETS["v4"]) and set(G.GV_FEATURES) <= set(G.GV_BOUNDS)
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


def test_the_trainer_refuses_when_the_raw_audit_did_not_run_or_fails(shards, tmp_path):
    out, trainer = tmp_path / "out", FakeTrainer()
    assert K.main(["--shards", str(shards), "--out", str(out), "--raw", str(tmp_path / "empty")], trainer=trainer) == K.EXIT_REFUSED
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
    root = _shards(tmp_path / "s", "nba", nba_summary, "hoopr_espn_nba", line, "B", games=6)
    out = tmp_path / "out"
    assert K.main(["--shards", str(root), "--out", str(out), "--raw", str(_raw_dir(root)), "--leagues", "nba", "--personal-nba", "--folds", "2"], trainer=FakeTrainer()) == 0
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
    ("nfl", None, [True] * 7, False), ("nfl", float("nan"), [True] * 7, False), ("nba", 79.9, [True] * 7, True), ("nba", 72.4, [True] * 7, False),
    ("nhl", 99.0, [True] * 7, False), ("nfl", 90.0, [False] + [True] * 7, True), ("nfl", 90.0, [1] * 7, False),
])
def test_promotion_needs_the_real_planner_beaten_and_seven_of_seven(league, k4, nights, ok):
    assert G.promotion(league, k4, nights, 72.4)[0] is ok


@pytest.mark.parametrize("bar", [None, float("nan"), True, "72.4"])
def test_promotion_refuses_without_a_bar_from_the_same_nights(bar):
    may, why = G.promotion("nfl", 99.0, [True] * 7, bar)
    assert may is False and "same nights" in why
    assert G.promotion("nfl", 99.0, [True] * 7)[0] is False


def test_holdset_ranker_scores_each_decision_and_feeds_the_bar(run, monkeypatch):
    gid = "g1"
    summary = nfl_summary(0.0, 9)
    rows = holdset.game_row("nfl", gid, summary)
    night = [rows]
    monkeypatch.setattr(H.holdset, "games", lambda league, only=None: [("n", gid, summary), ("n", "other", summary)])
    ranks = H.ModelRanker(_model(run))("nfl", "n", night)
    assert set(ranks) == set(holdset.decision_times(night))
    assert all(set(v) == {gid} for v in ranks.values()) and all(0 <= s < 100 for v in ranks.values() for s in v.values())
    model = {"nfl": {"K4": {"planner_pct": 73.0}, "nights_test": 23, "decisions_test": 900}}
    planner = {"nfl": {"K4": {"planner_pct": 70.0}, "nights_test": 23, "decisions_test": 900}}
    report = H.verdict(model, planner)["nfl"]
    assert report["beats_planner"] is True and report["promotion"][0] is False    # no shadow nights yet
    assert report["real_planner_k4_pct"] == 70.0


def test_verdict_has_no_bar_when_the_two_runs_scored_different_decisions():
    model = {"nfl": {"K4": {"planner_pct": 73.0}, "nights_test": 23, "decisions_test": 900}}
    other = {"nfl": {"K4": {"planner_pct": 60.0}, "nights_test": 22, "decisions_test": 880}}
    report = H.verdict(model, other)["nfl"]
    assert report["real_planner_k4_pct"] is None and report["beats_planner"] is False and report["same_decisions"] is False
    assert report["promotion"][0] is False


def test_holdset_grader_measures_model_and_planner_on_the_same_rows(run, tmp_path, monkeypatch):
    nights = NIGHTS[:5]
    games = {}
    for w, night in enumerate(nights):
        start = (dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - KICK).total_seconds()
        games[night] = [(night, f"g{w}{i}", nfl_summary(start + i * 60, 5 + (w + i) % 9 * 4)) for i in range(6)]
    monkeypatch.setattr(H.holdset, "games", lambda league, only=None: [g for n in (only or sorted(games)) for g in games[n]])

    class FakePlanner:
        def __init__(self, config, poll):
            pass

        def __call__(self, league, night, night_rows):
            return {t: {g[0]: float(i) for i, g in enumerate(night_rows)} for t in holdset.decision_times(night_rows)}
    monkeypatch.setattr(H, "PlannerRanker", FakePlanner)
    out = tmp_path / "report.json"
    assert H.main(["--archive", str(tmp_path / "a"), "--models", str(run[0]), "--out", str(out), "--leagues", "nfl"]) == 0
    got = json.loads(out.read_text())["nfl"]
    assert got["same_decisions"] is True and got["real_planner_k4_pct"] is not None and got["model_k4_pct"] is not None
    assert got["test_nights"] == 2 and got["promotion"][0] is False


def test_holdset_grading_counts_no_published_win_probability(monkeypatch):
    summary = nfl_summary(0.0, 9)
    plays = [p for d in summary["drives"]["previous"] for p in d["plays"]]
    with_wp = {**summary, "winprobability": [{"playId": p["id"], "homeWinPercentage": 0.2 + (i % 2) * 0.6} for i, p in enumerate(plays)]}
    monkeypatch.setattr(H.holdset, "games", lambda league, only=None: [("20231008", "g", with_wp)])
    rows = H.build_rows("nfl")
    plain = holdset.game_row("nfl", "g", summary)
    assert rows["20231008"][0][4] == plain[4]


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


def _tied_scores_case():
    """Tied tree scores (a tree head emits many identical values) in blocks of unequal size: the WP-26D trigger."""
    for seed in range(60):
        r = np.random.default_rng(seed)
        p = 0.1 + (r.random(20000) < 0.5) * 0.1      # two tied score values
        y = (r.random(20000) < p).astype(float)
        kx, ky = K.isotonic(p, y)
        if (np.diff(kx) < 0).any():
            return kx, ky
    raise AssertionError("no tied-score case produced decreasing knots")


def test_isotonic_knots_from_tied_scores_step_back_and_clean_knots_fixes_them():
    kx, ky = _tied_scores_case()
    assert np.diff(kx).min() < 0 and abs(np.diff(kx).min()) < 1e-12     # round-off only
    fx, fy = K.clean_knots(kx, ky)
    assert (np.diff(fx) >= 0).all() and (np.diff(fy) >= 0).all() and fy.min() >= 0 and fy.max() <= 1
    arrays, meta = big_model()
    arrays.update(p1_cal_x=fx, p1_cal_y=fy)
    G.GameValueModel(arrays, meta, "nfl")                                # the runtime accepts the cleaned curve
    arrays.update(p1_cal_x=kx, p1_cal_y=ky)
    with pytest.raises(G.ModelRejected, match="not monotone"):          # and still refused the raw one
        G.GameValueModel(arrays, meta, "nfl")


def test_clean_knots_does_not_hide_a_real_fault():
    with pytest.raises(K.Refused):
        K.clean_knots(np.array([0.0, 0.5, 0.4]), np.array([0.1, 0.2, 0.3]))
    with pytest.raises(K.Refused):
        K.clean_knots(np.array([0.0, 0.5, 1.0]), np.array([0.1, 0.9, 0.2]))
    with pytest.raises(K.Refused):
        K.clean_knots(np.array([0.0, 1.0]), np.array([0.0, 1.5]))


@pytest.mark.parametrize("x,y", [([0.0, 0.6, 0.5], [0.1, 0.2, 0.3]), ([0.0, 0.5, 1.0], [0.1, 0.9, 0.2]), ([0.0, 1.0], [0.0, 1.2])])
def test_runtime_still_refuses_non_monotone_knots(x, y):
    arrays, meta = big_model()
    arrays.update(p1_cal_x=np.array(x), p1_cal_y=np.array(y))
    with pytest.raises(G.ModelRejected, match="not monotone"):
        G.GameValueModel(arrays, meta, "nfl")


def test_fetch_raw_reuses_attached_raw_files(tmp_path, monkeypatch):
    shards, raw = tmp_path / "s", tmp_path / "raw"
    (shards / "nba").mkdir(parents=True)
    (raw / "nba").mkdir(parents=True)
    (shards / "nba" / "2024.parquet").write_bytes(b"x")
    (raw / "nba" / "pbp_2024.parquet").write_bytes(b"x")
    import scripts.noggin_factory as NF
    monkeypatch.setattr(NF, "_fetch", lambda *a, **k: pytest.fail("downloaded a file that was already attached"))
    K.fetch_raw(shards, raw, ["nba"], True)
