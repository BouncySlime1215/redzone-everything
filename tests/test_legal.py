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
