"""Why a forced jump was not made (A-02b): the step's jump test copied read-only (gate_refusal) on hand-made sticks and
games, the reason picked from a rollout's forcing log, and each reason's class (constraint or artefact).
"""
from __future__ import annotations

from types import SimpleNamespace

from redzone.lab.gym2 import qdata as Q
from redzone.noggin.park_core import stretches

N = 100
GUARD, FLIP, LEAD, T = 3, 2, 0, 50


def _game(dead_spans, hl_spans=()):
    """A game on a 100 s grid: dead (legal) in each span, live elsewhere; high leverage in hl_spans."""
    mask = bytearray(N)
    for a, b in dead_spans:
        mask[a:b] = b"\x01" * (b - a)
    since, left, _, _ = stretches(mask)
    hl = [0] * N
    for a, b in hl_spans:
        for k in range(a, b):
            hl[k] = 1
    hlc = [0] * (N + 1)
    for k in range(N):
        hlc[k + 1] = hlc[k] + hl[k]
    return SimpleNamespace(since=since, left=left, hlc=hlc, a1=N - 1)


SETTING = SimpleNamespace(mode="park", min_stay=0, min_live_s=3, leave_rule="dead", land_wait_max=20)


def _state(i: int, j: int, **kw):
    """The screen (game 0) at position i and the hidden stick (game 1) paused at j, both ready, at second T."""
    S = SimpleNamespace(sg=0, hg=1, sb=float(T - i), se=0.0, hb=float(T - j), he=0.0, h_ready=0, black=0, s_land=0,
                        h_paused=True, s_live=10)
    for k, v in kw.items():
        setattr(S, k, v)
    return S


def _why(A, S, spot=None, know=30, s=SETTING):
    hb_i = int(T - S.hb + S.he)
    return Q.gate_refusal(T, S, A, s, guard=GUARD, reach=175, flip=FLIP, lead=LEAD, know=know, n=N,
                          spot=hb_i if spot is None else spot)


SCREEN = _game([(40, 70)])                       # i = 50: 11 s into a 30 s dead ball, 20 s left
HIDDEN = _game([(30, 90)])                       # a dead ball whose play starts at 90


def test_a_jump_the_step_lets_through_has_no_reason():
    assert _why([SCREEN, HIDDEN], _state(50, 75)) is None                 # landing 15 s before its play


def test_a_landing_too_long_before_its_play_is_refused():
    assert _why([SCREEN, HIDDEN], _state(50, 40)) == "landing_long_wait"  # 50 s before its play: over land_wait_max 20
    roomy = SimpleNamespace(**{**vars(SETTING), "land_wait_max": 999})
    assert _why([SCREEN, HIDDEN], _state(50, 40), s=roomy) is None


def test_each_refusal_in_the_steps_order():
    A = [SCREEN, HIDDEN]
    assert _why(A, _state(50, 75, h_ready=T + 1)) == "stick_not_ready"
    assert _why(A, _state(50, 75, hg=-1)) == "stick_not_ready"
    assert _why(A, _state(50, 75, black=T + 1)) == "flip_in_progress"
    stay = SimpleNamespace(**{**vars(SETTING), "min_stay": 30})
    assert _why(A, _state(50, 75, s_land=T - 5), s=stay) == "min_stay"
    assert _why(A, _state(50, 75, hb=float(T - 75) + 500)) == "past_reach"
    assert _why(A, _state(50, 75), know=-30) == "feed_not_reported"
    assert _why([_game([(40, 60)]), HIDDEN], _state(50, 75, s_live=0)) == "min_live"     # nothing shown, 10 s left
    assert _why([_game([(40, 70)], [(49, 52)]), HIDDEN], _state(50, 75)) == "screen_high_leverage"
    assert _why([_game([(20, 30)]), HIDDEN], _state(50, 75)) == "screen_mid_play"
    assert _why([SCREEN, HIDDEN], _state(50, 95), know=60) == "landing_not_dead"         # the hidden game is in play


def test_an_unpaused_stick_lands_a_flip_later():
    # spot -1: the stick is told to play on, so it is not paused and lands at its position + flip
    S = _state(50, 86)
    assert _why([SCREEN, HIDDEN], S, know=60) is None
    assert _why([SCREEN, HIDDEN], S, spot=-1, know=60) == "landing_not_dead"         # 86 + 2 = 88: within the guard of 90


def test_the_reason_is_picked_from_the_forcing_log():
    t = 1000
    assert Q.not_made_reason([(t, "listed"), (t, "core:landing_long_wait"), (t + 1, "wait_leave")]) == \
        "core_landing_long_wait"
    assert Q.not_made_reason([(t, "bank_swap"), (t, "no_plan"), (t + 2, "bank_swap"), (t + 2, "no_plan")]) == "bank_swap"
    assert Q.not_made_reason([(t, "listed"), (t, "wait_leave"), (t + 2, "row_gone"), (t + 2, "plan_other")]) == "row_gone"
    assert Q.not_made_reason([(t, "listed"), (t + 2, "tau_drift"), (t + 4, "listed"), (t + 4, "wait_leave")]) == \
        "expired_wait_leave"
    assert Q.not_made_reason([(t, "listed"), (t, "stick_tune"), (t + 1, "stick_not_ready")]) == "expired_stick_not_ready"
    assert Q.not_made_reason([]) == "no_log"


def test_each_reason_has_a_class():
    assert Q.not_made_class(None) is None
    assert Q.not_made_class("core_landing_long_wait") == "constraint"
    assert Q.not_made_class("expired_wait_leave") == "constraint"
    for why in ("bank_swap", "tau_drift", "landing_moved", "row_gone", "no_log"):
        assert Q.not_made_class(why) == "artefact"
