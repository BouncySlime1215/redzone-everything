"""The Qwen grader: same holdset slot as GameValue, refuses bad models, says plainly when the small model wins. Fakes only."""
from __future__ import annotations

import json

import numpy as np
import pytest

from agent.kaggle import qwen_common as C
from agent.kaggle import qwen_eval as QE
from redzone.noggin import gamevalue as G
from scripts import kaggle_gamevalue_holdset as HOLD

NIGHTS = {f"202510{d:02d}": ["row"] for d in range(1, 11)}     # graded: the newest 4


class Fake:
    def digit_probs(self, prompts):
        out = np.full((len(prompts), 10), 0.01)
        out[:, 4] = 0.91
        return out

    def yes_probs(self, prompts):
        return np.full(len(prompts), 0.3)

    def words(self, prompts):
        return ["late, 3 point game"] * len(prompts)


def manifest(**over):
    return {"schema": C.SCHEMA, "status": C.STATUS, "recipe": "q1", "base": "mac-4bit", "steps_done": 10, "finished": True, "license_tag": "",
            "leak_check": {"passed": True, "raw_rows": 5}, "bucket_means": [float(i) for i in range(10)], "p_cal_x": [], "p_cal_y": [],
            "train_nights": ["20230910"], "calib_nights": ["20231001"], **over}


@pytest.fixture
def model_dir(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "qwen_model.json").write_text(json.dumps(manifest()))
    return d


def cell(k4, nights=4, decisions=300):
    return {"K4": {"planner_pct": k4}, "nights_test": nights, "decisions_test": decisions}


def wire(monkeypatch, tmp_path, qwen=60.0, planner=50.0, small=70.0, small_cell=None):
    """holdset.report keyed by what is in the ranker slot, and the rows a real archive would give."""
    monkeypatch.setattr(HOLD, "build_rows", lambda lg: NIGHTS)
    monkeypatch.setattr(QE, "PlannerRanker", lambda config, poll: "planner")

    def report(league, rows, ranker):
        assert rows is NIGHTS
        if ranker == "planner":
            return cell(planner)
        if isinstance(ranker.model, C.Predictor):
            X = np.array([[float({"period": 4.0, "late": 1.0}.get(f, 0.0)) for f in G.GV_FEATURES]])
            ranker.model.predict_matrix(X)
            return cell(qwen)
        return small_cell or cell(small)
    monkeypatch.setattr(QE.holdset, "report", report)
    gv = tmp_path / "gv"
    gv.mkdir()
    (gv / G.model_path("nfl", "v1", gv).name).write_text("x")
    monkeypatch.setattr(QE.G, "load", lambda path, league: "small-model")
    return gv


def run(model_dir, tmp_path, gv, *extra, fake=None, clock=lambda: 0.0):
    out = tmp_path / "eval.json"
    argv = ["--model", str(model_dir), "--archive", str(tmp_path / "archive"), "--gamevalue", str(gv), "--out", str(out), "--leagues", "nfl", *extra]
    code = QE.main(argv, backend_factory=lambda *a: fake or Fake(), clock=clock, log=lambda *a, **k: None)
    return code, json.loads(out.read_text()) if out.exists() else None


def test_refuses_on_the_mac(tmp_path, monkeypatch):
    monkeypatch.setattr(QE.sys, "platform", "darwin")
    assert QE.main(["--model", str(tmp_path), "--archive", str(tmp_path), "--gamevalue", str(tmp_path), "--out", str(tmp_path / "x")]) == QE.EXIT_MAC


def test_refuses_without_a_manifest(tmp_path, monkeypatch):
    gv = wire(monkeypatch, tmp_path)
    (tmp_path / "empty").mkdir()
    assert run(tmp_path / "empty", tmp_path, gv)[0] == QE.EXIT_REFUSED


@pytest.mark.parametrize("bad", [{"leak_check": {"passed": False, "raw_rows": 5}}, {"leak_check": {"passed": True, "raw_rows": 0}}, {"leak_check": None},
                                 {"status": "live"}])
def test_refuses_a_model_without_a_passed_leak_check(tmp_path, monkeypatch, model_dir, bad):
    (model_dir / "qwen_model.json").write_text(json.dumps(manifest(**bad)))
    assert run(model_dir, tmp_path, wire(monkeypatch, tmp_path))[0] == QE.EXIT_REFUSED


