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
