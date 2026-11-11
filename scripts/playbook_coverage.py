"""Playbook coverage (docs/playbooks.md): which entries have a night-lab / unit test that RAN and passed.

    python scripts/playbook_coverage.py                         # static: entries vs @pytest.mark.playbook ids
    python scripts/playbook_coverage.py --junit pytest.xml      # plus: did each marked test pass?
    python scripts/playbook_coverage.py --md cov.md             # a section for the PR comment

A test claims an entry with `@pytest.mark.playbook("SAFE-01")` (several ids allowed). Fails (exit 1) when
- a test names an id that is not in docs/playbooks.md (typo or a removed entry), or
- an entry whose status starts with `done` has no marked test, or (with --junit) its tests did not
  all pass (a skip counts as not run).
Everything else is reported, never failed: entries are built top-down over many cycles.
Why: Checks which playbook entries have a passing test; CI fails only on marked entries (docstring).
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = re.compile(r"^\|\s*([A-Z]+-\d+)\s*\|")


def entries(md: Path) -> dict[str, str]:
    """id -> status (the last column) for every table row in the playbook."""
    out = {}
    for line in md.read_text().splitlines():
        m = ROW.match(line)
        if m:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out[m.group(1)] = cells[-1].lower() if cells else ""
    return out


def _ids(dec: ast.expr) -> list[str]:
    # pytest.mark.playbook("A-1", "B-2")
    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "playbook"):
        return [a.value for a in dec.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return []


def marked_tests(tests: Path) -> dict[str, list[tuple[str, str]]]:
    """id -> [(module stem, test name)] from @pytest.mark.playbook decorators (functions and classes)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for f in sorted(tests.rglob("test_*.py")):
        try:
            tree = ast.parse(f.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for d in node.decorator_list:
                    for pid in _ids(d):
                        out.setdefault(pid, []).append((f.stem, node.name))
    return out


def junit_results(path: Path) -> dict[tuple[str, str], str]:
    """(module stem, test name or class name) -> passed / failed / skipped."""
    res: dict[tuple[str, str], str] = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        mod = (tc.get("classname") or "").split(".")
        name = (tc.get("name") or "").split("[")[0]
        state = ("failed" if tc.find("failure") is not None or tc.find("error") is not None
                 else "skipped" if tc.find("skipped") is not None else "passed")
        stem = next((p for p in mod if p.startswith("test_")), "")
        for key in {(stem, name), (stem, mod[-1])}:
            prev = res.get(key)
            res[key] = state if prev in (None, "passed") else prev   # any failure/skip sticks
    return res


def check(md: Path, tests: Path, junit: Path | None = None) -> dict:
    ent, marks = entries(md), marked_tests(tests)
    runs = junit_results(junit) if junit and junit.exists() else None
    unknown = sorted(set(marks) - set(ent))
    rows, errors = [], [f"unknown playbook id in tests: {u}" for u in unknown]
    for pid, status in ent.items():
        tests_for = marks.get(pid, [])
        states = [runs.get(t, "missing") for t in tests_for] if runs is not None else []
        ran_ok = bool(tests_for) and (runs is None or all(s == "passed" for s in states))
        rows.append({"id": pid, "status": status, "tests": len(tests_for), "passing": ran_ok})
        if status.startswith("done"):
            if not tests_for:
                errors.append(f"{pid} is done but has no @pytest.mark.playbook test")
            elif runs is not None and not ran_ok:
                errors.append(f"{pid} is done but its tests did not all pass: {states}")
    covered = sum(r["passing"] for r in rows)
    return {"entries": len(ent), "covered": covered, "rows": rows, "errors": errors, "junit": runs is not None}


def to_md(rep: dict) -> str:
    n, c = rep["entries"], rep["covered"]
    pct = round(100 * c / n, 1) if n else 0.0
    line = f"**Playbook coverage:** {c}/{n} entries ({pct}%) have a marked test that " + \
           ("ran and passed" if rep["junit"] else "exists")
    out = ["#### Playbooks (docs/playbooks.md)", "", line]
    if rep["errors"]:
        out += ["", "❌ " + "; ".join(rep["errors"])]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--junit", type=Path)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args(argv)
    md = a.root / "docs/playbooks.md"
    if not md.exists():
        print("no docs/playbooks.md")
        return 0
    rep = check(md, a.root / "tests", a.junit)
    text = to_md(rep)
    print(text, end="")
    if a.md:
        a.md.write_text(text)
    return 1 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
