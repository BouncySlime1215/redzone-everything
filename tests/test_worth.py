"""EXP-040: the "worth it?" rating is an evaluation line, never a training signal."""
import json
from pathlib import Path

from redzone.lab import worth

ROOT = Path(__file__).resolve().parents[1]


def test_load_keeps_last_valid_row_per_night(tmp_path):
    p = tmp_path / "worth.jsonl"
    p.write_text("\n".join([json.dumps({"night": "2026-11-08", "rating": 2}), "not json",
                            json.dumps({"night": "2026-11-08", "rating": 4}),
                            json.dumps({"night": "2026-11-09", "rating": 9}),
                            json.dumps({"night": "2026-11-10", "agency": 3})]))
    rows = worth.load(p)
    assert list(rows) == ["2026-11-08"] and rows["2026-11-08"]["rating"] == 4
    assert worth.load(tmp_path / "missing.jsonl") == {}


def test_compare_pass_fail_pending():
    assert worth.compare([4, 4, 5], [4, 4, 5])["status"] == "PENDING"            # too few nights
    assert worth.compare([], [4])["status"] == "PENDING"
    assert worth.compare([4, 4, 4, 4, 4, 4], [4, 4, 5, 4, 4, 5])["status"] == "PASS"
    assert worth.compare([4, 4, 4, 4, 5], [4, 5, 4, 4, 5])["status"] == "PENDING"      # 5 nights: interval still wide
    assert worth.compare([5, 5, 5, 5, 5], [2, 2, 1, 2, 2])["status"] == "FAIL"
    noisy = worth.compare([5, 1, 5, 1], [4, 2, 5, 1])
    assert noisy["status"] == "PENDING" and noisy["lo"] < -worth.MARGIN


def test_warning_rule_watch_up_rating_down():
    line = worth.compare([4, 4, 4, 4], [3, 3, 4, 3])
    assert worth.warning(5.0, line) is True
    assert worth.warning(-2.0, line) is False
    assert worth.warning(None, line) is False


def test_never_a_training_input():
    # Nick's ratings grade releases; no training, distillation or decision code may read them
    for d in ("redzone/noggin", "redzone/decider", "redzone/scoring"):
        for f in (ROOT / d).rglob("*.py"):
            text = f.read_text()
            assert "worth.jsonl" not in text and "lab.worth" not in text and "WORTH_LOG" not in text, f
