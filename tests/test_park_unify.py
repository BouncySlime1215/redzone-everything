"""PARK-UNIFY: the weak spots found in the merged two-stick show before its first real-TV test, on the fake sticks and
TV of tests/test_park_hands.py (the bench) under the real `Controller.park_*` steps.

  1. FOCUS GUARD (park.read_guard): the read gesture sends no key while the wrong app or screen has focus, and never
     commits on a frame whose focus was not read: the check every other overlay gesture uses
     (yttv_seek.front_and_playing, #356; LabelProbe.probe fails closed).
  2. RE-READ IN PLACE (park.reread_in_place): the show's re-read of a drifted stream on screen no longer sends that
     picture about 30 s back in front of the viewer.
  4. S-9: the config names Prime Video "primevideo", targets say "prime:"; one alias answers both.
Every behaviour change is behind its own flag, OFF; each test shows the flag-off behaviour too.

Replayed on fake sticks is NOT measured on the TV.

Why: reviews of PRs 423-426 found these before Thursday's first real-TV run (Prime football, Nov 12).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "tests").as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone import control as ctl  # noqa: E402
from redzone.tuners import yttv_seek  # noqa: E402
from redzone.tuners.adb import RunResult  # noqa: E402
from redzone.tuners.yttv_seek import OverlayRead  # noqa: E402
from test_park_controller import _placed  # noqa: E402  (stick a on screen, placed by park_start)
from test_park_hands import world  # noqa: E402,F401  (the fake sticks and TV of the park steps' own tests)

LAUNCHER = "com.amazon.tv.launcher/.Home"


def _flags(w, **on):
    w.c.cfg.update({"park": {"enabled": True, **on}})


def _with_overlay(w, ov=None):
    """The repo reader's behaviour on the fake: each label frame also records the overlay read of that frame."""
    reader = w.hands.label_reader

    async def read(stick):
        lab = await reader(stick)
        w.hands._overlay[stick.host] = ov if ov is not None else OverlayRead(overlay=stick.overlay, scrub_s=1.0)
        return lab
    w.hands.label_reader = read


def _with_right(stick):
    """The fake stick learns DPAD_RIGHT: with the overlay up it moves the knob one 15 s step forward (the inverse of
    LEFT behind the live edge; on the TV this is unmeasured, which the gesture's seek check covers)."""
    shell = stick._shell

    async def _shell(cmd, timeout):
        if cmd.startswith("input keyevent") and cmd.split()[-1] == "KEYCODE_DPAD_RIGHT":
            stick.keys.append("DPAD_RIGHT")
            if stick.overlay and stick.lefts > 0:
                stick.lefts -= 1
            return RunResult(True, "", 0.01)
        return await shell(cmd, timeout)
    stick._shell = _shell


# ---------------------------------------------------------------------------------------------- 1. the focus guard
async def test_flag_off_the_read_gesture_sends_keys_with_another_app_in_front(world):  # noqa: F811
    """Repro of the gap: with park.read_guard off the gesture never asks which app is in front."""
    w = world
    w.tuner.lags["espn"] = 20.0

    async def tune(stick, ref, target, may_cut=None, _t=w.tuner.tune):
        out = await _t(stick, ref, target, may_cut)
        stick.fg = LAUNCHER                                  # the stick's home screen came up over the stream
        return out
    w.tuner.tune = tune
    await w.c.park_tune("youtubetv:espn")
    assert "DPAD_CENTER" in w.b.keys                          # a CENTER pressed whatever the launcher had focused


async def test_the_read_gesture_sends_no_key_while_another_app_is_in_front(world):  # noqa: F811
    w = world
    _flags(w, read_guard=True)

    async def tune(stick, ref, target, may_cut=None, _t=w.tuner.tune):
        out = await _t(stick, ref, target, may_cut)
        stick.fg = LAUNCHER
        return out
    w.tuner.tune = tune
    out = await w.c.park_tune("youtubetv:espn")
    assert w.b.keys == []                                     # nothing at all reached the launcher
    assert not out.ok and out.why.startswith("the read gesture did not start (focus guard: YouTube TV is not in front")
    assert w.hands.counts["guarded"] == 1 and w.hands.place_of(w.b) is None


async def test_the_read_gesture_sends_no_key_while_the_player_does_not_play(world):  # noqa: F811
    """A dialog over YouTube TV (for one, 'still watching?') stops the player: the same check refuses the first key."""
    w = world
    w.b.load("espn")
    w.b._pause(0.0)
    assert await yttv_seek.front_and_playing(w.b, "yttv.pkg") == "player is paused, not playing"
    w.b._play(w.b.frozen)
    assert await yttv_seek.front_and_playing(w.b, "yttv.pkg") == ""
    w.b.fg = LAUNCHER
    assert (await yttv_seek.front_and_playing(w.b, "yttv.pkg")).startswith("YouTube TV is not in front")


async def test_no_committing_center_on_a_frame_whose_focus_was_never_read(world):  # noqa: F811
    """The label reader gave no overlay read (a capture or OCR that failed): with the guard on, BACK and resume, the place
    stays unread and nothing was sought; with it off (as merged) the CENTER commits blind."""
    w = world
    out = await w.c.park_tune("youtubetv:espn")              # flag off: the fake reader records no frame, CENTER commits
    assert out.ok and w.b.keys == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]
    _flags(w, read_guard=True)
    w.b.keys = []
    out = await w.c.park_tune("youtubetv:fs1")
    assert out.ok and out.numbers["read"] is False
    assert out.numbers["read_why"] == "focus guard: no frame said where the focus is"
    assert w.b.keys == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "BACK", "MEDIA_PLAY_PAUSE"]
    assert w.b.ps == "playing" and w.b.behind() < 45                # no 30 s step back


