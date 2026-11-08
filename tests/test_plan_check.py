"""One plan, with its required parts. scripts/plan_check.py, PLAN G-06."""
from pathlib import Path

import pytest

from scripts import plan_check as P

ROOT = Path(__file__).resolve().parents[1]
GOOD = """# Plan
Owner of the whole file: the coordinator.

## 8. Log (coordinator, newest first)
- 2026-11-08: b
- 2026-11-07: a

## 9. Progress
x

## 10. Surprises & Discoveries
x

## 11. Decision Log
x

## 12. Outcomes
x
"""


def test_the_real_plan_passes():
    assert P.problems(ROOT) == []


def test_a_good_plan_has_no_problems():
    assert P.plan_problems(GOOD) == []


@pytest.mark.parametrize("heading", ["## 9. Progress", "## 10. Surprises & Discoveries", "## 11. Decision Log", "## 12. Outcomes"])
def test_each_required_section_is_required(heading):
    name = heading.split(". ", 1)[1]
    assert f"missing section `{name}`" in P.plan_problems(GOOD.replace(heading, "## Other"))[0]


def test_a_decision_log_does_not_stand_in_for_the_log():
    text = GOOD.replace("## 8. Log (coordinator, newest first)", "## 8. Notes")
    assert any("`Log`" in p for p in P.plan_problems(text))


def test_owner_is_required():
    assert any("Owner" in p for p in P.plan_problems(GOOD.replace("Owner of the whole file:", "Someone:")))


def test_log_must_be_newest_first():
    text = GOOD.replace("2026-11-08: b", "2026-11-01: b")
    assert any("newest first" in p for p in P.plan_problems(text))


def test_log_needs_a_dated_entry():
    text = GOOD.replace("- 2026-11-08: b\n- 2026-11-07: a\n", "nothing yet\n")
    assert any("no dated entries" in p for p in P.plan_problems(text))


def test_plan_length_is_capped():
    assert any("limit" in p for p in P.plan_problems(GOOD + "x\n" * P.MAX_LINES))


def _tree(tmp_path, *plans):
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "PLAN.md").write_text(GOOD)
    for name, text in plans:
        (tmp_path / "docs" / "plans" / name).write_text(text)
    return tmp_path


def test_a_second_live_plan_is_refused(tmp_path):
    out = P.problems(_tree(tmp_path, ("2026-11-09-new.md", "# My new plan\nDo things.\n")))
    assert len(out) == 1 and "2026-11-09-new.md" in out[0]


def test_a_marked_plan_passes(tmp_path):
    text = "# Old plan\n\n> **Superseded by [docs/PLAN.md](../PLAN.md).** History.\n"
    assert P.problems(_tree(tmp_path, ("old.md", text))) == []


def test_the_marker_must_be_near_the_top(tmp_path):
    text = "# Old\n" + "x\n" * 20 + "Superseded by docs/PLAN.md\n"
    assert len(P.problems(_tree(tmp_path, ("old.md", text)))) == 1


def test_a_missing_plan_is_reported(tmp_path):
    assert P.problems(tmp_path) == ["docs/PLAN.md: missing"]
