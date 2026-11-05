"""scripts/grading_check.py: grader freeze, pre-registration, no moved goalposts (docs/grading.md)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("grading_check", ROOT / "scripts" / "grading_check.py")
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)

HEAD = "| rank | id | hypothesis | North Star line | success bar | nights needed | lane | status |\n|---|---|---|---|---|---|---|---|\n"


def test_repo_graders_match_the_freeze():
    assert gc.g1_freeze(ROOT)[0][0] == "PASS", gc.g1_freeze(ROOT)


def test_a_changed_grader_fails_until_refrozen(tmp_path):
    for p in gc.GRADERS:
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text("x = 1\n")
    (tmp_path / "docs").mkdir(exist_ok=True)
    gc.refreeze(tmp_path)
    assert gc.g1_freeze(tmp_path)[0][0] == "PASS"
    (tmp_path / gc.GRADERS[1]).write_text("x = 2\n")
    assert any(s == "FAIL" and gc.GRADERS[1] in m for s, m in gc.g1_freeze(tmp_path))
    gc.refreeze(tmp_path)
    assert gc.g1_freeze(tmp_path)[0][0] == "PASS"


def test_running_experiment_needs_a_success_bar_and_nights():
    ok = HEAD + "| 1 | EXP-001 | S ranks games | Crunch live | +2 vs live, CI excludes 0 | 8 locked | 1 | running |\n"
    bad = HEAD + "| 1 | EXP-001 | S ranks games | Crunch live |  | 8 locked | 1 | running (#1) |\n"
    queued = HEAD + "| 1 | EXP-002 | idea | line |  |  | 1 | queued |\n"
    assert gc.g2_prereg(ok)[0][0] == "PASS"
    assert gc.g2_prereg(bad)[0][0] == "FAIL"
    assert gc.g2_prereg(queued)[0][0] == "PASS"


def test_without_the_columns_it_is_pending_not_pass():
    old = "| rank | id | hypothesis | North Star line | lane | status |\n|---|---|---|---|---|---|\n| 1 | EXP-001 | h | l | 1 | running |\n"
    assert gc.g2_prereg(old)[0][0] == "PENDING"


def test_a_running_experiment_cannot_move_its_bar():
    base = HEAD + "| 1 | EXP-001 | h | Crunch live | +2 | 8 | 1 | running |\n"
    moved = HEAD + "| 1 | EXP-001 | h | Crunch live | +1 | 8 | 1 | running |\n"
    status_only = HEAD + "| 1 | EXP-001 | h | Crunch live | +2 | 8 | 1 | done: failed bar |\n"
    assert gc.g3_goalposts(moved, base)[0][0] == "FAIL"
    assert gc.g3_goalposts(status_only, base)[0][0] == "PASS"


def test_north_star_scores_only_nicks_leagues_no_wcbb():
    """Nick, Nov 4 7:55 PM ET: women's basketball is off; it never appears in a scorecard or gate."""
    spec2 = importlib.util.spec_from_file_location("northstar_l", ROOT / "scripts" / "northstar.py")
    ns = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(ns)
    assert "wcbb" not in ns.NICKS_LEAGUES and "wcbb" not in ns.LEAGUES and "wcbb" not in ns.ORACLE_LEAGUES
    assert set(ns.LEAGUES) <= set(ns.NICKS_LEAGUES)
