"""scripts/bisect_test.sh names the commit that broke a test, skips commits that say nothing, and puts the checkout
back. Runs on a throwaway repo. PLAN G-04."""
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bisect_test.sh"
PASS = "from m import V\ndef test_v():\n    assert V == 1\n"


def git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, msg, **files):
    for name, text in files.items():
        (repo / name.replace("__", ".")).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


def run(repo, *args):
    env = {**os.environ, "PYTHON": sys.executable}
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=repo, env=env, capture_output=True, text=True, timeout=120)


def make(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q")
    good = commit(repo, "start", m__py="V = 1\n", test_m__py=PASS)
    commit(repo, "harmless", other__py="A = 1\n")
    return repo, good


def test_names_the_first_bad_commit_and_restores_the_checkout(tmp_path):
    repo, good = make(tmp_path)
    commit(repo, "the bad one", m__py="V = 2\n")
    commit(repo, "later work", other__py="A = 2\n")
    head = git(repo, "rev-parse", "HEAD")
    r = run(repo, "--good", good, "test_m.py::test_v")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Bisected-to:" in r.stdout and "the bad one" in r.stdout
    assert git(repo, "rev-parse", "HEAD") == head
    assert not (repo / ".git" / "BISECT_LOG").exists()


def test_a_commit_that_cannot_be_collected_is_skipped_not_blamed(tmp_path):
    repo, good = make(tmp_path)
    commit(repo, "broken import", m__py="import nothing_here_xyz\nV = 1\n")
    commit(repo, "repaired", m__py="V = 1\n")
    commit(repo, "the bad one", m__py="V = 2\n")
    commit(repo, "later work", other__py="A = 2\n")
    r = run(repo, "--good", good, "test_m.py::test_v")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the bad one" in r.stdout


def test_missing_data_skips_every_commit_and_names_nothing(tmp_path):
    repo, good = make(tmp_path)
    commit(repo, "the bad one", m__py="V = 2\n")
    r = run(repo, "--good", good, "--needs", "data/no_such_night", "test_m.py::test_v")
    assert r.returncode == 1 and "Bisected-to:" not in r.stdout


def test_step_exit_codes_follow_git_bisect(tmp_path):
    repo, _ = make(tmp_path)
    assert run(repo, "--step", "test_m.py::test_v", "", "").returncode == 0
    commit(repo, "bad", m__py="V = 2\n")
    assert run(repo, "--step", "test_m.py::test_v", "", "").returncode == 1
    assert run(repo, "--step", "no_such_test.py", "", "").returncode == 125
    assert run(repo, "--step", "test_m.py::test_v", "", "data/none").returncode == 125


def test_uncommitted_changes_are_refused(tmp_path):
    repo, good = make(tmp_path)
    (repo / "m.py").write_text("V = 3\n")
    r = run(repo, "--good", good, "test_m.py::test_v")
    assert r.returncode == 2 and "refused" in r.stderr
    assert (repo / "m.py").read_text() == "V = 3\n"


def test_usage_error_without_good(tmp_path):
    repo, _ = make(tmp_path)
    assert run(repo, "test_m.py::test_v").returncode == 2
