"""[one brain, step 2] One crunch table (redzone/crunch.py + default_config.yaml `crunch:`). Exact parity:
every crunch predicate answers as the copy it replaced did, on a full grid of period x clock x margin
for every league (the old bodies are kept here verbatim as the reference)."""
import itertools
from types import SimpleNamespace

import pytest

from redzone import crunch, standby
from redzone.lab import sportsgrade
from redzone.models import League, Phase, Stoppage, pro_period
from redzone.scoring import engine as E

PERIODS = range(0, 12)
CLOCKS = [None, 0, 1, 59, 60, 119, 120, 121, 179, 180, 181, 300, 359, 360, 361, 419, 420, 421, 479, 480, 481, 720, 1200]
MARGINS = [None] + list(range(-10, 11))


def _g(league, period, clock, diff, phase=Phase.LIVE, stoppage=None):
    return SimpleNamespace(league=league, period=period, clock_s=clock, score_diff=diff, phase=phase, stoppage=stoppage)


# ---- the bodies this step replaced (Nov 4, integration/next), verbatim ----------------------------
def old_crunch_time(g):
    if g.phase != Phase.LIVE:
        return False
    if g.stoppage == Stoppage.HALFTIME:
        return False
    q, d, clk = pro_period(g.league, g.period, g.clock_s), g.score_diff, g.clock_s
    if d is None:
        return False
    if g.league.base == League.NFL:
        if q >= 5:
            return True
        return q == 4 and clk is not None and clk <= 120 and abs(d) <= 8
    if g.league.base == League.NBA:
        return q >= 5 or (q == 4 and clk is not None and clk <= 180 and abs(d) <= 7)
    if g.league == League.NHL:
        return q >= 4 or (q == 3 and clk is not None and clk <= 180 and abs(d) <= 1)
    if g.league == League.MLB:
        return q >= 10 or (q >= 8 and abs(d) <= 2)
    return False


def old_crunch_window(g):
    if not old_crunch_time(g):
        return False
    if g.league != League.NHL or pro_period(g.league, g.period, g.clock_s) >= 4:
        return True
    return g.clock_s is not None and g.clock_s <= 120


_OLD_SOON = {League.NFL: (4, 420, 8), League.NBA: (4, 360, 8), League.NHL: (3, 480, 1)}


def old_crunch_soon(g):
    d = g.score_diff
    if g.phase != Phase.LIVE or d is None:
        return False
    p = pro_period(g.league, g.period, g.clock_s)
    if g.league == League.MLB:
        return p >= 8 and abs(d) <= 2
    last, clock, margin = _OLD_SOON[g.league.base]
    if abs(d) > margin:
        return False
    return p > last or (p == last and g.clock_s is not None and g.clock_s <= clock)


def old_is_crunch(league, per, c, d):
    if league == League.NFL:
        return per >= 5 or (per == 4 and c is not None and c <= 120 and d <= 8)
    if league == League.NBA:
        return per >= 5 or (per == 4 and c is not None and c <= 120 and d <= 5)
    if league == League.NHL:
        return per >= 4 or (per == 3 and c is not None and c <= 120 and d <= 1)
    if league == League.MLB:
        return per >= 8 and d <= 2
    return False


GRID = list(itertools.product(League, PERIODS, CLOCKS, MARGINS))


@pytest.mark.parametrize("fn,old", [(E.crunch_time, old_crunch_time), (E.crunch_window, old_crunch_window),
                                    (standby.crunch_soon, old_crunch_soon)])
def test_live_predicates_match_the_old_copies_everywhere(fn, old):
    bad = [(lg.value, p, c, d) for lg, p, c, d in GRID if fn(_g(lg, p, c, d)) != old(_g(lg, p, c, d))]
    assert not bad, bad[:10]


def test_not_live_and_halftime_are_never_crunch():
    for lg in League:
        assert not E.crunch_time(_g(lg, 5, 0, 0, phase=Phase.PRE))
        assert not E.crunch_time(_g(lg, 5, 0, 0, stoppage=Stoppage.HALFTIME))


def test_the_grading_predicate_matches_the_old_copy(monkeypatch):
    for lg, p, c, d in GRID:
        if d is None:
            continue
        play = {"period": {"number": p}, "homeScore": abs(d), "awayScore": 0}
        monkeypatch.setattr(sportsgrade, "_clock", lambda league, pl, c=c: c)
        assert sportsgrade.is_crunch(lg, play) == old_is_crunch(lg, p, c, abs(d)), (lg, p, c, d)


def test_two_minute_drill_is_one_definition():
    for q, clk, od in itertools.product(range(0, 6), CLOCKS, MARGINS):
        assert crunch.trailing_drill(q, clk, od) == (q >= 4 and clk is not None and clk <= 120 and od is not None
                                                     and -8 <= od <= 0)


def test_every_kind_has_the_pro_leagues():
    for kind in crunch.CRUNCH_KINDS:
        assert {"nfl", "nba", "nhl", "mlb"} <= set(crunch.TABLE[kind]), kind
