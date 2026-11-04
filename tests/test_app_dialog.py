"""playbook=APP-04: an "update available" window over YouTube TV's player. It pauses the player and
ignores MEDIA_PLAY; one BACK dismisses it (or, forced, it keeps coming back on the hidden stick)."""
import asyncio
import time

import pytest

from redzone import control as ctl
from redzone.tuners.adb import RunResult
from tests.test_control import FakeStick, FakeTuner, two  # noqa: F401  (fixture)


class DialogStick(FakeStick):
    """A YouTube TV player behind an update window: PLAY does nothing, BACK clears it."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.playback_state, self.dialog = "paused", True

    async def key(self, *codes):
        self.log.extend(codes)
        if "KEYCODE_BACK" in codes and self.dialog:
            self.dialog, self.playback_state = False, "playing"
        elif "KEYCODE_MEDIA_PLAY" in codes and not self.dialog:
            self.playback_state = "playing"
        return RunResult(True, "", 0.01)

    async def playback(self, package=None):
        return {"state": self.playback_state, "pkg": package, "position": 1000 + time.time(),
                "updated": 1000 + time.time(), "speed": 1.0 if self.playback_state == "playing" else 0.0,
                "active": True}


def _no_prompt(monkeypatch, c):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(c, "_clear_screen_prompt", none)


@pytest.mark.playbook("APP-04")
def test_a_paused_youtube_tv_that_ignores_play_gets_one_back_before_a_relink(two, monkeypatch):   # noqa: F811
    c, tuner = two
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.05)
    _no_prompt(monkeypatch, c)
    stick = DialogStick("a:5555")
    how = asyncio.run(c._resume_playback(stick, "youtubetv:espn", 0.0))
    assert how == "back" and stick.log.count("KEYCODE_BACK") == 1
    assert stick.log.index("KEYCODE_MEDIA_PLAY") < stick.log.index("KEYCODE_BACK")
    assert tuner.calls == []                                # no re-link needed


@pytest.mark.playbook("APP-04")
def test_prime_never_gets_back(two, monkeypatch):   # noqa: F811
    c, _ = two
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.05)
    monkeypatch.setattr(ctl, "PRIME_RESUME_WAIT_S", 0.05)
    monkeypatch.setitem(ctl.TUNERS, "prime", FakeTuner(ok=False))
    _no_prompt(monkeypatch, c)
    stick = DialogStick("a:5555")
    asyncio.run(c._resume_playback(stick, "prime:abc", 0.0))
    assert "KEYCODE_BACK" not in stick.log


@pytest.mark.playbook("APP-04")
def test_a_standby_that_keeps_pausing_off_screen_is_blocked_there_for_a_while(two):   # noqa: F811
    c, _ = two
    hid = c.sticks[1]
    assert not c._standby_blocked(hid, "youtubetv:fox")     # 1st: a flip's CEC pause (STK-05)
    assert not c._standby_blocked(hid, "youtubetv:fox")
    assert c._standby_blocked(hid, "youtubetv:fox")         # 3rd within 10 min: the app is stuck
    assert c._failed["youtubetv:fox"] > time.time() + 500 and "keeps stopping" in c.note
    c._repaused[(hid.host, "youtubetv:espn")] = [time.time() - 700, time.time() - 650]
    assert not c._standby_blocked(hid, "youtubetv:espn")    # old ones don't count


def test_a_prompt_on_a_screen_we_forgot_is_still_cleared_during_a_switch_wait(two, monkeypatch):   # noqa: F811
    """Night lab seed 2: the stick on screen came back from an adb drop, its target was forgotten
    (_forget without hardening.sticks), and a "Still watching?" sat 110 s through four waits."""
    c, tuner = two
    tuner.screenshot_prompts = True                         # YouTube TV draws its own prompts
    c.active, c.on_target = 0, None
    c._input_known()
    stick = c.main
    stick.playback_state = "paused"
    pressed = []

    async def clear(st, target, pkg):
        pressed.append((st.host, target, pkg))
        st.playback_state = "playing"
        return "still_watching"
    monkeypatch.setattr(c, "_clear_screen_prompt", clear)
    asyncio.run(c._unstall_visible())                       # first look: notes it
    assert pressed == [] and c._visible_bad_since
    c._visible_bad_since -= ctl.VISIBLE_STALL_S + 1
    asyncio.run(c._unstall_visible())
    assert pressed == [(stick.host, "youtubetv:", "yttv.pkg")]
    assert c.on_target is None                              # prompt-only: nothing re-linked or assumed


def test_a_forgotten_screen_that_plays_is_left_alone(two, monkeypatch):   # noqa: F811
    c, tuner = two
    tuner.screenshot_prompts = True
    c.active, c.on_target = 0, None
    c._input_known()
    monkeypatch.setattr(c, "_clear_screen_prompt", lambda *a: pytest.fail("nothing to clear"))
    asyncio.run(c._unstall_visible())
    assert c._visible_bad_since is None
