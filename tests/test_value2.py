"""Value v2 moments: every detector kind, the grading bonuses, leverage, stakes and dead value on hand-built summaries.

Why: docs/plans/2026-11-09-value-v2.md build step 1. Each spec row that value2.py claims to detect is planted here once
and must come back with the table's points, and v1 must read the same summary exactly as before.
"""
import copy
import datetime as dt
import itertools

import pytest

from redzone.moments import tiers, value2
from redzone.moments.detect import detect
from redzone.noggin import value_shards as V

KICK = dt.datetime(2025, 10, 5, 17, 0, tzinfo=dt.timezone.utc)
_ids = itertools.count(1)


def _iso(i):
    return (KICK + dt.timedelta(seconds=40.0 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")


def fp(ty, text="", *, per=1, clock="10:00", home=0, away=0, down=1, dist=10, y=50, gain=0, scoring=False, **extra):
    """One football play on ESPN's shape. home / away are the score after the play."""
    i = next(_ids)
    return {"id": str(i), "type": {"text": ty}, "text": text, "period": {"number": per}, "clock": {"displayValue": clock},
            "wallclock": _iso(i), "scoringPlay": scoring, "homeScore": home, "awayScore": away, "statYardage": gain,
            "start": {"down": down, "distance": dist, "yardsToEndzone": y}, "end": {}, **extra}


def fb(*plays, **top):
    return {"drives": {"previous": [{"plays": list(plays)}]}, **top}


def bp(ty, text="", *, per=1, clock="6:00", home=0, away=0, value=0, team="H", who=(), shooting=None, **extra):
    """One basketball play. A scoring play carries the score after it and its own value."""
    i = next(_ids)
    return {"id": str(i), "type": {"text": ty}, "text": text, "period": {"number": per}, "clock": {"displayValue": clock},
            "wallclock": _iso(i), "scoringPlay": value > 0, "scoreValue": value, "homeScore": home, "awayScore": away,
            "team": {"id": team}, "participants": [{"athlete": {"id": w}} for w in who],
            "shootingPlay": ("free throw" not in ty.lower() and "shot" in ty.lower()) if shooting is None else shooting, **extra}


def hoops(*plays, **top):
    return {"plays": list(plays), **top}


def one(league, summary, kind):
    got = [g for g in value2.grade(league, summary) if g.kind == kind]
    assert len(got) == 1, [g.kind for g in value2.grade(league, summary)]
    return got[0]


def kinds(league, summary):
    return [g.kind for g in value2.grade(league, summary)]


# --- football: touchdowns ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, down, clock, per, base, grades", [
    ("J.Doe up the middle for 3 yards, TOUCHDOWN.", 1, "9:00", 1, 5.0, ()),
    ("J.Doe pass deep left to A.Bee for 25 yards, TOUCHDOWN.", 1, "9:00", 1, 6.0, ("td_off_20",)),
    ("J.Doe pass deep left to A.Bee for 55 yards, TOUCHDOWN.", 1, "9:00", 1, 7.0, ("td_off_50",)),
    ("J.Doe up the middle for 1 yard, TOUCHDOWN.", 4, "9:00", 1, 6.0, ("td_off_4th",)),
    ("J.Doe pass deep left to A.Bee for 30 yards, TOUCHDOWN.", 1, "0:00", 2, 8.0, ("td_off_20", "td_off_last_play")),
])
def test_offensive_touchdown_and_its_grades(text, down, clock, per, base, grades):
    g = one("nfl", fb(fp("Passing Touchdown", text, down=down, clock=clock, per=per, home=7, scoring=True)), "td_off")
    assert g.base == base and g.grades == grades and g.stakes == 1.0 and g.value == pytest.approx(base * g.leverage)


@pytest.mark.parametrize("ty, text, base", [
    ("Interception Return Touchdown", "J.Doe pass INTERCEPTED by K.Fox at BUF 37. K.Fox for 63 yards, TOUCHDOWN.", 8.0),
    ("Kickoff Return Touchdown", "Z.Kay kicks 65 yards from ATL 35 to SEA 0. R.Sha for 40 yards, TOUCHDOWN.", 7.0),
    ("Punt Return Touchdown", "M.Dix punts 60 yards to TEN 10. C.Dike for 90 yards, TOUCHDOWN.", 8.0),
    ("Fumble Return Touchdown", "J.Doe to MIN 32. FUMBLES, RECOVERED by MIN-I.Rod at MIN 34. I.Rod for 20 yards, TOUCHDOWN.", 7.0),
])
def test_defensive_and_special_teams_touchdowns_are_worth_more_and_count_once(ty, text, base):
    s = fb(fp(ty, text, away=7, scoring=True, isTurnover="Return" in ty and "Kick" not in ty and "Punt" not in ty))
    assert one("nfl", s, "td_def").base == base
    assert not {"interception", "fumble_lost", "td_off"} & set(kinds("nfl", s))


