"""Guard for Claude's auto-fix changes (.github/workflows/auto-fix.yml), run on the diff before any
commit is pushed. It refuses a change that would weaken the project's safety nets:

  - touches CI, workflows, the gates, the eval data pin, deploy secrets or anything secret-looking
    (PROTECTED below)
  - deletes a test file, removes test functions or asserts, or adds a skip / xfail
  - adds anything that looks like a credential
  - has no regression test (some file under tests/ must be added or changed) or no fix outside tests/
  - is too big for an unattended fix (MAX_FILES / MAX_LINES)

    python scripts/autofix_guard.py --base origin/integration/next      # exit 0 ok, 1 refused
Why: Guards Claude's auto-fix diffs before any push (auto-fix BRAIN tier commit).
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass, field

MAX_FILES = 12
MAX_LINES = 600

PROTECTED = [
    ".github/*", ".github/**", ".claude/*", ".claude/**",
    "scripts/ci_*.py", "scripts/release_gate.py", "scripts/eval*.py", "scripts/fetch_eval_data.py",
    "scripts/build_eval_data.py", "scripts/autofix_guard.py", "scripts/deploy.py",
    "docs/gate_baseline.json", "docs/auto-fix.md", "data/*", "data/**", "deploy/*", "deploy/**",
    "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "conftest.py", "tests/conftest.py",
    "requirements*.txt", "*.env", ".env*", "*secret*", "*token*", "*credential*", "*.pem", "*.key",
    "redzone/platform/autofix.py",
]
_SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|unittest\.skip|@skip\b|"
                   r"--deselect|-k\s+['\"]?not\b|importorskip")
_TEST_DEF = re.compile(r"^\s*(async\s+)?def\s+test_\w*")
_ASSERT = re.compile(r"^\s*assert\b|\.assert\w*\(|pytest\.raises")
_CRED = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|sk-ant-|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
                   r"|-----BEGIN [A-Z ]*PRIVATE KEY|CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|secrets\.)")


@dataclass
class FileDiff:
    path: str
    status: str                       # A M D R
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def parse(name_status: str, patch: str) -> list[FileDiff]:
    files: dict[str, FileDiff] = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            st = parts[0][0]
            for p in parts[1:]:                      # a rename names both paths: guard both
                files[p] = FileDiff(p, st)
    cur = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            p = line[4:]
            cur = files.get(p[2:] if p.startswith("b/") else p)
        elif line.startswith("--- "):
            p = line[4:]
            if p != "/dev/null":
                cur = files.get(p[2:] if p.startswith("a/") else p)
        elif cur is not None and line.startswith("+") and not line.startswith("+++"):
            cur.added.append(line[1:])
        elif cur is not None and line.startswith("-") and not line.startswith("---"):
            cur.removed.append(line[1:])
    return list(files.values())


def _protected(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat) for pat in PROTECTED)


def check(files: list[FileDiff]) -> list[str]:
    """Every reason to refuse (empty: the change may go up as a draft PR)."""
    why: list[str] = []
    if not files:
        return ["no change"]
    tests = [f for f in files if f.path.startswith("tests/")]
    code = [f for f in files if not f.path.startswith("tests/")]
    for f in files:
        if _protected(f.path):
            why.append(f"{f.path}: protected (CI, gates, eval data, deploy or secrets)")
        if any(_CRED.search(x) for x in f.added):
            why.append(f"{f.path}: adds something that looks like a credential or reads secrets")
    for f in tests:
        if f.status == "D":
            why.append(f"{f.path}: deletes a test file")
        gone = sum(bool(_TEST_DEF.match(x)) for x in f.removed) - sum(bool(_TEST_DEF.match(x)) for x in f.added)
        if gone > 0:
            why.append(f"{f.path}: removes {gone} test function(s)")
        asserts = sum(bool(_ASSERT.search(x)) for x in f.removed) - sum(bool(_ASSERT.search(x)) for x in f.added)
        if asserts > 0:
            why.append(f"{f.path}: removes {asserts} assert(s)")
        if any(_SKIP.search(x) for x in f.added):
            why.append(f"{f.path}: adds a skip/xfail")
    for f in code:
        if any(_SKIP.search(x) for x in f.added):
            why.append(f"{f.path}: adds a skip/xfail outside tests")
    if not any(f.status != "D" and f.added for f in tests):
        why.append("no regression test: add or change a test under tests/")
    if not code:
        why.append("no fix: only tests changed")
    if len(files) > MAX_FILES:
        why.append(f"{len(files)} files changed (limit {MAX_FILES} for an unattended fix)")
    n = sum(len(f.added) + len(f.removed) for f in files)
    if n > MAX_LINES:
        why.append(f"{n} lines changed (limit {MAX_LINES} for an unattended fix)")
    return why


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="origin/integration/next")
    a = ap.parse_args(argv)
    # Working tree + index against the base, untracked files included (git add -N first).
    _git("add", "-A", "-N")
    files = parse(_git("diff", "--name-status", "-M", a.base), _git("diff", "-M", a.base))
    why = check(files)
    if why:
        print("auto-fix guard: REFUSED")
        for w in why:
            print(f"- {w}")
        return 1
    print(f"auto-fix guard: ok ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
