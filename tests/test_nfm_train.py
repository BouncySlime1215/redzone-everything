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