def test_own_fumble_recovered_in_the_end_zone_is_an_offensive_touchdown():
    s = fb(fp("Fumble Return Touchdown", "T.Lock 0 Yd Fumble Recovery (J.Slye Kick)", home=7, scoring=True, isTurnover=False))
    assert kinds("nfl", s) == ["td_off"]


def test_two_point_try_rides_on_its_touchdown():
    s = fb(fp("Rushing Touchdown", "S.Tuck right end for 6 yards, TOUCHDOWN. TWO-POINT CONVERSION ATTEMPT. B.May is sacked. ATTEMPT FAILS.",
              home=6, scoring=True))
    assert sorted(kinds("nfl", s)) == ["td_off", "two_point"] and one("nfl", s, "two_point").base == 3.0


# --- football: turnovers and sacks -------------------------------------------------------------------------------

@pytest.mark.parametrize("y, text, base, grades", [
    (50, "J.Doe pass INTERCEPTED by K.Fox at NYG 45. K.Fox to NYG 47 for 2 yards.", 4.0, ()),
    (12, "J.Doe pass INTERCEPTED by K.Fox at NYG 2. Touchback.", 5.0, ("turnover_inside_20",)),
    (85, "J.Doe pass INTERCEPTED by K.Fox at NYG 30. K.Fox to NYG 2 for 28 yards.", 6.0, ("turnover_inside_20", "turnover_return_20")),
])
def test_interception_grades(y, text, base, grades):
    g = one("nfl", fb(fp("Pass Interception Return", text, y=y, isTurnover=True)), "interception")
    assert g.base == base and g.grades == grades


def test_fumble_lost_and_strip_sack():
    lost = fb(fp("Fumble Recovery (Opponent)", "K.Hunt right end to IND 12. FUMBLES (Z.Fra), RECOVERED by IND-Z.Fra at IND 12.", isTurnover=True))
    assert one("nfl", lost, "fumble_lost").base == 4.0
    strip = fb(fp("Sack Opp Fumble Recovery", "G.Smi sacked at LV 49 for -8 yards (M.Gar). FUMBLES (M.Gar), RECOVERED by CLV-M.Gar at LV 45.",
                  isTurnover=True))
    assert sorted(kinds("nfl", strip)) == ["fumble_lost", "strip_sack"] and one("nfl", strip, "strip_sack").base == 3.0
    kept = fb(fp("Fumble Recovery (Own)", "D.May sacked at NE 36 for -8 yards. FUMBLES, recovered by NE-W.Cam at NE 36."))
    assert kinds("nfl", kept) == ["strip_sack"]


def test_sack_is_worth_more_on_a_late_down():
    assert one("nfl", fb(fp("Sack", "J.Gof sacked at NYG 43 for -7 yards.", down=1)), "sack").base == 1.0
    assert one("nfl", fb(fp("Sack", "J.Gof sacked at NYG 43 for -7 yards.", down=3)), "sack").base == 1.5


def test_a_sack_inside_a_two_point_try_is_not_a_sack():
    s = fb(fp("Passing Touchdown", "B.You pass for 12 yards, TOUCHDOWN. TWO-POINT CONVERSION ATTEMPT. B.You is sacked. ATTEMPT FAILS.",
              home=6, scoring=True))
    assert "sack" not in kinds("nfl", s)


# --- football: fourth down ---------------------------------------------------------------------------------------

def test_fourth_down_converted_stopped_and_faked():
    assert kinds("nfl", fb(fp("Rush", "K.Hun right end for 6 yards.", down=4, dist=1, gain=6))) == ["fourth_try"]
    assert kinds("nfl", fb(fp("Pass Incompletion", "D.Pre pass incomplete deep left.", down=4, dist=3))) == ["turnover_on_downs"]
    fake = fb(fp("Rush", "(Punt formation) Direct snap to B.Hal. B.Hal up the middle for 12 yards.", down=4, dist=5, gain=12))
    assert kinds("nfl", fake) == ["fake_kick"] and one("nfl", fake, "fake_kick").base == 4.0
    assert kinds("nfl", fb(fp("Punt", "J.Fox punts 40 yards.", down=4))) == []


