"""[park.fast_steps] PARK's step mechanics with fewer seconds on the critical path, every check kept.

On the fake sticks and TV of tests/test_park_hands.py (fake time). With the flag on:
  - a jump sends the same keys in the same order and still reads back the TV's input, silence after the switch and the
    new picture playing before it returns; the stick left's BACK and read-back run after it returns, and nothing is
    sent to that stick by another step until they end (or a new tune / finish ends them before their next key);
  - a sound stop, a flip that does not land, a pause that does not hold: caught exactly as with the flag off;
  - the TV's own after-switch read is the jump's silence read-back (one read, not two queued);
  - SamsungTV.sound_state reads all four values in one round.
With the flag off nothing here runs (the existing tests/test_park_hands.py, and the replay, are unchanged).
Timing: scripts/park_step_bench.py (calibrated so the flag-off steps match the Nov 11 TV test) shows the jump under
3.2 s and the park and tune no slower with the flag on.

Why: tonight's real-TV hands test measured the jump at 6.9 s and the park at 4.2 s on the critical path.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from redzone import control as ctl
from redzone import tv as tvmod
from test_park_hands import near, parked, world  # noqa: F401  (world is the fixture)

BENCH = Path(__file__).resolve().parent.parent / "scripts" / "park_step_bench.py"


@pytest.fixture
def fast(world):  # noqa: F811
    world.c.cfg.update({"park": {"fast_steps": True}})
    return world


def on_screen(w):
    """Stick a is on screen with a place (as after an earlier jump)."""
    w.c.on_target = "youtubetv:tnt"
    w.hands.places[w.a.host] = ctl.ParkPlace("youtubetv:tnt", w.a.origin, True, w.clk.now(), w.clk.now(), w.a.pos())
    w.hands._to(w.a, "reading", "test: stick a's place is known")
    w.hands._to(w.a, "playing", "test: on screen")


class AfterTV:
    """The test TV, plus what the real SamsungTV.set_input does after every switch: the guard's after-switch check."""

    def __init__(self, tv):
        self.tv, self.reads = tv, 0
        real_state, real_set = tv.sound_state, tv.set_input

        async def sound_state(input_timeout_s=10.0):
            self.reads += 1
            return await real_state(input_timeout_s)

        async def set_input(hdmi, methods=None):
            try:
                return await real_set(hdmi, methods)
            finally:
                await tv.sound_guard.after_switch()
        tv.sound_state, tv.set_input = sound_state, set_input


# --- the jump -------------------------------------------------------------------------------------------------------

async def test_jump_returns_with_the_picture_up_and_the_hold_finishes_after(fast):
    w = fast
    await parked(w)
    on_screen(w)
    frozen = w.b.frozen
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_jump("youtubetv:espn")
    assert out.ok, out.why
    assert w.b.keys == ["MEDIA_PLAY_PAUSE"] and w.a.keys == ["MEDIA_PLAY_PAUSE"]       # BACK not yet: after the step
    assert w.tv.sets == [("HDMI2", ["smartthings"])] and w.tv.input == "HDMI2" and out.numbers["input_ok"]
    assert w.b.ps == "playing" and near(w.b.pos0, frozen, 0.01) and out.numbers["left_paused"] is True
    assert out.took_s <= 3.6 and out.numbers["left_hold"] == "after the step"
    assert w.hands.busy(w.a) and w.hands.m.state("Stick on HDMI1") == "pausing"
    assert w.c.standby_m.is_("park_held") and w.c.hidden_target == "youtubetv:tnt"
    await w.hands._settle(w.a)                                # its BACK and read-back
    assert w.a.keys == ["MEDIA_PLAY_PAUSE", "BACK"] and w.a.ps == "paused"
    assert w.hands.m.state("Stick on HDMI1") == "parked" and w.hands.place_of(w.a).paused
    assert w.c.standby_m.is_("park_held") and not w.hands.busy(w.a) and w.hands.pending == {}


async def test_the_same_jump_with_the_flag_off_holds_the_stick_left_before_it_returns(world):  # noqa: F811
    w = world
    await parked(w)
    on_screen(w)
    off = await w.c.park_jump("youtubetv:espn")
    assert off.ok and w.a.keys == ["MEDIA_PLAY_PAUSE", "BACK"] and w.hands.pending == {}
    assert off.took_s >= 4.4                                  # the hold (BACK, 1.6 s, read back) is inside the step


async def test_a_hold_that_does_not_hold_is_caught_by_its_read_back_and_the_standby_follows(fast):
    w = fast
    await parked(w)
    on_screen(w)
    w.a.hold_fails = True                                     # BACK resumes it
    out = await w.c.park_jump("youtubetv:espn")
    assert out.ok and w.hands.place_of(w.a).paused            # paused seen: marked so while its read-back runs
    await w.hands._settle(w.a)
    assert not w.hands.place_of(w.a).paused and w.a.ps == "playing"
    assert w.hands.m.state("Stick on HDMI1") == "playing" and w.c.standby_m.is_("empty")


