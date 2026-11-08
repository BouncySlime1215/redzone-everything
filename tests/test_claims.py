import json
import time

from scripts import claims


def write(d, card, files, expires_in=3600):
    (d / f"{card}.json").write_text(json.dumps({"card": card, "files": files, "expires": time.time() + expires_in}))


def test_overlap_with_someone_elses_open_claim(tmp_path):
    write(tmp_path, "A", ["redzone/decider/cuts.py", "tests/test_cuts*.py"])
    write(tmp_path, "B", ["redzone/tv.py"])
    cs = claims.load(tmp_path)
    assert claims.overlaps(["redzone/decider/cuts.py", "redzone/fsm.py"], cs, own={"B"}) == [("redzone/decider/cuts.py", "A")]
    assert claims.overlaps(["tests/test_cuts_more.py"], cs, own={"A"}) == []


def test_expired_claims_and_shared_files_never_block(tmp_path):
    write(tmp_path, "old", ["redzone/tv.py"], expires_in=-1)
    write(tmp_path, "C", ["docs/memory/decisions.md"])
    cs = claims.load(tmp_path)
    assert claims.overlaps(["redzone/tv.py", "docs/memory/decisions.md"], cs, own=set()) == []


def test_own_card_comes_from_the_claim_file_in_the_diff():
    assert claims.own_cards(["docs/claims/fix-tv.json", "redzone/tv.py"]) == {"fix-tv"}


def test_new_refuses_shared_files(tmp_path, monkeypatch):
    monkeypatch.setattr(claims, "DIR", tmp_path)
    assert claims.main(["new", "X", "--files", "docs/status.md"]) == 1
    assert claims.main(["new", "X", "--files", "redzone/tv.py"]) == 0
    assert claims.main(["new", "Y", "--files", "redzone/tv.py"]) == 1      # already claimed by X
    assert claims.main(["done", "X"]) == 0
    assert claims.main(["new", "Y", "--files", "redzone/tv.py"]) == 0