def test_refuses_when_a_graded_night_was_trained_on(tmp_path, monkeypatch, model_dir):
    (model_dir / "qwen_model.json").write_text(json.dumps(manifest(calib_nights=["2025-10-09"])))
    assert run(model_dir, tmp_path, wire(monkeypatch, tmp_path))[0] == QE.EXIT_REFUSED


def test_a_training_night_on_the_fit_side_is_fine(tmp_path, monkeypatch, model_dir):
    (model_dir / "qwen_model.json").write_text(json.dumps(manifest(train_nights=["20251002"])))
    assert run(model_dir, tmp_path, wire(monkeypatch, tmp_path))[0] == 0


def test_losing_to_gamevalue_is_said_plainly(tmp_path, monkeypatch, model_dir, capsys):
    code, got = run(model_dir, tmp_path, wire(monkeypatch, tmp_path, qwen=60.0, planner=50.0, small=70.0))
    cell_ = got["leagues"]["nfl"]
    assert code == 0 and not cell_["beats_gamevalue"] and cell_["beats_planner"]
    assert cell_["result"].startswith("DOES NOT BEAT GameValue: Qwen 60.0% vs 70.0%")
    assert "DOES NOT BEAT GameValue" in capsys.readouterr().out


def test_a_tie_with_gamevalue_is_not_a_win(tmp_path, monkeypatch, model_dir):
    assert not run(model_dir, tmp_path, wire(monkeypatch, tmp_path, qwen=70.0, small=70.0))[1]["leagues"]["nfl"]["beats_gamevalue"]


def test_beating_both_bars_still_does_not_promote(tmp_path, monkeypatch, model_dir):
    cell_ = run(model_dir, tmp_path, wire(monkeypatch, tmp_path, qwen=80.0, planner=50.0, small=70.0))[1]["leagues"]["nfl"]
    assert cell_["beats_gamevalue"] and cell_["beats_planner"] and cell_["promotion"][0] is False


def test_losing_to_the_planner_is_in_the_same_line(tmp_path, monkeypatch, model_dir):
    assert "Planner bar not beaten (90.0%)" in run(model_dir, tmp_path, wire(monkeypatch, tmp_path, planner=90.0))[1]["leagues"]["nfl"]["result"]


def test_a_gamevalue_run_on_other_decisions_gives_no_comparison(tmp_path, monkeypatch, model_dir):
    cell_ = run(model_dir, tmp_path, wire(monkeypatch, tmp_path, small_cell=cell(99.0, decisions=1)))[1]["leagues"]["nfl"]
    assert cell_["gamevalue_k4_pct"] is None and not cell_["beats_gamevalue"] and cell_["result"].startswith("NO COMPARISON")


def test_no_small_model_means_no_win(tmp_path, monkeypatch, model_dir):
    gv = wire(monkeypatch, tmp_path)
    (gv / G.model_path("nfl", "v1", gv).name).unlink()
    assert run(model_dir, tmp_path, gv)[1]["leagues"]["nfl"]["result"].startswith("NO COMPARISON")


def test_scored_rows_pass_the_brain_contract(tmp_path, monkeypatch, model_dir):
    got = run(model_dir, tmp_path, wire(monkeypatch, tmp_path))[1]["leagues"]["nfl"]["contract"]
    assert got == {"checked": 1, "valid": True, "problem": None}


def test_out_of_time_is_no_verdict(tmp_path, monkeypatch, model_dir):
    gv = wire(monkeypatch, tmp_path)
    ticks = iter([0.0, 1e9, 1e9, 1e9])
    code, got = run(model_dir, tmp_path, gv, clock=lambda: next(ticks))
    assert code == QE.EXIT_REFUSED and got is None


def test_a_locked_night_stops_the_league(tmp_path, monkeypatch, model_dir):
    gv = wire(monkeypatch, tmp_path)

    def locked(_lg):
        raise QE.holdset.LockedNightError("20261101 is locked")
    monkeypatch.setattr(HOLD, "build_rows", locked)
    assert run(model_dir, tmp_path, gv)[0] == QE.EXIT_REFUSED


def test_the_real_ranker_slot_is_used(tmp_path, monkeypatch, model_dir):
    seen = []
    real = HOLD.ModelRanker

    class Spy(real):
        def __init__(self, model, games=None):
            seen.append(type(model).__name__)
            super().__init__(model, games)
    monkeypatch.setattr(HOLD, "ModelRanker", Spy)
    gv = wire(monkeypatch, tmp_path)
    assert run(model_dir, tmp_path, gv)[0] == 0
    assert seen[0] == "Predictor"
