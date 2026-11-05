"""The data scout ledger (EXP-097) stays valid: every source has a license line, B rows say so, md is fresh."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scout_ledger as SL  # noqa: E402


def test_scout_json_valid_and_rendered():
    assert SL.main(["--check"]) == 0


def test_red_rows_and_bad_b_rows_rejected():
    d = json.loads(SL.SRC.read_text())
    s = dict(d["sources"][0], id="x-red", verdict="red")
    t = dict(d["sources"][0], id="x-b", verdict="yellow-B", license="no terms read")
    errs = SL.check({"sources": [s, t]})
    assert any("x-red" in e for e in errs) and any("x-b" in e for e in errs)


def test_rank_is_info_per_gb():
    a = {"est_tokens_m": 10, "novelty": 1.0, "raw_gb": 1}
    b = {"est_tokens_m": 100, "novelty": 1.0, "raw_gb": 50}
    assert SL.score(a) > SL.score(b)