def test_goal_line_stand_needs_three_snaps_inside_the_five():
    snaps = [fp("Rush", "up the middle for 1 yard.", down=d, dist=3, y=y, gain=1) for d, y in ((1, 3), (2, 2), (3, 1))]
    stop = fp("Pass Incompletion", "pass incomplete short left.", down=4, dist=1, y=1)
    assert kinds("nfl", fb(*snaps, stop))[-1] == "goal_line_stand" and one("nfl", fb(*snaps, stop), "goal_line_stand").base == 4.0
    assert kinds("nfl", fb(fp("Pass Incompletion", "pass incomplete.", down=4, dist=1, y=1))) == ["turnover_on_downs"]


# --- football: kicks ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("yards, base", [(21, 1.0), (39, 1.0), (40, 2.0), (49, 2.0), (50, 3.0), (61, 3.0)])
def test_field_goal_made_is_graded_by_distance(yards, base):
    s = fb(fp("Field Goal Good", f"Y.Koo {yards} yard field goal is GOOD.", down=4, home=3, scoring=True))
    assert kinds("nfl", s) == ["fg_made"] and one("nfl", s, "fg_made").base == base


def test_field_goal_missed_or_blocked():
    assert kinds("nfl", fb(fp("Field Goal Missed", "D.Car 48 yard field goal is No Good, Wide Left.", down=4))) == ["fg_missed"]
    assert kinds("nfl", fb(fp("Blocked Field Goal", "J.Bat 52 yard field goal is BLOCKED (S.Wil).", down=4, isTurnover=True))) == ["fg_missed"]


def test_safety_blocked_punt_and_onside_kicks():
    assert kinds("nfl", fb(fp("Safety", "K.Mer Safety", away=2, scoring=True, scoringType={"name": "safety"}))) == ["safety"]
    assert kinds("nfl", fb(fp("Blocked Punt", "C.Boj punt is BLOCKED by J.Hum, RECOVERED by BLT-S.Kan at CLV 29.", down=4, isTurnover=True))) == ["blocked_punt"]
    lost = fb(fp("Kickoff", "S.Mar kicks onside 13 yards from CAR 35 to CAR 48. B.Str (didn't try to advance) to CAR 48 for no gain.", down=0))
    won = fb(fp("Kickoff", "S.Mar kicks onside 14 yards from CAR 35 to CAR 49. RECOVERED by CAR-D.Ric.", down=0))
    assert one("nfl", lost, "onside").base == 3.0 and one("nfl", won, "onside_recovered").base == 6.0


# --- football: the rest ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("gain, base", [(19, None), (20, 1.0), (39, 1.0), (40, 2.5)])
def test_explosive_gain_bands(gain, base):
    got = [g.base for g in value2.grade("nfl", fb(fp("Pass Reception", f"pass deep left for {gain} yards.", gain=gain))) if g.kind == "explosive"]
    assert got == ([] if base is None else [base])


def test_third_and_long_conversion():
    assert "third_long" in kinds("nfl", fb(fp("Pass Reception", "pass short right for 9 yards.", down=3, dist=7, gain=9)))
    assert "third_long" not in kinds("nfl", fb(fp("Pass Reception", "pass short right for 9 yards.", down=3, dist=6, gain=9)))
    assert "third_long" not in kinds("nfl", fb(fp("Pass Reception", "pass short right for 5 yards.", down=3, dist=7, gain=5)))


