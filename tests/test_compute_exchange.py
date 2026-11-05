"""Compute exchange: shard store push/pull (license required, sha checked), checkpoints never move backwards,
the router's rules, and DiLoCo's outer step."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "exchange"))

import ckpt as C  # noqa: E402
import router as R  # noqa: E402
import shards as S  # noqa: E402


def _shard(root: Path, lic: str | None = "open"):
    d = root / "nfl"
    d.mkdir(parents=True)
    np.save(d / "2024.npy", np.arange(10, dtype=np.uint16))
    info = {"complete": True, "tokens": 10, "games": 1, "vocab_version": 2, "index": [["g", "20240101", 0, 10, 0]]}
    if lic:
        info["license"] = lic
    (d / "2024.json").write_text(json.dumps(info))


def test_shards_round_trip_and_skip_unchanged(tmp_path):
    _shard(tmp_path / "src")
    store = tmp_path / "store"
    assert len(S.push(tmp_path / "src", str(store), "v2")) == 1
    assert (store / "v2" / "nfl" / "2024.npy").exists()
    assert S.pull(str(store), tmp_path / "dst", "v2") == 1
    assert np.array_equal(np.load(tmp_path / "dst" / "nfl" / "2024.npy"), np.arange(10))
    assert S.pull(str(store), tmp_path / "dst", "v2") == 0              # already there, same sha


def test_shard_without_license_is_refused(tmp_path):
    _shard(tmp_path / "src", lic=None)
    with pytest.raises(SystemExit):
        S.manifest(tmp_path / "src")


def test_checkpoint_never_moves_backwards(tmp_path):
    a, b = C.CkptSync(str(tmp_path / "st"), "r1", "siteA"), C.CkptSync(str(tmp_path / "st"), "r1", "siteB")
    ck = tmp_path / "a" / "ckpt.pt"
    ck.parent.mkdir()
    ck.write_bytes(b"step500")
    a.push(ck, 500)
    old = tmp_path / "b" / "ckpt.pt"
    old.parent.mkdir()
    old.write_bytes(b"step200")
    b.push(old, 200)                                                     # behind: refused
    assert a.state()["step"] == 500
    fresh = tmp_path / "c" / "ckpt.pt"
    assert b.pull(fresh, -1) and fresh.read_bytes() == b"step500"
    assert not b.pull(fresh, 500)


def test_router_rules():
    led = {"sources": {
        "big": {"status": "active", "a100_equiv": 8, "hours_left": 100, "good_for": ["pretrain"]},
        "kaggle_tpu": {"status": "active", "a100_equiv": 4, "hours_left_week": 20, "max_session_h": 9,
                       "good_for": ["pretrain", "sweep"]},
        "colab_pro_plus": {"status": "active", "a100_equiv": 1, "cu_left": 150, "cu_reserve": 100,
                           "cu_per_a100_h": 13, "good_for": ["pretrain", "sweep"]},
        "paid": {"status": "active", "a100_equiv": 99, "hours_left": 99, "costs_money": True, "good_for": ["pretrain"]},
        "later": {"status": "applied", "a100_equiv": 50, "good_for": ["pretrain"]}}}
    assert R.route(led, "pretrain", 10)[0] == "big"                      # biggest free pool
    assert R.route(led, "sweep", 1)[0] == "kaggle_tpu"                   # free quota before Colab CU
    led["sources"]["kaggle_tpu"]["hours_left_week"] = 0
    assert R.route(led, "sweep", 1)[0] == "colab_pro_plus"
    assert R.route(led, "sweep", 5)[0] is None                           # would dip into the CU reserve


def test_outer_nesterov_step():
    torch = pytest.importorskip("torch")
    import diloco as D
    o = D.Outer(2, lr=0.5, momentum=0.9)
    th = o.step(torch.zeros(2), torch.ones(2))
    assert torch.allclose(th, torch.full((2,), -0.5 * (1 + 0.9)))