async def test_a_new_tune_ends_the_hold_before_its_next_key_and_never_trusts_the_pause(fast):
    w = fast
    await parked(w)
    on_screen(w)
    assert (await w.c.park_jump("youtubetv:espn")).ok
    out = await w.c.park_tune("youtubetv:tnt")                # the stick left, re-tuned at once
    assert out.ok and out.way.startswith("code")              # its place was dropped: tuned fresh, never "kept: parked"
    assert w.a.keys == ["MEDIA_PLAY_PAUSE", "DPAD_CENTER", "DPAD_LEFT", "DPAD_LEFT", "DPAD_CENTER"]   # no BACK went out
    assert w.hands.pending == {} and w.hands._ending == set() and w.hands._wake == {}


async def test_a_tune_a_second_later_ends_the_hold_in_its_wait_without_its_read_back(fast):
    w = fast
    await parked(w)
    on_screen(w)
    assert (await w.c.park_jump("youtubetv:espn")).ok
    await w.clk.sleep(1.0)                                    # the show's next tick: BACK has gone out, the 1.6 s runs
    assert w.a.keys == ["MEDIA_PLAY_PAUSE", "BACK"] and w.hands.busy(w.a)
    t0 = w.clk.now()
    reads = []
    real = w.hands._read

    async def read(stick):
        reads.append((stick.host, w.clk.now() - t0))
        return await real(stick)
    w.hands._read = read
    out = await w.c.park_tune("youtubetv:tnt")
    assert out.ok and out.way.startswith("code")
    assert not any(h == w.a.host and s < 1.0 for h, s in reads)     # the hold read nothing more: it was ended
    assert w.hands.pending == {}


async def test_a_jump_onto_a_stick_whose_check_still_runs_is_refused_with_nothing_sent(fast):
    w = fast
    await parked(w)
    on_screen(w)
    assert (await w.c.park_jump("youtubetv:espn")).ok and w.hands.busy(w.a)
    n_a, n_b, n_tv = len(w.a.keys), len(w.b.keys), len(w.tv.sets)
    out = await w.hands.jump(w.a, "youtubetv:tnt", w.b, "HDMI1")
    assert out.outcome == "kept" and "nothing sent" in out.why
    assert (len(w.a.keys), len(w.b.keys), len(w.tv.sets)) == (n_a, n_b, n_tv)
    await w.hands._settle(w.a)


async def test_a_sound_incident_mid_jump_stops_everything_and_starts_no_hold(fast):
    w = fast
    await parked(w)
    w.tv.unmute_on_flip = True
    out = await w.c.park_jump("youtubetv:espn")
    assert out.outcome == "stop" and "sound incident" in out.why
    assert w.tv.sound_guard.blocked and ("switch", "off") in w.tv.commands
    assert w.hands.pending == {} and "BACK" not in w.a.keys
    n_a, n_b, n_tv = len(w.a.keys), len(w.b.keys), len(w.tv.sets)
    assert (await w.c.park_tune("youtubetv:fs1")).outcome == "refused"
    assert (await w.c.park_finish()).outcome == "stop"
    assert (len(w.a.keys), len(w.b.keys), len(w.tv.sets)) == (n_a, n_b, n_tv)


async def test_a_failed_flip_keeps_the_picture_up_restarts_it_and_starts_no_hold(fast):
    w = fast
    await parked(w)
    w.c.on_target = "youtubetv:tnt"
    w.tv.flip_fails = True
    out = await w.c.park_jump("youtubetv:espn")
    assert out.outcome == "kept" and out.why == "the flip did not confirm"
    assert w.tv.input == "HDMI1" and w.a.ps == "playing" and w.a.keys == ["MEDIA_PLAY_PAUSE", "MEDIA_PLAY_PAUSE"]
    assert w.c.main is w.a and w.c.on_target == "youtubetv:tnt" and w.hands.pending == {}


async def test_finish_ends_a_running_hold_and_leaves_no_stick_paused(fast):
    w = fast
    await parked(w)
    on_screen(w)
    assert (await w.c.park_jump("youtubetv:espn")).ok and w.hands.busy(w.a)
    out = await w.c.park_finish()
    assert out.ok and w.a.ps == "playing" and w.b.ps == "playing" and w.hands.pending == {}


async def test_silence_after_the_switch_is_the_guards_own_after_switch_read_not_a_second_one(fast):
    w = fast
    await parked(w)
    on_screen(w)
    t = AfterTV(w.tv)
    assert await w.tv.sound_guard.check("session start") == "ok"
    t.reads = 0
    out = await w.c.park_jump("youtubetv:espn")
    assert out.ok and t.reads == 1                            # one read of mute, volume and input after the switch
    await w.hands._settle(w.a)


async def test_with_the_flag_off_the_jump_reads_silence_again_behind_the_after_switch_read(world):  # noqa: F811
    w = world
    await parked(w)
    on_screen(w)
    t = AfterTV(w.tv)
    assert await w.tv.sound_guard.check("session start") == "ok"
    t.reads = 0
    assert (await w.c.park_jump("youtubetv:espn")).ok and t.reads == 2


