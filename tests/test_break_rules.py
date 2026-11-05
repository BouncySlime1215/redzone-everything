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


# ---- breaks.network_lengths (default off) ---------------------------------------------------
def _nba_timeout(national, nets):
    from redzone.models import GameState, League, Phase, Stoppage, Team
    g = GameState(game_id="g", league=League.NBA, home=Team("H"), away=Team("A"), phase=Phase.LIVE)
    g.stoppage, g.national_tv, g.broadcasts = Stoppage.TIMEOUT, national, nets
    return g


def test_network_offset_nba_national_vs_local():
    """NBA reads Data A's 5-season cdn cells (national / local), long timeouts by default."""
    cdn = LENGTHS["nba_cdn"]["table"]["timeout_long"]
    g = _nba_timeout(True, ["ESPN"])
    assert breaks.network_offset(g) == cdn["national"]["q50"] - cdn["*"]["q50"] > 15
    assert breaks.network_offset(_nba_timeout(False, ["NBCS-WSH"])) == cdn["local"]["q50"] - cdn["*"]["q50"] < 0
    assert "personal-use" in LENGTHS["nba_cdn"]["license"] or "personal" in LENGTHS["nba_cdn"]["license"]


def test_network_offset_other_leagues_use_named_networks():
    from redzone.models import GameState, League, Phase, Stoppage, Team
    tab = LENGTHS["table"]["nfl"]["score_break"]
    g = GameState(game_id="n", league=League.NFL, home=Team("H"), away=Team("A"), phase=Phase.LIVE)
    g.stoppage, g.national_tv, g.broadcasts = Stoppage.SCORE_BREAK, True, ["NBC"]
    assert breaks.network_offset(g) == tab["NBC"]["q50"] - tab["*"]["q50"]


def test_with_network_is_off_by_default_and_skips_short_breaks():
    g = _nba_timeout(True, ["ESPN"])
    f = breaks.Forecast(1.0, breaks.LEN["nba_long"], "long timeout", True)
    assert breaks.with_network(f, g, []) is f                         # flag off: unchanged
    on = breaks.with_network(f, g, ["nba"])
    assert on.expect_s == f.expect_s + breaks.network_offset(g)
    short = breaks.Forecast(0.15, 50.0, "team timeout", True)         # not a visit: never stretched
    assert breaks.with_network(short, g, ["nba"]) is short
    book = breaks.BreakBook(clock=lambda: 0.0, network=lambda: [])
    assert book.forecast(g).expect_s == breaks.forecast(g).expect_s


def test_default_config_ships_flag_off():
    cfg = yaml.safe_load((Path(breaks.__file__).parents[1] / "default_config.yaml").read_text())
    assert cfg["breaks"]["network_lengths"] == []


def test_dead_tracker_takes_network_offset_only_when_set():
    from redzone.decider.cuts import MEDIAN_S, DeadTracker, Window
    from redzone.models import League
    off = breaks.network_kind_offsets(_nba_timeout(True, ["ESPN"]))
    if "timeout" not in off:
        return
    dt = DeadTracker(League.NBA)
    w = Window(start=100.0, end=None, kind="timeout")
    dt._windows = [w]
    dt.windows = lambda: [w]
    base = dt.resume_eta(100.0)
    assert base == 100.0 + MEDIAN_S[League.NBA]["timeout"]
    dt.net_offset = off
    assert dt.resume_eta(100.0) == base + off["timeout"]
