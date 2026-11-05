"""EXP-100 world model pilot: the split is by whole night in time order, windows cut on event multiples, and the
grader runs end to end on a tiny model (synthetic tokens, no download)."""
from __future__ import annotations

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
    with pytest.raises(SystemExit, match="ESPN-derived"):
        W.main(["--out", str(tmp_path), "--leagues", "nba_espn,nfl", "--smoke"])
    assert "nba_espn" in W.ESPN_DERIVED and min(W.SEASONS["nfl"]) == 2001 and "nba" not in W.ESPN_DERIVED
