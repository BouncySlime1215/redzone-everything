"""There is one plan and it has the parts a living plan needs. Runs in CI (job `memory`).

    python scripts/plan_check.py      # exit 1 = something to fix, one problem per line

Why: W1-RULES gap 5 (PLAN G-06). "The only plan" was prose, and new plan files kept appearing.
docs/PLAN.md: required sections, a named owner, a dated newest-first log, under MAX_LINES.
docs/plans/*.md: each says near its top that docs/PLAN.md supersedes it. Stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("Progress", "Surprises & Discoveries", "Decision Log", "Outcomes", "Log")
MAX_LINES = 150
HEADER_LINES = 8        # how far down docs/plans/*.md may carry the superseded line
_ENTRY = re.compile(r"^- (\d{4}-\d{2}-\d{2})\b", re.M)


def _section(text: str, name: str) -> str | None:
    """The body of `## 8. Name ...`, or None. The name must be the heading's first words."""
    m = re.search(rf"^##+\s+(?:\d+\.\s*)?{re.escape(name)}(?![\w ]*&)\b[^\n]*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1) if m else None


def plan_problems(text: str) -> list[str]:
    out = [f"docs/PLAN.md: missing section `{name}`" for name in REQUIRED if _section(text, name) is None]
    if "Owner of the whole file:" not in text:
        out.append("docs/PLAN.md: no `Owner of the whole file:` line")
    n = len(text.splitlines())
    if n >= MAX_LINES:
        out.append(f"docs/PLAN.md: {n} lines (limit {MAX_LINES}); move detail to the linked docs")
    log = _section(text, "Log")
    if log is not None:
        dates = _ENTRY.findall(log)
        if not dates:
            out.append("docs/PLAN.md: the Log has no dated entries (`- YYYY-MM-DD: ...`)")
        elif dates != sorted(dates, reverse=True):
            out.append("docs/PLAN.md: the Log must be newest first")
    return out


def is_superseded(text: str) -> bool:
    return any("superseded by" in ln.lower() and "PLAN.md" in ln for ln in text.splitlines()[:HEADER_LINES])


def old_plan_problems(root: Path = ROOT) -> list[str]:
    return [f"{p.relative_to(root).as_posix()}: add a `Superseded by docs/PLAN.md` line near the top (one plan only)"
            for p in sorted((root / "docs" / "plans").glob("*.md")) if not is_superseded(p.read_text(encoding="utf-8"))]


def problems(root: Path = ROOT) -> list[str]:
    plan = root / "docs" / "PLAN.md"
    if not plan.exists():
        return ["docs/PLAN.md: missing"]
    return plan_problems(plan.read_text(encoding="utf-8")) + old_plan_problems(root)


def main() -> int:
    out = problems()
    for p in out:
        print(p)
    if not out:
        print("plan ok")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
