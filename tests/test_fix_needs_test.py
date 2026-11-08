"""A fix PR needs a regression test and a repro; no PR deletes or skips tests. scripts/fix_needs_test.py, PLAN G-02."""
import pytest

from scripts import fix_needs_test as F
from scripts.autofix_guard import FileDiff

SKIP_LINE = "@pytest.mark." + "skip"          # split so this file's own diff stays clean
XFAIL_LINE = "pytest." + "xfail('later')"
NEW = 2_000_000_000                           # a commit made after the rule started
OLD = 1_000_000_000                           # one made before
REPRO = "## Repro\npython -m pytest x -> 1 failed\n"
TEST = FileDiff("tests/test_a.py", "M", added=["    assert a == 1"])
CODE = FileDiff("redzone/a.py", "M", added=["x = 1"])


@pytest.mark.parametrize("subject", ["fix the join", "Fix: join", "fixes #3", "fixed it", "bugfix: y", "hotfix z", "FIX(x): y"])
def test_fix_subjects(subject):
    assert F.is_fix(subject)


@pytest.mark.parametrize("subject", ["prefix the name", "Fixture cleanup", "Add a join", "", "Merge fix"])
def test_not_fix_subjects(subject):
    assert not F.is_fix(subject)


def test_fix_title_without_a_test_is_refused():
    why = F.problems("Fix the join", REPRO, [], [CODE])
    assert len(why) == 1 and "regression test" in why[0]


def test_fix_commit_without_a_test_is_refused_even_with_a_plain_title():
    why = F.problems("Tonight's work", REPRO, [("a", "fix the join", NEW)], [CODE])
    assert "regression test" in why[0] and "fix the join" in why[0]


def test_fix_with_a_test_and_repro_passes():
    assert F.problems("Fix the join", REPRO, [("a", "fix the join", NEW)], [CODE, TEST]) == []


def test_no_test_reason_excuses_the_test_only():
    body = REPRO + "No-test: config text only, covered by the schema check\n"
    assert F.problems("Fix the join", body, [], [CODE]) == []
    assert F.problems("Fix the join", "No-test: docs\n", [], [CODE, TEST])[0].startswith("fix without a repro")


def test_an_empty_no_test_line_does_not_count():
    assert "regression test" in F.problems("Fix the join", REPRO + "No-test:\n", [], [CODE])[0]


def test_a_no_test_line_inside_a_template_comment_does_not_count():
    assert "regression test" in F.problems("Fix", REPRO + "<!-- No-test: <reason>\nNo-test: x -->", [], [CODE])[0]


def test_deleting_a_test_is_not_a_regression_test():
    gone = FileDiff("tests/test_a.py", "D", removed=["def test_a():"])
    why = F.problems("Fix the join", REPRO, [], [CODE, gone])
    assert any("deletes" in w for w in why) and any("regression test" in w for w in why)


def test_old_fix_commits_are_not_judged():
    assert F.problems("Tonight's work", "", [("a", "fix the join", OLD)], [CODE]) == []


def test_non_fix_pr_needs_neither():
    assert F.problems("Add a screen", "", [("a", "Add a screen", NEW)], [CODE]) == []


@pytest.mark.parametrize("body,ok", [
    (REPRO, True),
    ("Repro: python x -> boom\n", True),
    ("## Repro (command and output before the fix)\n<!-- fill me in -->\n\n## Root cause\nx\n", False),
    ("## Repro\n\n## Root cause\nx\n", False),
    ("", False),
])
def test_repro_must_have_content(body, ok):
    assert F.has_repro(body) is ok


@pytest.mark.parametrize("title", ["Add a screen", "Fix the join"])
@pytest.mark.parametrize("line", [SKIP_LINE, XFAIL_LINE])
def test_any_pr_adding_a_skip_is_refused(title, line):
    f = FileDiff("tests/test_a.py", "M", added=[line, "    assert 1"])
    assert any("skip/xfail" in w for w in F.problems(title, REPRO, [], [CODE, f]))


def test_any_pr_deleting_a_test_file_is_refused():
    f = FileDiff("tests/test_a.py", "D", removed=["def test_a():"])
    assert any("deletes" in w for w in F.problems("Add a screen", "", [], [f]))


def test_removing_a_test_function_is_refused_but_renaming_is_not():
    gone = FileDiff("tests/test_a.py", "M", removed=["def test_a():"])
    assert any("removes 1" in w for w in F.test_problems([gone]))
    renamed = FileDiff("tests/test_a.py", "M", removed=["def test_a():"], added=["def test_b():"])
    assert F.test_problems([renamed]) == []
