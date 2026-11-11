"""docs/STANDARD.md is enforced: every agent report has valid front matter, unique ids, gate ids under the report id,
a dated file name, and docs/agents/INDEX.md is generated from them."""
import pytest

from scripts import doc_index

FM = """---
id: W9-X
title: A test report
date: 2026-11-08
stream: rules
author: test
status: draft
supersedes: []
sources: 3
verified: 2
affects: [scripts/]
gate: [W9-X-01]
---
body
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_index, "ROOT", tmp_path)
    monkeypatch.setattr(doc_index, "AGENTS", tmp_path / "docs" / "agents")
    (tmp_path / "docs" / "agents" / "rules").mkdir(parents=True)
    return tmp_path / "docs" / "agents" / "rules"


def test_a_good_report_passes_and_is_indexed(root):
    p = root / "2026-11-08-a-test.md"
    p.write_text(FM)
    rows, errs = doc_index.problems([p])
    assert errs == [] and rows[0]["id"] == "W9-X"
    out = doc_index.render(rows)
    assert "[A test report](rules/2026-11-08-a-test.md)" in out and "W9-X-01" in out


@pytest.mark.parametrize("name,text", [
    ("2026-11-08-no-fm.md", "no front matter here"),
    ("notdated.md", FM),
    ("2026-11-08-bad-status.md", FM.replace("status: draft", "status: maybe")),
    ("2026-11-08-bad-gate.md", FM.replace("gate: [W9-X-01]", "gate: [OTHER-01]")),
    ("2026-11-08-more-verified.md", FM.replace("verified: 2", "verified: 9")),
    ("2026-11-08-bad-stream.md", FM.replace("stream: rules", "stream: misc")),
])
def test_bad_reports_are_caught(root, name, text):
    p = root / name
    p.write_text(text)
    assert doc_index.problems([p])[1]


def test_duplicate_ids_are_caught(root):
    a, b = root / "2026-11-08-a.md", root / "2026-11-08-b.md"
    a.write_text(FM)
    b.write_text(FM)
    assert any("also used" in e for e in doc_index.problems([a, b])[1])


def test_the_repo_follows_the_standard():
    assert doc_index.main(["--check"]) == 0