def test_last_play_heave_long_penalty_ejection_and_overtime():
    heave = fb(fp("Pass Incompletion", "J.Lov pass incomplete deep middle to C.Wat.", per=2, clock="0:00", y=45))
    assert kinds("nfl", heave) == ["last_play_heave"]
    assert kinds("nfl", fb(fp("Pass Incompletion", "J.Lov pass incomplete deep middle.", per=2, clock="0:05", y=45))) == []
    dpi = fb(fp("Penalty", "pass incomplete deep right.PENALTY on PHI-Q.Mit, Defensive Pass Interference, 34 yards, enforced at PHI 46 - No Play."))
    assert kinds("nfl", dpi) == ["dpi_long"]
    short = fb(fp("Penalty", "PENALTY on PHI-Q.Mit, Defensive Pass Interference, 9 yards, enforced at PHI 46 - No Play."))
    assert kinds("nfl", short) == []
    assert kinds("nfl", fb(fp("Rush", "up the middle for no gain.PENALTY on PIT-J.Ram, Disqualification, 15 yards."))) == ["ejection"]
    ot = fb(fp("Kickoff", "kicks 65 yards, touchback.", per=5, clock="10:00", home=20, away=20, down=0), fp("Rush", "for 2 yards.", per=5, home=20, away=20))
    got = value2.grade("nfl", ot)
    assert [g.kind for g in got] == ["ot_start"] and got[0].base == 3.0 and got[0].leverage == 3.0


def test_a_play_with_no_wall_clock_yields_nothing():
    p = fp("Passing Touchdown", "for 3 yards, TOUCHDOWN.", home=7, scoring=True)
    del p["wallclock"]
    assert value2.grade("nfl", fb(p)) == []


# --- dead value and garbage time ---------------------------------------------------------------------------------

def test_kneel_down_and_upheld_review_are_flat_and_negative():
    s = fb(fp("Rush", "J.Her kneels to PIT 39 for -1 yards.", per=4, clock="0:40", home=24, away=21),
           fp("Pass Incompletion", "pass incomplete. Atlanta challenged the ruling, and the play was Upheld.", per=4, clock="0:30", home=24, away=21))
    got = {g.kind: g for g in value2.grade("nfl", s)}
    assert got["dead_kneel"].value == -1.0 and got["dead_kneel"].leverage == 1.0 and got["dead_kneel"].dead
    assert got["dead_review"].value == -0.5


def test_garbage_time_touchdown_is_worth_a_tenth_of_a_go_ahead_one():
    text = "J.Doe pass short left to A.Bee for 7 yards, TOUCHDOWN."
    garbage = one("nfl", fb(fp("Rush", "for 1 yard.", per=4, clock="1:30", home=38, away=3),
                            fp("Passing Touchdown", text, per=4, clock="1:10", home=38, away=10, scoring=True)), "td_off")
    winner = one("nfl", fb(fp("Rush", "for 1 yard.", per=4, clock="1:00", home=20, away=17),
                           fp("Passing Touchdown", text, per=4, clock="0:40", home=20, away=24, scoring=True)), "td_off")
    assert garbage.base == winner.base == 5.0
    assert garbage.leverage == 0.3 and garbage.value == pytest.approx(1.5)
    assert winner.leverage == 3.0 and winner.value == pytest.approx(15.0)
    assert (garbage.margin, garbage.period, garbage.seconds_left) == (35, 4, 70.0) and winner.margin_after == 4


def test_a_stale_score_on_a_timeout_row_does_not_move_the_margin():
    s = fb(fp("Rush", "for 1 yard.", per=4, clock="1:00", home=20, away=16),
           fp("Timeout", "Timeout #1 by LA.", per=4, clock="0:50", home=13, away=16, down=0),
           fp("Sack", "sacked for -7 yards.", per=4, clock="0:46", home=20, away=16))
    assert one("nfl", s, "sack").margin == 4


# --- basketball --------------------------------------------------------------------------------------------------

def lead(home, away, per=1, clock="11:00"):
    """Two baskets that set the score, so the tracker knows each team's side."""
    return [bp("Jump Shot", "H makes shot", per=per, clock=clock, home=home, away=0, value=home, team="H"),
            bp("Jump Shot", "A makes shot", per=per, clock=clock, home=home, away=away, value=away, team="A")]


def after_lead(league, summary):
    """The graded moments past the two set-up baskets of lead() (those are giant runs and lead changes by themselves)."""
    skip = {p["id"] for p in summary["plays"][:2]}
    return [g for g in value2.grade(league, summary) if g.play_id not in skip]


def last_kinds(league, summary, n=1):
    last = summary["plays"][-n]["id"]
    return sorted(g.kind for g in value2.grade(league, summary) if g.play_id == last)


