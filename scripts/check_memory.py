"""Keep the AI memory honest. Runs in CI (the `memory` job) and locally:

    python scripts/check_memory.py                    # links only
    python scripts/check_memory.py --base origin/integration/next   # + the decisions rule for this branch

1. Every relative link in CLAUDE.md and docs/memory/**/*.md points at a file or folder that exists.
2. A change to a default or a gate (GUARDED below) must also add a line to docs/memory/decisions.md.
3. docs/handoff.md stays short (W1-RULES gap 5): over HANDOFF_TARGET lines is a printed note, over HANDOFF_RATCHET
   fails. Lower the ratchet as the file is trimmed, until the two match.
Exit 0 = fine, 1 = something to fix (each problem is printed on its own line).
Why: Keeps AI memory honest: links resolve, decisions logged; runs in CI (docstring, memory commit).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECISIONS = "docs/memory/decisions.md"
# Defaults and gates. Keep in step with CLAUDE.md and the header of docs/memory/decisions.md.
GUARDED = ("redzone/default_config.yaml", "docs/gate_baseline.json", "scripts/release_gate.py")
HANDOFF = "docs/handoff.md"
HANDOFF_TARGET = 80
HANDOFF_RATCHET = 305   # its length when the cap started (2026-11-08); only ever goes down
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def memory_files(root: Path = ROOT) -> list[Path]:
    return [root / "CLAUDE.md", *sorted((root / "docs/memory").rglob("*.md"))]


def broken_links(root: Path = ROOT) -> list[str]:
    bad = []
    for f in memory_files(root):
        if not f.exists():
            bad.append(f"{f.relative_to(root)}: missing")
            continue
        text = re.sub(r"```.*?```", "", f.read_text(encoding="utf-8"), flags=re.S)   # skip code blocks
        for target in _LINK.findall(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                continue   # http(s), mailto, in-page anchors
            path = target.split("#", 1)[0]
            if path and not (f.parent / path).exists():
                bad.append(f"{f.relative_to(root)}: broken link {target}")
    return bad


def changed_files(base: str, root: Path = ROOT) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=root,
                         capture_output=True, text=True, check=True).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def decisions_missing(changed: list[str]) -> list[str]:
    hit = sorted(set(changed) & set(GUARDED))
    if hit and DECISIONS not in changed:
        return [f"{', '.join(hit)} changed (a default or a gate): add a dated line with the reason to {DECISIONS}"]
    return []


def handoff_size(root: Path = ROOT) -> tuple[list[str], list[str]]:
    """(problems, notes) for the length of the handoff."""
    f = root / HANDOFF
    n = len(f.read_text(encoding="utf-8").splitlines()) if f.exists() else 0
    if n > HANDOFF_RATCHET:
        return [f"{HANDOFF}: {n} lines (limit {HANDOFF_RATCHET}, target {HANDOFF_TARGET}); move history out of it"], []
    if n > HANDOFF_TARGET:
        return [], [f"note: {HANDOFF} is {n} lines (target {HANDOFF_TARGET})"]
    return [], []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="git ref the branch is compared against (enables the decisions rule)")
    a = ap.parse_args(argv)
    size_problems, notes = handoff_size()
    problems = broken_links() + size_problems
    for n in notes:
        print(n)
    if a.base:
        problems += decisions_missing(changed_files(a.base))
    for p in problems:
        print(p)
    if not problems:
        print("memory ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
