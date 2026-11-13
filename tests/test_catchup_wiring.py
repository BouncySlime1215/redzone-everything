"""[park.catchup, task E-01] The catch-up engine wired into the app's show: `ParkRun.begin` builds `CatchupShow` only with
park.catchup.enabled on, and the cue reaches the sticks through `ControllerRig.cu_cue` -> `Controller.park_cue` ->
`ParkHands.cue` (the overlay scrub back, measured by the label reader, then the ordinary park on the clip's frame).

Fake sticks and TV only (tests/test_park_hands.py `world`); the overlay scrub itself is a stand-in here
(yttv_seek.enter_buffer has its own tests). Nothing in this file has run on the real TV: that is task E-02.

Why: a merged catch-up engine with no caller cannot be switched on; wired, it must change nothing with the flag off and
fail closed at every step it cannot prove with the flag on.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix(), (ROOT / "tests").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone.models import Phase  # noqa: E402
from redzone.noggin.park_catchup import CatchupShow  # noqa: E402
from redzone.noggin.park_show import Show  # noqa: E402
from redzone.tuners import yttv_seek  # noqa: E402
from test_park_hands import world  # noqa: E402,F401  (the fake sticks and TV of the park steps' own tests)
from test_park_run_channel import _engine  # noqa: E402

ESPN = "youtubetv:espn"
STEP = 15.0


def _scrub(calls: list, *, ok: bool = True, measured: bool = True):
    """A stand-in for yttv_seek.enter_buffer on the fake stick: LEFT x n (15 s each, enough for the seconds asked),
    committed; the stick then plays from there. Reports the label reader's behind-live (the truth) when measured."""
    async def enter_buffer(stick, seconds, *, cfg=None, pkg=None, still_hidden=None, behind_before_s=0.0, **kw):
        calls.append({"seconds": seconds, "hidden": still_hidden(), "readers": sorted(kw), "behind_before_s": behind_before_s})
        n = int(math.ceil(seconds / STEP))
        stick.keys += ["DPAD_CENTER"] + ["DPAD_LEFT"] * n + ["DPAD_CENTER"]
        if not ok:
            return yttv_seek.BufferResult(ok=False, asked_s=seconds, presses=n, reason="knob moved 30s, not 45s")
        stick._play(stick.pos() - STEP * n)
        return yttv_seek.BufferResult(ok=True, asked_s=seconds, presses=n, committed=True, measured=measured,
                                      behind_live_s=stick.behind() - 20.0)
    return enter_buffer


async def _parked(w):
    """Stick b (hidden) tuned to ESPN and read, then parked: as the show leaves a hidden stick before a catch-up."""
    assert (await w.c.park_tune(ESPN)).ok
    assert (await w.c.park_pause(ESPN, None)).ok
    w.c.cfg.update({"park": {"enabled": True, "catchup": {"enabled": True}}})
    w.c.read_behind = lambda stick: None                       # the engine's label reader is wired (never called here)
    w.b.keys.clear()


# ---------------------------------------------------------------------------------------------- the controller's step
async def test_the_cue_is_refused_with_park_catchup_off_and_nothing_is_sent(world, monkeypatch):  # noqa: F811
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    assert (await w.c.park_tune(ESPN)).ok
    w.b.keys.clear()
    out = await w.c.park_cue(ESPN, w.clk.now() - 60.0)
    assert out.outcome == "refused" and out.why == "park.catchup.enabled is off"
    assert calls == [] and w.b.keys == [] and w.a.keys == []


async def test_a_cue_freezes_the_hidden_stick_on_the_clips_start_measured_by_the_label(world, monkeypatch):  # noqa: F811
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    await _parked(w)
    want = w.b.content(w.b.pos()) - 50.0                       # a clip that started 50 s before the parked frame
    out = await w.c.park_cue(ESPN, want)
    assert out.ok, out.why
    assert calls and calls[0]["hidden"] and "read_behind" in calls[0]["readers"]
    assert w.b.ps == "paused" and abs(w.b.content(w.b.frozen) - want) < 1.0       # frozen on the clip's start (truth)
    place = w.hands.place_of(w.b)
    assert place.paused and place.read and abs(place.content(place.frozen_pos) - want) < 1.0
    assert w.b.keys[0] == "MEDIA_PLAY_PAUSE" and w.b.keys[-2:] == ["MEDIA_PLAY_PAUSE", "BACK"]   # resume, scrub, pause
    assert w.a.keys == [] and w.tv.sets == []                  # the stick on screen and the TV are never touched
    assert w.b.behind_on == ESPN                               # a same-channel link would not bring it back (#391)
    assert w.c.standby_m.is_("park_held")


