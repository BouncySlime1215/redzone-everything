"""Portable trainer (EXP-106..108): preset sizes, the static-shape MoE layer, packing, resume-exact batches, LR."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import nfm_train as N  # noqa: E402


def test_ladder_sizes_and_moe_target():
    ne = {k: N.count_params(20096, **N.PRESETS[k])["non_embedding_active"] / 1e6 for k in ("60m", "150m", "300m")}
    assert 55 < ne["60m"] < 70 and 140 < ne["150m"] < 170 and 290 < ne["300m"] < 340
    big = N.count_params(20096, **N.PRESETS["moe-5b"])
    assert 4.5e9 < big["total"] < 5.5e9 and 0.9e9 < big["active"] < 1.3e9
    dense, moe = (N.count_params(20096, **N.PRESETS[k]) for k in ("60m", "moe-60m"))
    assert abs(dense["active"] - moe["active"]) / dense["active"] < 0.02 and moe["total"] > 2 * dense["total"]


def test_built_model_matches_the_count():
    for k in ("tiny",):
        m = N.build_model(512, **N.PRESETS[k])
        assert sum(p.numel() for p in m.parameters()) == N.count_params(512, **N.PRESETS[k])["total"]


def test_moe_forward_backward_static_shapes():
    m = N.build_model(256, d=64, layers=2, heads=2, experts=4, top_k=2)
    x = torch.randint(0, 256, (3, 32))
    y = m(x)
    assert y.shape == (3, 32, 256)
    loss = y.logsumexp(-1).mean() + 0.01 * m.aux_loss()
    loss.backward()
    assert m.blocks[0].ffn.w_gate.grad is not None and float(m.aux_loss()) > 0


def test_packing_and_resume_exact_batches():
    games = [{"tokens": np.arange(i * 10, i * 10 + 10, dtype=np.uint16)} for i in range(5)]
    s = N.pack(games)
    assert len(s) == 50 and s[10] == 10
    b = N.Batches(s, 8, 4)
    assert np.array_equal(b.get(7), N.Batches(s, 8, 4).get(7)) and b.get(7).shape == (4, 9)
    assert not np.array_equal(b.get(7), b.get(8))


def test_lr_warmup_then_cosine_to_ten_percent():
    assert N.lr_at(0, 1000, 1.0, 100) == pytest.approx(0.01)
    assert N.lr_at(99, 1000, 1.0, 100) == pytest.approx(1.0)
    assert N.lr_at(1000, 1000, 1.0, 100) == pytest.approx(0.1)


def test_speedrun_bundle_runs_and_doc_mask_isolates_games():
    torch.manual_seed(0)
    m = N.build_model(64, d=32, layers=2, heads=2, qk_norm=True, relu2=True, untie=True, softcap=15.0,
                      value_emb=True, doc_mask=True, bos_ids=[3])
    for blk in m.blocks:                                   # non-zero value-embedding mix so the check is meaningful
        blk.ve_w.data.fill_(0.5)
    m.head.weight.data.normal_()
    a = torch.tensor([[3, 10, 11, 12, 3, 20, 21, 22]])
    b = torch.tensor([[3, 40, 41, 42, 3, 20, 21, 22]])     # same second game, different first game
    ya, yb = m(a), m(b)
    assert torch.allclose(ya[0, 4:], yb[0, 4:], atol=1e-5) and not torch.allclose(ya[0, :4], yb[0, :4])
    assert float(ya.abs().max()) <= 15.0 + 1e-4
    ya.logsumexp(-1).mean().backward()


def test_muon_updates_hidden_matrices_and_adamw_the_rest():
    m = N.build_model(64, d=32, layers=1, heads=2)
    opt = N.Optims(m, 1e-3, muon=True)
    assert len(opt.opts) == 2
    w0, e0 = m.blocks[0].qkv.weight.detach().clone(), m.tok.weight.detach().clone()
    m(torch.randint(0, 64, (2, 8))).logsumexp(-1).mean().backward()
    opt.set_factor(1.0)
    opt.step()
    assert not torch.equal(w0, m.blocks[0].qkv.weight) and not torch.equal(e0, m.tok.weight)
    u = N.newton_schulz(torch.randn(16, 32)).float()
    sv = torch.linalg.svdvals(u)
    assert float(sv.min()) > 0.5 and float(sv.max()) < 1.5             # quintic NS: singular values pushed near 1


def test_wsd_schedule_flat_then_linear_to_zero():
    assert N.lr_at(500, 1000, 1.0, 100, "wsd") == 1.0
    assert N.lr_at(900, 1000, 1.0, 100, "wsd") == pytest.approx(0.5)
    assert N.lr_at(1000, 1000, 1.0, 100, "wsd") == 0.0


def _shards(root: Path, n_nights: int = 30):
    """Fake v1 shards (nba), 2 games a night: enough for a split, grading and the per-league valid loss."""
    import json

    import noggin_tokens as T
    (root / "nba").mkdir(parents=True)
    parts, index, off = [], [], 0
    for d in range(n_nights):
        for g in range(2):
            rng = np.random.default_rng(d * 2 + g)
            ev = []
            for _ in range(40):
                stop = T.STOP0 + (T.STOPS.index("timeout") if rng.random() < 0.2 else 0)
                ev += [T.KIND0 + int(rng.integers(0, 4)), T.DT0 + int(rng.integers(0, 12)), T.CLOCK0, T.MARGIN0,
                       T.PTS0, stop]
            t = np.array([T.BOS0, T.TEAM0, T.TEAM0 + 1] + ev + [T.EOS], dtype=np.uint16)
            parts.append(t)
            index.append([f"g{d}_{g}", f"2025{1 + d // 28:02d}{1 + d % 28:02d}", off, len(t), 0])
            off += len(t)
    np.save(root / "nba" / "2025.npy", np.concatenate(parts))
    (root / "nba" / "2025.json").write_text(json.dumps({"complete": True, "games": len(index), "tokens": off,
                                                         "license": "open (test)", "index": index}))


@pytest.mark.parametrize("exchange", [False, True])
def test_main_end_to_end_with_checkpoints_micro_batches_and_epoch_cap(tmp_path, exchange):
    """EXP-107 run 1 crashed at its first 30-min checkpoint (the exchange sync object was shadowed by the XLA sync
    function): run main for real, checkpoint every step, with and without an exchange store, micro-batched, with the
    epoch cap and a grading pass."""
    import json
    _shards(tmp_path / "shards")
    out = tmp_path / "run"
    args = ["--tokens", str(tmp_path / "shards"), "--leagues", "nba", "--out", str(out), "--preset", "tiny",
            "--ctx", "64", "--batch", "4", "--micro-batch", "2", "--max-epochs", "0.5", "--ckpt-min", "0",
            "--eval-every", "5", "--grade-games", "3", "--device", "cpu", "--warmup", "2", "--dropout", "0.1",
            "--exp", "EXP-test"]
    if exchange:
        args += ["--exchange", str(tmp_path / "store"), "--run", "t1"]
    assert N.main(args) == 0
    g = json.loads((out / "grades.json").read_text())
    assert g["epochs"] <= 0.5 + 1e-6 and g["steps"] == g["planned_steps"] and g["test_by_league"]["nba"]["games"] > 0
    assert (out / "ckpt.pt").exists()
    if exchange:
        assert (tmp_path / "store" / "runs" / "t1" / "ckpt.pt").exists()
    assert N.main(args[:-2] + ["--max-steps", "3", "--out", str(tmp_path / "smoke")] if exchange else
                  args + ["--max-steps", "3", "--out", str(tmp_path / "smoke")]) == 0
    assert json.loads((tmp_path / "smoke" / "grades.json").read_text())["steps"] == 3
