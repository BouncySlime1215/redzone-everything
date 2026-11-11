"""Moments: tier list, grader alias map, detectors on small synthetic ESPN summaries, rates-script maths.

Why: WP-07 must pin the per-league definitions so the queue and fill ladder can trust them. Archive-free.
"""
import re
from pathlib import Path

import pytest

from redzone.moments import detect, game_span, must_show, tier_of, tiers

T0 = "2026-10-01T00:00:00Z"


def wc(sec):
    return f"2026-10-01T{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}Z"


def fb(ty, sec, down=1, y=60, dist=10, gain=0, text="", scoring=False, pid=None):
    return {"id": pid or str(sec), "type": {"text": ty}, "text": text, "wallclock": wc(sec), "scoringPlay": scoring,
            "statYardage": gain, "start": {"down": down, "yardsToEndzone": y, "distance": dist}}


def fb_game(plays, wp=None):
    return {"drives": {"previous": [{"plays": plays}]}, "winprobability": wp or []}


def kinds_of(res):
    return [(m.kind, m.tier) for m in res]


# -- tier list ---------------------------------------------------------------------------------------------

def test_tier_list_matches_planner_definitions():
    assert tiers.kinds("nfl", 1) == ("td", "turnover") and tiers.kinds("cfb", 1) == tiers.kinds("nfl", 1)
    assert tier_of("nba", "clutch_fg") == 1 and tier_of("cbb", "run8") == 2
    assert tiers.kinds("nhl", 1) == ("goal", "penalty_shot") and tier_of("mlb", "hr") == 1
    assert tier_of("mlb", "risp_pa") == 3 and tier_of("nfl", "no_such_kind") is None


def test_football_must_show_is_every_td_and_major_play():
    for k in ("td", "turnover", "gain10", "fg", "big3rd", "4th_try", "wp5"):
        assert must_show("nfl", k) and must_show("cfb", k)
    assert not must_show("nfl", "rz_trip")           # a setup, not a payoff
    assert not must_show("nba", "dunk")


def test_every_kind_has_exactly_one_tier():
    for lg in ("nfl", "cfb", "nba", "cbb", "nhl", "mlb"):
        seen = [k for t in (1, 2, 3) for k in tiers.kinds(lg, t)]
        assert len(seen) == len(set(seen))


def test_grader_lists_map_onto_the_tier_vocabulary():
    """Both frozen BIG_KINDS lists are covered: each word maps to a kind in the tier list, or is named as unmapped."""
    root = Path(__file__).resolve().parent.parent
    words = set()
    for rel in ("redzone/web/phone_v2.py", "redzone/reasoner/brief.py"):
        m = re.search(r"BIG_KINDS = \{(.*?)\}", (root / rel).read_text(), re.S)
        words |= set(re.findall(r'"(\w+)"', m.group(1)))
    assert words <= set(tiers.GRADER_ALIASES), words - set(tiers.GRADER_ALIASES)
    fam_league = {"football": "nfl", "hoops": "nba", "hockey": "nhl", "baseball": "mlb"}
    for w, target in tiers.GRADER_ALIASES.items():
        if target:
            fam, kind = target
            assert tier_of(fam_league[fam], kind) is not None, w
    assert {w for w, t in tiers.GRADER_ALIASES.items() if t is None} == {"and1", "buzzer"}


# -- detectors ---------------------------------------------------------------------------------------------

def test_football_every_td_and_major_play():
    d = fb_game([
        fb("Rush", 10, down=1, y=60, gain=12),                                    # 10+ gain
        fb("Pass Reception", 20, down=3, y=18, dist=7, gain=9),                   # big 3rd + red-zone trip
        fb("Passing Touchdown", 30, down=1, y=9, gain=9, text="touchdown", scoring=True),
        fb("Interception Return", 40, down=2, y=50),
        fb("Field Goal Good", 50, down=4, y=30),
        fb("Pass Incompletion", 60, down=4, y=3, dist=3),                         # 4th-down try
        fb("Rush", 70, down=1, y=40, gain=15, text="no play"),                    # nullified
    ])
    got = kinds_of(detect("nfl", d))
    assert ("td", 1) in got and ("turnover", 1) in got and ("gain10", 3) in got and ("big3rd", 3) in got
    assert ("rz_trip", 2) in got and ("fg", 2) in got and ("4th_try", 2) in got
    assert got.count(("td", 1)) == 1 and got.count(("rz_trip", 2)) == 1             # one trip per drive
    assert all(m.play_id != "70" for m in detect("nfl", d))                         # no moment from a no-play


def test_football_td_is_not_also_a_gain10_and_wp_swing_marks_a_play():
    d = fb_game([fb("Rushing Touchdown", 10, gain=30, text="touchdown", scoring=True),
                 fb("Pass Reception", 20, gain=2, pid="p2")],
                wp=[{"playId": "a", "homeWinPercentage": 0.5}, {"playId": "p2", "homeWinPercentage": 0.58}])
    got = kinds_of(detect("cfb", d))
    assert ("td", 1) in got and ("gain10", 3) not in got and ("wp5", 3) in got


