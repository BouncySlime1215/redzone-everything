"""[Data F] The broadcast-format table (redzone/scoring/broadcast/) is well formed, licensed, and agrees
with the marks the engine hard-codes in redzone/scoring/breaks.py."""
import json
from pathlib import Path

import yaml

from redzone.scoring import breaks

DIR = Path(breaks.__file__).with_name("broadcast")
RULES = yaml.safe_load((DIR / "break_rules.yaml").read_text())
LENGTHS = json.loads((DIR / "break_lengths.json").read_text())
BY_ID = {r["id"]: r for r in RULES["rules"]}


def test_every_row_has_source_and_license():
    assert RULES["license_default"] and "personal-use" in RULES["license_default"]
    for r in RULES["rules"]:
        assert r["source_url"].startswith("https://"), r["id"]
        assert r["confidence"] in ("verified", "unverified"), r["id"]
        assert r["league"] in ("nfl", "cfb", "nba", "cbb", "nhl", "mlb"), r["id"]
    assert len(BY_ID) == len(RULES["rules"])            # ids unique


def test_all_six_leagues_covered():
    assert {r["league"] for r in RULES["rules"]} == {"nfl", "cfb", "nba", "cbb", "nhl", "mlb"}


def test_marks_agree_with_engine():
    assert tuple(BY_ID["cbb_media_timeouts"]["clock_marks_remaining_s"]) == breaks.CBB_MARKS
    assert tuple(BY_ID["nhl_tv_timeouts"]["clock_marks_remaining_s"]) == breaks.NHL_MARKS
    nba = (BY_ID["nba_mandatory_1"]["clock_marks_remaining_s"][0], BY_ID["nba_mandatory_2"]["clock_marks_remaining_s"][0])
    assert nba == breaks.NBA_MARKS


def test_measured_keys_resolve_and_are_licensed():
    assert "personal-use" in LENGTHS["license"] and "train nights" in LENGTHS["source"]
    tab = LENGTHS["table"]
    for r in RULES["rules"]:
        m = r.get("measured")
        if m:
            assert "*" in tab[m["league"]][m["kind"]], r["id"]


def test_measured_runs_over_official():
    """A break measured to the real restart can't be shorter than the rule's own length."""
    tab = LENGTHS["table"]
    for rid in ("nhl_tv_timeouts", "nba_mandatory_1"):
        r = BY_ID[rid]
        off = r["official_len_s"]
        off = min(off.values()) if isinstance(off, dict) else off
        assert tab[r["measured"]["league"]][r["measured"]["kind"]]["*"]["q50"] >= off
