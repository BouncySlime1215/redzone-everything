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


# ---- call, data and machine edges; --focus; --impact -------------------------------------------

@pytest.fixture
def edges(repo: Path) -> Path:
    _write(repo, "redzone/b.py", '"""Module b."""\nX = 1  # STK-06\n\n\n'
           'def helper(n: int) -> int:\n    """Add one."""\n    return n + 1\n\n\ndef _private():\n    pass\n')
    _write(repo, "redzone/pkg/d.py", '"""D."""\ny = 2\n\n\ndef make():\n    pass\n\n\n'
           "class Thing:\n    pass\n")
    _write(repo, "redzone/fsm.py", '"""Machines."""\n\n\nclass Spec:\n    def new(self):\n        pass\n')
    _write(repo, "redzone/machines/__init__.py", "")
    _write(repo, "redzone/machines/m.py",
           '"""Specs."""\nfrom redzone.fsm import Spec\n\n'
           'PARK = Spec("park.state", owner="holder.Holder.park_m", doc="", states={}, edges={}, initial="a")\n'
           'THING = Spec(name="thing.state", owner="pkg.Thing.m", doc="", states={}, edges={}, initial="a")\n')
    _write(repo, "redzone/holder.py", '"""Holds the park machine."""\n\n\nclass Holder:\n    pass\n')
    _write(repo, "redzone/user.py",
           '"""Uses everything."""\nimport redzone.pkg.d\nfrom redzone import b as bb\n'
           "from redzone.machines.m import PARK\n\nm = PARK.new()\nbb.helper(1)\nredzone.pkg.d.make()\n")
    _write(repo, "redzone/writer.py",
           '"""Writes."""\nfrom pathlib import Path\n\nDATA = Path("data")\nLOG = DATA / "jumps.jsonl"\n\n\n'
           'def save(row):\n    with open(LOG, "a") as f:\n        f.write(row)\n\n\n'
           'def snap():\n    (DATA / "held.json").write_text("{}")\n')
    _write(repo, "redzone/reader.py",
           '"""Reads."""\nimport json\nimport sqlite3\nfrom pathlib import Path\n\nimport redzone.writer\n'
           'STATE = "data/held.json"\n\n\nclass P:\n    log: Path = Path("data") / "x.csv"\n\n'
           "    def go(self):\n        return open(self.log)\n\n\n"
           'def load():\n    return json.loads(Path("data/jumps.jsonl").read_text())\n\n\n'
           'def db():\n    return sqlite3.connect("data/r.db")\n')
    _write(repo, "tests/test_b.py", "from redzone import b\n")
    _write(repo, "tests/test_user.py", "import redzone.user\n")
    _write(repo, "tests/fixtures/x/redzone/b.py", "import redzone.b\n")  # a fixture repo, not a test
    return repo


def test_call_edges(edges):
    m = citymap.build(edges)["modules"]
    assert m["redzone/user.py"]["calls"] == [
        "redzone/b.py:helper", "redzone/machines/m.py:PARK", "redzone/pkg/d.py:make"]
    assert "redzone/fsm.py:Spec" in m["redzone/machines/m.py"]["calls"]
    assert m["redzone/b.py"]["calls"] == []  # no self edges, stdlib ignored


def test_data_edges(edges):
    g = citymap.build(edges)
    m = g["modules"]
    assert m["redzone/writer.py"]["data"] == {
        "reads": [], "writes": ["data/held.json", "data/jumps.jsonl"], "uses": []}
    assert m["redzone/reader.py"]["data"] == {
        "reads": ["data/jumps.jsonl", "data/x.csv"], "writes": [], "uses": ["data/held.json", "data/r.db"]}
    jumps = g["data_files"]["data/jumps.jsonl"]
    assert jumps == {"readers": ["redzone/reader.py"], "writers": ["redzone/writer.py"], "users": []}
    assert g["summary"]["data_files"] == 4


