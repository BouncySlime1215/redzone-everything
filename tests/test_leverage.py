"""redzone/lab/leverage.py: the rule floor for a mid-play leave (Nick, Nov 4 3:15 PM ET)."""
from redzone.lab.leverage import high_leverage
from redzone.models import League


def play(per, clock, home, away):
    return {"period": {"number": per}, "clock": {"displayValue": clock}, "homeScore": home, "awayScore": away}


def test_close_and_late_is_high_leverage():
    assert high_leverage(League.NFL, play(4, "2:00", 20, 17))
    assert high_leverage(League.NBA, play(4, "5:00", 100, 96))


def test_early_or_not_close_is_low_leverage():
    assert not high_leverage(League.NFL, play(2, "2:00", 20, 17))
    assert not high_leverage(League.NFL, play(4, "2:00", 30, 10))
    assert not high_leverage(League.NBA, play(4, "9:00", 100, 98))


def test_overtime_is_always_high_leverage():
    assert high_leverage(League.NFL, play(5, "8:00", 20, 20))
    assert high_leverage(League.NHL, play(4, "1:00", 2, 2))


def test_nhl_clock_counts_up():
    assert high_leverage(League.NHL, play(3, "17:00", 2, 1))       # 3:00 left
    assert not high_leverage(League.NHL, play(3, "5:00", 2, 1))    # 15:00 left


def test_mlb_late_and_close():
    assert high_leverage(League.MLB, play(8, "", 3, 2))
    assert not high_leverage(League.MLB, play(3, "", 3, 2))


def test_missing_play_or_score_is_not_high():
    assert not high_leverage(League.NFL, None)
    assert not high_leverage(League.NFL, {"period": {"number": 4}})


def test_college_reads_its_pro_leagues_rule():
    """F1: CFB / CBB / WCBB were False for every state (no `.base`, no `pro_period`)."""
    assert high_leverage(League.CFB, play(4, "2:00", 20, 17))
    assert high_leverage(League.CFB, play(5, "8:00", 20, 20))            # OT
    assert not high_leverage(League.CFB, play(2, "2:00", 20, 17))
    assert high_leverage(League.CBB, play(2, "1:00", 60, 59))            # 2nd half, 1:00 left = "Q4"
    assert not high_leverage(League.CBB, play(1, "1:00", 60, 59))        # 1st half
    assert not high_leverage(League.CBB, play(2, "15:00", 60, 59))       # not late yet
    assert high_leverage(League.CBB, play(3, "5:00", 60, 60))            # college OT
    assert high_leverage(League.WCBB, play(4, "1:00", 60, 59))


def test_the_grader_agrees_with_the_decider_for_every_league():
    """F1: lab.leverage.high_leverage == scoring.engine.high_leverage for the same state, all leagues."""
    from redzone.models import GameState, Phase, Team
    from redzone.scoring import engine
    cases = [(4, "2:00", 20, 17), (4, "2:00", 30, 10), (2, "2:00", 20, 17), (5, "8:00", 20, 20),
             (1, "1:00", 60, 59), (2, "1:00", 60, 59), (2, "15:00", 60, 59), (3, "5:00", 60, 60),
             (4, "5:00", 100, 96), (3, "17:00", 2, 1), (8, "", 3, 2), (3, "", 3, 2)]
    for lg in League:
        for per, clock, h, a in cases:
            if lg.base == League.NHL:     # the NHL feed's clock counts up; the decider state holds seconds left
                continue
            p = play(per, clock, h, a)
            left = None
            if clock:
                m, s = clock.split(":")
                left = int(m) * 60 + int(s)
            g = GameState(league=lg, game_id="x", home=Team("H", score=h), away=Team("A", score=a),
                          phase=Phase.LIVE, period=per, clock_s=left)
            assert high_leverage(lg, p) == engine.high_leverage(g), (lg, per, clock, h, a)
