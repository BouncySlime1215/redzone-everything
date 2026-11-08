from scripts import commit_why


def test_missing_why_is_reported_and_merges_are_exempt():
    commits = [("a" * 40, "Fix cut timing\n\nWhy: CFB joins landed mid-play (D-01).\n"),
               ("b" * 40, "Tidy names\n\nno reason given\n"),
               ("c" * 40, "Merge branch 'x' into integration/next\n"),
               ("d" * 40, "Revert \"Tidy names\"\n")]
    assert commit_why.missing(commits) == ["bbbbbbbbb Tidy names"]