def bb(sec, ty, home, away, per=4, clock="2:00", scoring=True, shooting=True, value=2, pid=None):
    return {"id": pid or str(sec), "type": {"text": ty}, "wallclock": wc(sec), "homeScore": home, "awayScore": away,
            "period": {"number": per}, "clock": {"displayValue": clock}, "scoringPlay": scoring,
            "shootingPlay": shooting, "scoreValue": value}


def test_basketball_clutch_make_run_and_dunk():
    plays = [bb(10, "Jump Shot", 2, 0, per=1, clock="11:00"), bb(20, "Dunk", 4, 0, per=1, clock="10:50"),
             bb(30, "Three Point Jump Shot", 7, 0, per=1, clock="10:40", value=3),
             bb(40, "Layup", 9, 0, per=1, clock="10:30"),                      # 9-0 run: run8 fires once
             bb(45, "Jump Shot", 60, 58, per=4, clock="1:40", scoring=False),
             bb(50, "Jump Shot", 62, 58, per=4, clock="1:30"),                   # clutch: late, margin <= 5
             bb(60, "Free Throw", 63, 58, per=4, clock="1:00", shooting=True)]
    got = kinds_of(detect("nba", {"plays": plays}))
    assert got.count(("run8", 2)) == 1 and ("dunk", 3) in got and ("three", 3) in got
    assert got.count(("clutch_fg", 1)) == 1                                       # a free throw is not a clutch make


def test_cbb_uses_halves_for_late_game():
    mid = bb(10, "Jump Shot", 40, 38, per=2, clock="4:00")
    early = bb(20, "Jump Shot", 42, 38, per=1, clock="4:00")
    assert ("clutch_fg", 1) in kinds_of(detect("cbb", {"plays": [mid]}))
    assert ("clutch_fg", 1) not in kinds_of(detect("nba", {"plays": [mid]}))      # NBA period 2 is not late
    assert ("clutch_fg", 1) not in kinds_of(detect("cbb", {"plays": [early]}))


def hk(sec, ty, per=1, clock="10:00", team=None, home=0, away=0):
    p = {"id": str(sec), "type": {"text": ty}, "wallclock": wc(sec), "period": {"number": per},
         "clock": {"displayValue": clock}, "homeScore": home, "awayScore": away}
    if team:
        p["team"] = {"id": team}
    return p


def test_hockey_goal_shot_fight_power_play_and_coincidental():
    plays = [hk(1, "Shot"), hk(2, "Goal"), hk(3, "Fighting"), hk(4, "Tripping", clock="9:00", team="1"),
             hk(5, "Hooking", clock="5:00", team="1"), hk(6, "Slashing", clock="5:00", team="2"),   # coincidental
             hk(7, "Face Off", per=3, clock="19:00", home=2, away=2), hk(8, "Face Off", per=4, clock="5:00")]
    got = kinds_of(detect("nhl", {"plays": plays}))
    assert ("goal", 1) in got and ("sog", 3) in got and ("fight", 3) in got
    assert got.count(("pp_start", 2)) == 1                                        # only the lone Tripping
    assert ("late_1goal", 2) in got and ("ot_start", 2) in got


def ab(sec, aid, parts=(), text="", ty="Pitch", home=0, away=0, inn=1, pid=None):
    return {"id": pid or f"{aid}-{sec}", "atBatId": aid, "wallclock": wc(sec), "type": {"text": ty}, "text": text,
            "participants": [{"type": t} for t in parts], "homeScore": home, "awayScore": away, "period": {"number": inn}}


def test_baseball_home_run_run_bases_loaded_and_risp():
    plays = [ab(10, "a", text="homered", ty="Home Run", home=1), ab(20, "a", ty="Home Run", home=1),
             ab(30, "b", ("onFirst", "onSecond", "onThird"), home=1),             # bases loaded, no run: big_pa
             ab(40, "b", ("onFirst", "onSecond", "onThird"), home=1),
             ab(50, "c", ("onSecond",), home=1), ab(60, "c", ("onSecond",), home=1),   # risp: tier 3
             ab(70, "d", ("onSecond",), home=2), ab(80, "d", ("onSecond",), home=2)]  # run scores
    got = kinds_of(detect("mlb", {"plays": plays}))
    assert ("hr", 1) in got and ("run_scored", 1) in got and ("big_pa", 2) in got and ("risp_pa", 3) in got


def test_untimed_plays_make_no_moments_and_unknown_league_raises():
    assert len(detect("nhl", {"plays": [{"type": {"text": "Goal"}}]})) == 0
    with pytest.raises(KeyError):
        detect("wcbb", {})


def test_game_span_requires_a_whole_game():
    assert game_span(range(0, 10)) is None
    assert game_span([i * 400 for i in range(40)]) == (0, 39 * 400)               # 4.3 h
    assert game_span([i * 10 for i in range(40)]) is None                         # 6.5 min


def test_rates_script_compares_to_planner_within_5_percent():
    import importlib.util
    spec = importlib.util.spec_from_file_location("moment_rates", Path(__file__).resolve().parent.parent / "scripts" / "moment_rates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    row = {"league": "nfl", "per10": [0.40, 0.77, 2.5]}
    c = mod.compare(row)
    assert [x["within_5pct"] for x in c] == [True, True, False]
    assert mod.PLANNER["nfl"] == (0.39, 0.77, 2.29)
