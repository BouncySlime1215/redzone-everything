"""legal_options() and its rules (WP-16): pure, stay is always there, a brain never gets a resume or a skip."""
from redzone.contracts import brain
from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook
from redzone.models import League
from redzone.noggin import legal
from redzone.scoring.engine import Scored


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


def test_may_leave():
    assert legal.may_leave(True, "") and legal.may_leave(True, None)
    assert not legal.may_leave(False, "") and not legal.may_leave(True, "red zone drive")
    assert legal.legal_options(legal.Facts(now=1.0, current="a", gate_ok=False)) == (legal.STAY,)
    assert legal.legal_options(legal.Facts(now=1.0, current="a", moment_lock="crunch time")) == (legal.STAY,)


def test_must_see():
    assert legal.must_see(92.0, 72.0, 92.0, 20.0)
    assert not legal.must_see(91.9, 50.0, 92.0, 20.0)
    assert not legal.must_see(92.0, 72.1, 92.0, 20.0)


def _row(gid="b", **k):
    return legal.GameRow(game_id=gid, **k)


def test_options_list_only_what_is_legal():
    f = legal.Facts(now=1.0, current="a", games=(
        _row("a"), _row("b", l1_class=0), _row("muted", tunable=False), _row("dark", dark=True),
        _row("noland", landable=False), _row("pk", app="Peacock"), _row("reel", reel=True)))
    ids = [o.id for o in legal.legal_options(f)]
    assert ids == ["stay", "cut:b", "reel:reel"]
    assert legal.legal_options(f)[1].l1_class == 0


def test_focus_narrows_options_to_the_focus_game_or_a_must_see():
    rows = (_row("a"), _row("b"), _row("c", must_see=True), _row("f"))
    f = legal.Facts(now=1.0, current="a", focus_game="f", games=rows)
    assert [o.id for o in legal.legal_options(f)] == ["stay", "cut:f"]
    f = legal.Facts(now=1.0, current="a", focus_game="f", focus_must_see=True, games=rows)
    assert [o.id for o in legal.legal_options(f)] == ["stay", "cut:c", "cut:f"]


def test_a_block_leaves_only_stay():
    rows = (_row("b"),)
    for kw in ({"paused": True}, {"safe_mode": True}, {"locked_game": "a"}, {"manual_until": 9.0},
               {"arrival_until": 9.0}, {"gate_ok": False}, {"moment_lock": "overtime"}):
        f = legal.Facts(now=1.0, current="a", games=rows, **kw)
        assert legal.legal_options(f) == (legal.STAY,), kw
    assert [o.id for o in legal.legal_options(legal.Facts(now=1.0, current="a", games=rows))] == ["stay", "cut:b"]


def test_no_option_ever_carries_a_gate_exempt_kind():
    f = legal.Facts(now=1.0, games=(_row("b"), _row("reel", reel=True)))
    for o in legal.legal_options(f):
        assert o.verb in brain.OPTION_VERBS and legal.kind_of(o.verb) not in legal.FORBIDDEN_KINDS


# ---- the decider's own facts ---------------------------------------------------------------------------------------


def _dec(cfg, clock):
    delays = DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock)
    return Decider(cfg, delays, clock=clock)


def _c(gid, score=50.0, target="youtubetv:x", **k):
    return Candidate(Scored(gid, League.NFL, score, score, score, 1.0, 0.0, "r", [], False, None, None, 1.0), target, **k)


def test_decider_options_fail_closed_with_no_proof_of_a_dead_ball(cfg, clock):
    d = _dec(cfg, clock)
    d.current = "a"
    opts = d.legal_options({"a": _c("a"), "b": _c("b")})
    assert opts == (legal.STAY,)     # no timeline for a, so the leave gate proves nothing


def test_decider_pause_blocks_every_cut(cfg, clock):
    d = _dec(cfg, clock)
    d.user.paused = True
    assert d.legal_options({"a": _c("a")}) == (legal.STAY,)


def test_asking_for_options_never_changes_the_decision(cfg, clock):
    """legal_options reads only: two deciders, one asked every tick, make the same calls."""
    cands = lambda: {"a": _c("a", 60.0, "youtubetv:a"), "b": _c("b", 80.0, "youtubetv:b")}
    d1, d2 = _dec(cfg, clock), _dec(cfg, clock)
    for d in (d1, d2):
        d.timeline("a", League.NFL), d.timeline("b", League.NFL)
    log1, log2 = [], []
    for _ in range(40):
        clock.t += 1
        d2.legal_options(cands())
        a, b = d1.decide(cands()), d2.decide(cands())
        log1.append((a.action, a.target, a.reason, a.kind))
        log2.append((b.action, b.target, b.reason, b.kind))
    assert log1 == log2 and any(x[0] != "stay" or x[2] for x in log1)
