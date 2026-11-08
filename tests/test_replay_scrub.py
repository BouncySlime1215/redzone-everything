"""[replay on the scrub, Nick Nov 7] Catch-up replay sent MEDIA_REWIND presses in one shell call with a 20 s timeout:
the rewind landed on screen ('got right to the spot of the big play') but the call timed out, so every replay logged
'replay seek failed; kept current game' (20-24 s) and left the player menu up, and Nick SAW the rewind. A YouTube TV
catch-up now seeks the HIDDEN stick by yttv_seek.enter_buffer, READS its landing on the label probe, lets it play to
park_lead_s before the snap and pauses it there; the flip un-pauses it. Never rewound on screen. Fails closed."""
import asyncio
import sys
import time
import types

import pytest

from redzone.control import Parked
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)


class Res:
    def __init__(self, ok=True, behind_s=60.0, presses=3, committed=True, reason=""):
        self.ok, self.behind_s, self.presses, self.committed, self.reason = ok, behind_s, presses, committed, reason


class Probe:
    def __init__(self, armed=True):
        self.armed, self.armed_on = armed, []

    def arm(self, stick, behind_before_s=0.0):
        self.armed_on.append(stick.host)
        return self.armed

    async def overlay_reader(self, stick):
        return None

    async def __call__(self, stick):
        return None


@pytest.fixture
def scrub(two, monkeypatch):   # noqa: F811
    c, tuner = two
    c.label_probe = Probe()
    calls, result = [], {"r": None}

    async def enter_buffer(stick, seconds, *, cfg=None, pkg=None, still_hidden=None, read_overlay=None,
                           read_behind=None, presses=None, **kw):
        assert still_hidden is not None and still_hidden() and read_behind is not None
        calls.append((stick.host, seconds, presses))
        stick.log.extend(["KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_RIGHT"] + ["KEYCODE_DPAD_LEFT"] * (presses - 1)
                         + ["KEYCODE_DPAD_CENTER"])
        r = result["r"]
        return r(seconds, presses) if callable(r) else r
    mod = types.ModuleType("redzone.tuners.yttv_seek")
    mod.enter_buffer = enter_buffer
    monkeypatch.setitem(sys.modules, "redzone.tuners.yttv_seek", mod)
    import redzone.tuners as _tuners
    monkeypatch.setattr(_tuners, "yttv_seek", mod, raising=False)
    return c, calls, result


def run(coro):
    return asyncio.run(coro)


def rewinds(stick):
    return sum(str(x).count("KEYCODE_MEDIA_REWIND") for x in stick.log)


def landing(extra_s):
    """A scrub whose label reads the park point plus `extra_s` (the picture shows (snap - 1.5) after extra_s)."""
    def r(back_s, presses):
        return Res(behind_s=back_s - 3.0 + 1.5 + extra_s, presses=presses)   # back_s = now - snap + 3
    return r


@pytest.mark.parametrize("back,k", [(20, 2), (30, 3), (45, 4), (60, 5), (90, 7)])
def test_the_knob_steps_cover_the_park_point(scrub, back, k):
    """k = ceil((back - 3 + 1.5 + 4) / 15): the knob alone reaches the park point; the frozen overlay adds as much
    behind-live as it holds the picture still, so it cancels out."""
    c, calls, result = scrub
    result["r"] = landing(0.2)
    assert run(c._park(c.hidden, "youtubetv:fox", float(back), "p1"))
    assert calls == [("b:5555", float(back), k + 1)]          # the probe + k LEFTs (yttv_seek's fixed count)


def test_a_youtubetv_replay_parks_on_the_hidden_stick_just_before_the_snap(scrub):
    c, calls, result = scrub
    result["r"] = landing(0.6)                                # lands 0.6 s early: plays 0.6 s, then pauses
    hid = c.hidden
    hid.playback_state = "paused"                              # the fake stick doesn't model MEDIA_PAUSE itself
    t = time.time()
    assert run(c._park(hid, "youtubetv:fox", 60.0, "p1"))
    assert 0.4 < time.time() - t < 2.0                         # waited out the 0.6 s to the park point
    assert rewinds(hid) == 0 and hid.log[-1] == "KEYCODE_MEDIA_PAUSE"
    assert c.parked is not None and c.parked.target == "youtubetv:fox" and c.parked.replay_id == "p1"
    assert hid.behind_on == "youtubetv:fox"                   # #391: its next live tune resets through another channel
    assert c.label_probe.armed_on == ["b:5555"] and c.main.log == []   # the visible stick is never touched


