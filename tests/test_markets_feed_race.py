import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "markets"))

import feed_race as R  # noqa: E402

TEAMS = {"JAX", "BAL"}


def _comp(away, home):
    return {"competitors": [{"homeAway": "home", "score": str(home), "team": {"abbreviation": "BAL"}},
                            {"homeAway": "away", "score": str(away), "team": {"abbreviation": "JAX"}}]}


def test_scoreboard_and_summary_parse():
    sb = {"events": [{"id": "9", "competitions": [{"competitors": [
        {"homeAway": "home", "score": "3", "team": {"abbreviation": "NE"}},
        {"homeAway": "away", "score": "0", "team": {"abbreviation": "NYJ"}}]}]},
        {"id": "401", "competitions": [_comp(7, 3)]}]}
    assert R.scoreboard_game(sb, TEAMS) == ("401", "JAX@BAL", (7, 3))
    assert R.summary_game({"header": {"competitions": [_comp(0, 6)]}}, TEAMS) == ("JAX@BAL", (0, 6))


def test_changelog_first_poll_then_changes_only():
    c = R.ChangeLog()
    first = c.see("espn_scoreboard", "JAX@BAL", (0, 0), 1.0)
    assert first["prev"] is None
    assert c.see("espn_scoreboard", "JAX@BAL", (0, 0), 2.0) is None
    r = c.see("espn_scoreboard", "JAX@BAL", (0, 7), 3.0)
    assert r["prev"] == [0, 0] and r["recv_ts"] == 3.0


def test_grade_leads_and_leader():
    sync = [json.dumps({"kind": "score", "target": "prime:tnf", "t": 1000.0, "z": 16.0}),
            json.dumps({"kind": "boundary", "target": "prime:tnf", "t": 1100.0, "z": 9.0}),
            json.dumps({"kind": "score", "target": "prime:tnf", "t": 2000.0, "z": 15.0})]
    screens = R.screen_rows(sync, 0, 5000, "prime")
    assert [s["screen_ts"] for s in screens] == [1000.0, 2000.0]
    espn = [{"source": "espn_summary", "recv_ts": 900.0, "score": [0, 0], "prev": None},
            {"source": "espn_summary", "recv_ts": 1030.0, "score": [0, 7], "prev": [0, 0]},     # 30 s after screen
            {"source": "espn_summary", "recv_ts": 2040.0, "score": [3, 7], "prev": [0, 7]}]
    poly = [{"row": "trigger", "game_key": "g", "recv_ts": 992.0},                               # 8 s before screen
            {"row": "trigger", "game_key": "other", "recv_ts": 1999.0},
            {"row": "trigger", "game_key": "g", "recv_ts": 1995.0}]
    res = R.grade(screens, espn, poly, "g")
    assert res["events"] == 2
    assert res["rows"][0]["lead_s"] == {"app_feed": 16.0, "espn_summary": -30.0, "polymarket": 8.0}
    assert res["summary"]["polymarket"]["median_lead_s"] == 6.5
    assert res["summary"]["espn_summary"]["share_ahead_of_screen"] == 0.0
    assert res["leader"] == "app_feed"
