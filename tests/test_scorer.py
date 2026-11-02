from conftest import nfl

from redzone.models import GameState, League, Phase, Situation, Stoppage, Team
from redzone.scoring.engine import Personal, Scorer, estimate_li

ME = Personal(my_teams={League.NFL: {"WSH"}, League.NBA: {"WSH"}, League.NHL: {"WSH"}, League.MLB: {"WSH"}})


def _score(cfg, clock, g, me=ME):
    return Scorer(cfg, clock=clock).score(g, me)


# --- the three Washington rules from the spec -------------------------------

def test_washington_ordinary_beats_other_game_heating_up(cfg, clock):
    was = nfl("w", "WSH", "NYG", 7, 7, q=2, clock=600, pos="WSH", down=1, dist=10, ytg=40, wp=0.5)
    other = nfl("o", "KC", "BUF", 17, 20, q=3, clock=400, pos="KC", down=2, dist=8, ytg=18, wp=0.4)
    s_was, s_other = _score(cfg, clock, was), _score(cfg, clock, other)
    assert s_was.score > s_other.score, (s_was.score, s_other.score)


def test_other_crunch_time_beats_washington_ordinary_by_margin(cfg, clock):
    was = nfl("w", "WSH", "NYG", 7, 7, q=2, clock=600, pos="WSH", down=1, dist=10, ytg=40, wp=0.5)
    crunch = nfl("o", "KC", "BUF", 20, 17, q=4, clock=90, pos="BUF", down=2, dist=5, ytg=35, wp=0.65)
    s_was, s_c = _score(cfg, clock, was), _score(cfg, clock, crunch)
    assert s_c.score - s_was.score >= cfg.get("decider.switch_margin") + 3


def test_washington_crunch_beats_everything(cfg, clock):
    was = nfl("w", "WSH", "NYG", 17, 20, q=4, clock=90, pos="WSH", down=2, dist=5, ytg=35, wp=0.35)
    best_neutral = nfl("o", "KC", "BUF", 20, 17, q=5, clock=60, pos="BUF", down=4, dist=1, ytg=1,
                       gtg=True, wp=0.5)
    assert _score(cfg, clock, was).score > _score(cfg, clock, best_neutral).score


def test_neutral_game_never_above_cap(cfg, clock):
    g = nfl("o", "KC", "BUF", 20, 17, q=5, clock=30, pos="BUF", down=4, dist=1, ytg=1, gtg=True, wp=0.5)
    assert _score(cfg, clock, g, Personal()).score <= 90.0


# --- behaviour ---------------------------------------------------------------

def test_blowout_scores_low(cfg, clock):
    g = nfl("b", "KC", "BUF", 42, 3, q=4, clock=90, pos="BUF", down=1, dist=10, ytg=15, wp=0.99)
    assert _score(cfg, clock, g, Personal()).score < 25


def test_blowout_kills_washington_boost(cfg, clock):
    close = _score(cfg, clock, nfl("a", "WSH", "NYG", 14, 14, q=4, clock=600, wp=0.5))
    blown = _score(cfg, clock, nfl("b", "WSH", "NYG", 45, 3, q=4, clock=600, wp=0.99))
    assert close.personal > 20 and blown.personal < 5


def test_break_is_flagged_and_unlocked(cfg, clock):
    g = nfl("x", "KC", "BUF", 20, 17, q=4, clock=100, pos="BUF", down=1, dist=10, ytg=15,
            stoppage=Stoppage.TIMEOUT)
    s = _score(cfg, clock, g)
    assert s.is_break and s.lock is None
    assert s.reason.startswith("timeout")


def test_reason_reads_like_a_broadcaster(cfg, clock):
    g = nfl("x", "KC", "WSH", 20, 16, q=4, clock=72, pos="WSH", down=4, dist=2, ytg=2, gtg=True)
    s = _score(cfg, clock, g)
    assert "4th & goal at the 2" in s.reason
    assert "down 4" in s.reason
    assert "1:12 left in Q4" in s.reason
    assert s.pre_moment == "4th & goal"


def test_smoothing_fast_attack_slow_release(cfg, clock):
    sc = Scorer(cfg, clock=clock)
    hot = nfl("x", "KC", "BUF", 20, 17, q=4, clock=60, pos="BUF", down=1, dist=10, ytg=8)
    cold = nfl("x", "KC", "BUF", 20, 17, q=4, clock=55, pos="KC", down=1, dist=10, ytg=80)
    high = sc.score(hot).score
    clock.t += 10
    after = sc.score(cold)
    assert after.raw < after.score < high  # drifting down, not dropping


def test_missing_everything_does_not_crash(cfg, clock):
    for lg in League:
        g = GameState(league=lg, game_id=lg.value, home=Team("A"), away=Team("B"), phase=Phase.LIVE)
        s = _score(cfg, clock, g)
        assert 0 <= s.score <= 100


