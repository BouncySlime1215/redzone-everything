"""Data H game-context (scripts/gamectx/wiki_context.py): parsing on a synthetic article, numbers only out."""
import csv
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("lxml")
pytest.importorskip("pandas")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from gamectx import wiki_context as W  # noqa: E402

# Synthetic (made-up) article in the shape the MediaWiki parser renders; no real Wikipedia text.
NFL_HTML = """<div class="mw-parser-output">
<table class="infobox"><tr><th>Head coach</th><td>Coach Two</td></tr></table>
<p>Lead paragraph about the season.</p>
<div class="mw-heading mw-heading2"><h2>Offseason</h2></div>
<div class="mw-heading mw-heading3"><h3>Signings</h3></div>
<table class="wikitable"><tr><th>Position</th><th>Player</th></tr><tr><td>QB</td><td>A</td></tr>
<tr><td>WR</td><td>B</td></tr></table>
<div class="mw-heading mw-heading3"><h3>Departures</h3></div>
<table class="wikitable"><tr><th>Position</th><th>Player</th></tr><tr><td>CB</td><td>C</td></tr></table>
<p>The starter was injured and had surgery.</p>
<div class="mw-heading mw-heading2"><h2>Preseason</h2></div>
<table class="wikitable"><tr><th>Week</th><th>Date</th><th>Opponent</th><th>Result</th></tr>
<tr><td>1</td><td>August 10</td><td>Dallas Cowboys</td><td>W 20–10</td></tr></table>
<div class="mw-heading mw-heading2"><h2>Regular season</h2></div>
<table class="wikitable"><tr><th>Week</th><th>Date</th><th>Opponent</th><th>Result</th><th>Record</th></tr>
<tr><td>1</td><td>September 8</td><td>at Dallas Cowboys</td><td>W 27–20</td><td>1–0</td></tr>
<tr><td>2</td><td>September 15</td><td>New York Giants</td><td>L 10–17</td><td>1–1</td></tr>
<tr><td>3</td><td colspan="4">Bye</td></tr>
<tr><td>4</td><td>September 29</td><td>at Philadelphia Eagles</td><td>L 3–30</td><td>1–2</td></tr>
<tr><td>5</td><td>January 4</td><td>Washington Commanders</td><td>W 31–28 (OT)</td><td>2–2</td></tr>
</table>
<div class="mw-heading mw-heading2"><h2>Game summaries</h2></div>
<div class="mw-heading mw-heading3"><h3>Week 1: at Dallas Cowboys</h3></div>
<p>A rivalry game with a late comeback in overtime to snap a losing streak.</p>
<div class="mw-heading mw-heading3"><h3>Week 2: vs. New York Giants</h3></div>
<p>Short one.</p>
<div class="mw-heading mw-heading2"><h2>Postseason</h2></div>
<table class="wikitable"><tr><th>Round</th><th>Date</th><th>Opponent</th><th>Result</th></tr>
<tr><td>Wild Card</td><td>January 12</td><td>Green Bay Packers</td><td>L 14–21</td></tr></table>
</div>"""


def test_team_abbr():
    assert W.team_abbr("at Dallas Cowboys", "nfl") == "DAL"
    assert W.team_abbr("Washington Football Team", "nfl") == "WAS"
    assert W.team_abbr("@ New Orleans Hornets", "nba") == "NOP"
    assert W.team_abbr("Charlotte Hornets", "nba") == "CHA"
    assert W.team_abbr("Seattle SuperSonics", "nba") == "OKC"
    assert W.team_abbr("Portland Trail Blazers", "nba") == "POR"
    assert W.team_of_title("2023–24 Washington Wizards season", "nba") == "WAS"
    assert W.team_of_title("2009 Pittsburgh Steelers season", "nfl") == "PIT"


def test_article_features_and_tables():
    f = W.article_features(NFL_HTML, "nfl", 2024)
    g = f["games"]
    assert [x["opp"] for x in g] == ["DAL", "NYG", "PHI", "WAS", "GB"]          # preseason dropped, bye skipped
    assert g[0]["date"] == "2024-09-08" and g[3]["date"] == "2025-01-04"        # Jan -> next year
    assert g[0]["home"] == 0 and g[1]["home"] == 1
    assert g[-1]["postseason"] == 1 and g[-1]["week"] == 101
    assert f["offseason"]["off_signings"] == 2 and f["offseason"]["off_departures"] == 1
    assert f["offseason"]["off_tag_injury"] >= 2
    assert f["summaries"][1]["tag_rivalry"] == 1 and f["summaries"][1]["tag_comeback"] == 1
    games, weeks, ts = W.season_tables("nfl", 2024, "WAS", f, 1, 2, {"WAS": hash("coach one")})
    assert ts["coach_change"] == 1
    assert [x["streak_in"] for x in games] == [0, 1, -1, -2, 1]
    assert games[2]["win_pct_in"] == 0.5
    assert games[0]["writeup_words_postgame"] > games[1]["writeup_words_postgame"]
    assert weeks[0]["week"] == 0 and weeks[0]["off_signings"] == 2              # offseason row
    assert any(w["week"] == 4 and w["games_in"] == 2 for w in weeks)


