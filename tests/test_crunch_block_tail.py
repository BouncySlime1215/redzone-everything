"""[crunch block tail] North Star: crunch_live / crunch_on_replay (NHL). Owner: lane 2 (noggin logic).
A game the feed ended in crunch time (an OT winner) keeps the catch-up block until it has aired."""
from redzone.models import League
from tests.test_decider import cands, dec, sc  # noqa: F401


def _ot(gid):
    s = sc(gid, 90, league=League.NHL)
    s.crunch = s.crunch_now = True
    return s


def test_block_holds_until_the_ended_crunch_game_airs(dec, clock):
    dec.cfg.update({"replay": {"crunch_block": True, "crunch_block_tail": True}}, record=False)
    dec.decide(cands(a=50, ot=_ot("ot")))
    assert dec._crunch_on(cands(a=50, ot=_ot("ot")))
    clock.t += 10                                # the feed has the OT winner; the game drops out
    assert dec._crunch_on(cands(a=50))           # still airing for a stream delay
    clock.t += 200                               # well past mu (40) + 2 sigma + a poll
    assert not dec._crunch_on(cands(a=50))


def test_off_by_default(dec, clock):
    dec.cfg.update({"replay": {"crunch_block": True}}, record=False)
    dec.decide(cands(a=50, ot=_ot("ot")))
    clock.t += 10
    assert not dec._crunch_on(cands(a=50))