def test_not_live_scores_zero(cfg, clock):
    g = nfl("p")
    g.phase = Phase.PRE
    assert _score(cfg, clock, g).score == 0


def test_nba_run_detected(cfg, clock):
    sc = Scorer(cfg, clock=clock)
    def nba(hs, as_):
        return GameState(league=League.NBA, game_id="n", home=Team("BOS", score=hs), away=Team("NYK", score=as_),
                         phase=Phase.LIVE, period=3, clock_s=400, stoppage=Stoppage.NONE)
    sc.score(nba(60, 60))
    for hs in (63, 66, 68, 71):
        clock.t += 20
        s = sc.score(nba(hs, 60))
    assert "BOS on a 11-0 run" in s.reason


def test_nhl_power_play_late_locks(cfg, clock):
    g = GameState(league=League.NHL, game_id="h", home=Team("WSH", score=2), away=Team("PIT", score=2),
                  phase=Phase.LIVE, period=3, clock_s=200, stoppage=Stoppage.NONE,
                  situation=Situation(power_play="WSH", strength="5v4"))
    s = _score(cfg, clock, g)
    assert s.lock == "power play" and "WSH power play" in s.reason
    assert s.score > 70


def test_mlb_leverage_estimate_orders_sensibly():
    calm = estimate_li(2, 4, 0, (False, False, False))
    tense = estimate_li(9, -1, 2, (True, True, True))
    assert tense > 3 > 0.5 > calm


def test_mlb_walk_off_spot(cfg, clock):
    g = GameState(league=League.MLB, game_id="m", home=Team("WSH", score=3), away=Team("NYM", score=4),
                  phase=Phase.LIVE, period=9, half="bottom", stoppage=Stoppage.NONE,
                  situation=Situation(outs=2, runners=(True, True, False)))
    s = _score(cfg, clock, g)
    assert s.pre_moment == "walk-off spot" and "walk-off spot" in s.reason


def test_bet_sweat_boosts(cfg, clock):
    g = nfl("x", "KC", "BUF", 20, 17, q=3, clock=400)
    base = _score(cfg, clock, g, Personal())
    me = Personal(bet_sweat={"x": 1.0}, bet_labels={"x": "your bet: BUF +3.5"})
    assert _score(cfg, clock, g, me).score - base.score >= 19


# --- crunch time ---------------------------------------------------------------

def test_crunch_time_is_final_two_minutes_of_one_score_game_or_ot(cfg, clock):
    def crunch(**kw):
        return _score(cfg, clock, nfl("c", "KC", "BUF", **kw)).crunch
    assert crunch(hs=20, as_=13, q=4, clock=120)                 # 7 points, 2:00 left
    assert crunch(hs=20, as_=12, q=4, clock=5)                   # 8 is still one score
    assert not crunch(hs=21, as_=12, q=4, clock=60)              # 9 is two scores
    assert not crunch(hs=20, as_=17, q=4, clock=121)             # not yet
    assert not crunch(hs=20, as_=17, q=2, clock=60)              # end of the half isn't crunch
    assert crunch(hs=30, as_=20, q=5, clock=500)                 # any overtime


def test_crunch_survives_the_two_minute_warning(cfg, clock):
    g = nfl("c", "KC", "WSH", 20, 16, q=4, clock=120, pos="WSH", down=1, dist=10, ytg=60,
            stoppage=Stoppage.TWO_MINUTE_WARNING)
    s = _score(cfg, clock, g)
    # A break: nothing to lock into, but it's exactly when to get there.
    assert s.is_break and s.lock is None and s.crunch
    assert s.pre_moment == "two-minute warning, one score"
    half = _score(cfg, clock, nfl("h", "KC", "WSH", 20, 16, q=2, clock=120, stoppage=Stoppage.TWO_MINUTE_WARNING))
    assert half.pre_moment is None and not half.crunch


def test_tied_game_final_two_minutes_is_a_two_minute_drill(cfg, clock):
    # Driving to win counts as much as driving to tie; only a team that's ahead is just running clock.
    tied = _score(cfg, clock, nfl("t", "KC", "BUF", 17, 17, q=4, clock=90, pos="BUF", down=1, dist=10, ytg=60))
    ahead = _score(cfg, clock, nfl("a", "KC", "BUF", 17, 20, q=4, clock=90, pos="BUF", down=1, dist=10, ytg=60))
    assert tied.moment - ahead.moment >= cfg.get("score.nfl.two_minute_drill")


def test_other_sports_have_no_nfl_crunch_flag(cfg, clock):
    g = GameState(league=League.NBA, game_id="n", home=Team("BOS", score=100), away=Team("NYK", score=99),
                  phase=Phase.LIVE, period=4, clock_s=30, stoppage=Stoppage.NONE)
    assert not _score(cfg, clock, g).crunch
