from redzone.models import GameState, League, Phase, Team
from redzone.sources.merge import Merger


def _g(period, clock, hs, as_, **kw):
    return GameState(
        league=League.NFL, game_id="1",
        home=Team("WSH", score=hs), away=Team("DAL", score=as_),
        phase=Phase.LIVE, period=period, clock_s=clock, **kw,
    )


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_furthest_ahead_source_wins():
    clk = FakeClock()
    m = Merger(clock=clk)
    m.update("espn", _g(4, 120, 20, 17))
    clk.t += 2
    m.update("fast", _g(4, 90, 27, 17))
    assert m.games["1"].home.score == 27
    clk.t += 1
    m.update("espn", _g(4, 118, 20, 17))  # slower source catches up partially
    assert m.games["1"].home.score == 27
    assert m.games["1"].clock_s == 90


def test_slow_source_fills_missing_fields():
    m = Merger(clock=FakeClock())
    m.update("fast", _g(4, 90, 27, 17))
    m.update("espn", _g(4, 100, 20, 17, home_win_prob=0.8, spread=-3.5))
    assert m.games["1"].home_win_prob == 0.8
    assert m.games["1"].home.score == 27


def test_regressing_source_gets_benched():
    m = Merger(clock=FakeClock())
    m.update("bad", _g(4, 60, 30, 17))
    for _ in range(3):
        m.update("bad", _g(1, 900, 0, 0))
    assert m.health["bad"].benched
    assert m.games["1"].home.score == 30


def test_race_log_measures_lag():
    clk = FakeClock()
    m = Merger(clock=clk)
    m.update("fast", _g(2, 300, 7, 0))
    clk.t += 4.5
    m.update("espn", _g(2, 280, 7, 0))
    summary = m.race_summary()
    assert summary["fast"]["first"] == 1
    assert summary["espn"]["avg_lag_s"] == 4.5


def test_missing_scores_do_not_crash():
    m = Merger(clock=FakeClock())
    g = GameState(league=League.NBA, game_id="2", home=Team("WAS"), away=Team("BOS"))
    m.update("espn", g)
    assert m.is_stale("2") is False  # not live, so not stale
