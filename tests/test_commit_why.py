from scripts import commit_why


def test_missing_why_is_reported_and_merges_are_exempt():
    commits = [("a" * 40, "Fix cut timing\n\nWhy: CFB joins landed mid-play (D-01).\n"),
               ("b" * 40, "Tidy names\n\nno reason given\n"),
               ("c" * 40, "Merge branch 'x' into integration/next\n"),
               ("d" * 40, "Revert \"Tidy names\"\n")]
    assert commit_why.missing(commits) == ["bbbbbbbbb Tidy names"]


def test_commits_before_the_rule_started_are_exempt():
    old = 1794000000            # 2026-11-07, before SINCE
    new = 1794200000            # after SINCE
    assert commit_why.missing([("e" * 40, "Old work\n", old)]) == []
    assert commit_why.missing([("f" * 40, "New work\n", new)]) == ["fffffffff New work"]
