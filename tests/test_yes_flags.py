"""[YES-1] The flags that take false refusals out of the leave gate, the legal list and the arbiter. Every one ships OFF.

Why: Nick, Nov 10: "the code keeps saying no... a no is rare, like 95% yes", with the guarantee that touchdowns and big plays must air. Five
inspections measured which refusals were logic errors (research ledger L6); each fix sits behind its own flag, and flag off the night decides as before.
"""
import pytest
import yaml

from redzone import measured_timing as mt
from redzone.decider import gate
from redzone.decider.decider import PSEUDO, Decision, State
from redzone.decider.screen import leave_window
from redzone.models import Phase
from tests.test_exits_catchup import cand, host
from tests.test_timing_truth import _night_decisions

from pathlib import Path

CONFIG = yaml.safe_load((Path(mt.__file__).parent / "default_config.yaml").read_text())
ALL = ["nfl", "cfb", "nba", "cbb", "nhl", "mlb"]
# The fixture night with the arbiter choosing and a held pick allowed to wait: the paths the flags live on.
CTX = {"arbiter.enabled": True, "legal.leave_at_flip": True, "legal.leave_low_leverage": ALL}
OFF = {"gate.leave_unaired_row_only_ended": False}        # every new flag at its shipped default


def night(ctx=None, **sets):
    return _night_decisions(**{**(CTX if ctx is None else ctx), **sets})


def flag(key):
    node = CONFIG
    for part in key.split("."):
        node = node[part]
    return node


def test_every_flag_ships_at_its_old_behaviour():
    assert {k: flag(k) for k in OFF} == OFF


@pytest.mark.parametrize("ctx", [{}, CTX], ids=["shipped", "arbiter"])
def test_the_defaults_spelled_out_decide_the_night_as_no_flag_at_all(ctx):
    after = night(ctx)
    assert len(after[0]) > 500 and after[1]
    assert night(ctx, **OFF) == after


def flip(d, cands, *, held=False, now=200.0):
    dec = Decision(now, "switch", "a", "", State.WATCHING, kind="better", leave_held=held)
    return gate.Flip(gate.DeciderHost(d, cands, now), dec, "on", "yt:a", cands, None, now)


def between_plays(**cfg):
    """test_exits_catchup's host, 6 s later with a fresh poll: the play (logged 165, 35 s delay) is off the screen at 206, but the
    'last row still airing' timer (165 + 35 + 2 sigma + 5) runs to 207."""
    d = host((), **cfg)
    d.timelines["on"].fresh_at = 206.0
    d.replay_until = d.replay_exit_after = 0.0
    return d


CANDS = {"on": cand(), "a": cand()}


# ----- step 2: gate.leave_unaired_row_only_ended --------------------------------------------------------------------------------------

ROW = "gate.leave_unaired_row_only_ended"


def _old_leave_open(dec, gid, target, phase, now, uncalibrated_sigma, required_s=0.0):
    """gate.leave_open as it was before YES-1, word for word."""
    if not gid or gid in PSEUDO:
        return True
    if dec.replaying or dec.after_replay:
        return now >= max(dec.replay_until, dec.replay_exit_after)
    if gid not in dec.timelines:
        return False
    if dec._ending_still_airing(gid, now) > 0:
        return False
    if phase == Phase.POST:
        return True
    return leave_window(dec.timelines[gid], dec.delays.get(target or "unknown"), now, uncalibrated_sigma, required_s).safe


def test_flag_off_a_live_games_unaired_row_refuses_before_the_window_is_asked():
    d = between_plays()
    assert d._ending_still_airing("on", 206.0) > 0
    assert leave_window(d.timelines["on"], d.delays.get("yt:on"), 206.0, 8.0, 8.0).safe          # the window itself is open
    assert gate.leave_refusal(d, "on", "yt:on", Phase.LIVE, 206.0, 8.0, 8.0) == "unaired_row"
    assert gate._leave(flip(d, CANDS, now=206.0)) is True


def test_flag_on_a_live_game_goes_straight_to_the_leave_window():
    d = between_plays(**{ROW: True})
    assert gate.leave_refusal(d, "on", "yt:on", Phase.LIVE, 206.0, 8.0, 8.0) == ""
    assert gate._leave(flip(d, CANDS, now=206.0)) is False
    assert gate.leave_refusal(d, "on", "yt:on", Phase.LIVE, 200.0, 8.0, 8.0) == "window"         # mid-play: the window still refuses
    assert gate._leave(flip(d, CANDS, now=200.0)) is True


@pytest.mark.parametrize("phase", [Phase.POST, None, Phase.PRE, Phase.UNKNOWN])
def test_flag_on_a_game_that_ended_or_is_missing_still_waits_for_its_last_row(phase):
    d = between_plays(**{ROW: True})
    assert gate.leave_refusal(d, "on", "yt:on", phase, 206.0, 8.0, 8.0) == "ended_airing"
    assert gate.leave_refusal(d, "on", "yt:on", phase, 208.0, 8.0, 8.0) in ("", "window")     # aired: POST is free, anything else asks the window


def test_flag_off_the_night_decides_as_the_old_leave_rule(monkeypatch):
    after = night()
    monkeypatch.setattr(gate, "leave_refusal", lambda *a, **k: "" if _old_leave_open(*a, **k) else "refused")
    assert night() == after