@pytest.mark.parametrize("clock, home_after, kind, base", [
    ("0.4", 102, "game_winner", 8.0),          # down 1, makes a two at the buzzer
    ("7.0", 102, "go_ahead_last10", 6.0),
    ("45.0", 102, "go_ahead", 5.0),
    ("3:00", 102, "lead_change_late", 3.0),
    ("8:00", 102, "lead_change", 0.5),
])
def test_one_main_kind_per_basket_the_largest_that_applies(clock, home_after, kind, base):
    s = hoops(*lead(100, 101, per=3), bp("Jump Shot", "H makes 12-foot jumper", per=4, clock=clock, home=home_after, away=101, value=2, team="H"))
    got = [g for g in value2.grade("nba", s) if g.play_id == s["plays"][-1]["id"]]
    assert [g.kind for g in got] == [kind] and got[0].base == base


def test_a_tying_basket_at_the_buzzer_is_not_a_game_winner():
    s = hoops(*lead(100, 102, per=3), bp("Jump Shot", "H makes 12-foot jumper", per=4, clock="0.0", home=102, away=102, value=2, team="H"))
    assert last_kinds("nba", s) == ["go_ahead_last10"]


def test_clutch_basket_and_its_bonuses():
    plain = hoops(*lead(100, 97, per=3), bp("Jump Shot", "H makes 12-foot jumper", per=4, clock="3:00", home=102, away=97, value=2, team="H"))
    assert last_kinds("nba", plain) == ["clutch_fg"] and one("nba", plain, "clutch_fg").base == 2.0
    three = hoops(*lead(100, 97, per=3), bp("Jump Shot", "H makes 25-foot three point jumper", per=4, clock="3:00", home=103, away=97, value=3, team="H"))
    assert one("nba", three, "clutch_fg").base == 2.5 and one("nba", three, "clutch_fg").grades == ("clutch_three",)
    and1 = hoops(*lead(100, 97, per=3), bp("Layup Shot", "H makes layup", per=4, clock="3:00", home=102, away=97, value=2, team="H"),
                 bp("Shooting Foul", "A shooting foul", per=4, clock="3:00", home=102, away=97, team="A"),
                 bp("Free Throw - 1 of 1", "H makes free throw 1 of 1", per=4, clock="3:00", home=103, away=97, value=1, team="H"))
    assert one("nba", and1, "clutch_fg").base == 3.0 and one("nba", and1, "clutch_fg").grades == ("clutch_and1",)
    not_close = hoops(*lead(100, 90, per=3), bp("Jump Shot", "H makes jumper", per=4, clock="3:00", home=102, away=90, value=2, team="H"))
    assert last_kinds("nba", not_close) == []


def test_clutch_free_throws_count_each_made_or_missed_and_others_are_dead():
    s = hoops(*lead(100, 101, per=3),
              bp("Free Throw - 1 of 2", "H misses free throw 1 of 2", per=4, clock="5.0", home=100, away=101, team="H"),
              bp("Free Throw - 2 of 2", "H makes free throw 2 of 2", per=4, clock="5.0", home=101, away=101, value=1, team="H"))
    got = value2.grade("nba", s)
    assert [g.base for g in got if g.kind == "clutch_ft"] == [2.0, 2.0] and not [g for g in got if g.kind == "dead_ft"]
    early = hoops(*lead(20, 30), bp("Free Throw - 1 of 2", "H makes free throw 1 of 2", per=1, clock="5:00", home=21, away=30, value=1, team="H"))
    dead = one("nba", early, "dead_ft")
    assert dead.value == -0.25 and dead.dead and dead.leverage == 1.0


def test_runs_pay_two_then_one_more_at_twelve_and_fifteen():
    plays = lead(30, 30)
    for n in range(1, 6):
        plays.append(bp("Jump Shot", "H makes 25-foot three point jumper", per=2, clock=f"{10 - n}:00", home=30 + 3 * n, away=30, value=3, team="H"))
    got = [(g.kind, g.base) for g in after_lead("nba", hoops(*plays)) if g.kind.startswith("run")]
    assert got == [("run8", 2.0), ("run12", 1.0), ("run15", 1.0)]


def test_comeback_fires_once_when_a_15_point_hole_is_cut_to_5():
    plays = lead(50, 66, per=2)
    for n in range(1, 5):
        plays.append(bp("Jump Shot", "H makes 25-foot three point jumper", per=3, clock=f"{10 - n}:00", home=50 + 3 * n, away=66, value=3, team="H"))
    got = [g for g in after_lead("nba", hoops(*plays)) if g.kind == "comeback"]
    assert len(got) == 1 and got[0].margin_after == 4 and got[0].base == 4.0


