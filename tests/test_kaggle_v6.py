"""V6 student-phase fixes: stratified answer sample, grading on the sample, heartbeat (no torch needed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.kaggle import grade, run_students as R  # noqa: E402


def _rows():
    rows = [{"id": f"nfl:{i}:0", "task": "reasoner_blind"} for i in range(3000)]
    rows += [{"id": f"nhl:{i}:0", "task": "reasoner_blind"} for i in range(1000)]
    rows += [{"id": f"nba:{i}:0|x", "task": "jump_blind"} for i in range(40)]
    rows += [{"id": f"wcbb:{i}:0", "task": "reasoner_blind_valid"} for i in range(2)]
    return rows


def test_sample_is_stratified_deterministic_and_keeps_every_stratum():
    rows = _rows()
    a, b = R.answer_sample(rows, 1500), R.answer_sample(list(rows), 1500)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert 1480 <= len(a) <= 1520
    by = lambda rs, p: sum(r["id"].startswith(p) for r in rs)  # noqa: E731
    assert abs(by(a, "nfl:") / len(a) - 0.74) < 0.02 and by(a, "wcbb:") >= 1 and by(a, "nba:") >= 1
    assert R.answer_sample(rows[:100], 1500) == rows[:100]


def test_grade_filters_test_rows_to_the_sample(tmp_path):
    p = tmp_path / "answer_sample.json"
    assert grade.sampled_ids(p) is None
    p.write_text(json.dumps({"mode": "stratified sample", "ids": ["nfl:1:0", "nba:2:0|jump"]}))
    keep = grade.sampled_ids(p)
    rows = [{"id": "nfl:1:0", "split": "test"}, {"id": "nfl:9:0", "split": "test"},
            {"id": "nba:2:0", "split": "test"}, {"id": "nfl:5:0", "split": "valid"}]
    assert [r["id"] for r in grade.on_sample(rows, keep)] == ["nfl:1:0", "nba:2:0", "nfl:5:0"]
    p.write_text(json.dumps({"mode": "all", "ids": []}))
    assert grade.sampled_ids(p) is None


def test_heartbeat_writes_atomically(tmp_path):
    R.heartbeat(tmp_path, phase="train", step=50)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["phase"] == "train" and hb["step"] == 50 and hb["ts"] > 0
