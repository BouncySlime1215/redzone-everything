"""Tests for scripts/markets/lines_history.py (markets worker D3: open/close line history)."""
import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "markets"))
import lines_history as lh  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures", "markets", "lines")
FIELDS = {"source", "league", "game_key", "ext_game_id", "market_id", "market_kind", "phase", "ts",
          "p_home", "line", "volume", "n_trades", "interval_s", "license", "tag"}


def _by(ticks, gk, kind, phase):
    hit = [t for t in ticks if t["game_key"] == gk and t["market_kind"] == kind and t["phase"] == phase]
    assert len(hit) == 1, (gk, kind, phase, hit)
    return hit[0]


def _contract(ticks):
    for t in ticks:
        assert set(t) == FIELDS
        assert t["tag"] == "personal-use, not for commercial release"
        assert t["interval_s"] == 0
        assert t["license"]
        assert t["p_home"] is None or 0 < t["p_home"] < 1
        if t["market_kind"] in ("spread", "total"):
            assert t["p_home"] is None and t["line"] is not None


def test_odds_math():
    assert lh.american_to_prob(-110) == pytest.approx(110 / 210)
    assert lh.american_to_prob(150) == pytest.approx(0.4)
    assert lh.american_to_prob("NL") is None and lh.american_to_prob(50) is None
    assert lh.p_home_from_moneylines(-110, -110) == pytest.approx(0.5)
    ph, pa = 175 / 275, 100 / 255
    assert lh.p_home_from_moneylines(-175, 155) == pytest.approx(ph / (ph + pa), abs=1e-6)
    assert lh.decimal_to_prob(2.5) == pytest.approx(0.4)
    assert lh._num("pk") == 0.0 and lh._num("o6½") == 6.5


def test_nflverse_closing_lines():
    ticks = lh.nflverse_rows_to_ticks(lh.read_table(os.path.join(FIX, "nflverse_games.csv")))
    _contract(ticks)
    assert {t["phase"] for t in ticks} == {"close"}
    assert len(ticks) == 6  # 2 games x (ml, spread, total); future game without lines skipped
    gk = "nfl:20240905:BAL@KC"
    sp = _by(ticks, gk, "spread", "close")
    assert sp["line"] == -3.0  # nflverse +3 = home favored -> contract -3
    assert sp["ext_game_id"] == "2024_01_BAL_KC"
    kick = dt.datetime(2024, 9, 6, 0, 20, tzinfo=dt.timezone.utc).timestamp()  # 20:20 EDT
    assert sp["ts"] == kick - lh.CLOSE_LEAD_S
    ml = _by(ticks, gk, "moneyline", "close")
    assert 0.55 < ml["p_home"] < 0.6
    assert _by(ticks, gk, "total", "close")["line"] == 46.0
    assert _by(ticks, "nfl:20240906:GB@PHI", "spread", "close")["line"] == 1.5


def test_sbro_nba_open_close_and_flip():
    rows = lh.read_table(os.path.join(FIX, "nba odds 2019-20.csv"))
    ticks = lh.sbro_rows_to_ticks(rows, "nba", lh._season_years("nba odds 2019-20.csv"))
    _contract(ticks)
    assert all(t["ext_game_id"] == "" for t in ticks)
    g1 = "nba:20191022:PHI@BOS"
    assert _by(ticks, g1, "spread", "open")["line"] == -4.5
    assert _by(ticks, g1, "spread", "close")["line"] == -5.0
    assert _by(ticks, g1, "total", "open")["line"] == 213.5
    assert _by(ticks, g1, "total", "close")["line"] == 211.0
    ph, pa = 175 / 275, 100 / 255
    assert _by(ticks, g1, "moneyline", "close")["p_home"] == pytest.approx(ph / (ph + pa), abs=1e-6)
    o, c = _by(ticks, g1, "spread", "open")["ts"], _by(ticks, g1, "spread", "close")["ts"]
    assert c - o == lh.OPEN_LEAD_S["nba"] - lh.CLOSE_LEAD_S
    # favorite flips: visitor -3 at open (home +3) -> home -1.5 at close
    g2 = "nba:20191022:LAL@GSW"
    assert _by(ticks, g2, "spread", "open")["line"] == 3.0
    assert _by(ticks, g2, "spread", "close")["line"] == -1.5
    assert _by(ticks, g2, "total", "close")["line"] == 227.0
    # pick'em with no moneyline
    g3 = "nba:20191023:MIA@ORL"
    assert _by(ticks, g3, "spread", "close")["line"] == 0.0
    assert not [t for t in ticks if t["game_key"] == g3 and t["market_kind"] == "moneyline"]
    # January belongs to the second season year
    assert _by(ticks, "nba:20200105:UTA@DEN", "spread", "open")["line"] == -7.0