async def test_an_unmute_seen_by_the_after_switch_read_stops_the_jump(fast):
    w = fast
    await parked(w)
    on_screen(w)
    AfterTV(w.tv)
    w.tv.unmute_on_flip = True
    out = await w.c.park_jump("youtubetv:espn")
    assert out.outcome == "stop" and "sound incident" in out.why and w.hands.pending == {}


# --- park, the show's rig -------------------------------------------------------------------------------------------

async def test_park_with_the_flag_on_reads_the_stick_and_its_app_and_still_catches_a_pause_that_does_not_hold(fast):
    w = fast
    assert (await w.c.park_tune("youtubetv:espn")).ok
    w.b.keys.clear()
    at = w.clk.now() + 3.0
    out = await w.c.park_pause("youtubetv:espn", at)
    assert out.ok and w.b.keys == ["MEDIA_PLAY_PAUSE", "BACK"] and near(w.b.event, at, 0.5)
    assert w.hands.place_of(w.b).paused and w.hands.m.state("Stick on HDMI2") == "parked"
    assert (await w.c.park_finish()).ok
    assert (await w.c.park_tune("youtubetv:fs1")).ok
    w.b.hold_fails = True
    out = await w.c.park_pause("youtubetv:fs1", None)
    assert out.outcome == "failed" and "after BACK it reports playing" in out.why


async def test_the_show_rig_hands_the_hold_to_the_show_and_never_cancels_its_read_back(fast):
    from redzone.noggin.park_show import ShowParams
    from redzone.park_run import ControllerRig, WallClock
    w = fast
    await parked(w)
    on_screen(w)
    rig = ControllerRig(w.c, WallClock(0, now=w.clk.now, sleep=w.clk.sleep),
                        ["youtubetv:tnt", "youtubetv:espn", "youtubetv:fs1"], ShowParams())
    assert rig.confirming(0) is None                          # nothing running yet
    assert (await w.c.park_jump("youtubetv:espn")).ok
    fut = rig.confirming(0)
    assert fut is not None and not fut.done()
    ok, frozen = await fut
    assert ok and frozen == rig.frozen[0] and rig.paused[0]
    assert (await w.c.park_tune("youtubetv:fs1")).ok          # (stick b is on screen now: a is hidden)
    assert (await w.c.park_pause("youtubetv:fs1", None)).ok
    out = await w.c.park_jump("youtubetv:fs1")
    assert out.ok
    fut = rig.confirming(1)
    fut.cancel()                                              # the show drops the hold to re-tune: the read-back goes on
    await asyncio.sleep(0)
    assert w.hands.busy(w.b)
    await w.hands._settle(w.b)
    assert w.b.keys[-1] == "BACK" and w.hands.place_of(w.b).paused


async def test_the_rig_with_the_flag_off_never_hands_a_hold_to_the_show(world):  # noqa: F811
    from redzone.noggin.park_show import ShowParams
    from redzone.park_run import ControllerRig, WallClock
    w = world
    await parked(w)
    on_screen(w)
    rig = ControllerRig(w.c, WallClock(0, now=w.clk.now, sleep=w.clk.sleep), ["youtubetv:tnt", "youtubetv:espn"],
                        ShowParams())
    assert (await w.c.park_jump("youtubetv:espn")).ok and rig.confirming(0) is None


# --- the TV's sound read ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fast_on", [False, True])
async def test_sound_state_reads_the_input_beside_the_other_three_only_with_the_flag(fast_on):
    class TV(tvmod.SamsungTV):
        has_readback = property(lambda self: True)
    tv = TV(st_device="test-display", cloud_enabled=True)
    tv.sound_settings = lambda k, fb=None: fast_on if k == "park.fast_steps" else fb
    live, most = [0], [0]

    async def slow(value):
        live[0] += 1
        most[0] = max(most[0], live[0])
        await asyncio.sleep(0.01)
        live[0] -= 1
        return value

    async def st_value(capability, attr):
        return await slow({"switch": "on", "mute": "muted", "volume": 0}[attr])

    async def input_status():
        return await slow("HDMI2")
    tv._st_value, tv.input_status = st_value, input_status
    s = await tv.sound_state()
    assert s == {"power": "on", "mute": "muted", "volume": 0, "input": "HDMI2"}
    assert most[0] == (4 if fast_on else 3)


# --- the bench: the seconds (fake sticks and TV, calibrated to the Nov 11 TV test) -----------------------------------

def test_the_bench_jump_is_under_3_2_s_and_no_step_is_slower_with_the_flag():
    spec = importlib.util.spec_from_file_location("park_step_bench", BENCH)
    bench = sys.modules["park_step_bench"] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    off, on = (bench.summary(bench.bench(f, moves=6, look=0.0)) for f in (False, True))
    assert off["not_ok"] == [] and on["not_ok"] == []
    assert 6.0 <= off["jump"]["median_s"] <= 7.5 and on["jump"]["median_s"] <= 3.2
    assert on["park"]["median_s"] <= off["park"]["median_s"] and on["tune"]["median_s"] <= off["tune"]["median_s"]
