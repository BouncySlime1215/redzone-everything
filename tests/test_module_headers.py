"""Module headers (G-07): every module says what it is and why, and the ratchet only ever tightens."""
import json

from scripts import module_headers as mh


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


GOOD = '"""A module with a real purpose line.\n\nWhy: because a recorded decision said so (EXP-1).\n"""\nx = 1\n'


def test_a_good_header_passes():
    assert mh.classify(GOOD) is None


def test_problems_are_named_worst_first():
    assert mh.classify("x = 1\n") == "no-docstring"
    assert mh.classify('"""short"""\nx = 1\n') == "no-purpose"
    assert mh.classify('"""A longer purpose line."""\nx = 1\n') == "no-why"
    assert mh.classify('"""A longer purpose line.\n\nWhy: brief.\n"""\nx = 1\n') == "no-why"     # too short to be a reason
    assert mh.classify('"""A longer purpose line.\n\nWhy: unknown (needs owner)\n"""\n') == "why-unknown"


def test_an_init_with_no_code_needs_no_header():
    assert mh.classify("", is_init=True) is None
    assert mh.classify('"""Package."""\n', is_init=True) is None
    assert mh.classify("x = 1\n", is_init=True) == "no-docstring"


def test_new_module_fails_and_worse_fails_and_better_passes():
    base = {"a.py": "no-why", "b.py": "no-why"}
    res = mh.compare({"a.py": "no-why", "b.py": "why-unknown", "c.py": "no-why"}, base)
    assert res["new"] == {"c.py": "no-why"} and set(res["better"]) == {"b.py"}
    assert mh.compare({"a.py": "no-docstring"}, {"a.py": "no-why"})["new"] == {"a.py": "no-docstring"}


def test_update_only_shrinks():
    base = {"a.py": "no-why", "gone.py": "no-why", "up.py": "no-why"}
    assert mh.updated({"a.py": "no-docstring", "up.py": "why-unknown", "new.py": "no-why"}, base) == {
        "a.py": "no-why", "up.py": "why-unknown"}


def test_main_exit_codes_and_update_round_trip(tmp_path, capsys):
    write(tmp_path, "redzone/a.py", '"""A longer purpose line."""\nx = 1\n')
    write(tmp_path, "scripts/s.py", GOOD)
    write(tmp_path, "agent/z.py", GOOD)
    (tmp_path / "docs").mkdir()
    root = ["--root", str(tmp_path)]
    assert mh.main(root) == 1                                                # no baseline yet
    assert mh.main([*root, "--update"]) == 0
    assert mh.main(root) == 0
    write(tmp_path, "redzone/new.py", '"""A longer purpose line."""\nx = 1\n')
    assert mh.main(root) == 1
    assert "NEW no-why: redzone/new.py" in capsys.readouterr().out
    write(tmp_path, "redzone/new.py", GOOD)
    write(tmp_path, "redzone/a.py", GOOD)
    assert mh.main(root) == 0                                                # improving never fails
    mh.main([*root, "--update"])
    assert json.loads((tmp_path / mh.BASELINE).read_text())["modules"] == {}


def test_the_repo_has_no_new_violations():
    assert mh.main([]) == 0


def test_the_core_modules_are_backfilled():
    current = mh.scan(mh.ROOT)
    for rel in ("redzone/control.py", "redzone/engine.py", "redzone/fsm.py", "redzone/decider/cuts.py"):
        assert rel not in current, f"{rel}: {current.get(rel)}"