async def test_a_cue_with_no_label_reader_or_out_of_reach_presses_nothing(world, monkeypatch):  # noqa: F811
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    await _parked(w)
    now_c = w.b.content(w.b.pos())
    out = await w.c.park_cue(ESPN, now_c - 400.0)              # past the scrub's reach (yttv_seek.max_back_s 150)
    assert out.outcome == "refused" and "reach" in out.why
    w.c.read_behind = None
    out = await w.c.park_cue(ESPN, now_c - 40.0)
    assert out.outcome == "refused" and "label reader" in out.why
    assert calls == [] and w.b.keys == [] and w.hands.place_of(w.b).paused


async def test_a_scrub_that_fails_or_lands_unmeasured_drops_the_place_so_the_show_tunes_again(world, monkeypatch):  # noqa: F811
    for ok, measured in ((False, True), (True, False)):
        w, calls = world, []
        monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls, ok=ok, measured=measured))
        await _parked(w)
        out = await w.c.park_cue(ESPN, w.b.content(w.b.pos()) - 40.0)
        assert out.outcome == "failed" and "did not land measured" in out.why
        assert w.hands.place_of(w.b) is None and w.b.behind_on == ESPN
        assert not w.c.standby_m.is_("park_held")


# ---------------------------------------------------------------------------------------------- the show's rig
async def test_the_rig_cues_only_the_hidden_stick_and_maps_the_grid_second_both_ways(world, monkeypatch, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", ESPN, "youtubetv:fs1"], [Phase.LIVE] * 3)
    eng.cfg.update({"park": {"catchup": {"enabled": True}}})
    w.c.read_behind = lambda stick: None
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    assert run_.state == "running", run_.why
    show, rig = run_.show, run_.rig
    assert isinstance(show, CatchupShow) and show.cu_ok and not show.cu_screen      # wired, hidden-only
    assert rig.cue_on_screen is False
    on = show.on
    assert await rig.cu_cue(on, rig.game[on], 0.0, "rewind", True) == (False, 0.0)  # never the stick on screen
    assert calls == []
    h = 1 - on
    ok, _ = await rig.tune(h, 1)
    assert ok
    ok, _ = await rig.pause(h, None)
    assert ok and rig.paused[h]
    frame = rig.frozen[h] - 45.0                               # a clip's start, 45 s before the parked frame
    assert await rig.cu_cue(h, 2, frame, "rewind", False) == (False, 0.0)           # not the game it holds
    ok, took = await rig.cu_cue(h, 1, frame, "rewind", False)
    assert ok and took > 0 and rig.paused[h] and rig.game[h] == 1
    assert abs(rig.frozen[h] - frame) < 1.0                    # the frame read back on the show's grid
    assert w.a.forbidden() == [] and w.b.forbidden() == []


async def test_park_run_builds_the_plain_show_with_the_flag_off(world, tmp_path):  # noqa: F811
    from redzone.park_run import ParkRun, catchup_params
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", ESPN, "youtubetv:fs1"], [Phase.LIVE] * 3)
    assert catchup_params(eng.cfg) is None
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    assert run_.state == "running", run_.why
    assert type(run_.show) is Show and not hasattr(run_.show, "cu_ok")


async def test_the_loop_waits_out_a_catchups_own_flip_instead_of_standing_down(world, tmp_path):  # noqa: F811
    """During a catch-up's flip the show's `on` already names the stick it flips to: the loop must not read that as
    the TV input changed outside the show. Without a flip pending it still does."""
    from redzone.park_run import ParkRun
    w = world
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", ESPN, "youtubetv:fs1"], [Phase.LIVE] * 3)
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    show = run_.show
    show.flip_pending = lambda: True
    show.on = 1 - w.c.active
    await run_.clock.sleep(1.0)
    await run_.step()
    assert run_.show is show and run_.state == "running", run_.why
    show.on = w.c.active
    show.flip_pending = lambda: False
    show.on = 1 - w.c.active
    await run_.clock.sleep(1.0)
    await run_.step()
    assert run_.show is None and "input changed outside the show" in run_.why