def test_period_buzzer_and_the_half_court_bonus():
    s = hoops(*lead(20, 30), bp("Jump Shot", "H makes 30-foot three point jumper", per=1, clock="0.0", home=23, away=30, value=3, team="H"))
    assert one("nba", s, "period_buzzer").base == 3.0
    far = hoops(*lead(20, 30), bp("Heave Jump Shot", "H makes 52-foot three point heave", per=2, clock="0.3", home=23, away=30, value=3, team="H"))
    assert one("nba", far, "period_buzzer").base == 4.0
    final = hoops(*lead(100, 110, per=3), bp("Jump Shot", "H makes 20-foot jumper", per=4, clock="0.0", home=102, away=110, value=2, team="H"))
    assert "period_buzzer" not in kinds("nba", final)


def test_dunks_alley_oops_deep_threes_and_four_point_plays():
    assert last_kinds("nba", hoops(*lead(20, 30), bp("Driving Dunk Shot", "H makes driving dunk", home=22, away=30, value=2))) == ["dunk"]
    assert last_kinds("nba", hoops(*lead(20, 30), bp("Alley Oop Dunk Shot", "H makes alley oop dunk", home=22, away=30, value=2))) == ["alley_oop"]
    assert last_kinds("nba", hoops(*lead(20, 30), bp("Jump Shot", "H makes 28-foot three point jumper", home=23, away=30, value=3))) == ["deep_three"]
    assert last_kinds("nba", hoops(*lead(20, 30), bp("Jump Shot", "H makes 27-foot three point jumper", home=23, away=30, value=3))) == []
    four = hoops(*lead(20, 30), bp("Jump Shot", "H makes 24-foot three point jumper", clock="5:00", home=23, away=30, value=3),
                 bp("Free Throw - 1 of 1", "H makes free throw 1 of 1", clock="5:00", home=24, away=30, value=1))
    assert last_kinds("nba", four) == ["dead_ft", "four_point_play"]


def test_steal_into_a_quick_score():
    quick = hoops(*lead(20, 30), bp("Bad Pass\nTurnover", "A bad pass\nturnover (H steals)", clock="5:00", home=20, away=30, team="A"),
                  bp("Running Layup Shot", "H makes running layup", clock="4:56", home=22, away=30, value=2, team="H"))
    assert last_kinds("nba", quick) == ["steal_score"]
    slow = hoops(*lead(20, 30), bp("Bad Pass\nTurnover", "A bad pass\nturnover (H steals)", clock="5:00", home=20, away=30, team="A"),
                 bp("Running Layup Shot", "H makes running layup", clock="4:40", home=22, away=30, value=2, team="H"))
    assert last_kinds("nba", slow) == []


def test_technicals_flagrants_ejections_and_reviews():
    s = hoops(*lead(20, 30), bp("Technical Foul", "K technical foul"), bp("Flagrant Foul Type 1", "D flagrant foul type 1"),
              bp("Ejection", "T ejected"), bp("Defensive 3-Seconds Technical", "C defensive 3-seconds (technical foul)"),
              bp("Coach's Challenge (Stands)", "COACH'S CHALLENGE (CALL STANDS)"), bp("Coach's Challenge (Overturned)", "(CALL OVERTURNED)"))
    got = [(g.kind, g.value) for g in after_lead("nba", s)]
    assert got == [("technical", 2.0), ("flagrant", 2.0), ("ejection", 2.0), ("dead_review", -0.5)]


def test_fortieth_point_and_triple_double_come_from_the_plays():
    plays = lead(0, 2)
    for n in range(1, 14):
        plays.append(bp("Jump Shot", "S makes 25-foot three point jumper", home=3 * n, away=2, value=3, team="H", who=("star",)))
    plays.append(bp("Free Throw - 1 of 2", "S makes free throw 1 of 2", home=40, away=2, value=1, team="H", who=("star",)))
    assert last_kinds("nba", hoops(*plays)) == ["dead_ft", "star_points"]
    td = lead(0, 2)
    td += [bp("Jump Shot", "S makes jumper", home=2 * n, away=2, value=2, team="H", who=("s",)) for n in range(1, 6)]
    td += [bp("Jump Shot", "M makes jumper (S assists)", home=10 + 2 * n, away=2, value=2, team="H", who=("m", "s")) for n in range(1, 11)]
    td += [bp("Defensive Rebound", "S defensive rebound", home=30, away=2, team="H", who=("s",), shooting=False) for _ in range(10)]
    assert last_kinds("nba", hoops(*td)) == ["triple_double"]
    assert [g.kind for g in value2.grade("nba", hoops(*td, bp("Defensive Rebound", "S defensive rebound", who=("s",), shooting=False)))].count("triple_double") == 1