async def test_with_the_guard_on_a_read_frame_with_the_bar_focused_reads_as_before(world):  # noqa: F811
    w = world
    _flags(w, read_guard=True)
    _with_overlay(w)
    out = await w.c.park_tune("youtubetv:espn")
    assert out.ok and out.numbers["read"] is True and w.b.keys == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]
    assert w.hands.counts.get("guarded", 0) == 0


# ---------------------------------------------------------------------------------------------- 2. re-read in place
async def _drift_and_reread(w, rig):
    w.a.pos0 -= 5.0                                           # the stream on screen slipped 5 s (a pause that did not hold)
    before = w.a.behind()
    ok, _took = await rig.reread(0)
    return ok, w.a.behind() - before


async def test_flag_off_a_re_read_sends_the_picture_on_screen_about_30_s_back(world):  # noqa: F811
    """Repro: the hold watch's re-read of the stream ON SCREEN commits where its two LEFTs put the knob."""
    w, c, rig = await _placed(world)
    keys = len(w.a.keys)
    ok, moved = await _drift_and_reread(w, rig)
    assert ok and w.a.keys[keys:] == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]
    assert moved == pytest.approx(30.0 + 6.0, abs=2.0)       # 2 x 15 s, plus the time it sat paused for the read


async def test_a_re_read_in_place_reads_the_place_without_moving_the_picture(world):  # noqa: F811
    w, c, rig = await _placed(world)
    _flags(w, reread_in_place=True)
    _with_right(w.a)
    keys = len(w.a.keys)
    ok, moved = await _drift_and_reread(w, rig)
    assert ok and rig.read[0]
    assert w.a.keys[keys:] == ["DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_RIGHT", "DPAD_CENTER"]
    assert 0.0 < moved < 10.0                                 # only the seconds it sat paused for the read
    assert w.hands.last.way.endswith("in place (no seek)")
    assert (await rig.look(0))["lag"] == pytest.approx(rig.lag[0], abs=0.3)   # the new place is where the picture is


async def test_a_re_read_in_place_whose_seek_did_not_land_is_not_believed(world):  # noqa: F811
    """RIGHT behind the live edge is unmeasured on the TV: if it does nothing, the commit lands 30 s back and the seek
    check says so; the place is not believed (the show re-reads later) and no fresh tune reloads the picture."""
    w, c, rig = await _placed(world)
    _flags(w, reread_in_place=True)                           # no _with_right: RIGHT does nothing on this fake
    calls = len(w.tuner.calls)
    ok, moved = await _drift_and_reread(w, rig)
    assert not ok and not rig.read[0]
    assert w.hands.last.why == "the seek did not land where the LEFTs put it"
    assert len(w.tuner.calls) == calls                        # no fresh tune of the picture on screen


async def test_a_re_read_never_touches_the_parked_hidden_stick(world):  # noqa: F811
    """Only the stream on screen is re-read: asking for the hidden stream sends nothing (park_run.ControllerRig.reread)."""
    w, c, rig = await _placed(world)
    assert (await rig.tune(1, 1))[0]
    assert (await rig.pause(1, None))[0] and rig.paused[1]
    keys, frozen = list(w.b.keys), w.b.frozen
    assert await rig.reread(1) == (False, 0.0)
    assert w.b.keys == keys and w.b.ps == "paused" and w.b.frozen == frozen


# ---------------------------------------------------------------------------------------------- 4. S-9: primevideo vs prime:
def test_one_alias_answers_both_spellings_of_prime_video():
    assert ctl.app_listed("prime", ["youtubetv", "primevideo"]) and ctl.app_listed("youtubetv", ["youtubetv", "primevideo"])
    assert not ctl.app_listed("prime", ["youtubetv"]) and not ctl.app_listed("espn", ["youtubetv", "primevideo"])


async def test_s9_a_prime_hidden_tune_matches_the_buffer_list_only_with_its_flag(world):  # noqa: F811
    """Repro: with the shipped list [youtubetv, primevideo], a "prime:<gti>" tune got no behind-live buffer while the
    pause-hold check (which had its own alias) said Prime holds. Flag on: one alias answers both checks."""
    from redzone.tuners.tuners import TuneResult
    c = world.c
    c.cfg.update({"timing": {"behind_live_s": {"enabled": True}}})
    c.behind_of = lambda target: 60.0
    res = TuneResult(True, "prime:amzn1.dv.gti.x", 27.0, [], "")
    stick = world.b
    assert c._buffer_for(stick, "youtubetv:espn", res) == 60.0
    assert c._buffer_for(stick, "prime:amzn1.dv.gti.x", res) == 0.0               # flag off: as before (the S-9 gap)
    assert c.hidden is stick
    assert c._pause_hold_ok("prime:amzn1.dv.gti.x") is True                       # ...while this check said Prime holds
    c.cfg.update({"timing": {"behind_live_s": {"enabled": True, "prime_buffer": True}}})
    assert c._buffer_for(stick, "prime:amzn1.dv.gti.x", res) == 60.0
    assert c._buffer_for(stick, "youtubetv:espn", res) == 60.0 and c._buffer_for(stick, "espn:x", res) == 0.0
