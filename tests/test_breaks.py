"""Break predictor (redzone/scoring/breaks.py) and the BREAK standby class it feeds."""
from conftest import FakeClock

from redzone import standby
from redzone.decider.decider import Candidate
from redzone.models import GameState, League, Phase, Play, Situation, Stoppage, Team
from redzone.scoring import breaks
from redzone.scoring.engine import Scored


def g(league, period, clock, *, stoppage=Stoppage.NONE, last=None, plays=(), gid="g", **sit):
    x = GameState(league=league, game_id=gid, home=Team("WSH", score=7), away=Team("DAL", score=3),
                  phase=Phase.LIVE, period=period, clock_s=clock, stoppage=stoppage, situation=Situation(**sit))
    x.last_play = last
    x.recent_plays = list(plays)
    return x


def play(pid, kind, text=""):
    return Play(pid, text, kind)


def test_nfl_score_and_punt_are_long_breaks_but_a_team_timeout_is_not():
    td = breaks.forecast(g(League.NFL, 2, 500, stoppage=Stoppage.SCORE_BREAK, last=play("1", "td")))
    punt = breaks.forecast(g(League.NFL, 2, 500, last=play("2", "punt")))
    team = breaks.forecast(g(League.NFL, 2, 500, stoppage=Stoppage.TIMEOUT,
                             last=play("3", "timeout", "Timeout #1 by MIN at 00:46.")))
    tv = breaks.forecast(g(League.NFL, 2, 500, stoppage=Stoppage.TIMEOUT,
                           last=play("4", "official_timeout", "Official Timeout at 10:27.")))
    assert td.p >= 0.9 and td.visit and td.now
    assert punt.p >= 0.7 and punt.expect_s == breaks.LEN["nfl_punt"]
    assert team.p < 0.5 and not team.visit
    assert tv.p == 1.0 and tv.visit
    assert breaks.forecast(g(League.NFL, 2, 500, down=2, yards_to_goal=60)).p == 0.0


def test_nba_mandatory_timeouts_are_owed_under_7_and_3_minutes():
    assert breaks.forecast(g(League.NBA, 1, 450)).p == 0.0
    due = breaks.forecast(g(League.NBA, 1, 400))
    assert due.p >= 0.5 and due.expect_s == breaks.LEN["nba_long"]
    assert breaks.forecast(g(League.NBA, 1, 400), clocks=(410.0,)).p == 0.0     # #1 taken
    assert breaks.forecast(g(League.NBA, 1, 170), clocks=(410.0,)).p >= 0.5      # #2 owed under 3:00
    assert breaks.forecast(g(League.NBA, 1, 170), clocks=(410.0, 200.0)).p == 0.0


def test_nba_third_timeout_of_a_quarter_is_short():
    to = play("t", "full_timeout", "Celtics Full timeout")
    two = breaks.forecast(g(League.NBA, 2, 300, stoppage=Stoppage.TIMEOUT, last=to), clocks=(500.0, 400.0))
    three = breaks.forecast(g(League.NBA, 2, 300, stoppage=Stoppage.TIMEOUT, last=to),
                            clocks=(500.0, 400.0, 300.0))
    assert two.expect_s == breaks.LEN["nba_long"] and three.expect_s == breaks.LEN["nba_short"]


def test_college_media_marks_and_hockey_tv_timeouts():
    assert breaks.forecast(g(League.CBB, 1, 1000)).p == 0.0
    assert breaks.forecast(g(League.CBB, 1, 950)).p >= 0.8                       # under 16:00, none yet
    assert breaks.forecast(g(League.CBB, 1, 950), clocks=(951.0,)).p == 0.0
    assert breaks.forecast(g(League.CBB, 1, 700), clocks=(951.0,)).p >= 0.8      # 12:00 owed
    assert breaks.forecast(g(League.CBB, 1, 700), clocks=(745.0,)).p >= 0.8      # 16 used late; 12 still owed
    assert breaks.forecast(g(League.NHL, 1, 900)).p == 0.0                       # under 6 min elapsed
    assert breaks.forecast(g(League.NHL, 1, 800)).p >= 0.5
    assert breaks.forecast(g(League.NHL, 1, 800, power_play="WSH")).p == 0.0
    assert breaks.forecast(g(League.NHL, 1, 800), clocks=(830.0,)).p == 0.0


def test_book_counts_timeouts_per_period_and_holds_a_break_on_its_way_to_the_screen():
    clock = FakeClock(1000.0)
    book = breaks.BreakBook(clock=clock)
    to = play("t1", "full_timeout", "Magic Full timeout")
    book.observe(g(League.NBA, 1, 410, plays=[to]))
    book.observe(g(League.NBA, 1, 405, plays=[to]))                              # same play: counted once
    assert book.clocks("g", 1) == (410,)
    book.observe(g(League.NBA, 2, 700, plays=[to]))
    assert book.clocks("g", 2) == ()
    td = g(League.NFL, 1, 500, stoppage=Stoppage.SCORE_BREAK, last=play("1", "td"), gid="f")
    assert book.forecast(td).p >= 0.9
    clock.t += 30
    kick = g(League.NFL, 1, 500, last=play("2", "kickoff"), gid="f")
    assert book.forecast(kick).p >= 0.9                                          # still coming to the screen
    clock.t += 60
    assert book.forecast(kick).p == 0.0


def sc(gid, score):
    return Scored(gid, League.NFL, score, score, score, 1.0, 0.0, f"reason {gid}", [], False, None, None, 1.0)


def test_break_destination_outranks_a_better_plain_game_and_takes_over_at_once():
    cs = {k: Candidate(sc(k, v), f"youtubetv:{k}") for k, v in (("dest", 40), ("plain", 90))}
    opts = standby.options(cs, current="cur", brk="dest", brk_why="punt")
    assert (opts[0].game_id, opts[0].kind) == ("dest", "break")
    clock = FakeClock(0.0)
    p = standby.Planner({"two_stick.hold_s": 20, "two_stick.min_gain": 8}, clock=clock)
    p.plan(standby.options(cs, current="cur"), ("youtubetv:plain", None))
    clock.t += 1
    pick = p.plan(opts, ("youtubetv:plain", None))
    assert pick is not None and pick.game_id == "dest"


def test_shadow_counts_breaks_filled():
    s = standby.ShadowStick(tune_s=7)
    s.preload("a", None, 0)
    s.switch("a", None, "home", 10, brk=True)
    s.switch("b", None, "a", 20, brk=True)
    s.switch("home", None, "b", 30)
    card = s.card()
    assert card["breaks_filled"] == "1/2" and card["break_fill_rate"] == 50.0
