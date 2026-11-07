"""[quiet break, Nick Nov 7 12:40] UNC@CONN (CFB, CBSSN): at the end of Q1 the decider wanted another game
but the leave gate said "delay not calibrated yet" and then "no fresh data for 103-128s, waiting for an
official break": during the commercial the feed logs nothing, so the stale-data guard blocked leaving
exactly when it was safest. decider.leave.quiet_break treats a quiet feed right after a period end,
a 0:00 clock or a score (stop-start sports) as an official stoppage, for floor_s after it."""
from redzone.decider.decider import Decider
from redzone.decider.screen import Delay, DelayBook, Timeline, leave_gate
from redzone.models import GameState, League, Phase, Play, Stoppage, Team

T0 = 10_000.0
ON = {"enabled": True, "floor_s": {"period_end": 120, "score": 40}, "near_end_clock_s": 0, "near_end_quiet_s": 60}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _g(league, plays, at, q=1, clock_s=300.0, hs=7, as_=0):
    return GameState(league=league, game_id="g", home=Team("UNC", score=hs), away=Team("CONN", score=as_),
                     phase=Phase.LIVE, period=q, clock_s=clock_s, stoppage=Stoppage.NONE,
                     recent_plays=plays, last_play=plays[-1] if plays else None, updated_at=at)


def _tl(league, last: Play, cfg=ON):
    """A game watched for a while, then `last` logged at T0 and nothing after it."""
    c = Clock(T0 - 200)
    tl = Timeline(league, clock=c)
    tl.quiet_cfg = cfg
    tl.observe(_g(league, [Play("p0", "run", "run", wallclock=T0 - 210, seen_at=T0 - 200, clock=600, period=1)], T0 - 200))
    c.t = T0 - 120
    tl.observe(_g(league, [Play("p1", "pass", "pass", wallclock=T0 - 130, seen_at=T0 - 120, clock=500, period=1)], T0 - 120))
    last.wallclock, last.seen_at = T0, T0 + 5
    c.t = T0 + 5
    tl.observe(_g(league, [last], T0 + 5))
    return tl


CALIBRATED = Delay(34.9, 0.6, calibrated_at=T0)
UNCALIBRATED = Delay(35.0, 15.0)        # CBSSN today: sigma 15 > uncalibrated_sigma_s 8


def end_q1():
    return Play("e", "End of 1st Quarter", "end_period", clock=0.0, period=1)


def test_the_incident_was_blocked_before(cfg=None):
    tl = _tl(League.CFB, end_q1(), cfg={"enabled": False})
    for d in (CALIBRATED, UNCALIBRATED):
        g = leave_gate(tl, d, T0 + 110, 8.0)
        assert not g.safe and "official break" in g.why


def test_quiet_after_a_period_end_is_a_break_even_uncalibrated():
    tl = _tl(League.CFB, end_q1())
    for d in (CALIBRATED, UNCALIBRATED):
        g = leave_gate(tl, d, T0 + 110, 8.0)
        assert g.safe and g.on_screen_break and "quiet break" in g.why, (d, g)


def test_not_before_it_has_surely_aired():
    tl = _tl(League.CFB, end_q1())
    # uncalibrated: aired surely only after T0 + 35 + 2*15 + 3
    assert not leave_gate(tl, UNCALIBRATED, T0 + 60, 8.0).safe
    assert leave_gate(tl, UNCALIBRATED, T0 + 70, 8.0).safe


def test_not_after_the_floor_may_have_run_out():
    tl = _tl(League.CFB, end_q1())
    # calibrated: T0 + 120 + 34.9 - 1.2 - 6 = T0 + 147.7
    assert leave_gate(tl, CALIBRATED, T0 + 147, 8.0).safe
    assert not leave_gate(tl, CALIBRATED, T0 + 149, 8.0).safe


def test_anything_newer_in_the_data_ends_it():
    tl = _tl(League.CFB, end_q1())
    tl._clock.t = T0 + 60
    tl.observe(_g(League.CFB, [Play("k", "kickoff", "kickoff", wallclock=T0 + 55, seen_at=T0 + 60, clock=900, period=2)],
                  T0 + 60, q=2, clock_s=900))
    g = leave_gate(tl, CALIBRATED, T0 + 110, 8.0)
    assert "quiet break" not in g.why


def test_a_zero_clock_play_counts_as_the_period_end():
    tl = _tl(League.CFB, Play("z", "pass incomplete", "pass", clock=0.0, period=1))
    assert leave_gate(tl, CALIBRATED, T0 + 110, 8.0).safe


def test_a_football_score_gets_the_short_floor():
    tl = _tl(League.CFB, Play("td", "touchdown", "td", scoring=True, clock=200.0, period=1))
    assert leave_gate(tl, CALIBRATED, T0 + 50, 8.0).safe          # 34.9 + 1.2 + 3 <= 50 <= 40 + 34.9 - 1.2 - 6
    assert not leave_gate(tl, CALIBRATED, T0 + 70, 8.0).safe


def test_a_basketball_basket_is_not_a_break():
    tl = _tl(League.NBA, Play("b", "layup", "layup", scoring=True, clock=200.0, period=1))
    assert not leave_gate(tl, CALIBRATED, T0 + 50, 8.0).safe


def test_an_ordinary_play_is_not_a_break():
    tl = _tl(League.CFB, Play("x", "run for 3", "run", clock=22.0, period=1))
    g = leave_gate(tl, CALIBRATED, T0 + 110, 8.0)
    assert not g.safe and "no fresh data" in g.why


def test_near_end_guess_is_opt_in():
    """Today's exact data: the last entry left 0:22 in Q1 and the end of the quarter never arrived."""
    last = lambda: Play("x", "run for 3", "run", clock=22.0, period=1)
    assert not leave_gate(_tl(League.CFB, last()), CALIBRATED, T0 + 110, 8.0).safe
    tl = _tl(League.CFB, last(), cfg={**ON, "near_end_clock_s": 30})
    g = leave_gate(tl, CALIBRATED, T0 + 110, 8.0)       # quiet 105 s >= 60; period can end at T0 + 22 + 10
    assert g.safe and "clock likely ran out" in g.why
    assert not leave_gate(tl, CALIBRATED, T0 + 50, 8.0).safe   # quiet only 45 s
    q4 = _tl(League.CFB, Play("y", "run", "run", clock=22.0, period=4), cfg={**ON, "near_end_clock_s": 30})
    assert not leave_gate(q4, CALIBRATED, T0 + 110, 8.0).safe   # never guesses the end of the game


def test_default_on_and_a_settings_change_reaches_a_game_already_on(cfg):
    dec = Decider(cfg, DelayBook({}, 3.0))
    tl = dec.timeline("g", League.CFB)
    assert tl.quiet_cfg["enabled"] is True and tl.quiet_cfg["floor_s"]["period_end"] == 120
    assert tl.quiet_cfg["near_end_clock_s"] == 0
    cfg.data["decider"]["leave"]["quiet_break"]["enabled"] = False
    assert dec.timeline("g", League.CFB).quiet_cfg["enabled"] is False
