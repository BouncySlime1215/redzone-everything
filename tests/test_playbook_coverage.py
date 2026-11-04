"""scripts/playbook_coverage.py and the RAM/CPU line in scripts/ci_report.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ci_report  # noqa: E402
import playbook_coverage as pc  # noqa: E402

MD = """| id | trigger | action | fallback | test | line | lane | status |
|---|---|---|---|---|---|---|---|
| SAFE-01 | a | b | c | d | mid_play | 2 | done |
| SAFE-02 | a | b | c | d | mid_play | 2 | new |
"""

TEST = '''import pytest

@pytest.mark.playbook("SAFE-01")
def test_holds_on_stale_feed():
    pass
'''

JUNIT = """<testsuites><testsuite>
<testcase classname="tests.test_pb" name="test_holds_on_stale_feed" time="0.1"/>
</testsuite></testsuites>"""


def _tree(tmp_path, test=TEST, junit=JUNIT):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/playbooks.md").write_text(MD)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_pb.py").write_text(test)
    (tmp_path / "j.xml").write_text(junit)
    return tmp_path


def test_done_entry_with_a_passing_test_is_covered(tmp_path):
    t = _tree(tmp_path)
    rep = pc.check(t / "docs/playbooks.md", t / "tests", t / "j.xml")
    assert rep["errors"] == [] and rep["covered"] == 1 and rep["entries"] == 2
    assert "1/2" in pc.to_md(rep)


def test_done_entry_whose_test_was_skipped_fails(tmp_path):
    t = _tree(tmp_path, junit=JUNIT.replace('time="0.1"/>', 'time="0"><skipped/></testcase>'))
    rep = pc.check(t / "docs/playbooks.md", t / "tests", t / "j.xml")
    assert rep["errors"] and "SAFE-01" in rep["errors"][0]


def test_done_entry_without_a_test_and_unknown_ids_fail(tmp_path):
    t = _tree(tmp_path, test=TEST.replace("SAFE-01", "SAFE-99"))
    rep = pc.check(t / "docs/playbooks.md", t / "tests")
    assert any("SAFE-99" in e for e in rep["errors"]) and any("SAFE-01 is done" in e for e in rep["errors"])
    assert pc.main(["--root", str(t)]) == 1


def test_the_real_playbook_parses_and_passes():
    rep = pc.check(ROOT / "docs/playbooks.md", ROOT / "tests")
    assert rep["entries"] >= 50 and rep["errors"] == []


def test_ram_cpu_line_shows_pr_and_base():
    line = ci_report.perf_line({"games": 8, "ticks": 300, "tick_ms_p50": 1.0, "tick_ms_p99": 5.3,
                                "peak_rss_mb": 55.8, "cpu_s": 1.2, "startup_s": 0.4},
                               {"tick_ms_p50": 0.9, "peak_rss_mb": 54.0})
    assert line.startswith("**RAM/CPU (8 live games, 300 decide ticks):**")
    assert "tick p50 1.0 ms (base 0.9)" in line and "peak RSS 55.8 MB (base 54.0)" in line and "p99 5.3 ms," in line


def test_perf_probe_runs_the_real_tick():
    import asyncio

    import perf_probe
    out = asyncio.run(perf_probe.probe(ROOT, 4, 20))
    assert out["ticks"] == 20 and out["tick_ms_p50"] > 0 and out["peak_rss_mb"] > 0
