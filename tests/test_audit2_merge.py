"""AUDIT-2, merge: a None clock must not read as the end of a period, and a glitched leader must not stick.

Why: progress_key turned a missing clock into 0 (the end of the period), so a bare payload outranked real
readings and the next real reading counted as a backward jump that benched the source; and a glitched period
that nothing could overtake stayed on the merged game for the rest of the night.
"""
from redzone.models import GameState, League, Phase, Team
from redzone.sources.merge import Merger, progress_key


def _g(period, clock, hs=0, as_=0, gid="1"):
    return GameState(league=League.NFL, game_id=gid, home=Team("WSH", score=hs), away=Team("DAL", score=as_),
                     phase=Phase.LIVE, period=period, clock_s=clock)


class Clk:
    t = 1000.0

    def __call__(self):
        return self.t


def test_none_clock_is_not_ahead_of_a_real_clock():
    assert progress_key(_g(2, None)) < progress_key(_g(2, 600.0))


def test_a_bare_clockless_payload_does_not_take_the_game_over():
    """Repro: a scoreboard poll with no clock outranked a real reading (None became 0 = end of period), so
    its stoppage (UNKNOWN) replaced the leader's dead ball."""
    from redzone.models import Stoppage
    clk = Clk()
    m = Merger(clock=clk)
    lead = _g(2, 590.0)
    lead.stoppage = Stoppage.TIMEOUT
    m.update("espn", lead)
    clk.t += 1
    m.update("board", _g(2, None))
    assert m.games["1"].stoppage == Stoppage.TIMEOUT
    assert m.games["1"].sources.get("board") is None or m.games["1"].clock_s == 590.0


def test_clockless_payload_in_the_middle_is_not_a_backward_jump():
    clk = Clk()
    m = Merger(clock=clk)
    for i in range(4):
        for c in (600.0 - i * 10, None):
            clk.t += 1
            m.update("espn", _g(2, c))
    assert m.health["espn"].regressions == 0


def test_glitched_period_is_replaced_once_the_glitching_source_is_benched():
    clk = Clk()
    m = Merger(clock=clk)
    m.update("espn", _g(2, 600.0))
    m.update("nfl", _g(2, 600.0))
    clk.t += 1
    m.update("espn", _g(4, 100.0))          # glitch: period 4 in a period-2 game
    assert m.games["1"].period == 4
    for i in range(3):                        # espn goes back to the truth, and nfl agrees
        clk.t += 1
        m.update("espn", _g(2, 590.0 - i))
        m.update("nfl", _g(2, 590.0 - i))
    assert m.games["1"].period == 2
