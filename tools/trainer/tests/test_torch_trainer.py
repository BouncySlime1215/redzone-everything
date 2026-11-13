"""B-03 trainer tests on the torch backend: GPU runtime only (Kaggle or Colab), CPU toy sizes, about a minute.

Run from the repo root on a runtime that has torch:  python -m pytest -q tools/trainer/tests
CI and the Mac have no torch, so CI does not collect this folder (pyproject testpaths = tests); the same loop,
data, resume and fence logic is tested there on the numpy reference (tests/test_trainer_reference.py).
The plan's list: initial loss near ln(vocab), overfit one batch, causality, gradient-accumulation equivalence,
save-resume equivalence (bitwise on CPU), plus the end-to-end kill-and-resume smoke through the job.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from tools.trainer import data, loop, testkit  # noqa: E402
from tools.trainer.model import DecoderConfig, EventDecoder  # noqa: E402
from tools.trainer.torch_backend import TorchBackend  # noqa: E402

TINY = {"d_model": 32, "n_layers": 2, "n_heads": 4, "n_kv_heads": 2}


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return testkit.toy_corpus(tmp_path_factory.mktemp("toycorpus"))


def _backend(cfg, device="cpu", **kw):
    return TorchBackend(data.TOY_VOCAB_SIZE, cfg.ctx, seed=cfg.seed, device=device, **{**TINY, **kw})


def _state(tr):
    return {k: v.detach().cpu().clone() for k, v in tr.backend.model.state_dict().items()}


def test_initial_loss_is_near_ln_vocab(corpus):
    v = data.vocab_v1()["vocab_size"]
    be = TorchBackend(v, 32, seed=0, device="cpu", d_model=64, n_layers=2, n_heads=4, n_kv_heads=2)
    b = data.WindowSet(corpus.docs, 32).batch(range(8))
    r = be.eval_batch(b)
    loss = float((r["nll"] * b["mask"]).sum() / b["mask"].sum())
    assert abs(loss - math.log(v)) < 0.1, loss


def test_overfits_one_tiny_batch():
    torch.manual_seed(0)
    be = TorchBackend(56, 16, seed=1, device="cpu", weight_decay=0.0, **TINY)
    s = np.random.default_rng(0).integers(8, 56, (4, 17))
    b = {"x": s[:, :16], "y": s[:, 1:], "mask": np.ones((4, 16))}
    for _ in range(300):
        loss = be.accumulate(b, 1 / 64) / 64
        be.step(3e-3)
    assert loss < 0.05, loss


def test_no_token_sees_the_future():
    torch.manual_seed(0)
    m = EventDecoder(DecoderConfig(vocab_size=56, ctx=16, init_std=0.2, **TINY)).eval()
    x = torch.randint(0, 56, (3, 16))
    with torch.no_grad():
        base = m(x)
        for k in (0, 7, 15):
            x2 = x.clone()
            x2[:, k] = (x2[:, k] + 7) % 56
            out = m(x2)
            assert torch.allclose(out[:, :k], base[:, :k], atol=1e-6, rtol=0)
            assert not torch.allclose(out[:, k], base[:, k])


def test_gradient_accumulation_equals_one_big_batch(corpus, tmp_path):
    """The accumulated gradient of one optimizer step equals the big-batch gradient (float32 sums, so allclose;
    parameters after several Adam steps are not compared elementwise: Adam turns float noise in a near-zero
    gradient into a full-size update), and the loss curves match."""
    grads, curves = [], []
    for mb, acc in ((8, 1), (4, 2), (1, 8)):
        cfg = testkit.toy_cfg(micro_batch=mb, accum=acc, total_steps=5, model={"backend": "torch", "vocab": "toy"})
        tr = testkit.toy_trainer(corpus, tmp_path / f"a{mb}", _backend(cfg), cfg)
        ids, _ = data.take(tr.cursor, mb * acc, len(tr.ws), cfg.seed)
        chunks = [tr.ws.batch(ids[i * mb:(i + 1) * mb]) for i in range(acc)]
        n = float(sum(b["mask"].sum() for b in chunks))
        for b in chunks:
            tr.backend.accumulate(b, 1.0 / n)
        grads.append({k: p.grad.detach().clone() for k, p in tr.backend.model.named_parameters()})
        tr.backend.opt.zero_grad(set_to_none=True)
        curves.append([tr.train_step()["loss"] for _ in range(5)])
    for g, c in zip(grads[1:], curves[1:]):
        for k in g:
            assert torch.allclose(g[k], grads[0][k], atol=1e-7, rtol=1e-4), k
        assert np.allclose(c, curves[0], rtol=1e-4)


def test_save_resume_equals_uninterrupted_bitwise_on_cpu(corpus, tmp_path):
    cfg = testkit.toy_cfg(total_steps=10, ckpt_every=3, eval_every=5, model={"backend": "torch", "vocab": "toy"})
    a = testkit.toy_trainer(corpus, tmp_path / "a", _backend(cfg), cfg)
    a.run()
    testkit.toy_trainer(corpus, tmp_path / "b", _backend(cfg), cfg).run(stop_step=4)
    c = testkit.toy_trainer(corpus, tmp_path / "b", _backend(cfg), cfg)
    assert c.resume() and c.step == 4
    assert c.run()["status"] == loop.STATUS_DONE
    sa, sc = _state(a), _state(c)
    for k in sa:
        assert torch.equal(sa[k], sc[k]), k
    assert a.monitor.state() == c.monitor.state() and a.last_eval == c.last_eval


def test_attention_logit_and_gqa_shapes():
    for kv in (1, 2, 4):
        be = TorchBackend(56, 16, seed=0, device="cpu", d_model=32, n_layers=1, n_heads=4, n_kv_heads=kv)
        b = data.WindowSet([data.Doc("nba", "g", "20240101", data.toy_game(np.random.default_rng(0), 5))], 16).batch([0])
        assert math.isfinite(be.max_attention_logit(b))
        assert be.eval_batch(b)["nll"].shape == (1, 16)


def test_full_run_on_the_runtime_device(corpus, tmp_path):
    """On a GPU runtime: bf16 (A100/L4) or fp16 with loss scaling (T4), and the GPU name is logged."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    cfg = testkit.toy_cfg(total_steps=6, model={"backend": "torch", "vocab": "toy"})
    tr = testkit.toy_trainer(corpus, tmp_path, _backend(cfg, device=device, precision="auto"), cfg)
    res = tr.run()
    assert res["status"] == loop.STATUS_DONE and math.isfinite(res["last_train"]["loss"])
    desc = tr.backend.describe()
    assert (desc["gpu_type"] != "cpu") == (device == "cuda")
    assert desc["precision"] in (("bf16", "fp16") if device == "cuda" else ("fp32",))


def test_job_smoke_kill_and_resume(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "tools.trainer.job", "smoke", "--backend", "torch", "--out",
                           str(tmp_path)], cwd=REPO, capture_output=True, text=True, timeout=900)
    assert proc.returncode == 0 and "SMOKE PASS" in proc.stdout, proc.stdout + proc.stderr