def test_a_landing_past_the_snap_fails_closed(scrub):
    c, calls, result = scrub
    result["r"] = landing(-5.0)                               # the picture is already 5 s past the park point
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1"))
    assert c.parked is None and "KEYCODE_MEDIA_PAUSE" not in c.hidden.log


def test_no_label_read_never_parks_blind(scrub):
    c, calls, result = scrub
    result["r"] = Res(ok=True, behind_s=None)
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and c.parked is None


def test_an_unarmed_label_probe_presses_nothing(scrub):
    c, calls, result = scrub
    c.label_probe.armed = False                              # e.g. the stick is still marked behind (#391)
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and calls == [] and c.hidden.log == []


def test_with_the_scrub_label_reader_off_a_replay_gets_its_own_probe(scrub, monkeypatch):
    from redzone.tuners import yttv_label
    c, calls, result = scrub
    c.label_probe = None
    made = []
    monkeypatch.setattr(yttv_label, "LabelProbe", lambda cfg=None: made.append(1) or Probe())
    result["r"] = landing(0.1)
    assert run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and made == [1] and calls


def test_a_failed_scrub_fails_closed(scrub):
    c, calls, result = scrub
    result["r"] = Res(ok=False, behind_s=None, reason="knob moved 0s, not 30s")
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1"))
    assert c.parked is None and c.hidden.behind_on == "youtubetv:fox"


def test_a_too_deep_catchup_is_not_seeked(scrub):
    c, calls, _ = scrub
    assert not run(c._park(c.hidden, "youtubetv:fox", 400.0, "p1")) and calls == []


def test_single_stick_never_rewinds_on_screen(scrub):
    c, calls, _ = scrub
    out = run(c._execute_one("youtubetv:espn", 60.0, time.time()))
    assert not out.ok and out.screen_unchanged and calls == [] and c.main.log == []


def test_keys_mode_is_the_old_media_rewind(scrub):
    c, calls, _ = scrub
    c.cfg.update({"replay": {"yttv_seek": "keys"}})
    assert run(c._park(c.hidden, "youtubetv:fox", 30.0, "p1")) and calls == [] and rewinds(c.hidden) > 0


def test_a_drifted_youtubetv_park_is_not_rewound_by_keys(scrub):
    c, calls, _ = scrub
    c.parked = Parked("p1", "youtubetv:fox", time.time() - 60, False)   # didn't hold still
    assert not run(c._unpark(c.hidden)) and rewinds(c.hidden) == 0 and calls == []


def test_a_fresh_paused_park_rolls_on_play(scrub):
    c, calls, _ = scrub
    c.parked = Parked("p1", "youtubetv:fox", time.time() - 20, True)
    c.hidden.playback_state = "paused"
    assert run(c._unpark(c.hidden)) and c.hidden.log[-1] == "KEYCODE_MEDIA_PLAY"


@pytest.mark.parametrize("age,state", [(120, "paused"), (20, "playing")])
def test_an_old_or_unpaused_park_is_never_rolled(scrub, age, state):
    """A pause past ~90 s may have reset; a park that no longer reads paused may have gone back to live."""
    c, calls, _ = scrub
    c.parked = Parked("p1", "youtubetv:fox", time.time() - age, True)
    c.hidden.playback_state = state
    assert not run(c._unpark(c.hidden)) and "KEYCODE_MEDIA_PLAY" not in c.hidden.log


def test_a_failed_replay_scrub_is_not_retried_for_a_while(scrub):
    """CI's simulated night: a park that failed was re-tried every ~5 s on the same channel (a key storm)."""
    c, calls, result = scrub
    result["r"] = Res(ok=False, behind_s=None, reason="no label read")
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and len(calls) == 1
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and len(calls) == 1   # backed off: no keys
    c._replay_scrub_backoff["youtubetv:fox"] = 0.0
    result["r"] = landing(0.1)
    assert run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and len(calls) == 2


