"""legal_options() and its rules (WP-16): pure, stay is always there, a brain never gets a resume or a skip."""
from redzone.contracts import brain
from redzone.noggin import legal


def test_no_verb_a_brain_can_choose_is_a_resume_or_a_skip():
    for v in brain.OPTION_VERBS:
        assert legal.kind_of(v) not in legal.FORBIDDEN_KINDS
    assert legal.kind_of("rewind") == "replay"
    assert legal.kind_of("resume") is None and legal.kind_of("skip") is None


def test_stay_is_always_there():
    assert legal.legal_options(legal.Facts(now=100.0)) == (legal.STAY,)


def test_user_block_order():
    b = legal.user_block
    assert b(True, True, "g", 200.0, 100.0) == "safe_mode"
    assert b(False, True, "g", 200.0, 100.0) == "paused"
    assert b(False, False, "g", 200.0, 100.0) == "locked_game"
    assert b(False, False, None, 200.0, 100.0) == "manual_pause"
    assert b(False, False, "", 100.0, 100.0) is None


def test_arriving_holds_until_the_picture_is_up():
    assert legal.arriving(99.9, 100.0) and not legal.arriving(100.0, 100.0)
    assert legal.legal_options(legal.Facts(now=99.0, arrival_until=100.0)) == (legal.STAY,)


def test_tunable():
    class X:
        def __init__(self, target, stale=False):
            self.target, self.stale = target, stale
    assert legal.tunable("a", X("yt:a"), set())
    assert not legal.tunable("a", X(None), set())
    assert not legal.tunable("a", X("yt:a", stale=True), set())
    assert not legal.tunable("a", X("yt:a"), {"a"})


def test_feed_dark():
    assert legal.feed_dark(False, None, None, 100.0, 30.0)
    assert legal.feed_dark(True, {}, 99.0, 100.0, 30.0)
    assert legal.feed_dark(True, {1: 1}, None, 100.0, 30.0)
    assert legal.feed_dark(True, {1: 1}, 69.0, 100.0, 30.0)
    assert not legal.feed_dark(True, {1: 1}, 70.0, 100.0, 30.0)
    assert legal.no_dead_ball_data(False, None) and legal.no_dead_ball_data(True, {})
    assert not legal.no_dead_ball_data(True, {1: 1})


def test_dwell():
    assert legal.dwell_left(100.0, 30.0, "better", ["better"], 110.0) == 20.0
    assert legal.dwell_left(100.0, 30.0, "better", ["better"], 130.0) <= 0
    assert legal.dwell_left(100.0, 0.0, "better", ["better"], 110.0) == 0.0
    assert legal.dwell_left(100.0, 30.0, "crunch", ["better"], 110.0) == 0.0
    assert legal.dwell_left(100.0, 30.0, "better", None, 110.0) == 0.0
    f = legal.Facts(now=110.0, current="a", since=100.0, dwell_min_s=30.0, dwell_kinds=("better",))
    assert legal.legal_options(f) == (legal.STAY,)


def test_too_soon():
    assert legal.too_soon(90.0, 60.0, 100.0) and not legal.too_soon(40.0, 60.0, 100.0)
    assert not legal.too_soon(None, 60.0, 100.0) and not legal.too_soon(99.0, 0.0, 100.0)
    assert legal.legal_options(legal.Facts(now=100.0, last_switch_at=90.0, min_gap_s=60.0)) == (legal.STAY,)
