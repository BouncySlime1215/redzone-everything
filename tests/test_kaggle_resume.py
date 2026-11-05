"""[Colab V8] The student trainer resumes after a disconnect: a training checkpoint, answers that append and
skip what is done, finished jobs skipped, and no reuse of an adapter trained on other data (V7's output).

The helpers run anywhere; the end-to-end part trains a tiny random Qwen3 on CPU (torch, transformers, peft).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.kaggle import run_students as R

ROOT = Path(__file__).resolve().parents[1]


def test_answered_ids_drops_a_torn_last_line(tmp_path):
    f = tmp_path / "test_out.jsonl"
    f.write_text('{"id": "a", "text": "x"}\n{"id": "b", "text": "y"}\n{"id": "c", "te')
    assert R.answered_ids(f) == {"a", "b"}
    assert f.read_text().splitlines() == ['{"id": "a", "text": "x"}', '{"id": "b", "text": "y"}']
    assert R.answered_ids(tmp_path / "missing.jsonl") == set()


def _adapter(d: Path, name: str, sha: str | None):
    d.mkdir(parents=True)
    (d / "adapter_config.json").write_text("{}")
    (d / "adapter_model.safetensors").write_bytes(b"x")
    (d / "recipe.txt").write_text(R.recipe_of(name))
    if sha:
        (d / "train_sha.txt").write_text(sha)
    return d


def test_an_adapter_trained_on_other_data_is_not_reused(tmp_path, monkeypatch):
    """V7's output is attached for its teacher answers: same recipe stamp, other rows, so V8 must retrain."""
    monkeypatch.delenv("RZ_RETRAIN", raising=False)
    v7 = _adapter(tmp_path / "v7" / "students" / "game-reasoner" / "peft", "game-reasoner", None)
    assert R.previous_adapter("game-reasoner", [tmp_path], sha="abc") is None       # no data hash: not ours
    (v7 / "train_sha.txt").write_text("old")
    assert R.previous_adapter("game-reasoner", [tmp_path], sha="abc") is None
    (v7 / "train_sha.txt").write_text("abc")
    assert R.previous_adapter("game-reasoner", [tmp_path], sha="abc") == v7


def test_train_sha_follows_the_rows(tmp_path):
    (tmp_path / "train.jsonl").write_text('{"a": 1}\n')
    a = R.train_sha(tmp_path)
    (tmp_path / "train.jsonl").write_text('{"a": 2}\n')
    assert a and R.train_sha(tmp_path) != a and R.train_sha(tmp_path / "none") is None


def test_job_order_weights_and_done(tmp_path, monkeypatch):
    info = {"game-reasoner": {"train": 30000, "test": 5500}, "watchdog": {"train": 11000, "test": 686}}
    monkeypatch.delenv("RZ_JOB_ORDER", raising=False)
    monkeypatch.delenv("RZ_JOB_WEIGHTS", raising=False)
    assert R.job_order(info) == ["game-reasoner", "watchdog"]
    monkeypatch.setenv("RZ_JOB_ORDER", "watchdog,game-reasoner")
    assert R.job_order(info) == ["watchdog", "game-reasoner"]
    w = R.job_weights(info)
    assert w["game-reasoner"] > w["watchdog"] > 0
    monkeypatch.setenv("RZ_JOB_WEIGHTS", "watchdog:1,game-reasoner:5")
    assert R.job_weights(info) == {"watchdog": 1.0, "game-reasoner": 5.0}
    d = tmp_path / "watchdog"
    d.mkdir()
    assert not R.job_done(d)
    (d / "train_report.json").write_text(json.dumps({"complete": False}))
    assert not R.job_done(d)
    (d / "train_report.json").write_text(json.dumps({"complete": True}))
    assert R.job_done(d)


def test_colab_notebook_cells_compile_and_pin():
    import ast
    nb = json.loads((ROOT / "agent/kaggle/colab_v8_students.ipynb").read_text())
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code) >= 4 and nb["metadata"]["accelerator"] == "GPU"
    for c in code:
        ast.parse("".join(c["source"]))
    src = "".join("".join(c["source"]) for c in code)
    for need in ("RZ_CKPT_S", "RZ_MODE", "RZ_PREV_ROOTS", "codeload.github.com", "flush_and_unmount"):
        assert need in src, need
    assert "token" not in src.lower() and "secret" not in src.lower()        # nothing to paste, nothing to leak


