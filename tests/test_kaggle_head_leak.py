"""V7's NBA head scored AUC 0.999: the label (crunch/big plays) is defined by clock + margin, so the head
just learned the definition. context_baseline must flag that, and grades.json must carry the flag."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

lgb = pytest.importorskip("lightgbm")
from agent.kaggle import build_moments_kaggle as B  # noqa: E402
from agent.kaggle.grade import short_grades  # noqa: E402
from train_reasoner import auc  # noqa: E402

FEATS = ["period", "clock_s", "abs_diff", "noise"]
PARAMS = dict(objective="binary", learning_rate=0.1, num_leaves=7, min_data_in_leaf=20, verbose=-1, seed=1)


def _data(label_from_clock: bool, n=6000, seed=0):
    rng = np.random.default_rng(seed)
    per = rng.integers(1, 5, n).astype(np.float32)
    clk = rng.uniform(0, 720, n).astype(np.float32)
    d = rng.integers(0, 25, n).astype(np.float32)
    z = rng.normal(size=n).astype(np.float32)
    if label_from_clock:
        y = ((per == 4) & (clk <= 120) & (d <= 5)).astype(np.float32)
    else:
        y = (z + 0.3 * rng.normal(size=n) > 1.2).astype(np.float32)
    sp = np.where(np.arange(n) < n * 0.7, "train", "test")
    return np.stack([per, clk, d, z], 1), y, sp


def test_clock_defined_label_is_flagged():
    X, y, sp = _data(True)
    r = B.context_baseline("nba", X, y, sp, FEATS, PARAMS, 50, auc)
    assert r["auc_test_context_only"] > 0.98 and r["leak_suspect"] is True
    assert abs(r["auc_lift_over_context"]) < 0.01


def test_real_signal_is_not_flagged():
    X, y, sp = _data(False)
    r = B.context_baseline("nba", X, y, sp, FEATS, PARAMS, 50, auc)
    assert r["auc_lift_over_context"] > 0.2 and r["leak_suspect"] is False


def test_short_grades_reads_report():
    rep = {"reasoner": {"gate": {"student": [], "head": ["lock_acc 80 < 85"]},
                        "student": {"all": {"n": 10, "lock_acc": 90.0, "unsafe_leave_pct": 0.0}}},
           "watchdog": {"student": {"n": 5, "accuracy": 96.0, "gate": "PASS", "by_kind": {}}},
           "build": {"head": {"nba": {"base_rate": 0.033, "auc_test": 0.999, "leak_suspect": True, "rows": 9}}}}
    g = short_grades(rep, "r1", "full", 6.5)
    assert g["reasoner_gate"] == {"student": "PASS", "head": "FAIL: lock_acc 80 < 85"}
    assert g["reasoner_all"]["student"]["lock_acc"] == 90.0
    assert g["watchdog"]["student"] == {"n": 5, "accuracy": 96.0, "gate": "PASS"}
    assert g["head"]["nba"]["leak_suspect"] is True and "rows" not in g["head"]["nba"]
