from redzone import fantasy
from redzone.models import GameState, League, Phase, Situation, Team

LEAGUE = {
    "status": {"currentMatchupPeriod": 9},
    "teams": [
        {"id": 1, "name": "Nick's Squad", "owners": ["{ABC}"], "roster": {"entries": [
            {"lineupSlotId": 4, "playerPoolEntry": {"player": {"fullName": "Terry McLaurin", "proTeamId": 28, "defaultPositionId": 3}}},
            {"lineupSlotId": 20, "playerPoolEntry": {"player": {"fullName": "Bench Guy", "proTeamId": 12, "defaultPositionId": 2}}}]}},
        {"id": 2, "name": "Rival", "owners": ["{XYZ}"], "roster": {"entries": [
            {"lineupSlotId": 2, "playerPoolEntry": {"player": {"fullName": "Saquon Barkley", "proTeamId": 21, "defaultPositionId": 2}}}]}}],
    "schedule": [{"matchupPeriodId": 9, "home": {"teamId": 1, "totalPointsLive": 88.4}, "away": {"teamId": 2, "totalPoints": 91}}],
}


def _game(poss, ytg):
    return GameState(league=League.NFL, game_id="g", home=Team("WSH"), away=Team("PHI"), phase=Phase.LIVE,
                     situation=Situation(possession=poss, yards_to_goal=ytg, down=1, distance=10))


def test_parse_finds_my_team_by_cookie_and_starters():
    m = fantasy.parse(LEAGUE, None, "{ABC}")
    assert m.me == "Nick's Squad" and m.opponent == "Rival"
    assert m.my_points == 88.4 and m.opp_points == 91.0
    assert {(s.name, s.mine) for s in m.starters} == {("Terry McLaurin", True), ("Saquon Barkley", False)}


def test_red_zone_boosts():
    m = fantasy.parse(LEAGUE, None, "{ABC}")
    pts, label = fantasy.boosts(m, _game("WSH", 12), 12, 8)
    assert pts == 12 and "your WR McLaurin" in label
    assert fantasy.boosts(m, _game("PHI", 5), 12, 8)[0] == 8
    assert fantasy.boosts(m, _game("WSH", 60), 12, 8) is None


def test_unknown_team_is_reported():
    assert "couldn't tell" in fantasy.parse(LEAGUE, None, "").error
