"""An open sound incident stops the controller's own ways to change the TV's input (fake sticks, no adb, no TV).

Why: Nick's hard rule (Nov 10: "Volume must be 1000% off"). redzone/tv.py refuses its own input switch while a sound
incident is open, but Controller._flip_to then fell back to HOME on the stick, and the input-recovery step
(_maybe_reassert) presses HOME too: both change the TV's input, and HOME can turn a TV the guard powered off back on,
without the guard seeing it. With sound_guard.enabled off the TV has no guard, nothing is asked and every flip goes
exactly as before.
"""
import pytest

from redzone import control as ctl
from test_hdmi_flip import keys, live
from test_hot_standby import FakeStick, two  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ctl, "FLIP_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "REASSERT_FIRST_S", 0.0)
    monkeypatch.setattr(ctl, "REASSERT_GAP_S", 0.0)
    FakeStick.last_input_host = "a:5555"     # the TV starts on stick a (HDMI1)
    yield
    FakeStick.last_input_host = None


class Guard:
    """What SamsungTV asks of a sound guard, with an incident open (`why`) or not ("")."""

    def __init__(self, why=""):
        self.why, self.asked = why, 0

    def blocked_why(self):
        self.asked += 1
        return self.why

    def ensure_running(self):
        pass

    async def before_switch(self):
        return not self.why

    async def after_switch(self):
        pass

    def view(self):
        return {}


def sent(stick):
    """Everything that could change the picture or the TV's input from this stick."""
    return keys(stick, "KEYCODE_HOME", "onetouchplay", "WAKEUP", "MEDIA_PLAY", "am start")


def never(*_a, **_k):
    raise AssertionError("with no sound guard the block is never asked")


# --- flag off: no guard, nothing asked, the same flip ---------------------------------------------------------

async def test_no_guard_the_block_is_never_asked_and_the_flip_goes_as_before(two, monkeypatch):
    c, _ = two
    assert c.tv.sound_guard is None                       # sound_guard.enabled is off: the shipped default
    monkeypatch.setattr(c.tv, "sound_blocked", never)
    assert c._sound_blocked() == ""
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c.main is c.sticks[1]
    assert keys(c.sticks[1], "KEYCODE_HOME")              # this fixture flips by HOME, as it always did


async def test_no_guard_input_recovery_still_presses_home(two, monkeypatch):
    c = live(two[0])
    a, b = c.sticks
    monkeypatch.setattr(c.tv, "sound_blocked", never)
    c.on_target = "youtubetv:fox"
    c._mark_input_unknown("youtubetv:fox")
    FakeStick.last_input_host = None
    await c.keepalive()
    assert keys(a, "KEYCODE_HOME") and not c.input_unknown


async def test_a_tv_stand_in_without_the_guard_field_is_not_asked_either(two):
    c, _ = two

    class BareTV:                                         # an older fake TV: no sound_guard, no sound_blocked
        last_error = ""
    real, c.tv = c.tv, BareTV()
    try:
        assert c._sound_blocked() == ""
    finally:
        c.tv = real


# --- a guard with no incident changes nothing -----------------------------------------------------------------

async def test_a_guard_with_no_incident_lets_the_flip_through(two):
    c, _ = two
    c.tv.sound_guard = g = Guard("")
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c.main is c.sticks[1] and g.asked >= 1


# --- an open incident: nothing that changes the input goes out ------------------------------------------------

async def test_an_open_incident_refuses_the_flip_before_anything_is_sent(two, monkeypatch):
    c, _ = two
    c.tv.sound_guard = Guard("not silent")

    async def must_not(*_a, **_k):
        raise AssertionError("no TV command while a sound incident is open")
    monkeypatch.setattr(c.tv, "set_input", must_not)
    b = c.sticks[1]
    assert not await c._flip_to(b)
    assert sent(b) == [] and b.log == []
    assert "sound incident open (not silent)" in c.tv.last_error
    assert c._flip_s is None and c._flip_how == "" and not c._home_flipped


async def test_an_open_incident_fails_the_switch_and_no_stick_gets_home(two, monkeypatch):
    c, tuner = two
    c.tv.sound_guard = Guard("not silent")
    a, b = c.sticks
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert not out.ok and "sound incident open" in out.detail
    assert c.main is a                                    # the TV was never moved off stick a
    assert not keys(a, "KEYCODE_HOME", "onetouchplay", "WAKEUP") and not keys(b, "KEYCODE_HOME", "onetouchplay", "WAKEUP")


async def test_an_open_incident_stops_input_recovery_and_uses_up_no_try(two):
    c = live(two[0])
    a, b = c.sticks
    c.tv.sound_guard = g = Guard("not silent")
    c.on_target = "youtubetv:fox"
    c._mark_input_unknown("youtubetv:fox")
    FakeStick.last_input_host = None                      # the guard powered the TV off: no stick claims it
    for _ in range(4):
        await c.keepalive()
    assert not keys(a, "KEYCODE_HOME", "WAKEUP") and not keys(b, "KEYCODE_HOME", "WAKEUP")
    assert c.input_unknown and c._reasserts == 0
    g.why = ""                                            # the incident is cleared by hand
    await c.keepalive()
    assert keys(a, "KEYCODE_HOME") and not c.input_unknown   # recovery runs again, with all its tries left
