"""F8: datasets and models built on restrictive-terms sources carry the personal-use tag (Nick, Nov 4 11:20 PM)."""
import json

from scripts import license_check as lc

TERMS = {"tag": "personal-use, not for commercial release",
         "sources": {"espn": "restrictive", "retrosheet": "open"}}


def _f(tmp_path, doc, name="m.json"):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return p


def test_restrictive_source_needs_the_tag(tmp_path):
    p = _f(tmp_path, {"rows": [{"source": "espn", "license": "ESPN terms"},
                               {"source": "espn", "license": "ESPN terms", "tag": TERMS["tag"]}]})
    got = lc.check([p], TERMS)
    assert [x[0] for x in got] == ["FAIL"] and "[0]" in got[0][1]


def test_open_source_needs_only_a_license_and_unknown_is_flagged(tmp_path):
    p = _f(tmp_path, {"shards": [{"source": "retrosheet", "license": "notice"}, {"source": "retrosheet"},
                                 {"source": "mystery", "license": "?"}]})
    got = lc.check([p], TERMS)
    assert [x[0] for x in got] == ["FAIL", "WARN"]


def test_a_model_card_lists_its_sources(tmp_path):
    p = _f(tmp_path, {"seed": 0, "sources": ["retrosheet", "espn"], "license": TERMS["tag"]}, "grades.json")
    assert lc.check([p], TERMS) == []
    p = _f(tmp_path, {"seed": 0, "sources": ["retrosheet", "espn"], "license": "open"}, "grades.json")
    assert lc.check([p], TERMS)[0][0] == "FAIL"


def test_tags_list_counts(tmp_path):
    p = _f(tmp_path, [{"source": "ESPN", "license": "x", "tags": ["synthetic", TERMS["tag"]]}])
    assert lc.check([p], TERMS) == []


def test_the_repo_ledgers_pass():
    assert lc.main([]) == 0
