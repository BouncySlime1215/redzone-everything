import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "markets"))

import pregame_lines as P  # noqa: E402


def test_vig_free_and_parse_old_and_new_shapes():
    assert P.vig_free(-150, 130) == round((150 / 250) / (150 / 250 + 100 / 230), 4)
    old = [{"provider": {"name": "ESPN BET"}, "spread": -3.5, "overUnder": 224.5,
            "homeTeamOdds": {"moneyLine": -160}, "awayTeamOdds": {"moneyLine": 135}}]
    r = P.parse_pickcenter(old)
    assert r["spread_now"] == -3.5 and r["total_now"] == 224.5 and r["spread_move"] is None
    new = [{"provider": {"name": "DraftKings"},
            "pointSpread": {"home": {"open": {"line": "-2.5"}, "close": {"line": "-4"}}},
            "total": {"over": {"open": {"line": "o221.5"}, "close": {"line": "o224.5"}}},
            "moneyline": {"home": {"open": {"odds": "-130"}, "close": {"odds": "-180"}},
                          "away": {"open": {"odds": "+110"}, "close": {"odds": "+150"}}}}]
    r = P.parse_pickcenter(new)
    assert r["spread_open"] == -2.5 and r["spread_now"] == -4.0 and r["spread_move"] == -1.5
    assert r["total_move"] == 3.0 and r["ml_p_home_move"] > 0
