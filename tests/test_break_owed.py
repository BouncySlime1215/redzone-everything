"""[EXP-020] Rulebook "break owed" countdown (scoring/breaks.owed_in) and the decider's use of it
(decider.break_owed, off). North Star: landings in dead time, dead air. Owner: lane 2 (noggin logic)."""
import dataclasses

from redzone.models import GameState, League, Phase, Situation, Team
from redzone.scoring.breaks import owed_in
from tests.test_decider import cands, dec, sc  # noqa: F401


def g(league, period, clock, pp=None):
    return GameState(league=league, game_id="x", home=Team("A"), away=Team("B"), phase=Phase.LIVE,
                     period=period, clock_s=clock, situation=Situation(power_play=pp))


def test_nba_counts_down_to_the_mandatory_timeouts():
    assert owed_in(g(League.NBA, 2, 500.0)) == 80.0          # 1:20 to the under-7:00 mark
    assert owed_in(g(League.NBA, 2, 400.0)) == 0.0           # past it, none called: owed now
    assert owed_in(g(League.NBA, 2, 400.0), (410.0,)) == 220.0   # #1 taken: next is under 3:00
    assert owed_in(g(League.NBA, 2, 100.0), (410.0, 150.0)) is None   # both taken
    assert owed_in(g(League.NBA, 5, 200.0)) is None          # overtime owes none


def test_nhl_counts_down_to_tv_timeouts():
    assert owed_in(g(League.NHL, 1, 900.0)) == 60.0          # 5:00 elapsed: 6:00 mark in 60 s
    assert owed_in(g(League.NHL, 1, 800.0)) == 0.0           # mark passed, unused: next stoppage
    assert owed_in(g(League.NHL, 1, 800.0), (830.0,)) == 200.0   # used: the 10:00 mark next


def test_nhl_rulebook_exceptions():
    assert owed_in(g(League.NHL, 1, 800.0, pp="A")) is None  # never on a power play
    assert owed_in(g(League.NHL, 2, 25.0)) is None           # last 30 s of P2
    assert owed_in(g(League.NHL, 3, 110.0)) is None          # last 2:00 of P3
    assert owed_in(g(League.NHL, 4, 200.0)) is None          # overtime


def test_cbb_media_marks_and_other_leagues():
    assert owed_in(g(League.CBB, 1, 1000.0)) == 40.0         # under-16 mark in 40 s
    assert owed_in(g(League.NFL, 2, 500.0)) is None


def _owed(gid, score, owed_s, league=League.NBA, **kw):
    s = sc(gid, score, league=league)
    return dataclasses.replace(s, factors={"owed_s": owed_s}, **kw)


def test_decider_flag_off_by_default(dec):
    assert not dec._owed_on_arrival(cands(b=_owed("b", 80, 5.0))["b"])


def test_decider_avoids_landing_into_an_owed_break(dec):
    dec.cfg.update({"decider": {"break_owed": {"enabled": True}}}, record=False)
    assert dec._owed_on_arrival(cands(b=_owed("b", 80, 10.0))["b"])            # NBA window 24
    assert not dec._owed_on_arrival(cands(b=_owed("b", 80, 60.0))["b"])
    assert dec._owed_on_arrival(cands(b=_owed("b", 80, 25.0, League.NHL))["b"])  # NHL window 30
    assert not dec._owed_on_arrival(cands(b=_owed("b", 80, 0.0, crunch=True))["b"])   # crunch: never
    assert not dec._owed_on_arrival(cands(b=sc("b", 80))["b"])                 # no countdown: no change
