"""EXP-100 world model pilot: the split is by whole night in time order, windows cut on event multiples, and the
grader runs end to end on a tiny model (synthetic tokens, no download)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import noggin_tokens as T  # noqa: E402
import world_model_pilot as W  # noqa: E402


def _game(i: int, night: str, n_ev: int = 30) -> dict:
    rng = np.random.default_rng(i)
    ev = []
    for _ in range(n_ev):
        stop = T.STOP0 + (T.STOPS.index("timeout") if rng.random() < 0.2 else 0)
        ev += [T.KIND0 + int(rng.integers(0, 4)), T.DT0 + int(rng.integers(0, 12)), T.CLOCK0, T.MARGIN0, T.PTS0, stop]
    toks = np.array([T.BOS0, T.TEAM0, T.TEAM0 + 1] + ev + [T.EOS], dtype=np.uint16)
    return {"league": "nba", "gid": f"g{i}", "night": night, "tokens": toks}


def test_split_is_by_whole_night_in_time_order():
    games = [_game(i, f"202501{d:02d}") for i, d in enumerate(range(1, 21))]
    sp = W.split(games)
    tr, va, te = ({g["night"] for g in sp[k]} for k in ("train", "valid", "test"))
    assert max(tr) < min(va) <= max(va) < min(te) and not (tr & va or va & te)


def test_split_gives_every_league_its_own_test_nights():
    nba = [_game(i, f"202501{d:02d}") for i, d in enumerate(range(1, 21))]
    mlb = [{**_game(100 + i, f"202406{d:02d}"), "league": "mlb", "gid": f"m{i}"} for i, d in enumerate(range(1, 21))]
    sp = W.split(nba + mlb)
    for k in ("train", "valid", "test"):
        assert {g["league"] for g in sp[k]} == {"nba", "mlb"}
    for lg in ("nba", "mlb"):
        tr = {g["night"] for g in sp["train"] if g["league"] == lg}
        te = {g["night"] for g in sp["test"] if g["league"] == lg}
        assert max(tr) < min(te)


def test_windows_cut_on_whole_events():
    w = W.windows([_game(0, "20250101")], ctx=24, rng=np.random.default_rng(0))
    assert w.shape[1] == 25 and len(w) == -(-(3 + 180 + 1 - 1) // 24)


def test_grade_runs_end_to_end_on_a_tiny_model():
    torch = pytest.importorskip("torch")
    torch.manual_seed(0)
    games = [_game(i, "20250101") for i in range(4)]
    model = W.make_model(T.VOCAB_SIZE, 32, 1, 2, 60)
    base = W.baselines(games)
    g = W.grade(model, games, base, 60, "cpu")
    assert g["games"] == 4 and g["next_kind_acc"]["n"] == 4 * 29
    assert 0 <= g["next_kind_acc"]["value"] <= 1 and g["break_dt_mae_s"]["n"] > 0
    assert g["stop_ece_baseline"]["value"] is not None and g["stop_brier"] is not None


def test_espn_derived_nba_needs_nicks_yes(tmp_path):
    pytest.importorskip("pandas")                      # CI test jobs have no pandas
    with pytest.raises(SystemExit, match="ESPN-derived"):
        W.main(["--out", str(tmp_path), "--leagues", "nba_espn,nfl", "--smoke"])
    assert "nba_espn" in W.ESPN_DERIVED and min(W.SEASONS["nfl"]) == 2001 and "nba" not in W.ESPN_DERIVED


def test_extra_shards_join_train_only_before_valid_and_capped_at_3x():
    real = [_game(i, f"202501{d:02d}") for i, d in enumerate(range(1, 21))]
    sp = W.split(real)
    starts = W.valid_start(real)
    assert starts["nba"] == min(g["night"] for g in sp["valid"])
    syn = [{**_game(200 + i, f"202501{d:02d}"), "gid": f"s{i}"} for i, d in enumerate(range(1, 21))]
    train, rep = W.add_extra(sp["train"], syn, starts)
    added = [g for g in train if g.get("synthetic")]
    assert added and all(g["night"] < starts["nba"] for g in added)
    assert rep["dropped_not_before_valid"] == 4 and 0 < rep["ratio_by_league"]["nba"] <= W.SYNTH_CAP
    too_many = [{**g, "gid": f"{g['gid']}x{k}"} for k in range(4) for g in syn[:16]]
    with pytest.raises(SystemExit):
        W.add_extra(sp["train"], too_many, starts)


def test_best_model_is_saved_before_grading_and_a_rerun_grades_only(tmp_path):
    """EXP-103 died twice in grading and lost the trained model: now best.pt + trained.json land before grading,
    --stop-before-grade exits there, and a rerun on the same --out grades without training (here on the CPU)."""
    pytest.importorskip("torch")
    root = tmp_path / "shards"
    (root / "nba").mkdir(parents=True)
    games = [_game(i, f"2025{m:02d}{d:02d}", 60) for i, (m, d) in enumerate((m, d) for m in (1, 2) for d in range(1, 21))]
    arr = np.concatenate([g["tokens"] for g in games])
    off, index = 0, []
    for g in games:
        index.append([g["gid"], g["night"], off, len(g["tokens"]), 0])
        off += len(g["tokens"])
    np.save(root / "nba" / "2025.npy", arr)
    (root / "nba" / "2025.json").write_text(json.dumps({"complete": True, "games": len(games), "tokens": len(arr),
                                                         "license": "open (test)", "index": index}))
    out = tmp_path / "run"
    args = ["--out", str(out), "--from-tokens", str(root), "--leagues", "nba", "--minutes", "0.05",
            "--d", "32", "--layers", "1", "--heads", "2", "--ctx", "120", "--batch", "8"]
    assert W.main(args + ["--stop-before-grade"]) == 0
    assert (out / "best.pt").exists() and (out / "trained.json").exists() and not (out / "grades.json").exists()
    assert W.main(args + ["--grade-device", "cpu"]) == 0
    g = json.loads((out / "grades.json").read_text())
    assert "partial" not in g and g["test"]["games"] > 0 and g["throughput"]["grade_device"] == "cpu"
    assert g["recipe"]["steps"] == json.loads((out / "trained.json").read_text())["step"]
