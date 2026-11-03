"""CI helpers: the scorecard smoke runner and the PR-comment report (scripts/ci_*.py)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ci_report  # noqa: E402
import ci_scorecard  # noqa: E402


def test_last_json_skips_the_demo_timeline():
    text = "Replay of 20261101: 9 games\n 13:00 switch {\"x\": 1}\n{\n \"mid_play_cuts\": 0,\n \"a\": {\"b\": 2}\n}\n"
    assert ci_scorecard.last_json(text) == {"mid_play_cuts": 0, "a": {"b": 2}}
    with pytest.raises(ValueError):
        ci_scorecard.last_json("Traceback ...\nboom")


@pytest.fixture(autouse=True)
def _no_ci_env(monkeypatch):
    """CI sets these for the real run (RZ_SCORECARD_FULL=1 on integration/next): the tests pin
    their own sizes."""
    for k in ("RZ_SCORECARD_FULL", "RZ_SMOKE_NFL_N", "RZ_SMOKE_NIGHTS", "RZ_SMOKE_DEMO_DAY", "RZ_SCORECARD_RESYNC"):
        monkeypatch.delenv(k, raising=False)


def _fake_root(tmp_path, nfl_body: str):
    """A checkout whose scorecards are stand-ins, with the data folders they need."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/eval.py").write_text(nfl_body)
    (tmp_path / "scripts/eval_sports.py").write_text(
        "import json, sys\nprint(json.dumps({'league': sys.argv[1], 'mid_play_cuts': 0,"
        " 'nights': int(sys.argv[3])}, indent=1))\n")
    for lg in ("nfl", "nba"):          # NHL data deliberately missing
        (tmp_path / "data/archive" / lg).mkdir(parents=True)
    return tmp_path


def test_scorecard_runner_records_each_card_and_survives_a_crash(tmp_path):
    root = _fake_root(tmp_path, "raise SystemExit('eval_config rejected')\n")
    out = tmp_path / "out.json"
    assert ci_scorecard.main(["--root", str(root), "--out", str(out), "--demo-day", "", "--nights", "2"]) == 0
    res = json.loads(out.read_text())
    assert set(res["cards"]) == {"nfl", "nba", "nhl", "nfl_resync", "nba_resync", "nhl_resync"}
    assert "eval_config rejected" in res["cards"]["nfl"]["error"]
    assert res["cards"]["nba"]["metrics"] == {"league": "nba", "mid_play_cuts": 0, "nights": 2}
    assert res["cards"]["nhl"]["error"].startswith(ci_report.MISSING)


def test_full_mode_uses_the_merge_scorecard_sizes(tmp_path):
    root = _fake_root(tmp_path, "import json, sys\nprint(json.dumps({'n': sys.argv[2]}, indent=1))\n")
    out = tmp_path / "out.json"
    ci_scorecard.main(["--root", str(root), "--out", str(out), "--demo-day", "", "--full"])
    res = json.loads(out.read_text())
    assert res["slice"]["full"] and res["cards"]["nfl"]["metrics"] == {"n": "16"}
    assert res["cards"]["nba"]["metrics"]["nights"] == 14


def test_resync_cards_run_the_same_slice_with_the_flag_and_can_be_turned_off(tmp_path):
    root = _fake_root(tmp_path, "import json, sys\nprint(json.dumps({'argv': sys.argv[1:]}, indent=1))\n")
    out = tmp_path / "out.json"
    ci_scorecard.main(["--root", str(root), "--out", str(out), "--demo-day", ""])
    res = json.loads(out.read_text())
    assert res["cards"]["nfl_resync"]["metrics"]["argv"] == res["cards"]["nfl"]["metrics"]["argv"] + ["--resync"]
    ci_scorecard.main(["--root", str(root), "--out", str(out), "--demo-day", "", "--no-resync"])
    assert set(json.loads(out.read_text())["cards"]) == {"nfl", "nba", "nhl"}
    assert ci_report.card_kind("nhl_resync") == "nhl" and ci_report.card_kind("demo_20261101") == "demo"


