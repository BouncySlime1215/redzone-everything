"""Offline tests for scripts/markets/polymarket.py against fixtures shaped like the documented responses."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "markets" / "polymarket"
_spec = importlib.util.spec_from_file_location("polymarket", ROOT / "scripts" / "markets" / "polymarket.py")
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)

CONTRACT = {"source", "league", "game_key", "market_id", "market_kind", "phase", "ts", "p_home", "line",
            "volume", "n_trades", "interval_s", "license", "tag"}


def _load(name):
    return json.loads((FIX / name).read_text())


def _markets():
    return {m["id"]: m for m in _load("markets_keyset.json")["markets"]}


def test_sports_tag_and_ordering():
    sports = _load("sports.json")
    assert pm.pick_tag(sports, "nba") == 745
    assert pm.pick_tag(sports, "cbb") == 100149
    assert pm.pick_tag(sports, "nfl") is None
    assert pm.sport_ordering(sports, "epl") == "home"


def test_two_team_moneyline_picks_home_token():
    p = pm.parse_market(_markets()["540817"], "nba", "away")
    assert p["market_id"] == "222" and p["invert"] is False
    assert p["game_key"] == "nba:20250115:LAL@BOS"
    assert p["start_ts"] == 1736987400.0


def test_will_away_team_win_is_inverted():
    p = pm.parse_market(_markets()["540818"], "nba", "away")
    assert p["market_id"] == "333" and p["invert"] is True
    assert p["game_key"] == "nba:20250116:GSW@NYK"
    ticks = pm.history_to_ticks({"history": [{"t": 1737073800, "p": 0.3}]}, p, "nba")
    assert ticks[0]["p_home"] == 0.7


def test_non_moneyline_and_three_way_rules():
    ms = _markets()
    assert pm.parse_market(ms["540819"], "nba", "away") is None          # spread
    home = pm.parse_market(ms["540820"], "epl", "home")
    assert home["market_id"] == "777" and home["invert"] is False
    assert home["game_key"] == "epl:20250115:CHE@ARS"
    assert pm.parse_market(ms["540821"], "epl", "home") is None          # away-win: not 1 - p_home
    assert pm.parse_market(ms["540822"], "epl", "home") is None          # draw


def test_team_abbr_mapping():
    assert pm.team_abbr("nba", "Trail Blazers") == "POR"
    assert pm.team_abbr("nba", "Los Angeles Lakers") == "LAL"
    assert pm.team_abbr("nhl", "la") == "LAK"
    assert pm.team_abbr("nfl", "Jets") == "NYJ" and pm.team_abbr("nhl", "Jets") == "WPG"
    assert pm.team_abbr("mlb", "Nonexistent Club") == ""
    assert pm.team_abbr("cfb", "uga") == "UGA"


def test_history_to_ticks_contract_and_phases():
    p = pm.parse_market(_markets()["540817"], "nba", "away")
    rows = [pm.scrub(t) for t in pm.history_to_ticks(_load("prices_history.json"), p, "nba")]
    assert len(rows) == 4                                                 # duplicate minute dropped
    assert all(set(r) == CONTRACT for r in rows)
    assert [r["phase"] for r in rows] == ["pregame", "live", "live", "live"]
    assert rows[-1]["p_home"] == 0.55 and rows[0]["interval_s"] == 60
    assert all(r["tag"] == "personal-use, not for commercial release" for r in rows)
    assert all(r["license"].startswith("yellow-B") for r in rows)


def test_no_wallet_fields_survive():
    trade = _load("data_trades.json")["data"][0]
    clean = pm.scrub(trade)
    assert not set(clean) & pm.DROP_KEYS
    assert "0x5668" not in json.dumps(clean)


def test_history_windows_cover_game():
    p = pm.parse_market(_markets()["540817"], "nba", "away")
    w = pm.history_windows(p)
    assert w[0][0] == int(p["start_ts"] - pm.PRE_S)
    assert w[-1][1] >= int(p["start_ts"]) + 3600
    assert all(b - a <= pm.HISTORY_CHUNK_S for a, b in w)


def test_dailies_container_uses_market_question_and_legacy_is_skipped():
    ms = _markets()
    p = pm.parse_market(ms["601001"], "nba", "away")                     # event title is just "NBA"
    assert p["game_key"] == "nba:20251021:HOU@OKC" and p["market_id"] == "1302"
    assert pm.parse_market(ms["500001"], "nba", "away") is None          # Apr 2024: home-first era


def test_pick_tag_prefers_league_specific_over_sport_wide():
    sports = _load("sports.json")
    assert pm.pick_tag(sports, "elc") == 102643 and pm.pick_tag(sports, "epl") == 82