def test_overtime_start_grows_with_each_further_overtime():
    s = hoops(*lead(100, 100, per=4), bp("Jumpball", "jump", per=5, clock="5:00", home=100, away=100),
              bp("Jumpball", "jump", per=6, clock="5:00", home=100, away=100))
    assert [(g.kind, g.base, g.leverage) for g in after_lead("nba", s)] == [("ot_start", 3.0, 3.0), ("ot_start", 4.0, 3.0)]


def test_college_halves_read_period_two_as_the_last():
    s = hoops(*lead(60, 61, per=1), bp("Jump Shot", "H makes 12-foot jumper", per=2, clock="45.0", home=62, away=61, value=2, team="H"))
    assert last_kinds("cbb", s) == ["go_ahead"] and last_kinds("nba", s) == ["lead_change"]


def test_stale_and_swapped_score_rows_do_not_break_the_running_count():
    s = hoops(*lead(100, 97, per=3),
              bp("Substitution", "X enters", per=4, clock="3:10", home=97, away=104),                       # stale row
              bp("Jump Shot", "H makes 12-foot jumper", per=4, clock="3:00", home=102, away=97, value=2, team="H"))
    g = one("nba", s, "clutch_fg")
    assert (g.margin, g.margin_after) == (3, 5)


# --- leverage ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("league", ["nfl", "cfb", "nba", "wnba", "cbb"])
def test_leverage_table_is_monotone_and_bounded(league):
    cfg = value2.config()
    tab = cfg["leverage"][value2.family(league)]
    assert all(len(row) == len(tab["margin_max"]) + 1 for row in tab["table"])
    assert len(tab["table"]) == len(tab["left_frac"]) + len(tab["left_secs"]) + 1
    periods, length = cfg["regulation"][league]
    lefts = [periods * length - 1.0, periods * length * 0.4, 600.0, 250.0, 100.0, 30.0, 0.0]
    margins = list(range(0, 41))
    grid = [[value2.leverage(league, m, left) for m in margins] for left in lefts]
    for row in grid:                                          # more margin never means more leverage
        assert all(a >= b for a, b in zip(row, row[1:]))
        assert all(cfg["leverage"]["floor"] <= x <= cfg["leverage"]["cap"] for x in row)
    close = [row[0] for row in grid]
    blowout = [row[-1] for row in grid]
    assert all(a <= b for a, b in zip(close, close[1:]))      # a tied game matters more as time runs out
    assert all(a >= b for a, b in zip(blowout, blowout[1:]))  # a blowout matters less
    assert close[0] == 1.0 and close[-1] == 3.0 and blowout[-1] == 0.3


def test_leverage_overtime_and_unknown_clock():
    assert value2.leverage("nfl", 0, 300.0, overtime=True) == 3.0 and value2.leverage("nba", 2, 120.0, overtime=True) == 3.0
    assert value2.leverage("nba", 12, 30.0, overtime=True) == 0.3
    assert value2.leverage("nfl", 40, -1.0) == 1.0
    assert value2.seconds_left("cbb", 2, 60.0) == (60.0, False) and value2.seconds_left("cbb", 3, 200.0) == (200.0, True)
    assert value2.seconds_left("nfl", 1, 900.0) == (3600.0, False) and value2.seconds_left("nba", 4, None) == (-1.0, False)


def test_a_score_takes_the_smaller_of_the_margins_around_it():
    """A touchdown that cuts 16 to 9 late is graded on the 9 (two scores), one that makes 3 into 10 on the 3."""
    cut = one("nfl", fb(fp("Rush", "x", per=4, clock="4:00", home=30, away=14),
                        fp("Passing Touchdown", "for 5 yards, TOUCHDOWN.", per=4, clock="3:50", home=30, away=21, scoring=True)), "td_off")
    assert cut.leverage == value2.leverage("nfl", 9, 230.0)


# --- stakes ------------------------------------------------------------------------------------------------------

def _td(per=4, clock="3:00", home=20, away=24, **top):
    return fb(fp("Rush", "x", per=per, clock=clock, home=home, away=away - 7),
              fp("Passing Touchdown", "for 5 yards, TOUCHDOWN.", per=per, clock=clock, home=home, away=away, scoring=True), **top)


