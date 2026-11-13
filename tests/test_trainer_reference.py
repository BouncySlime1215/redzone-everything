"""B-03 trainer tests on the numpy reference backend (CI and the Mac, seconds, no torch).

The plan's test list (WORKPLAN B-03, TRAINING-PLAN section 2): initial loss near ln(vocab), overfit one batch,
causality, gradient-accumulation equivalence, save-resume equivalence (kill and resume == uninterrupted), the
spike monitor; plus the WSD schedule, the manifest pin, the leak fence and the registry record. The torch
backend repeats the model-level tests on the GPU runtime: tools/trainer/tests/test_torch_trainer.py.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from redzone.lab.datamanifest import ManifestError  # noqa: E402
from redzone.lab.fence import FenceError  # noqa: E402
from tools.trainer import checkpoint, data, job, loop, monitor, schedule, testkit  # noqa: E402
from tools.trainer.metrics import EvalAggregator, per_target  # noqa: E402
from tools.trainer.reference import NumpyBackend, causal_attention  # noqa: E402


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    return testkit.toy_corpus(tmp_path_factory.mktemp("toycorpus"))


def _backend(cfg, d_model=16, **kw):
    return NumpyBackend(data.TOY_VOCAB_SIZE, cfg.ctx, d_model=d_model, seed=cfg.seed, **kw)


def _params(tr):
    return {k: v.copy() for k, v in tr.backend.p.items()}


# --- schedule -------------------------------------------------------------------------------------------------------

def test_wsd_warmup_stable_decay_and_branch():
    T, peak = 1000, 3e-3
    lr = [schedule.wsd_lr(s, T, peak, warmup_frac=0.02, decay_frac=0.2, final_frac=0.1, shape="linear")
          for s in range(T + 5)]
    assert lr[0] == pytest.approx(peak / 20) and lr[19] == pytest.approx(peak)            # 20 warmup steps
    assert all(a < b for a, b in zip(lr[:19], lr[1:20]))
    assert set(lr[20:800]) == {peak}                                                       # stable
    assert all(a >= b for a, b in zip(lr[800:T], lr[801:T + 1]))                            # monotone cooldown
    assert lr[T] == pytest.approx(0.1 * peak) and lr[T + 4] == pytest.approx(0.1 * peak)
    br = [schedule.wsd_lr(s, T, peak, decay_start=400, shape="sqrt") for s in range(T)]
    assert br[:400] == [schedule.wsd_lr(s, T, peak, decay_start=400) for s in range(400)]
    assert br[100:400] == lr[100:400] and br[400] == pytest.approx(peak) and br[401] < peak
    assert schedule.branch_points(250) == [500, 1000, 2000, 4000]
    with pytest.raises(ValueError):
        schedule.wsd_lr(0, 100, 1e-3, decay_start=101)


# --- the plan's model-level tests -----------------------------------------------------------------------------------

def test_initial_loss_is_near_ln_vocab(corpus, tmp_path):
    v = data.vocab_v1()["vocab_size"]                     # the real vocab v1: 10,511 ids
    assert v == 10511
    cfg = testkit.toy_cfg()
    be = NumpyBackend(v, cfg.ctx, d_model=32, seed=0)
    ws = data.WindowSet(corpus.docs, cfg.ctx)
    b = ws.batch(range(8))
    nll = per_target(be.logits(b["x"]), b["y"])["nll"]
    assert abs(float((nll * b["mask"]).sum() / b["mask"].sum()) - math.log(v)) < 0.01
    tr = testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg)                          # through the loop
    assert abs(tr.train_step()["loss"] - math.log(data.TOY_VOCAB_SIZE)) < 0.01


def test_gradients_match_finite_differences():
    rng = np.random.default_rng(1)
    be = NumpyBackend(7, 5, d_model=4, seed=2, z_loss=1e-2, init_std=0.5)
    x = rng.integers(0, 7, (2, 5))
    y = rng.integers(0, 7, (2, 5))
    mask = np.array([[1, 1, 1, 0, 1], [1, 0, 1, 1, 1]], float)
    b = {"x": x, "y": y, "mask": mask}

    def total():
        lg = be.logits(x)
        lse = np.log(np.exp(lg - lg.max(-1, keepdims=True)).sum(-1)) + lg.max(-1)
        nll = lse - np.take_along_axis(lg, y[..., None], -1)[..., 0]
        return float(((nll + be.z * lse ** 2) * mask).sum() * 0.25)

    be.zero_grad()
    be.accumulate(b, 0.25)
    for k in ("E", "P", "W", "b"):
        for idx in list(np.ndindex(be.p[k].shape))[::3][:12]:
            old = be.p[k][idx]
            be.p[k][idx] = old + 1e-6
            up = total()
            be.p[k][idx] = old - 1e-6
            dn = total()
            be.p[k][idx] = old
            assert be.g[k][idx] == pytest.approx((up - dn) / 2e-6, rel=1e-4, abs=1e-8), (k, idx)


def test_overfits_one_tiny_batch():
    rng = np.random.default_rng(0)
    be = NumpyBackend(56, 16, d_model=32, seed=1, weight_decay=0.0)
    s = rng.integers(8, 56, (4, 17))
    s[:, 0] = [2, 3, 4, 5]                                   # distinct first tokens: nothing is irreducible
    b = {"x": s[:, :16], "y": s[:, 1:], "mask": np.ones((4, 16))}
    first = None
    for _ in range(300):
        loss = be.accumulate(b, 1 / 64) / 64
        first = loss if first is None else first
        be.step(2e-2)
    assert first > 3.5 and loss < 0.01


def test_no_token_sees_the_future():
    rng = np.random.default_rng(3)
    be = NumpyBackend(56, 16, d_model=16, seed=4, init_std=0.5)
    x = rng.integers(0, 56, (3, 16))
    base = be.logits(x)
    for k in (0, 5, 15):
        x2 = x.copy()
        x2[:, k] = (x2[:, k] + 7) % 56
        out = be.logits(x2)
        assert np.array_equal(out[:, :k], base[:, :k])          # positions before k unchanged, bit for bit
        assert not np.allclose(out[:, k], base[:, k])           # and position k does read its own token
    q, kk, v = (rng.normal(size=(2, 9, 4)) for _ in range(3))
    a = causal_attention(q, kk, v)
    kk2, v2 = kk.copy(), v.copy()
    kk2[:, 6], v2[:, 6] = 9.0, -9.0
    b2 = causal_attention(q, kk2, v2)
    assert np.allclose(a[:, :6], b2[:, :6]) and not np.allclose(a[:, 6:], b2[:, 6:])


def test_windows_never_mix_games_and_targets_are_shifted(corpus):
    ws = data.WindowSet(corpus.docs, 16)
    b = ws.batch(range(len(ws)))
    assert np.array_equal(b["x"][:, 1:] * b["mask"][:, 1:], b["y"][:, :-1] * b["mask"][:, 1:])
    for r, (di, s) in enumerate(ws.windows):
        t = corpus.docs[di].tokens
        n = int(b["mask"][r].sum())
        assert np.array_equal(b["y"][r, :n], t[s + 1: s + 1 + n])               # one game per window
    f = b["field"][b["mask"] > 0]
    assert set(np.unique(f)) == {-1, 0, 1, 2, 3, 4, 5}


def test_gradient_accumulation_equals_one_big_batch(corpus, tmp_path):
    finals = []
    for mb, acc in ((8, 1), (4, 2), (2, 4), (1, 8)):
        cfg = testkit.toy_cfg(micro_batch=mb, accum=acc, total_steps=6)
        tr = testkit.toy_trainer(corpus, tmp_path / f"acc{mb}", _backend(cfg), cfg)
        losses = [tr.train_step()["loss"] for _ in range(6)]
        finals.append((_params(tr), losses))
    ref_p, ref_l = finals[0]
    for p, losses in finals[1:]:
        assert np.allclose(losses, ref_l, rtol=1e-12, atol=0)
        for k in ref_p:
            assert np.allclose(p[k], ref_p[k], rtol=1e-10, atol=1e-13), k
    # the windows differ in length, so a per-micro-batch mean (the classic bug) would NOT match
    cfg = testkit.toy_cfg(micro_batch=8)
    ws = data.WindowSet(testkit.toy_trainer(corpus, tmp_path / "x", _backend(cfg), cfg).ws.docs, cfg.ctx)
    ids, _ = data.take(data.Cursor(), 8, len(ws), cfg.seed)
    halves = [ws.batch(ids[:4]), ws.batch(ids[4:])]
    assert halves[0]["mask"].sum() != halves[1]["mask"].sum()


def test_save_resume_equals_uninterrupted(corpus, tmp_path):
    cfg = testkit.toy_cfg(total_steps=14, eval_every=4, ckpt_every=3)
    a = testkit.toy_trainer(corpus, tmp_path / "a", _backend(cfg), cfg)
    ra = a.run()
    b = testkit.toy_trainer(corpus, tmp_path / "b", _backend(cfg), cfg)
    assert b.run(stop_step=7)["status"] == loop.STATUS_STOPPED
    c = testkit.toy_trainer(corpus, tmp_path / "b", _backend(cfg), cfg)      # a fresh process would do this
    assert c.resume() and c.step == 7
    rc = c.run()
    assert ra["status"] == rc["status"] == loop.STATUS_DONE and rc["resumed_from"] == 7
    for k, v in _params(a).items():
        assert np.array_equal(v, c.backend.p[k]), k                         # bitwise
    assert a.backend.t == c.backend.t and a.cursor == c.cursor and a.tokens == c.tokens
    assert a.monitor.state() == c.monitor.state() and ra["last_eval"] == rc["last_eval"]


def test_kill_mid_run_and_rerun_equals_uninterrupted(tmp_path):
    """Through real processes: the job dies after step 13 without saving (last checkpoint step 10); the same
    command run again resumes and ends bitwise equal to an uninterrupted run."""
    proc = subprocess.run([sys.executable, "-m", "tools.trainer.job", "smoke", "--out", str(tmp_path)], cwd=REPO,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SMOKE PASS" in proc.stdout and "resumed from 10" in proc.stdout and "bitwise identical" in proc.stdout


def test_resume_refuses_a_different_run(corpus, tmp_path):
    cfg = testkit.toy_cfg(total_steps=4)
    testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg).run(stop_step=2)
    for other in (testkit.toy_cfg(total_steps=4, seed=6), testkit.toy_cfg(total_steps=4, peak_lr=2e-2),
                  testkit.toy_cfg(total_steps=4, train_through="20240215")):
        with pytest.raises(loop.ResumeRefused):
            testkit.toy_trainer(corpus, tmp_path, _backend(other), other).resume()


def test_resume_refuses_a_precision_change_unless_allowed(corpus, tmp_path, monkeypatch):
    cfg = testkit.toy_cfg(total_steps=4)
    testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg).run(stop_step=2)
    t = testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg)
    monkeypatch.setattr(t.backend, "describe", lambda: {"gpu_type": "T4", "n_params": 1, "backend": "numpy-reference",
                                                        "precision": "fp16"})
    with pytest.raises(loop.ResumeRefused, match="precision"):
        t.resume()
    assert t.resume(allow_precision_change=True) and t.step == 2


def test_corrupt_checkpoint_falls_back_to_previous(corpus, tmp_path):
    cfg = testkit.toy_cfg(total_steps=8, ckpt_every=2, keep_ckpts=3)
    testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg).run(stop_step=6)
    ck = tmp_path / "ckpt"
    assert sorted(p.name for p in ck.iterdir() if p.is_dir()) == ["step_00000002", "step_00000004", "step_00000006"]
    blob = ck / "step_00000006" / "backend.bin"
    blob.write_bytes(blob.read_bytes()[:-8] + b"corrupt!")
    folder, notes = checkpoint.latest(ck)
    assert folder.name == "step_00000004" and any("sha256 mismatch" in n for n in notes)
    t = testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg)
    assert t.resume() and t.step == 4


def test_spike_monitor_flags_spikes_and_halts():
    rng = np.random.default_rng(0)
    m = monitor.SpikeMonitor(window=100, z=7.0, min_history=20, halt_after=3)
    for s in range(200):                                  # a smooth, noisy, falling loss: no spikes
        assert not m.update(4.0 * math.exp(-s / 80) + 2.0 + rng.normal(0, 0.01), 1.0 + rng.normal(0, 0.01))["loss_spike"]
    assert m.loss_spike_score == 0.0 and m.total_loss_spikes == 0
    r = m.update(9.0, 50.0)
    assert r["loss_spike"] and r["gnorm_spike"] and not r["halt"]
    assert m.loss_spike_score == pytest.approx(0.01)
    clone = monitor.SpikeMonitor.from_state(json.loads(json.dumps(m.state())))
    assert clone.state() == m.state()
    assert m.update(9.5)["loss_spike"] and m.update(10.0)["halt"]             # three in a row
    assert monitor.SpikeMonitor().update(float("nan"))["halt"]
    assert monitor.SpikeMonitor().update(1.0, float("inf"))["halt"]


def test_trainer_stops_on_divergence_and_keeps_last_good_checkpoint(corpus, tmp_path):
    cfg = testkit.toy_cfg(total_steps=10, ckpt_every=3)
    tr = testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg)
    real = tr.backend.accumulate
    tr.backend.accumulate = lambda b, s: real(b, s) if tr.step < 5 else float("nan")
    res = tr.run()
    assert res["status"] == loop.STATUS_DIVERGED and res["step"] == 6
    assert checkpoint.latest(tmp_path / "ckpt")[0].name == "step_00000003"


def test_time_budget_self_stop_saves_a_resumable_checkpoint(corpus, tmp_path):
    cfg = testkit.toy_cfg(total_steps=10)
    res = testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg).run(max_seconds=0.0)
    assert res["status"] == loop.STATUS_BUDGET and res["step"] == 0
    assert checkpoint.latest(tmp_path / "ckpt")[0] is not None


# --- data pin, leak fence, registry ---------------------------------------------------------------------------------

def test_tampered_or_unpinned_shards_are_refused(tmp_path):
    m = data.write_toy_corpus(tmp_path)
    npy = tmp_path / "tokens" / "nba" / "2024.npy"
    raw = bytearray(npy.read_bytes())
    raw[-1] ^= 1
    npy.write_bytes(bytes(raw))
    with pytest.raises(ManifestError):
        data.load_pinned_corpus(m, root=tmp_path, vocab_size=data.TOY_VOCAB_SIZE, vocab_version=data.TOY_VOCAB_VERSION)

    class OnlyNpy:                                        # a pin that lists the shard but not its index
        tag, manifest_sha256 = "x", "0" * 64

        def paths(self, suffix=None):
            return [npy]
    with pytest.raises(data.CorpusError, match="not pinned"):
        data.load_corpus(OnlyNpy(), data.TOY_VOCAB_SIZE, data.TOY_VOCAB_VERSION)


def test_vocab_mismatch_is_refused(tmp_path):
    m = data.write_toy_corpus(tmp_path)
    with pytest.raises(data.CorpusError, match="vocab_version"):
        data.load_pinned_corpus(m, root=tmp_path)                 # toy shards are not vocab v1
    with pytest.raises(data.CorpusError, match="vocab_size"):
        data.load_pinned_corpus(m, root=tmp_path, vocab_size=20, vocab_version=data.TOY_VOCAB_VERSION)


@pytest.mark.parametrize("night,side", [("20251215", "train"), ("20261010", "held")])
def test_fenced_nights_are_refused(tmp_path, night, side):
    nights = ["20240101", "20240102", "20240301"] + [night]
    c = testkit.toy_corpus(tmp_path, nights=nights)
    tt, vf = ("20251231", "20260301") if side == "train" else ("20240201", "20240301")
    with pytest.raises(FenceError, match="REFUSED"):
        data.split_walk_forward(c, tt, vf)


def test_walk_forward_split_is_by_whole_night(corpus):
    train, held = data.split_walk_forward(corpus, "20240215", "20240305", "20240308")
    assert max(d.night for d in train) <= "20240215" and {d.night for d in held} == {"20240305", "20240306",
                                                                                     "20240307", "20240308"}
    with pytest.raises(ValueError):
        data.split_walk_forward(corpus, "20240305", "20240301")


def test_data_order_is_a_function_of_seed_and_epoch():
    a = data.epoch_order(1, 0, 50)
    assert np.array_equal(a, data.epoch_order(1, 0, 50)) and not np.array_equal(a, data.epoch_order(1, 1, 50))
    ids, cur = data.take(data.Cursor(), 120, 50, seed=1)
    assert cur == data.Cursor(2, 20)
    assert np.array_equal(ids[:50], a) and np.array_equal(ids[100:], data.epoch_order(1, 2, 50)[:20])
    ids2, cur2 = data.take(data.Cursor(), 70, 50, 1)
    ids3, _ = data.take(cur2, 50, 50, 1)
    assert np.array_equal(np.concatenate([ids2, ids3]), ids)


def test_checkpoints_and_model_carry_the_cutoff_and_registry_record_validates(corpus, tmp_path, monkeypatch):
    from redzone.lab import fence
    cfg = testkit.toy_cfg(total_steps=4, ckpt_every=2, eval_every=2)
    tr = testkit.toy_trainer(corpus, tmp_path, _backend(cfg), cfg)
    tr.run()
    folder, _ = checkpoint.latest(tmp_path / "ckpt")
    st = checkpoint.read_state(folder)
    assert st["meta"]["training_cutoff"] == "2024-02-20" and st["identity"]["training_cutoff"] == "2024-02-20"
    out = tr.export("b03-test-run", "toy")
    meta = fence.read_meta(tmp_path / "model" / "model.meta.json")
    fence.guard_model(meta, ["20240305"])                                   # the fence accepts it
    with pytest.raises(FenceError):
        fence.guard_model(meta, ["20240210"])                                # but not on nights it trained on
    rec = out["registry_record"]
    assert rec["verdict"] == "INCONCLUSIVE" and rec["promoted"] is False and rec["git_sha"] == testkit.TOY_GIT_SHA
    assert rec["artifact_sha256"] == checkpoint.sha256_path(tmp_path / "model" / "model.bin")
    assert rec["training_cutoff"] == "2024-02-20" and "eval_nll_event" in rec["metrics"]


def test_trainer_refuses_eval_nights_it_trained_on(corpus, tmp_path):
    cfg = testkit.toy_cfg()
    train, _ = data.split_walk_forward(corpus, cfg.train_through, cfg.valid_from)
    with pytest.raises(FenceError):
        loop.Trainer(cfg, _backend(cfg), train, train[:2], data_manifest_hash="a" * 64, out_dir=tmp_path,
                     code_hash="c" * 64, git_sha=testkit.TOY_GIT_SHA)


def test_metrics_aggregate_field_nll_and_calibration():
    agg = EvalAggregator(n_fields=2, bins=10)
    nll = np.array([[1.0, 2.0, 3.0, 9.0]])
    agg.add(nll, np.array([[0.95, 0.95, 0.15, 0.5]]), np.array([[1, 1, 0, 1]]), np.array([[1, 1, 1, 0]]),
            np.array([[0, 1, 0, -1]]))
    r = agg.result()
    assert r["tokens"] == 3 and r["nll_token"] == pytest.approx(2.0) and r["acc_top1"] == pytest.approx(2 / 3)
    assert r["nll_field0"] == pytest.approx(2.0) and r["nll_field1"] == pytest.approx(2.0)
    assert r["nll_event"] == pytest.approx(4.0)
    assert r["ece_top1"] == pytest.approx((2 * 0.05 + 0.15) / 3)


def test_job_refuses_a_fenced_corpus_with_exit_code_2(tmp_path):
    m = data.write_toy_corpus(tmp_path / "d", nights=["20240101", "20251220", "20260301"])
    cfg = dict(job.TOY_CONFIG, train_through="20251231", valid_from="20260301")
    (tmp_path / "c.json").write_text(json.dumps(cfg))
    proc = subprocess.run([sys.executable, "-m", "tools.trainer.job", "train", "--config", str(tmp_path / "c.json"),
                           "--data", str(m), "--data-root", str(tmp_path / "d"), "--out", str(tmp_path / "o")],
                          cwd=REPO, capture_output=True, text=True, timeout=120, env={**os.environ})
    assert proc.returncode == job.EXIT_REFUSED, proc.stdout + proc.stderr
    assert "REFUSED" in proc.stdout and not (tmp_path / "o" / "ckpt").exists()
