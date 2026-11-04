"""V6 student-phase fixes: full test set every round, heartbeat (no torch needed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.kaggle import grade, run_students as R  # noqa: E402


def test_answers_the_full_test_set():
    """Nick, Nov 4 3:00 AM: every round grades the FULL test set, never a sample."""
    src = (Path(R.__file__)).read_text()
    assert "answer_sample" not in src and "RZ_ANSWER_N" not in src
    assert not hasattr(grade, "on_sample")


def test_heartbeat_writes_atomically(tmp_path):
    R.heartbeat(tmp_path, phase="train", step=50)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["phase"] == "train" and hb["step"] == 50 and hb["ts"] > 0


def _adapter(root, name, weights=True):
    d = root / "v5-output" / "students" / name / "peft"
    d.mkdir(parents=True)
    (d / "adapter_config.json").write_text("{}")
    if weights:
        (d / "adapter_model.safetensors").write_bytes(b"x")
    return d


def test_reuses_a_trained_adapter_from_the_attached_output(tmp_path, monkeypatch):
    """Nick's V5 fix: a crash after training (the V5 game-reasoner OOM) never costs the training again."""
    monkeypatch.delenv("RZ_RETRAIN", raising=False)
    d = _adapter(tmp_path, "game-reasoner")
    _adapter(tmp_path / "other", "watchdog", weights=False)          # no weights: not reusable
    assert R.previous_adapter("game-reasoner", [tmp_path]) == d
    assert R.previous_adapter("watchdog", [tmp_path]) is None
    assert R.previous_adapter("game-reasoner", [tmp_path / "missing"]) is None
    monkeypatch.setenv("RZ_RETRAIN", "1")
    assert R.previous_adapter("game-reasoner", [tmp_path]) is None


def test_answers_in_small_fp16_batches():
    assert R.ANSWER_BATCH == 8 and R.ANSWER_LOG_ROWS == 400
