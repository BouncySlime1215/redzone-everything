"""The city map and its contract checker (scripts/citymap.py, scripts/contract_check.py,
redzone/lab/citymap.py, rules in redzone/contracts/map.py)."""
import json
from pathlib import Path

from redzone.lab import citymap as C
from scripts import citymap as S
from scripts import contract_check as K

ROOT = Path(__file__).resolve().parents[1]


def _tree(tmp: Path, files: dict[str, str]) -> Path:
    base = {"redzone/__init__.py": "", "redzone/scoring/__init__.py": "", "redzone/scoring/drive.py": "class DriveScorer: ...\n",
            "redzone/web/__init__.py": "", "agent/__init__.py": "", "scripts/run.py": "import redzone.a\nimport redzone.web.page\n",
            "tests/test_x.py": "import redzone.only_tests\n"}
    for p, src in {**base, **files}.items():
        (tmp / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp / p).write_text(src)
    return tmp


def _rules(tmp, files):
    return {(v["rule"], v["module"]) for v in C.violations(C.build(_tree(tmp, files)))}


def test_each_rule_catches_its_violation(tmp_path):
    got = _rules(tmp_path, {
        "redzone/a.py": "import redzone.b\nFLIP_S = 3.0\nWAIT_GAP_S = 2\n",          # timing cost literal
        "redzone/b.py": "WAIT_GAP_S = 2\n",                                            # defined twice
        "redzone/web/page.py": "from redzone.scoring.drive import DriveScorer\nURL = 'https://site.api.espn.com/x'\n",
        "redzone/only_tests.py": "", "redzone/lost.py": "",
    })
    assert ("timing-literal", "redzone.a") in got
    assert {("defined-twice", "redzone.a"), ("defined-twice", "redzone.b")} <= got
    assert ("boundary", "redzone.web.page") in got                                    # ESPN outside sources/
    assert ("second-scoring-path", "redzone.web.page") in got                          # a scorer outside the path
    assert ("dead", "redzone.only_tests") in got and ("orphan", "redzone.lost") in got
    assert not any(m == "redzone.b" and r in ("orphan", "dead") for r, m in got)      # reached via redzone.a


def test_waits_and_offline_modules_are_not_violations(tmp_path):
    got = _rules(tmp_path, {
        "redzone/a.py": "FLIP_POLL_S = 0.12\nPRELOAD_BACKOFF_S = 120\n",               # waits, not costs
        "redzone/b.py": "",
        "redzone/lab/__init__.py": "", "redzone/lab/fit.py": "from redzone.scoring import drive\n",   # offline
        "redzone/sources/__init__.py": "", "redzone/sources/espn.py": "U = 'https://site.api.espn.com/'\n",
        "redzone/web/page.py": "",
    })
    assert not {r for r, m in got if m in ("redzone.a", "redzone.lab.fit", "redzone.sources.espn")} - {"orphan", "dead"}


def test_relative_imports_and_main_blocks_count(tmp_path):
    got = _rules(tmp_path, {
        "redzone/a.py": "from . import b\n", "redzone/b.py": "",
        "redzone/web/page.py": "", "redzone/tool.py": "if __name__ == '__main__':\n    pass\n",
    })
    assert not any(m in ("redzone.b", "redzone.tool") for _, m in got)


def test_baseline_fails_only_on_new_violations():
    v = [{"key": "a|1", "rule": "orphan"}, {"key": "b|2", "rule": "dead"}]
    new, fixed = K.check(v, {"a|1", "c|3"})
    assert [x["key"] for x in new] == ["b|2"] and fixed == ["c|3"]


def test_the_repo_has_no_new_contract_violation_and_every_module_has_an_owner():
    m = C.build(ROOT)
    assert all(x["owner"] != "?" for x in m["modules"].values())
    new, _ = K.check(C.violations(m), K.load_baseline())
    assert not new, "\n".join(f"{x['rule']}: {x['where']}: {x['detail']}" for x in new)


def test_citymap_json_has_the_northstar_shape():
    m = C.build(ROOT)
    v = C.violations(m)
    js = S.payload(m, v)
    assert all({"rule", "where"} <= set(x) for x in js["violations"]) and len(js["violations"]) == len(v)
    json.dumps(js)
    assert "_src" not in next(iter(js["modules"].values()))
    md = S.render(m, v)
    assert md.startswith("# City map") and "```mermaid" in md