# ------------------------------------------------------------------ end to end on CPU (tiny model)
def _job(tmp_path: Path) -> tuple[Path, Path, dict]:
    for mod in ("torch", "transformers", "peft", "tokenizers", "safetensors"):
        pytest.importorskip(mod)
    from tests.test_kaggle_e2e import tiny_base
    out = tmp_path / "students"
    jd = out / "data" / "watchdog"
    jd.mkdir(parents=True)
    chat = lambda i: {"messages": [{"role": "system", "content": "watchdog"},  # noqa: E731
                                   {"role": "user", "content": f"stream froze {i}"},
                                   {"role": "assistant", "content": '{"action": "none"}'}]}
    (jd / "train.jsonl").write_text("".join(json.dumps(chat(i)) + "\n" for i in range(64)))
    (jd / "valid.jsonl").write_text("".join(json.dumps(chat(i)) + "\n" for i in range(4)))
    (jd / "test.jsonl").write_text("".join(json.dumps({"id": f"wd-test-{i}", "task": "watchdog",
                                                       "messages": chat(i)["messages"][:2]}) + "\n" for i in range(6)))
    env = {**os.environ, "RZ_SMOKE": "1", "RZ_BASE_DIR": str(tiny_base(tmp_path / "base")), "CUDA_VISIBLE_DEVICES": "",
           "RZ_CONSTRAINED": "0", "RZ_CKPT_S": "100000", "RZ_REUSE_BASE": "", "PYTHONPATH": str(ROOT)}
    env.pop("RZ_DEADLINE", None)
    return out, jd, env


def _run(out: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "agent/kaggle/run_students.py"), "--pack", str(out),
                           "--out", str(out), "--job", "watchdog"], env=env, capture_output=True, text=True,
                          timeout=900)


def test_training_and_answers_resume_after_a_disconnect(tmp_path):
    out, jd, env = _job(tmp_path)
    # session 1: the budget ends training at the first step; FINISH_TRAINING checkpoints and stops
    p = _run(out, {**env, "RZ_FINISH_TRAINING": "1", "RZ_DEADLINE": str(time.time())})
    assert p.returncode == R.PAUSED, (p.stdout + p.stderr)[-3000:]
    ck = out / "watchdog" / "ckpt" / "state.pt"
    assert ck.exists() and not (out / "watchdog" / "peft").exists()
    # session 2: resumes from the checkpoint and finishes
    p = _run(out, env)
    log = p.stdout + p.stderr
    assert p.returncode == 0, log[-3000:]
    assert "resumed from the checkpoint at step 1/" in log, log[-3000:]
    rep = json.loads((out / "watchdog" / "train_report.json").read_text())
    assert rep["complete"] and rep["ckpt_resumes"] == 1 and rep["steps"] == 8 and not ck.exists()
    assert (out / "watchdog" / "peft" / "train_sha.txt").read_text() == R.train_sha(jd)
    # session 3: a disconnect mid-answers (2 answers kept + a torn line); the rerun reuses its own adapter
    f = out / "watchdog" / "test_out.jsonl"
    lines = f.read_text().splitlines()
    assert len(lines) == 6
    f.write_text("\n".join(lines[:2]) + "\n" + lines[2][:10])
    (out / "watchdog" / "train_report.json").unlink()
    p = _run(out, env)
    log = p.stdout + p.stderr
    assert p.returncode == 0, log[-3000:]
    assert "reusing the trained adapter" in log and "2 answers already in test_out.jsonl" in log, log[-3000:]
    ids = [json.loads(x)["id"] for x in f.read_text().splitlines()]
    assert sorted(ids) == sorted(f"wd-test-{i}" for i in range(6))
    rep = json.loads((out / "watchdog" / "train_report.json").read_text())
    assert rep["complete"] and rep["steps"] == 8 and rep["answered"]["student"]["answered"] == 6


def test_watchdog_v81_rows_are_v7_plus_counterfactuals_and_opt_in(tmp_path, monkeypatch):
    """[EXP-101] RZ_WATCHDOG_TRAIN=v81 swaps in V7's rows + counterfactual rows; valid/test stay the pack's."""
    import gzip

    from agent.kaggle import student_data as S
    v7 = [json.loads(x) for x in gzip.open(S.WATCHDOG_TRAIN_V7 / "train.jsonl.gz", "rt")]
    v81 = [json.loads(x) for x in gzip.open(S.WATCHDOG_TRAIN_V81 / "train.jsonl.gz", "rt")]
    assert v81[:len(v7)] == v7 and len(v81) - len(v7) >= 2000
    d = tmp_path / "data" / "watchdog"
    d.mkdir(parents=True)
    row = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                        {"role": "assistant", "content": "{}"}]}
    for f in ("train.jsonl", "valid.jsonl", "test.jsonl"):
        (d / f).write_text(json.dumps(row) + "\n")
    monkeypatch.delenv("RZ_WATCHDOG_TRAIN", raising=False)
    assert len(S.watchdog_job(tmp_path)["train"]) == len(v7)
    monkeypatch.setenv("RZ_WATCHDOG_TRAIN", "v81")
    j = S.watchdog_job(tmp_path)
    assert len(j["train"]) == len(v81) and len(j["valid"]) == 1 and len(j["test"]) == 1
