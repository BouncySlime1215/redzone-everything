"""AUDIT-6: a stoppage that arrives without its own play did not start at an old play's time stamp.

NHL's stoppage comes from api-web and is laid onto ESPN's copy of the game (merge._owned_stoppage), and /score's
inIntermission sets it with no play at all. The last play in that copy can be ~50 s old, so the break was stamped 50 s
early and "whistle on screen" came up while the screen still showed live hockey."""
from redzone.decider.screen import Delay, Timeline, leave_gate
from redzone.models import GameState, League, Phase, Play, Stoppage, Team

T0 = 1_800_000_000.0


class Clock:
    t = T0

    def __call__(self):
        return self.t


def _game(league, stoppage, plays):
    g = GameState(league=league, game_id="g", home=Team("1", "AAA", "A"), away=Team("2", "BBB", "B"))
    g.phase, g.stoppage, g.period, g.clock_s = Phase.LIVE, stoppage, 2, 600.0
    g.recent_plays, g.last_play = plays, plays[-1]
    return g


def _timeline(league):
    c = Clock()
    tl = Timeline(league, clock=c)
    shot = Play("a", "shot", "shot", wallclock=T0 - 1, seen_at=T0, type_text="Shot")
    g = _game(league, Stoppage.NONE, [shot])
    g.updated_at = T0
    tl.observe(g)
    return tl, c, shot


def test_a_stoppage_with_no_new_play_starts_after_the_last_poll_not_at_the_old_play():
    tl, c, shot = _timeline(League.NHL)
    c.t = T0 + 50                     # a whole stretch of live hockey since the shot; the whistle is only now reported
    g = _game(League.NHL, Stoppage.WHISTLE, [shot])
    g.updated_at = c.t
    tl.observe(g)
    start = tl.last("stop_start")
    assert start.lo >= shot.wallclock  # never before the play that came before it
    assert start.seen >= c.t - 2.0


def test_that_stoppage_is_not_on_screen_while_the_screen_is_still_before_it():
    tl, c, shot = _timeline(League.NHL)
    c.t = T0 + 50
    g = _game(League.NHL, Stoppage.WHISTLE, [shot])
    g.updated_at = c.t
    tl.observe(g)
    c.t = T0 + 55                     # the screen, 40 s behind, shows real time T0 + 15: the whistle hasn't happened
    assert not leave_gate(tl, Delay(40.0, 4.0), c.t, 8.0).safe


def test_a_stoppage_from_its_own_new_play_keeps_that_plays_time():
    tl, c, shot = _timeline(League.NHL)
    c.t = T0 + 50
    whistle = Play("b", "stoppage", "stoppage", wallclock=T0 + 45, seen_at=c.t, type_text="Stoppage")
    g = _game(League.NHL, Stoppage.WHISTLE, [shot, whistle])
    g.updated_at = c.t
    tl.observe(g)
    assert tl.last("stop_start").seen == T0 + 45


def test_football_keeps_timing_a_stoppage_by_its_last_play():
    tl, c, shot = _timeline(League.NFL)
    c.t = T0 + 50
    g = _game(League.NFL, Stoppage.TIMEOUT, [shot])
    g.updated_at = c.t
    tl.observe(g)
    assert tl.last("stop_start").seen == T0 - 1   # the huddle after a whistle is dead either way (_stoppage_reached)
