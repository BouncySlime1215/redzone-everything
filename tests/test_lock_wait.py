"""[lock wait] A held/locked game we can't reach must not wait forever (Nov 6: a hold sat
96 s and 261 s on "switch: you locked this game" after ACC Network's video never started and the
game lost its channel; ~600 switch attempts, each a 0 s 'no channel' failure, no reason shown)."""
import time

import pytest

from redzone.decider.decider import Candidate, Decider, Decision, State
from redzone.decider.screen import DelayBook
from redzone.models import League
from tests.test_app import engine  # noqa: F401  (fixture)
from tests.test_decider import sc, with_fresh_timing

TICK_S = 0.5


@pytest.fixture
def dec(cfg, clock):
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)
    with_fresh_timing(d, clock)
    d.current = "cur"
    d.timeline("cur", League.NFL)
    d.user.locked_game = "held"
    return d


def on(cfg):
    cfg.update({"decider": {"lock_wait": {"enabled": True}}})


def slate(held_target):
    return {"cur": Candidate(sc("cur", 50), "youtubetv:espnu"),
            "held": Candidate(sc("held", 60), held_target)}


def nov6_episode(dec, clock, seconds=300.0):
    """Replay the shape of the Nov 6 episode: the held game has no channel for `seconds`.
    Returns (switch attempts, seconds until the lock was given up or None)."""
    attempts, gave_up, t0 = 0, None, clock.t
    while clock.t - t0 < seconds:
        d = dec.decide(slate(None), commit=False)
        if d.action == "switch":
            attempts += 1
            dec.reject(d)   # control: "no channel for this game" (not the target's fault)
        if dec.lock_expired and gave_up is None:
            gave_up = clock.t - t0
            break
        clock.t += TICK_S
    return attempts, gave_up


def test_flag_off_keeps_the_old_behavior(dec, clock):
    attempts, gave_up = nov6_episode(dec, clock)
    assert attempts == 600 and gave_up is None   # the bug, unchanged with the flag off
    dec.lock_failed(Decision(clock.t, "switch", "held", "x", State.PAUSED, kind="user"), "video never started")
    assert dec.lock_since is None and dec.lock_retry_at == 0.0


def test_no_channel_shows_why_and_gives_up(dec, clock, cfg):
    on(cfg)
    d = dec.decide(slate(None), commit=False)
    assert d.action == "stay" and d.target == "cur" and d.want == "held"
    assert "no working channel" in d.reason and "Resume auto" in d.reason
    clock.t += TICK_S
    attempts, gave_up = nov6_episode(dec, clock)
    assert attempts == 0
    assert gave_up is not None and gave_up + TICK_S >= cfg.get("decider.lock_wait.max_s") - TICK_S
    assert gave_up <= cfg.get("decider.lock_wait.max_s")
    assert "gave up" in dec.log[-1].reason or "gave up" in dec.decide(slate(None), commit=False).reason


def test_failed_tune_backs_off_then_retries(dec, clock, cfg):
    on(cfg)
    d = dec.decide(slate("youtubetv:accn"), commit=False)
    assert d.action == "switch" and d.target == "held" and d.kind == "user"
    dec.reject(d)
    dec.lock_failed(d, "video never started")
    clock.t += 1
    d = dec.decide(slate("youtubetv:accn"), commit=False)
    assert d.action == "stay" and "video never started" in d.reason and "trying again in 19s" in d.reason
    clock.t += cfg.get("decider.lock_wait.retry_s")
    d = dec.decide(slate("youtubetv:accn"), commit=False)
    assert d.action == "switch" and d.target == "held"


def test_repeated_failures_give_up_by_max(dec, clock, cfg):
    on(cfg)
    t0 = clock.t
    while not dec.lock_expired:
        d = dec.decide(slate("youtubetv:accn"), commit=False)
        if d.action == "switch":
            clock.t += 34   # Nov 6: each "video never started" took 34 s
            dec.reject(d)
            dec.lock_failed(d, "video never started")
        clock.t += TICK_S
        assert clock.t - t0 < 300
    # the clock starts at the first failure (one 34 s tune in), and a tune in flight finishes
    assert clock.t - t0 <= 34 + cfg.get("decider.lock_wait.max_s") + 34 + 2 * TICK_S


def test_arriving_clears_the_wait(dec, clock, cfg):
    on(cfg)
    dec.decide(slate(None), commit=False)
    assert dec.lock_since is not None
    dec.current = "held"
    d = dec.decide(slate("youtubetv:accn"), commit=False)
    assert d.reason == "locked here by you" and dec.lock_since is None


def test_a_new_lock_starts_a_fresh_wait(dec, clock, cfg):
    on(cfg)
    dec.decide(slate(None), commit=False)
    clock.t += 60
    dec.user.locked_game = "other"
    s = {**slate(None), "other": Candidate(sc("other", 40), None)}
    d = dec.decide(s, commit=False)
    assert "(0s)" in d.reason and not dec.lock_expired


# ----- engine: the give-up drops the hold and says why -------------------------------------

def test_engine_releases_hold_and_toasts(engine):
    from tests.test_phone_control import _live
    a = _live(engine)[0]
    engine.cfg.update({"decider": {"lock_wait": {"enabled": True}}})
    engine.hold = {"game_id": a, "force": False, "since": time.time(), "auto_resume": True,
                   "resume_at": None, "why": ""}
    engine.decider.user.locked_game = a
    toasts = []
    engine.toast = lambda text, level="info": toasts.append((text, level))
    engine.decider.lock_why = "video never started"
    engine.decider.lock_expired = a
    engine._lock_expired()
    assert engine.hold is None and engine.decider.user.locked_game is None
    assert toasts and "video never started" in toasts[0][0] and "autopilot" in toasts[0][0]
    assert engine.decider.lock_expired is None


def test_engine_ignores_a_stale_give_up(engine):
    from tests.test_phone_control import _live
    a, b = _live(engine)[:2]
    engine.decider.user.locked_game = b   # you re-locked elsewhere meanwhile
    engine.decider.lock_expired = a
    engine._lock_expired()
    assert engine.decider.user.locked_game == b