def test_machine_edges(edges):
    g = citymap.build(edges)
    m = g["modules"]
    assert m["redzone/machines/m.py"]["machines"]["declares"] == ["park.state", "thing.state"]
    assert m["redzone/holder.py"]["machines"]["drives"] == ["park.state"]  # Spec.owner
    assert m["redzone/user.py"]["machines"]["drives"] == ["park.state"]  # PARK.new()
    assert m["redzone/pkg/d.py"]["machines"]["drives"] == ["thing.state"]  # package -> defining submodule
    assert g["machines"]["park.state"] == {
        "declared_in": "redzone/machines/m.py", "owner": "holder.Holder.park_m",
        "driven_by": ["redzone/holder.py", "redzone/user.py"]}
    md = citymap.render_md(g)
    assert "## Machines (2)" in md and "## Shared data files (2 of 4)" in md and "## Most-called symbols" in md


def test_focus_ranked_and_budgeted(edges):
    g = citymap.build(edges)
    out = citymap.focus(g, edges, "redzone/b.py", tokens=1500)
    lines = out.splitlines()
    assert lines[0].startswith("# focus redzone/b.py")
    assert lines[1].startswith("redzone/b.py  [hop 0,")
    assert "  def helper(n: int) -> int  # Add one.  [called by 1]" in lines
    assert not any("_private" in x for x in lines)  # signatures only, public only
    hops = [int(x.split("[hop ")[1][0]) for x in lines if "[hop " in x]
    assert hops == sorted(hops) and 1 in hops and 2 in hops  # nearest first
    assert out == citymap.focus(g, edges, "redzone/b.py", tokens=1500)  # deterministic
    short = citymap.focus(g, edges, "redzone/b.py", tokens=40)
    assert len(short) < len(out) and "cut at ~40 tokens" in short
    assert len(short) <= 40 * citymap.CHARS_PER_TOKEN + 200


def test_focus_links_data_and_machines(edges):
    g = citymap.build(edges)
    hop1 = [x.split()[0] for x in citymap.focus(g, edges, "redzone/holder.py").splitlines() if "[hop 1" in x]
    assert hop1 == ["redzone/machines/m.py"]  # only the machine links holder to anything
    out = citymap.focus(g, edges, "redzone/writer.py")
    assert "redzone/reader.py  [hop 1" in out  # imports it and shares data/jumps.jsonl
    assert "~ writes data/held.json; writes data/jumps.jsonl" in out


def test_impact(edges):
    g = citymap.build(edges)
    out = citymap.impact(g, edges, "redzone/b.py")
    assert " depth 1 (" in out and " depth 2 (" in out
    d1 = out.split(" depth 1")[1].split(" depth 2")[0]
    assert "redzone/a.py" in d1 and "redzone/user.py" in d1
    assert "scripts/run.py" in out.split(" depth 2")[1].split("symbols called")[0]  # run -> a -> b
    assert "helper (1)" in out
    assert "tests importing it directly (1):\n  tests/test_b.py" in out
    assert "tests/test_user.py" in out.split("tests importing an importer")[1]
    assert "fixtures" not in out
    assert "north star / serves:\n  STK-06" in out
    a = citymap.impact(g, edges, "redzone/a.py")
    assert "contracts implemented:\n  BreakModel" in a
    w = citymap.impact(g, edges, "redzone/writer.py")
    assert "writes data/jumps.jsonl; also: redzone/reader.py (reader)" in w
    assert "machines declared:\n  park.state, thing.state" in citymap.impact(g, edges, "redzone/machines/m.py")


def test_focus_and_impact_cli(edges, capsys):
    assert citymap.main(["--root", str(edges), "--focus", "redzone/b.py", "--tokens", "200"]) == 0
    assert capsys.readouterr().out.startswith("# focus redzone/b.py")
    assert citymap.main(["--root", str(edges), "--impact", str(edges / "redzone/b.py")]) == 0
    assert capsys.readouterr().out.startswith("# impact redzone/b.py")
    assert citymap.main(["--root", str(edges), "--impact", "redzone/nope.py"]) == 2
    assert not (edges / citymap.MD_OUT).exists()  # the views never write the docs
