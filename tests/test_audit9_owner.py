"""AUDIT-9: _own reads a locked game the way _decide does: a lock on a game off the board holds nothing."""
from redzone.decider.decider import Decision, State
from tests.test_decider_machines import cands, setup


def test_a_lock_on_a_game_off_the_board_does_not_hand_the_tv_to_the_user(cfg, clock):
    dec = setup(cfg, clock)
    dec.user.locked_game = "gone"
    opt = Decision(clock.t, "switch", "b", "better", State.WATCHING, kind="better")
    assert dec._own(opt, cands(a=60, b=70), clock.t) == ("jump", "rotation")


def test_a_lock_on_a_game_on_the_board_still_holds(cfg, clock):
    dec = setup(cfg, clock)
    dec.user.locked_game = "a"
    opt = Decision(clock.t, "switch", "b", "better", State.WATCHING, kind="better")
    assert dec._own(opt, cands(a=60, b=70), clock.t) == ("user", "locked_game")