def test_scalar_reads_demo_fraction_strings():
    assert ci_report.scalar("3/4 (75%)") == 75.0
    assert ci_report.scalar("0/0 (0%)") == 0.0
    assert ci_report.scalar(7) == 7.0 and ci_report.scalar("no game that day") is None
    assert ci_report.pick({"nfl_big_plays": {"seen": "9/10 (90%)"}}, "nfl_big_plays.seen") == 90.0


def _res(cards):
    return {"slice": {"nfl_n": 4, "nights": 3, "demo_day": ""}, "cards": cards}


def test_report_flags_added_cuts_and_regressions(tmp_path):
    base = _res({"nfl": {"metrics": {"mid_play_cuts": 0, "big_seen_pct": 95.4, "crunch_live_pct": 70.0},
                         "seconds": 5}})
    head = _res({"nfl": {"metrics": {"mid_play_cuts": 1, "big_seen_pct": 93.0, "crunch_live_pct": 71.4},
                         "seconds": 5}})
    lines, hard = ci_report.scorecard_section(head, base)
    text = "\n".join(lines)
    assert hard == ["nfl: 1 mid-play cuts (base 0)"]
    assert "| big plays seen % | 95.4 | 93 | ▼ 2.4 ⚠️ |" in text
    assert "| crunch live % | 70 | 71.4 | ▲ 1.4 ✅ |" in text


def test_report_cuts_without_a_base_and_crashes_are_hard_but_missing_data_is_not():
    _, hard = ci_report.scorecard_section(_res({"nba": {"metrics": {"mid_play_cuts": 2}}}), None)
    assert hard == ["nba: 2 mid-play cuts (base –)"]
    _, hard = ci_report.scorecard_section(_res({"nhl": {"error": "exit 1: Traceback"}}), None)
    assert hard and "scorecard failed" in hard[0]
    _, hard = ci_report.scorecard_section(_res({"nhl": {"error": "no data/archive/nhl (pinned data missing)"}}), None)
    assert hard == []


def test_report_cli_exit_code_and_comment(tmp_path):
    head, base = tmp_path / "h.json", tmp_path / "b.json"
    head.write_text(json.dumps(_res({"nfl": {"metrics": {"mid_play_cuts": 0}}})))
    base.write_text(json.dumps(_res({"nfl": {"metrics": {"mid_play_cuts": 0}}})))
    junit = tmp_path / "j.xml"
    junit.write_text('<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1" time="2.5">'
                     '<testcase name="a"/><testcase name="b"/><testcase name="c">'
                     '<skipped message="mac_only: needs macOS"/></testcase></testsuite></testsuites>')
    out = tmp_path / "c.md"
    rc = ci_report.main(["--head", str(head), "--base", str(base), "--junit", str(junit),
                         "--lint-status", "0", "--out", str(out)])
    body = out.read_text()
    assert rc == 0 and body.startswith(ci_report.MARKER)
    assert "2 passed, 0 failed, 1 skipped (1 of them mac_only)" in body
    head.write_text(json.dumps(_res({"nfl": {"metrics": {"mid_play_cuts": 3}}})))
    assert ci_report.main(["--head", str(head), "--base", str(base), "--out", str(out)]) == 1


def test_junit_with_failures_or_no_tests_is_not_ok(tmp_path):
    j = tmp_path / "j.xml"
    j.write_text('<testsuite tests="2" failures="1" errors="0" skipped="0" time="1"/>')
    assert ci_report.junit_summary(j)[1] is False
    j.write_text('<testsuite tests="0" failures="0" errors="0" skipped="0" time="0"/>')
    assert ci_report.junit_summary(j)[1] is False
    assert ci_report.junit_summary(tmp_path / "missing.xml")[1] is False
