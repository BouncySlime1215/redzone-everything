"""The proof kernel (scripts/contract_check.py): each rule fires on its fixture and not on the clean
twin, the baseline ignores line shifts, only NEW violations fail, and K7 can only warn."""
import json
import re
import shutil
from pathlib import Path

import pytest

from scripts import contract_check as C

FIXTURES = Path(__file__).parent / "fixtures" / "contract_check"
SUMMARY = re.compile(r"^contract violations: \d+ \(baseline \d+\)$")


def tree(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root)
    return root


def run(root: Path, capsys, *args: str) -> tuple[int, str]:
    code = C.main(["--root", str(root), *args])
    return code, capsys.readouterr().out


def rules(root: Path) -> list[tuple[str, str]]:
    found, _ = C.check(root)
    return [(v.rule, v.key) for v in found if not v.warn]


@pytest.mark.parametrize("rule, keys", [
    ("k1", [("K1", "LONG_BREAKS"), ("K1", "LONG_BREAKS")]),
    ("k2", [("K2", "Shadow.FLIP_S"), ("K2", "sleep@wait")]),
    ("k3", [("K3", "flip")]),
    ("k4", [("K4", "rank")]),
    ("k5", [("K5", "redzone.lonely")]),
    ("k6", [("K6", "redzone.lab.timing")]),
])
def test_each_rule_fires_and_its_clean_twin_does_not(tmp_path, rule, keys):
    assert rules(tree(tmp_path, f"{rule}_hit")) == keys
    assert rules(tree(tmp_path, f"{rule}_clean")) == []


def test_identical_function_bodies_are_duplicates(tmp_path):
    root = tree(tmp_path, "k1_clean")
    body = "def _clock(p):\n    c = p.get('clock')\n    return c\n"
    (root / "redzone/a.py").write_text(body)
    (root / "redzone/b.py").write_text(body)
    assert rules(root) == [("K1", "_clock"), ("K1", "_clock")]


def test_baseline_identity_ignores_line_shifts(tmp_path, capsys):
    root = tree(tmp_path, "k3_hit")
    assert run(root, capsys, "--write-baseline")[0] == 0
    base = json.loads((root / C.BASELINE).read_text())
    assert base["version"] == 1 and base["generated_from"] == "integration/next"
    assert base["violations"] == [{"rule": "K3", "path": "redzone/control.py", "key": "flip"}]
    f = root / "redzone/control.py"
    f.write_text("\n\n\n# moved down\n" + f.read_text())
    code, out = run(root, capsys)
    assert code == 0
    assert "NEW" not in out
    assert out.splitlines()[-1] == "contract violations: 1 (baseline 1)"


def test_new_violation_fails_and_is_listed(tmp_path, capsys):
    root = tree(tmp_path, "k3_hit")
    run(root, capsys, "--write-baseline")
    (root / "redzone/remote.py").write_text("def home(stick):\n    stick.shell('input keyevent 3')\n")
    (root / "redzone/__main__.py").write_text("from redzone import control, remote  # noqa: F401\n")
    code, out = run(root, capsys)
    assert code == 1
    assert "redzone/remote.py:2 K3 device-io [home] .shell(" in out
    assert out.splitlines()[-1] == "contract violations: 2 (baseline 1)"


def test_fixed_violation_is_reported_and_passes(tmp_path, capsys):
    root = tree(tmp_path, "k3_hit")
    run(root, capsys, "--write-baseline")
    (root / "redzone/control.py").write_text("def flip(tuner):\n    return tuner.home()\n")
    code, out = run(root, capsys)
    assert code == 0
    assert "FIXED: 1" in out
    assert out.splitlines()[-1] == "contract violations: 0 (baseline 1)"


def test_no_baseline_means_everything_is_new(tmp_path, capsys):
    code, out = run(tree(tmp_path, "k4_hit"), capsys)
    assert code == 1
    assert out.splitlines()[-1] == "contract violations: 1 (baseline 0)"


def test_untagged_is_warn_only(tmp_path, capsys):
    root = tree(tmp_path, "k5_clean")
    code, out = run(root, capsys)
    assert code == 0
    assert "untagged: skipped (docs/citymap.json not found)" in out
    (root / "docs").mkdir()
    (root / C.CITYMAP).write_text(json.dumps(
        {"modules": [{"path": "redzone/lonely.py", "serves": ["untagged"]},
                     {"path": "redzone/__init__.py", "serves": ["N1"]}]}))
    code, out = run(root, capsys)
    assert code == 0
    assert "WARN redzone/lonely.py K7 untagged" in out
    assert "redzone/__init__.py" not in out
    assert out.splitlines()[-1] == "contract violations: 0 (baseline 0)"
    run(root, capsys, "--write-baseline")   # warnings never enter the baseline
    assert json.loads((root / C.BASELINE).read_text())["violations"] == []


def test_summary_line_and_output_are_deterministic(tmp_path, capsys):
    root = tree(tmp_path, "k2_hit")
    first = run(root, capsys)[1]
    second = run(root, capsys)[1]
    assert first == second
    assert SUMMARY.match(first.splitlines()[-1])
    assert run(root, capsys, "--json")[1] == run(root, capsys, "--json")[1]


def test_json_output(tmp_path, capsys):
    code, out = run(tree(tmp_path, "k6_hit"), capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["total"] == 1 and data["baseline"] == 0
    assert data["new"][0] == {"rule": "K6", "path": "redzone/engine.py", "key": "redzone.lab.timing",
                              "line": 1, "detail": "imports redzone.lab.timing", "warn": False}


def test_real_repo_matches_committed_baseline(capsys):
    code = C.main([])
    out = capsys.readouterr().out
    assert code == 0, out
    assert SUMMARY.match(out.splitlines()[-1])
