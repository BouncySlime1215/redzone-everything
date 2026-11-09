"""AUDIT-OLD, feeds: defects in the league enrichers and the merger that run on game nights.

Why: each test pins one bug found by reading the live feed path (2026-11-09); it failed before its fix.
"""
from redzone.models import GameState, League, Phase, Play, Stoppage, Team
from redzone.sources import leagues
from redzone.sources.merge import BENCH_S, Merger


def _nba(period=2, clock=0.0, stoppage=Stoppage.UNKNOWN):
    g = GameState(league=League.NBA, game_id="1", home=Team("WSH", score=50), away=Team("BOS", score=55))
    g.phase, g.period, g.clock_s, g.stoppage = Phase.LIVE, period, clock, stoppage
    return g


def _board(period, clock, text):
    return {"gameStatus": 2, "gameStatusText": text, "period": period, "gameClock": clock,
            "homeTeam": {"score": 50}, "awayTeam": {"score": 55}}


END_Q2 = {"actionNumber": 1, "actionType": "period", "subType": "end", "period": 2, "clock": "PT00M00.00S"}
START_Q3 = {"actionNumber": 2, "actionType": "period", "subType": "start", "period": 3, "clock": "PT12M00.00S"}
SHOT = {"actionNumber": 3, "actionType": "2pt", "subType": "Jump Shot", "period": 3, "clock": "PT11M30.00S",
        "shotResult": "Missed"}


def _pbp(*acts):
    return {"game": {"actions": list(acts)}}


def test_nba_halftime_ends_when_the_third_quarter_starts():
    """Repro: enrich_nba copied the merged game, and its "don't overwrite halftime" guard could not tell the
    halftime it had just read from one inherited from that copy, so every later poll kept HALFTIME to the end."""
    m = Merger(stoppage_owner={League.NBA: "nba"})
    m.update("espn_scoreboard", _nba())
    seen = {"_primed": 0.0}
    g = m.update("nba", leagues.enrich_nba(m.games["1"], _board(2, "PT00M00.00S", "Halftime"), _pbp(END_Q2), seen))
    assert g.stoppage == Stoppage.HALFTIME
    g = m.update("nba", leagues.enrich_nba(m.games["1"], _board(3, "PT11M30.00S", "Q3 11:30"),
                                           _pbp(END_Q2, START_Q3, SHOT), seen))
    assert g.period == 3 and g.stoppage == Stoppage.NONE


def test_nba_halftime_stays_while_the_board_says_halftime():
    seen = {"_primed": 0.0}
    out = leagues.enrich_nba(_nba(stoppage=Stoppage.HALFTIME), _board(2, "PT00M00.00S", "Halftime"), _pbp(END_Q2), seen)
    assert out.stoppage == Stoppage.HALFTIME


def test_nba_inherited_halftime_is_dropped_without_play_by_play_too():
    """No play-by-play this poll (an S3 miss, or the primary feed off): the board alone says the half is over."""
    out = leagues.enrich_nba(_nba(period=2, stoppage=Stoppage.HALFTIME), _board(3, "PT11M30.00S", "Q3 11:30"))
    assert out.stoppage == Stoppage.UNKNOWN


def _nhl(clock, period=2, pp=None, strength=None, plays=True):
    g = GameState(league=League.NHL, game_id="9", home=Team("WSH", score=1), away=Team("BOS", score=1))
    g.phase, g.period, g.clock_s = Phase.LIVE, period, clock
    g.situation.power_play, g.situation.strength = pp, strength
    if plays:
        g.last_play = Play("p", "shot", "shot", False)
        g.recent_plays = [g.last_play]
    return g


class _Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def test_a_bench_ends_even_while_the_source_still_reads_behind_its_high_water_mark():
    """Repro: one glitched copy (period 5) left the source behind its own high-water mark, so every later poll was
    another "backward jump", and each one pushed the bench end out BENCH_S again: the bench never ended."""
    clock = _Clock()
    m = Merger(clock=clock)
    m.update("espn_summary", _nhl(600.0, period=2))
    m.update("espn_summary", _nhl(500.0, period=5))             # the glitch
    while clock.t < 1000.0 + BENCH_S + 30:
        clock.t += 3.0
        m.update("espn_summary", _nhl(400.0, period=2))
    assert not m.health["espn_summary"].benched
    assert not m._benched(("espn_summary", "9"))
