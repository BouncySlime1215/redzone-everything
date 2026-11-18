"""scripts/ci_shard.py: the CI shards run every test file exactly once, the same way on every run, and the
gather step catches a gap or a double. CI-FIX (Nov 10)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_shard  # noqa: E402


def real_suite():
    """The repo's own test files with a rough test count each (no import, no collection)."""
    files = sorted(p for p in (ROOT / "tests").rglob("test_*.py") if "fixtures" not in p.parts)
    return {str(p.relative_to(ROOT)): max(1, p.read_text(errors="ignore").count("def test_")) for p in files}


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7])
def test_every_file_lands_in_exactly_one_shard(n):
    counts = real_suite()
    shards = ci_shard.assign(counts, n, ci_shard.load_weights())
    flat = [f for s in shards for f in s]
    assert len(shards) == n
    assert sorted(flat) == sorted(counts)          # nothing missing
    assert len(flat) == len(set(flat))             # nothing doubled


def test_the_split_is_deterministic_and_ignores_input_order():
    counts = real_suite()
    w = ci_shard.load_weights()
    a = ci_shard.assign(counts, 4, w)
    b = ci_shard.assign(dict(reversed(list(counts.items()))), 4, w)
    assert a == b


def test_a_new_file_moves_into_one_shard_and_the_heavy_file_is_spread():
    counts = {f"tests/test_{i:03d}.py": 10 for i in range(40)} | {"tests/test_slow.py": 1}
    shards = ci_shard.assign(counts, 4, {"tests/test_slow.py": 500.0})
    slow = [s for s in shards if "tests/test_slow.py" in s]
    assert len(slow) == 1 and len(slow[0]) < max(len(s) for s in shards)   # the slow file gets a lighter share
    grown = ci_shard.assign(counts | {"tests/test_new.py": 3}, 4, {"tests/test_slow.py": 500.0})
    assert sum("tests/test_new.py" in s for s in grown) == 1


def test_shard_spec_is_one_based_and_checked():
    assert ci_shard.parse_shard("1/4") == (1, 4)
    for bad in ("0/4", "5/4", "x/4"):
        with pytest.raises(ValueError):
            ci_shard.parse_shard(bad)
    with pytest.raises(ValueError):
        ci_shard.assign({"a": 1}, 0)


def _reports(counts, n):
    every = sorted(counts)
    return [{"shard": i + 1, "count": n, "all": every, "mine": s, "tests": 0}
            for i, s in enumerate(ci_shard.assign(counts, n))]


def test_check_accepts_a_true_partition_and_names_each_kind_of_break():
    counts = {f"tests/test_{c}.py": 1 for c in "abcdef"}
    good = _reports(counts, 3)
    assert ci_shard.check(good, 3) == []

    missing = [dict(r) for r in good]
    missing[0]["mine"] = missing[0]["mine"][1:]
    assert any("in no shard" in e for e in ci_shard.check(missing, 3))

    doubled = [dict(r) for r in good]
    doubled[1]["mine"] = doubled[1]["mine"] + doubled[0]["mine"][:1]
    assert any("more than one shard" in e for e in ci_shard.check(doubled, 3))

    assert any("one report per shard" in e for e in ci_shard.check(good[:2], 3))

    other = [dict(r) for r in good]
    other[2]["all"] = other[2]["all"] + ["tests/test_z.py"]
    assert any("different suites" in e for e in ci_shard.check(other, 3))


def test_run_keeps_only_its_shard_and_check_proves_the_union(tmp_path):
    """End to end on a throwaway suite: two shards, real pytest, then `check` over both reports."""
    suite = tmp_path / "suite"
    (suite / "tests").mkdir(parents=True)
    for name in ("a", "b", "c", "d", "e"):
        (suite / "tests" / f"test_{name}.py").write_text("def test_one():\n    pass\n\ndef test_two():\n    pass\n")
    reports = []
    for i in (1, 2):
        rep = tmp_path / f"shard-{i}.json"
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci_shard.py"), "run", "--shard", f"{i}/2",
                            "--report", str(rep), "--", "-q", "-p", "no:cacheprovider", "tests"],
                           cwd=suite, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        reports.append(rep)
        assert len(json.loads(rep.read_text())["all"]) == 5
    mine = [set(json.loads(p.read_text())["mine"]) for p in reports]
    assert mine[0] and mine[1] and not (mine[0] & mine[1]) and len(mine[0] | mine[1]) == 5
    ok = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci_shard.py"), "check", "--count", "2",
                         *map(str, reports)], capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0, ok.stdout
    short = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci_shard.py"), "check", "--count", "3",
                            *map(str, reports)], capture_output=True, text=True, timeout=60)
    assert short.returncode == 1


def test_weights_from_junit_sum_per_file(tmp_path):
    xml = tmp_path / "j.xml"
    xml.write_text('<testsuites><testsuite>'
                   '<testcase classname="tests.test_ci_shard" name="a" time="1.5"/>'
                   '<testcase classname="tests.test_ci_shard" name="b" time="2.0"/>'
                   '<testcase classname="tests.no_such_file" name="c" time="9"/>'
                   '</testsuite></testsuites>')
    assert ci_shard.junit_weights([str(xml)]) == {"tests/test_ci_shard.py": 3.5}
