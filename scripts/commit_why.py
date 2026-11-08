"""Every commit on a PR says why (docs/coordination.md, research: the "why" is what goes missing).

    python scripts/commit_why.py --base origin/integration/next     # CI: exit 1 if a commit lacks a Why: line

A commit message needs a line starting with `Why:` (one sentence: the reason, the North Star line or the bug).
Merge commits and reverts are exempt. Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHY = re.compile(r"^Why:\s*\S", re.M)


def missing(commits: list[tuple[str, str]]) -> list[str]:
    out = []
    for sha, msg in commits:
        subject = msg.strip().splitlines()[0] if msg.strip() else ""
        if subject.startswith(("Merge ", "Revert ")) or WHY.search(msg):
            continue
        out.append(f"{sha[:9]} {subject}")
    return out


def commits_since(base: str) -> list[tuple[str, str]]:
    r = subprocess.run(["git", "log", "--no-merges", "--format=%H%x00%B%x1e", f"{base}..HEAD"], cwd=ROOT,
                       capture_output=True, text=True)
    out = []
    for chunk in r.stdout.split("\x1e"):
        if "\x00" in chunk:
            sha, msg = chunk.strip("\n").split("\x00", 1)
            out.append((sha, msg))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/integration/next")
    a = ap.parse_args(argv)
    bad = missing(commits_since(a.base))
    if bad:
        print("Commits without a `Why:` line (add one sentence: the reason, the North Star line or the bug):")
        print("\n".join(f"  {b}" for b in bad))
        return 1
    print("every commit says why")
    return 0


if __name__ == "__main__":
    sys.exit(main())