def test_stakes_default_to_one_and_read_only_what_the_summary_has():
    assert one("nfl", _td(), "td_off").stakes == 1.0
    post = _td(header={"season": {"type": 3}})
    assert one("nfl", post, "td_off").stakes == 1.5 and value2.game_stakes(post).postseason
    ranked = _td(header={"season": {"type": 2}, "competitions": [{"competitors": [{"rank": 4}, {"rank": 11}]}]})
    assert one("cfb", ranked, "td_off").stakes == 1.4
    half = _td(header={"competitions": [{"competitors": [{"rank": 4}, {"rank": 99}]}]})
    assert one("cfb", half, "td_off").stakes == 1.0


def test_upset_needs_a_7_point_underdog_close_in_the_last_period():
    pick = {"pickcenter": [{"spread": -7.5, "homeTeamOdds": {"favorite": True}, "awayTeamOdds": {"favorite": False}}]}
    assert one("nfl", _td(**pick), "td_off").stakes == 1.5                      # away underdog leads late
    assert one("nfl", _td(per=2, **pick), "td_off").stakes == 1.0               # not late
    assert one("nfl", _td(home=40, away=14, **pick), "td_off").stakes == 1.0    # underdog down 26
    small = {"pickcenter": [{"spread": -3.0, "homeTeamOdds": {"favorite": True}, "awayTeamOdds": {"favorite": False}}]}
    assert one("nfl", _td(**small), "td_off").stakes == 1.0
    assert value2.game_stakes(_td(pickcenter=[{"spread": 9.5}])).underdog == "home"     # no flags: the sign is the home side's


def test_fixed_stakes_replace_the_summary():
    post = _td(header={"season": {"type": 3}})
    assert one("nfl", post, "td_off").stakes == 1.5
    assert [g.stakes for g in value2.grade("nfl", post, fixed_stakes=1.0) if g.kind == "td_off"] == [1.0]
    g = [g for g in value2.grade("nfl", post, fixed_stakes=1.5) if g.kind == "td_off"][0]
    assert g.value == pytest.approx(g.base * g.leverage * 1.5)


# --- guards ------------------------------------------------------------------------------------------------------

def test_published_win_probability_never_changes_a_value():
    s = _td()
    pids = [p["id"] for p in s["drives"]["previous"][0]["plays"]]
    with_wp = {**s, "winprobability": [{"playId": pid, "homeWinPercentage": 0.05 + 0.9 * (i % 2)} for i, pid in enumerate(pids)]}
    assert value2.grade("nfl", with_wp) == value2.grade("nfl", s)


def test_grading_does_not_touch_the_summary_and_v1_reads_it_as_before():
    s = fb(fp("Passing Touchdown", "for 25 yards, TOUCHDOWN.", home=7, scoring=True, y=25),
           fp("Pass Interception Return", "INTERCEPTED by K.Fox.", home=7, isTurnover=True),
           fp("Field Goal Good", "45 yard field goal is GOOD.", home=10, down=4, scoring=True),
           fp("Rush", "for 12 yards.", home=10, gain=12))
    before = copy.deepcopy(s)
    v1_before = [(m.kind, m.tier, m.play_id) for m in detect("nfl", s)]
    value2.grade("nfl", s)
    assert s == before and [(m.kind, m.tier, m.play_id) for m in detect("nfl", s)] == v1_before
    assert [k for k, _, _ in v1_before] == ["td", "turnover", "fg", "gain10"]
    assert V.TIER_WEIGHTS == {1: 5.0, 2: 2.0, 3: 0.5}
    assert tiers.kinds("nfl", 1) == ("td", "turnover") and tiers.kinds("nba", 1) == ("clutch_fg",)


def test_every_number_comes_from_the_data_file():
    cfg = copy.deepcopy(value2.config())
    cfg["football"]["base"]["td_off"] = 50.0
    s = fb(fp("Passing Touchdown", "for 3 yards, TOUCHDOWN.", home=7, scoring=True))
    assert [g.base for g in value2.grade("nfl", s, cfg=cfg)] == [50.0] and one("nfl", s, "td_off").base == 5.0


def test_hockey_and_baseball_are_not_graded_yet():
    with pytest.raises(KeyError):
        value2.grade("nhl", {"plays": []})