def test_sbro_nhl_moneyline_open_close():
    rows = lh.read_table(os.path.join(FIX, "nhl odds 2021-22.csv"))
    ticks = lh.sbro_rows_to_ticks(rows, "nhl", (2021, 2022))
    _contract(ticks)
    gk = "nhl:20211013:PIT@TBL"
    po, pc = _by(ticks, gk, "moneyline", "open")["p_home"], _by(ticks, gk, "moneyline", "close")["p_home"]
    assert 0.5 < po < pc < 0.7  # line moved toward Tampa
    assert _by(ticks, gk, "spread", "close")["line"] == -1.5
    assert _by(ticks, gk, "total", "open")["line"] == 6.0
    assert _by(ticks, gk, "total", "close")["line"] == 6.5


def test_odds_api_consensus():
    with open(os.path.join(FIX, "odds_api_nba.json")) as f:
        ev = json.load(f)
    obs = dt.datetime(2026, 11, 5, 23, 0, tzinfo=dt.timezone.utc).timestamp()
    ticks = lh.odds_api_to_ticks(ev, "nba", observed_ts=obs)
    _contract(ticks)
    gk = "nba:20261105:PHI@BOS"  # 00:30Z Nov 6 = 7:30 PM ET Nov 5
    ml = _by(ticks, gk, "moneyline", "pregame")
    ps = sorted(lh.p_home_from_moneylines(h, a) for h, a in ((-200, 170), (-180, 155), (-190, 160)))
    assert ml["p_home"] == pytest.approx(ps[1], abs=1e-6)
    assert ml["ts"] == obs and ml["ext_game_id"] == "abc123"
    assert _by(ticks, gk, "spread", "pregame")["line"] == -5.25
    assert _by(ticks, gk, "total", "pregame")["line"] == 221.0
    # without an observed clock, ts = latest bookmaker update; after tip -> live
    late = lh.odds_api_to_ticks(ev, "nba", observed_ts=obs + 7200)
    assert {t["phase"] for t in late} == {"live"}
    assert lh.odds_api_to_ticks(ev, "nba")[0]["ts"] == dt.datetime(
        2026, 11, 5, 22, 5, tzinfo=dt.timezone.utc).timestamp()


def test_odds_api_decimal_prices():
    ev = [{"id": "d1", "commence_time": "2026-11-06T00:00:00Z", "home_team": "Boston Bruins",
           "away_team": "Toronto Maple Leafs", "bookmakers": [{"key": "b", "markets": [
               {"key": "h2h", "outcomes": [{"name": "Boston Bruins", "price": 1.8},
                                           {"name": "Toronto Maple Leafs", "price": 2.1}]}]}]}]
    t = lh.odds_api_to_ticks(ev, "nhl", observed_ts=0)[0]
    assert t["game_key"] == "nhl:20261105:TOR@BOS"
    assert t["p_home"] == pytest.approx((1 / 1.8) / (1 / 1.8 + 1 / 2.1), abs=1e-6)


def test_odds_api_schedule_fits_free_tier():
    assert lh.monthly_credits() <= 450  # 500/month minus a 50-credit reserve


def test_cli_writes_contract_files(tmp_path):
    out = tmp_path / "markets"
    assert lh.main(["--source", "sbro", "--in", FIX, "--league", "nba", "--out", str(out)]) == 0
    man = json.loads((out / "sbro" / "manifest.json").read_text())
    assert man["ts_quality"] == "approx" and man["n_games"] >= 4
    files = sorted((out / "sbro" / "nba").glob("*.jsonl"))
    assert files and all(len(f.stem) == 8 for f in files)
    rows = [json.loads(l) for f in files for l in f.read_text().splitlines()]
    _contract(rows)
    assert lh.main(["--source", "nflverse", "--in", os.path.join(FIX, "nflverse_games.csv"),
                    "--out", str(out)]) == 0
    assert (out / "nflverse_lines" / "manifest.json").exists()


def test_sbro_xlsx_unnamed_odds_columns(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Rot", "VH", "Team", "Final", "Open", "Close", "PuckLine", None, "OpenOU", None,
               "CloseOU", None])
    ws.append([1013, 1, "V", "Pittsburgh", 6, 150, 165, 1.5, -150, 6, -110, 6.5, 105])
    ws.append([1013, 2, "H", "TampaBay", 2, -170, -190, -1.5, 130, 6, -110, 6.5, -125])
    p = tmp_path / "nhl odds 2021-22.xlsx"
    wb.save(p)
    rows = lh.read_table(str(p))
    assert "PuckLineOdds" in rows[0] and "CloseOUOdds" in rows[0]
    ticks = lh.sbro_rows_to_ticks(rows, "nhl", (2021, 2022))
    assert len(ticks) == 5  # ml open/close, puck line close, total open/close