def test_nicks_espn_park_63s_lands_112_behind_and_is_1_5s_before_the_snap():
    """Nick's stick run (Nov 8, ESPN, 63 s back): 5 LEFTs, label 111.8 s behind live. That is not 49 s too deep:
    the scrub itself took ~37 s of wall time (17.3 s in the overlay + the label read + checks), so the snap was then
    60 + 37 s behind live. The park waits until the picture is 1.5 s before it."""
    from redzone.tuners import replay_park as rp

    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t
    clk = Clock()

    class Stick:
        log, host = [], "b:5555"

        async def key(self, k):
            self.log.append(k)
            return types.SimpleNamespace(ok=True)

        async def playback(self, pkg=None):
            return {"state": "paused"}

    async def enter_buffer(stick, seconds, **kw):
        clk.t += 41.0                      # the scrub's wall time
        return Res(ok=True, behind_s=111.8, presses=5)

    async def sleep(dt):
        clk.t += dt
    mod = types.ModuleType("redzone.tuners.yttv_seek")
    mod.enter_buffer = enter_buffer
    sys.modules["redzone.tuners.yttv_seek"], old = mod, sys.modules.get("redzone.tuners.yttv_seek")
    import redzone.tuners as _t
    oldattr = getattr(_t, "yttv_seek", None)
    _t.yttv_seek = mod
    try:
        r = run(rp.park(Stick(), 63.0, probe=Probe(), pkg="yttv.pkg", clock=clk, sleep=sleep))
    finally:
        sys.modules["redzone.tuners.yttv_seek"] = old
        _t.yttv_seek = oldattr
    assert r.ok and r.lefts == 5 and r.to_snap_s == pytest.approx(1.5, abs=0.3)
    assert r.waited_s == pytest.approx(111.8 - 60 - 1.5 - 41.0, abs=0.3)      # played ~9.3 s, then paused


def test_a_transient_none_after_pause_is_reread_not_a_lost_park():
    """Nick's rerun on fd1ba542: to_snap_s 1.5, but 'player is none after PAUSE'; media_session read PAUSED right after
    and +10 / +20 s. The park re-reads for pause_settle_s and accepts paused."""
    from redzone.tuners import replay_park as rp

    class Clock:
        t = 1000.0

        def __call__(self):
            return self.t
    clk = Clock()

    class Stick:
        log, host, reads = [], "b:5555", iter(["none", "none", "paused"])

        async def key(self, k):
            self.log.append(k)
            return types.SimpleNamespace(ok=True)

        async def playback(self, pkg=None):
            return {"state": next(self.reads)}

    async def enter_buffer(stick, seconds, **kw):
        clk.t += 41.0
        return Res(ok=True, behind_s=111.8, presses=5)

    async def sleep(dt):
        clk.t += dt
    mod = types.ModuleType("redzone.tuners.yttv_seek")
    mod.enter_buffer = enter_buffer
    old = sys.modules.get("redzone.tuners.yttv_seek")
    sys.modules["redzone.tuners.yttv_seek"] = mod
    import redzone.tuners as _t
    oldattr = getattr(_t, "yttv_seek", None)
    _t.yttv_seek = mod
    try:
        r = run(rp.park(Stick(), 63.0, probe=Probe(), pkg="yttv.pkg", clock=clk, sleep=sleep))
    finally:
        sys.modules["redzone.tuners.yttv_seek"] = old
        _t.yttv_seek = oldattr
    assert r.ok and r.paused and r.to_snap_s == pytest.approx(1.5, abs=0.3)


def test_a_park_without_the_youtubetv_package_presses_nothing():
    """Nick's rerun on 89656de9: the try script never identify()'d the stick, so park() had no package and the pause
    read 'none' among several media sessions. A park that can't confirm its pause never presses a key."""
    from redzone.tuners import replay_park as rp

    class Stick:
        log, host = [], "b:5555"

        async def key(self, k):
            self.log.append(k)
    probe = Probe()
    r = run(rp.park(Stick(), 63.0, probe=probe))
    assert not r.ok and "package" in r.reason and Stick.log == [] and probe.armed_on == []


def test_a_freshly_tuned_stick_waits_out_the_banner_before_the_scrub(scrub):
    """[Nick, Nov 8 3778a401] right after a tune YouTube TV's LIVE/title banner swallows keys ('focus guard: no scrub
    label after the LEFT', 62 s). The park waits replay.park_after_tune_s from the tune, sends nothing meanwhile, and
    asks the scrub for that much more."""
    c, calls, result = scrub
    c.cfg.update({"replay": {"park_after_tune_s": 0.6}})
    result["r"] = landing(0.1)
    c._tuned_at = {c.hidden.host: time.time()}
    t = time.time()
    assert run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1"))
    assert calls and calls[0][1] >= 60.5 and time.time() - t >= 0.5


def test_a_stick_that_turns_visible_during_the_banner_wait_presses_nothing(scrub):
    c, calls, result = scrub
    c.cfg.update({"replay": {"park_after_tune_s": 5.0}})
    c._tuned_at = {c.hidden.host: time.time()}
    c._flipping = True
    assert not run(c._park(c.hidden, "youtubetv:fox", 60.0, "p1")) and calls == [] and c.hidden.log == []
