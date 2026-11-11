"""Check that every module says what it is and why it exists, ratcheted so only new gaps fail.

Why: process gate G-07 (finding W1-CONTEXT): a module's reason lived only in people's heads, so the
context card had nothing to show (docs/STANDARD.md section 5, docs/agents/context/2026-11-08-code-context-on-read.md).

    python scripts/module_headers.py            # exit 1 iff a module is new or got worse than the baseline
    python scripts/module_headers.py --json     # machine output
    python scripts/module_headers.py --update   # shrink the baseline to today; it never adds a module

A header is a module docstring whose first line is a purpose of 10+ characters, plus a line starting
`Why: ` with 15+ characters of reason. `Why: unknown (needs owner)` is an honest placeholder, not an
answer: it counts as better than nothing and stays in docs/module_headers_baseline.json until someone
finds the reason. Worst to best: no-docstring, no-purpose, no-why, why-unknown. Stdlib only, pure AST,
never imports redzone (it runs in the lint job). An __init__.py with no code needs no header.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "docs/module_headers_baseline.json"
TREES = ("redzone", "agent", "scripts")
WORST_FIRST = ("no-docstring", "no-purpose", "no-why", "why-unknown")
MIN_PURPOSE = 10
_WHY = re.compile(r"^\s*Why:\s+(.{15,})$", re.M)
_UNKNOWN = re.compile(r"^unknown\b", re.I)
_HINT = {
    "no-docstring": "add a module docstring: purpose on line 1, then a Why: line",
    "no-purpose": f"make the docstring's first line say what this is ({MIN_PURPOSE}+ characters)",
    "no-why": "add `Why: <reason, with a decision or finding id>` to the docstring (docs/STANDARD.md section 5)",
    "why-unknown": "replace `Why: unknown` with the reason once it is known",
}


def modules(root: Path) -> list[Path]:
    files = [p for tree in TREES for p in sorted((root / tree).rglob("*.py")) if "__pycache__" not in p.parts]
    return files


def classify(source: str, is_init: bool = False) -> str | None:
    """The module's one (worst) header problem, or None when the header is good or not needed."""
    tree = ast.parse(source)
    doc = ast.get_docstring(tree, clean=True)
    code = tree.body[1:] if doc is not None else tree.body
    if is_init and not code:
        return None
    if doc is None:
        return "no-docstring"
    if len(doc.splitlines()[0].strip()) < MIN_PURPOSE:
        return "no-purpose"
    found = _WHY.search(doc)
    if not found:
        return "no-why"
    return "why-unknown" if _UNKNOWN.match(found.group(1)) else None


def scan(root: Path) -> dict[str, str]:
    out = {}
    for p in modules(root):
        rel = p.relative_to(root).as_posix()
        try:
            kind = classify(p.read_text(encoding="utf-8", errors="replace"), p.name == "__init__.py")
        except SyntaxError:
            kind = "no-docstring"
        if kind:
            out[rel] = kind
    return out


def load(root: Path) -> dict[str, str] | None:
    try:
        return json.loads((root / BASELINE).read_text())["modules"]
    except (OSError, ValueError, KeyError):
        return None


def rank(kind: str | None) -> int:
    """Higher is better; a module with no problem outranks every problem."""
    return len(WORST_FIRST) if kind is None else WORST_FIRST.index(kind)


def compare(current: dict[str, str], baseline: dict[str, str]) -> dict[str, dict[str, str]]:
    new = {p: k for p, k in current.items() if p not in baseline or rank(k) < rank(baseline[p])}
    better = {p: k for p, k in baseline.items() if rank(current.get(p)) > rank(k)}
    return {"new": new, "better": better}


def write(root: Path, baseline: dict[str, str]) -> None:
    # One module per line, so a ratchet diff reads as a list of fixed modules.
    rows = ",\n".join(f"  {json.dumps(p)}: {json.dumps(k)}" for p, k in sorted(baseline.items()))
    body = '{\n "version": 1,\n "modules": {\n' + rows + "\n }\n}\n"
    (root / BASELINE).write_text(body)


def updated(current: dict[str, str], baseline: dict[str, str] | None) -> dict[str, str]:
    """The next baseline: no new modules, and an entry only ever moves toward fixed."""
    if baseline is None:
        return dict(current)
    out = {}
    for path, kind in baseline.items():
        now = current.get(path)
        if now is not None:
            out[path] = now if rank(now) > rank(kind) else kind
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--update", action="store_true", help="shrink the baseline to today (never adds a module)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    current = scan(a.root)
    baseline = load(a.root)
    if a.update:
        next_base = updated(current, baseline)
        write(a.root, next_base)
        print(f"baseline: {len(next_base)} modules still without a full header")
        return 0
    if baseline is None:
        print(f"{BASELINE} is missing: run python scripts/module_headers.py --update", file=sys.stderr)
        return 1
    res = compare(current, baseline)
    if a.json:
        print(json.dumps({"total": len(modules(a.root)), "missing": len(current), **res}, indent=1, sort_keys=True))
    else:
        print(f"{len(modules(a.root))} modules, {len(current)} without a full header, "
              f"{len(baseline)} in the baseline, {len(res['new'])} new, {len(res['better'])} improved")
        for path, kind in sorted(res["new"].items()):
            print(f"NEW {kind}: {path}\n  fix: {_HINT[kind]}")
        if res["better"]:
            print("improved modules can leave the baseline: python scripts/module_headers.py --update")
    return 1 if res["new"] else 0


if __name__ == "__main__":
    sys.exit(main())