class FakeWiki:
    calls, bytes = 0, 0

    def members(self, cat):
        if "rivalries" in cat:
            return [{"pageid": 9, "title": "Cowboys–Commanders rivalry"}]
        return [{"pageid": 1, "title": "2024 Washington Commanders season"}]

    def html(self, pageid):
        if pageid == 9:
            return ('<div><table class="infobox"><tr><th>First meeting</th><td>October 9, 1960</td></tr>'
                    '<tr><th>Meetings total</th><td>130</td></tr></table><p>A rivalry.</p></div>', 5)
        return NFL_HTML, 7


def test_pull_writes_numbers_only(tmp_path):
    led = W.pull("nfl", 2024, 2024, tmp_path, wiki=FakeWiki())
    assert led["games"] == 5 and led["rivalries"] == 1 and led["raw_text_stored"] is False
    assert led["tag"] == W.TAG
    riv = list(csv.DictReader((tmp_path / "nfl" / "rivalries.csv").open()))
    assert riv[0]["team_a"] == "DAL" and riv[0]["first_meeting_year"] == "1960" and riv[0]["meetings_total"] == "130"
    blob = "".join(p.read_text() for p in (tmp_path / "nfl").glob("*"))
    for phrase in ("rivalry game", "late comeback", "Short one", "Lead paragraph", "Dallas Cowboys", "Coach Two"):
        assert phrase not in blob                                                # no article text leaves
    assert json.loads((tmp_path / "nfl" / "ledger.json").read_text())["team_weeks"] >= 5


def test_archive_join_pregame_only(tmp_path):
    from gamectx import archive_join as J
    W.pull("nfl", 2024, 2024, tmp_path, wiki=FakeWiki())
    arch = tmp_path / "archive" / "nfl"
    arch.mkdir(parents=True)
    ev = {"events": [{"id": "401", "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"abbreviation": "PHI"}},
        {"homeAway": "away", "team": {"abbreviation": "WSH"}}]}]}]}
    (arch / "scoreboard_20240929.json").write_text(json.dumps(ev))
    rows = J.join(tmp_path, tmp_path / "archive", "nfl")
    assert len(rows) == 1
    r = rows[0]
    assert r["gid"] == "401" and r["night"] == "20240929" and r["ctx_found_away"] == 1 and r["ctx_found_home"] == 0
    assert r["away_win_pct_in"] == 0.5 and r["away_streak_in"] == -1 and r["away_off_signings"] == 2
    assert not any("postgame" in k or k.endswith("result") or "margin" in k for k in r)


def _ev(gid, start, home, away, hs=None, as_=None, done=True, nets=()):
    return {"id": gid, "date": start, "season": {"type": 2}, "competitions": [{
        "status": {"type": {"completed": done}},
        "broadcasts": [{"market": "national", "names": list(nets)}],
        "competitors": [{"homeAway": "home", "team": {"abbreviation": home}, "score": str(hs) if hs is not None else None},
                        {"homeAway": "away", "team": {"abbreviation": away}, "score": str(as_) if as_ is not None else None}]}]}


def test_pregame_report_is_pregame():
    from gamectx import pregame as P
    sb = {"events": [_ev("1", "2026-10-21T23:00Z", "WSH", "BOS", 100, 90),
                     _ev("2", "2026-10-22T23:30Z", "BOS", "WSH", 110, 100),
                     _ev("3", "2026-10-23T23:00Z", "WSH", "ATL", 99, 101),
                     _ev("4", "2026-10-25T00:30Z", "BOS", "WSH", done=False, nets=["ESPN"])]}
    evs = P.events_of(sb)
    rows = P.report("nba", evs, [e for e in evs if e["gid"] == "4"], None, "t")
    r = rows[0]
    assert r["away_w"] == 1 and r["away_l"] == 2 and r["away_streak"] == -2       # games before tip only
    assert r["away_revenge"] == 1 and r["home_revenge"] == 0                        # WSH lost the last meeting
    assert r["div_game"] == 0 and r["conf_game"] == 1 and r["national_tv"] == 1 and r["prime_slot"] == 1
    assert 1.0 < r["away_rest_days"] < 1.1 and r["away_b2b"] == 1
    assert r["night"] == "20261024"
    nfl = P.report("nfl", P.events_of({"events": [_ev("9", "2026-10-25T17:00Z", "WSH", "DAL", done=False,
                                                      nets=["Fox"])]}), P.events_of(
        {"events": [_ev("9", "2026-10-25T17:00Z", "WSH", "DAL", done=False, nets=["Fox"])]}), None, "t")
    assert nfl[0]["national_tv"] == 0 and nfl[0]["div_game"] == 1                  # Sunday Fox = regional


def test_nflverse_event_and_divisions():
    from gamectx import nflverse_backfill as N
    g = {"game_id": "2023_17_SF_WAS", "season": "2023", "game_type": "REG", "week": "17", "gameday": "2023-12-31",
         "weekday": "Sunday", "gametime": "13:00", "away_team": "SF", "home_team": "WAS", "away_score": "27",
         "home_score": "10", "espn": "401547600", "div_game": "0"}
    e = N.to_event(g)
    assert e["gid"] == "401547600" and e["home"] == "WSH" and e["start"] == "2023-12-31T18:00Z"
    assert e["national"] == [] and e["done"]
    assert N.div_map(2001)["SEA"] == ("AFC", "") and "HOU" not in N.div_map(2001)
