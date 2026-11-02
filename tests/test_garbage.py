from conftest import nfl

from redzone.models import GameState, League, Phase, Situation, Team
from redzone.scoring import garbage
from redzone.scoring.engine import Personal, Scorer

ME = Personal(my_teams={lg: {"WSH"} for lg in League})


def nba(hs, as_, q, clock, gid="n"):
    return GameState(league=League.NBA, game_id=gid, home=Team("WSH", score=hs), away=Team("BOS", score=as_),
                     phase=Phase.LIVE, period=q, clock_s=clock)


def nhl(hs, as_, p, clock, empty_net=None):
    return GameState(league=League.NHL, game_id="h", home=Team("WSH", score=hs), away=Team("PIT", score=as_),
                     phase=Phase.LIVE, period=p, clock_s=clock, situation=Situation(empty_net=empty_net))


def test_thresholds_by_league():
    assert garbage.is_garbage(nfl(hs=35, as_=10, q=3, clock=600))           # 25 in the 2nd half
    assert not garbage.is_garbage(nfl(hs=24, as_=10, q=3, clock=600))       # 14 in Q3: still a game
    assert garbage.is_garbage(nfl(hs=27, as_=10, q=4, clock=400))           # 17 under 8:00
    assert not garbage.is_garbage(nfl(hs=27, as_=10, q=4, clock=600))       # 17 with 10:00 left
    assert garbage.is_garbage(nfl(hs=20, as_=10, q=4, clock=30))            # 10 under 0:45
    assert not garbage.is_garbage(nfl(hs=24, as_=10, q=5, clock=300))       # overtime never
    assert garbage.is_garbage(nba(110, 90, 4, 500))                         # 20 under 9:00
    assert not garbage.is_garbage(nba(110, 92, 4, 500))                     # 18 under 9:00
    assert garbage.is_garbage(nba(100, 90, 4, 100))                         # 10 under 2:00
    assert garbage.is_garbage(nhl(5, 1, 3, 900))                            # 4 in the 3rd
    assert not garbage.is_garbage(nhl(3, 1, 3, 900))                        # 2 with 15:00 left
    assert garbage.is_garbage(nhl(3, 1, 3, 40))                             # 2 in the last minute...
    assert not garbage.is_garbage(nhl(3, 1, 3, 40, empty_net="PIT"))        # ...unless the goalie is pulled


def test_hysteresis_needs_a_real_comeback():
    g = nba(110, 90, 4, 500)
    assert garbage.is_garbage(g)
    g.home.score = 108                     # 18: below the line, but not a comeback yet
    assert garbage.is_garbage(g, was=True)
    g.home.score = 103                     # 13: it's a game again
    assert not garbage.is_garbage(g, was=True)


def test_my_team_gets_no_boost_in_garbage_time(cfg, clock):
    s = Scorer(cfg, clock=clock).score(nfl("w", "WSH", "NYG", 38, 7, q=4, clock=300), ME)
    assert s.garbage and s.personal == 0 and s.reason.startswith("garbage time")
    assert s.score < 15 and s.lock is None


def test_bets_still_count_in_garbage_time(cfg, clock):
    me = Personal(my_teams={League.NFL: {"WSH"}}, bet_sweat={"w": 1.0}, bet_labels={"w": "your bet: NYG +31.5"})
    s = Scorer(cfg, clock=clock).score(nfl("w", "WSH", "NYG", 38, 7, q=4, clock=300), me)
    assert s.garbage and s.personal >= 19


def test_comeback_gets_a_boost(cfg, clock):
    sc = Scorer(cfg, clock=clock)
    g = nba(110, 88, 4, 500, gid="c")
    assert sc.score(g, Personal()).garbage
    clock.t += 120
    g.home.score, g.clock_s = 110, 300
    g.away.score = 102                     # 22 -> 8 with 5:00 left
    s = sc.score(g, Personal())
    assert not s.garbage and "comeback" in s.reason


async def test_focus_suspends_in_garbage_time_and_returns(tmp_path):
    from redzone.config import Config
    from redzone.decider.decider import FocusMode
    from redzone.engine import Engine
    from redzone.store import Store
    e = Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "db"))
    e.toast = lambda *a, **k: None
    g = nba(110, 85, 4, 500, gid="f")
    e.merger.update("t", g)
    e.decider.user.focus_game, e.decider.user.focus_mode = "f", FocusMode.FOCUS
    e.decide()
    assert e.decider.user.focus_mode == FocusMode.AUTOPILOT and e._suspended_focus
    g2 = nba(110, 104, 4, 300, gid="f")
    e.merger.update("t", g2)
    e.decide()
    assert e.decider.user.focus_mode == FocusMode.FOCUS and e._suspended_focus is None
