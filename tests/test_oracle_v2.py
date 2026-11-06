"""NBA oracle v2 (EXP-1722): the frozen rule, and v1 untouched."""
from redzone.lab import oracle as O, oracle_v2 as V


def test_v1_objectives_unchanged_until_register():
    before = dict(O.OBJECTIVES)
    assert "combined" in before
    V.register()
    assert {k: v for k, v in O.OBJECTIVES.items() if k != "v2"} == {k: v for k, v in before.items() if k != "v2"}
    assert O.OBJECTIVES["v2"] == (1.0, 0.05, 100.0, 0.002, 0.02)


def test_big_v2_rule():
    p = {"scoringPlay": False, "type": {"text": "Jump Shot"}}
    assert V.is_big_v2("nba", p, None, 0.50, 0.65) == (True, 0.15000000000000002)   # big WP swing
    assert V.is_big_v2("nba", p, None, 0.97, 0.80)[0] is False                      # pre-play WP out of [0.05, 0.95]
    assert V.is_big_v2("nba", dict(p, scoringPlay=True), None, 0.5, 0.53) == (True, 0.030000000000000027)
    assert V.is_big_v2("nba", dict(p, scoringPlay=True), None, 0.5, 0.51)[0] is False
    assert V.is_big_v2("nba", p, None, None, 0.6)[0] is False                       # no pre-play WP


def test_wp_by_play():
    assert V.wp_by_play({"winprobability": [{"playId": 7, "homeWinPercentage": 0.4}, {"playId": 8}]}) == {"7": 0.4}
