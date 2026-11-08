"""A fix PR carries a regression test and a repro; no PR deletes or skips tests. Runs in CI (job `memory`).

    PR_TITLE=... PR_BODY=... python scripts/fix_needs_test.py --base origin/integration/next    # exit 1 = refused

Why: W1-RULES gap 1 (PLAN G-02). Only auto-fix PRs needed a test, so a bug fixed by hand could come back.
A PR is a fix when its title or any commit subject starts with fix / fixes / fixed / bugfix / hotfix.
A fix must change a file under tests/ (or say `No-test: <reason>` in the PR body) and fill in the Repro section.
Every PR: no deleted test files, no net-removed test functions, no new skip or xfail (scripts/autofix_guard.py
helpers). Commits older than SINCE are not judged, so a branch is not failed for history it can't change.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autofix_guard import _SKIP, _TEST_DEF, FileDiff, parse  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SINCE = "2026-11-09T00:00:00Z"     # the rule started (decisions.md 2026-11-08); committer time, UTC
FIX = re.compile(r"^\s*(bug|hot)?fix(es|ed)?\b", re.I)
NO_TEST = re.compile(r"^No-test:\s*\S", re.M)
REPRO_LINE = re.compile(r"^Repro:\s*\S", re.M)
REPRO_SECTION = re.compile(r"^##\s*Repro\b[^\n]*\n(.*?)(?=^##\s|\Z)", re.M | re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)


def is_fix(subject: str) -> bool:
    return bool(FIX.match(subject or ""))


def fix_commits(commits: list[tuple]) -> list[str]:
    """commits: (sha, subject) or (sha, subject, committer epoch seconds). Subjects that are fixes."""
    since = datetime.fromisoformat(SINCE.replace("Z", "+00:00")).timestamp()
    return [c[1] for c in commits if is_fix(c[1]) and not (len(c) > 2 and c[2] is not None and float(c[2]) < since)]


def has_repro(body: str) -> bool:
    text = COMMENT.sub("", body or "")
    if REPRO_LINE.search(text):
        return True
    m = REPRO_SECTION.search(text)
    return bool(m and m.group(1).strip())


def test_problems(files: list[FileDiff]) -> list[str]:
    """Every PR: tests are the acceptance signal, so they don't get deleted or skipped."""
    why = []
    for f in files:
        if any(_SKIP.search(x) for x in f.added):
            why.append(f"{f.path}: adds a skip/xfail")
        if not f.path.startswith("tests/"):
            continue
        if f.status == "D":
            why.append(f"{f.path}: deletes a test file")
            continue
        gone = sum(bool(_TEST_DEF.match(x)) for x in f.removed) - sum(bool(_TEST_DEF.match(x)) for x in f.added)
        if gone > 0:
            why.append(f"{f.path}: removes {gone} test function(s)")
    return why


def problems(title: str, body: str, commits: list[tuple], files: list[FileDiff]) -> list[str]:
    why = test_problems(files)
    fixes = fix_commits(commits)
    if is_fix(title) or fixes:
        tested = any(f.path.startswith("tests/") and f.status != "D" and f.added for f in files)
        if not tested and not NO_TEST.search(COMMENT.sub("", body or "")):
            named = title if is_fix(title) else fixes[0]
            why.append(f"fix without a regression test ({named!r}): change a file under tests/, "
                       "or add `No-test: <reason>` to the PR body")
        if not has_repro(body):
            why.append("fix without a repro: fill in `## Repro` with the command and its output before the fix")
    return why


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def commits_since(base: str) -> list[tuple]:
    out = []
    for line in _git("log", "--no-merges", "--format=%H%x00%ct%x00%s", f"{base}..HEAD").splitlines():
        sha, ct, subject = line.split("\x00", 2)
        out.append((sha, subject, int(ct)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="git ref the PR is compared against")
    a = ap.parse_args(argv)
    spec = f"{a.base}...HEAD"
    files = parse(_git("diff", "--name-status", "-M", spec), _git("diff", "-M", spec))
    why = problems(os.environ.get("PR_TITLE", ""), os.environ.get("PR_BODY", ""), commits_since(a.base), files)
    for w in why:
        print(f"- {w}")
    if not why:
        print("fix_needs_test: ok")
    return 1 if why else 0


if __name__ == "__main__":
    sys.exit(main())
