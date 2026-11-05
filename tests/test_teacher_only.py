"""[EXP-086/102] Teacher-only round: the data gate, TRAIN-night-only queue, and grades against real outcomes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.kaggle import teacher_only as TO  # noqa: E402


def test_espn_builder_needs_plan_b_and_green_is_not_built():
    with pytest.raises(TO.DataGate, match="red"):
        TO.gate("", "espn")
    with pytest.raises(TO.DataGate, match="red"):
        TO.gate("A", "espn")
    TO.gate("B", "espn")
    with pytest.raises(TO.DataGate, match="green moment builder"):
        TO.gate("A", "green")


def test_refuses_to_run_without_plan_b(tmp_path):
    p = subprocess.run([sys.executable, str(ROOT / "agent/kaggle/teacher_only.py"), "--out", str(tmp_path)],
                       env={**os.environ, "RZ_DATA_PLAN": "", "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
    assert p.returncode != 0 and "red" in (p.stdout + p.stderr) and not (tmp_path / "moments").exists()


def test_end_to_end_on_fixture_archive(tmp_path):
    from tests.test_kaggle_e2e import archive
    env = {**os.environ, "RZ_DATA_PLAN": "B", "RZ_FAKE_TEACHER": "1", "PYTHONPATH": str(ROOT),
           "RZ_BUILD_ARCHIVE": str(archive(tmp_path / "archive"))}
    p = subprocess.run([sys.executable, str(ROOT / "agent/kaggle/teacher_only.py"), "--out", str(tmp_path / "t"),
                        "--leagues", "nfl", "nhl", "--budget-h", "0.2", "--build-h", "0.2"], env=env,
                       capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, (p.stdout + p.stderr)[-3000:]
    g = json.loads((tmp_path / "t" / "grades.json").read_text())
    plan = json.loads((tmp_path / "t" / "moments" / "split_plan.json").read_text())
    for lg, v in g["per_league"].items():
        assert v["asked"] > 0 and v["answered_pct"] == 100.0 and v["valid_json_pct"] == 100.0, (lg, v)
        assert v.get("blind_n_teacher"), (lg, v)
    for r in (json.loads(x) for x in open(tmp_path / "t" / "teacher_requests.jsonl")):
        lg, gid, _ = r["id"].split("|")[0].split(":")
        rows = TO.read_rows(tmp_path / "t" / "moments", lg, "train")
        night = next(x["night"] for x in rows if x["game_id"] == gid)
        assert plan[lg][str(night)] == "train"                         # only train nights are ever asked


def test_colab_teacher_notebook_is_gated_and_self_contained(tmp_path):
    import ast

    from agent.kaggle.build_notebook import build
    src = (ROOT / "agent/kaggle/colab_teacher.py").read_text().replace("__PIN__", "0" * 40)
    (tmp_path / "colab_teacher.py").write_text(src)
    build(tmp_path / "colab_teacher.py", tmp_path / "nb.ipynb", "A100")
    nb = json.loads((tmp_path / "nb.ipynb").read_text())
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    ast.parse(code)
    assert nb["metadata"]["colab"]["gpuType"] == "A100"
    assert 'DATA_PLAN = ""' in code and "codeload.github.com" in code and "drive" not in code.lower()
    assert "token" not in code.lower() and "secret" not in code.lower()
