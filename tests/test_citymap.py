"""scripts/citymap.py: module graph, owners, serves tags, stable output and --check."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import citymap

ROOT = Path(__file__).resolve().parent.parent

ARCH = """# Architecture

## 4. Owners

### File ownership (one owner per file)

| Owner | Files |
|---|---|
| DECISION | `redzone/{a,b}.py` |
| SWITCHING | `redzone/pkg/*`, `redzone/a.py` |

## 5. Next
"""

NORTHSTAR = """# North Star

| metric | target | current |
|---|---|---:|
| Mid-play cuts + entries (per 100 landings) | 0 | 2.45 |
| Landings in dead time | >= 98% | 97.5% |
"""


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _write(tmp_path, "redzone/__init__.py", '"""Root package."""\n')
    _write(tmp_path, "redzone/a.py", '"""Module a. More text."""\nimport redzone.b\n'
           "from redzone.contracts.breaks import BreakModel\n\n\nclass Mine(BreakModel):\n    pass\n")
    _write(tmp_path, "redzone/b.py", '"""Module b."""\nX = 1  # STK-06 and FAKE-99 and STK-99\n')
    _write(tmp_path, "redzone/contracts/__init__.py", "")
    _write(tmp_path, "redzone/contracts/breaks.py", '"""Break contract."""\nfrom typing import Protocol\n\n\n'
           "class BreakModel(Protocol):\n    pass\n")
    _write(tmp_path, "redzone/pkg/__init__.py", "from . import c\n")
    _write(tmp_path, "redzone/pkg/c.py", "from ..b import X\nfrom .d import y\n")
    _write(tmp_path, "redzone/pkg/d.py", '"""D."""\ny = 2\n')
    _write(tmp_path, "redzone/lonely.py", '"""Nobody imports me."""\n')
    _write(tmp_path, "scripts/run.py", '"""Entry point."""\nfrom redzone import a\nfrom scripts import helper\n')
    _write(tmp_path, "scripts/helper.py", '"""Helper script."""\n')
    _write(tmp_path, "agent/__init__.py", "")
    _write(tmp_path, "agent/policy.py", '"""Agent policy."""\nimport redzone.b\n')
    _write(tmp_path, "agent/watchdog.py", '"""Watchdog."""\nfrom agent.policy import guard\n')
    _write(tmp_path, "docs/architecture.md", ARCH)
    _write(tmp_path, "docs/playbooks.md", "| id |\n|---|\n| STK-06 | pause buffer lost |\n")
    _write(tmp_path, "docs/northstar.md", NORTHSTAR)
    _write(tmp_path, "docs/citymap_tags.json",
           json.dumps({"redzone/pkg/*": ["Landings in dead time"], "redzone/a.py": ["STK-06"]}))
    return tmp_path


def test_edges_both_ways(repo):
    m = citymap.build(repo)["modules"]
    a = m["redzone/a.py"]
    assert "redzone/b.py" in a["inputs"] and "redzone/contracts/breaks.py" in a["inputs"]
    assert "uses:BreakModel" in a["inputs"] and a["contracts"] == ["BreakModel"]
    assert "redzone/a.py" in m["redzone/b.py"]["outputs"]
    assert "implements:BreakModel" in a["outputs"]
    assert "scripts/run.py" in a["outputs"] and m["scripts/run.py"]["kind"] == "entry"
    assert m["redzone/pkg/__init__.py"]["kind"] == "package"


def test_agent_and_script_edges(repo):
    m = citymap.build(repo)["modules"]
    assert m["agent/watchdog.py"]["inputs"] == ["agent/policy.py"]
    assert "agent/policy.py" in m["redzone/b.py"]["outputs"]
    assert m["agent/__init__.py"]["kind"] == "package"
    assert "scripts/run.py" in m["scripts/helper.py"]["outputs"]


def test_tag_key_matching_nothing_exits_2(repo):
    _write(repo, "docs/citymap_tags.json", json.dumps({"redzone/gone.py": ["STK-06"]}))
    assert citymap.main(["--root", str(repo)]) == 2


def test_relative_imports(repo):
    m = citymap.build(repo)["modules"]
    assert m["redzone/pkg/c.py"]["inputs"] == ["redzone/b.py", "redzone/pkg/d.py"]
    assert m["redzone/pkg/__init__.py"]["inputs"] == ["redzone/pkg/c.py"]
    assert "redzone/pkg/c.py" in m["redzone/b.py"]["outputs"]


def test_owner_globs_and_braces(repo):
    m = citymap.build(repo)["modules"]
    assert m["redzone/a.py"]["owner"] == "DECISION"  # brace set, first match wins
    assert m["redzone/b.py"]["owner"] == "DECISION"
    assert m["redzone/pkg/c.py"]["owner"] == "SWITCHING"
    assert m["redzone/lonely.py"]["owner"] == "unowned"


def test_serves_role_and_orphans(repo):
    g = citymap.build(repo)
    m = g["modules"]
    assert m["redzone/b.py"]["serves"] == ["STK-06"]  # from source; unknown ids dropped
    assert m["redzone/a.py"]["serves"] == ["STK-06"]  # from the tags file
    assert m["redzone/pkg/d.py"]["serves"] == ["Landings in dead time"]
    assert m["redzone/lonely.py"]["serves"] == ["untagged"]
    assert m["redzone/a.py"]["role"] == "Module a."
    assert m["redzone/pkg/c.py"]["role"] == "no docstring"
    md = citymap.render_md(g)
    assert "- `redzone/lonely.py`" in md.split("## Orphans")[1]
    assert "## DECISION (2)" in md and "## Untagged" in md


def test_unknown_tag_exits_2(repo):
    _write(repo, "docs/citymap_tags.json", json.dumps({"redzone/a.py": ["Made-up metric"]}))
    assert citymap.main(["--root", str(repo)]) == 2


def test_stable_and_check(repo):
    assert citymap.main(["--root", str(repo)]) == 0
    first = [(repo / p).read_bytes() for p in (citymap.MD_OUT, citymap.JSON_OUT)]
    assert citymap.main(["--root", str(repo)]) == 0
    assert [(repo / p).read_bytes() for p in (citymap.MD_OUT, citymap.JSON_OUT)] == first
    assert str(repo) not in first[0].decode() + first[1].decode()
    assert citymap.main(["--root", str(repo), "--check"]) == 0
    _write(repo, "redzone/b.py", '"""Module b, edited."""\n')
    assert citymap.main(["--root", str(repo), "--check"]) == 1
    assert citymap.main(["--root", str(repo)]) == 0
    assert citymap.main(["--root", str(repo), "--check"]) == 0


def test_real_repo_has_every_module():
    mods = citymap.build(ROOT)["modules"]
    files = [*(ROOT / "redzone").rglob("*.py"), *(ROOT / "agent").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]
    assert files
    for p in files:
        assert p.relative_to(ROOT).as_posix() in mods
