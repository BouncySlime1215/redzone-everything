"""[park.catchup, task E-01] The catch-up engine wired into the app's show: `ParkRun.begin` builds `CatchupShow` only with
park.catchup.enabled on, and the cue reaches the sticks through `ControllerRig.cu_cue` -> `Controller.park_cue` ->
`ParkHands.cue` (the overlay scrub back, measured by the label reader, then the ordinary park on the clip's frame).

Fake sticks and TV only (tests/test_park_hands.py `world`); the overlay scrub itself is a stand-in here
(yttv_seek.enter_buffer has its own tests). Nothing in this file has run on the real TV: that is task E-02.

Why: a merged catch-up engine with no caller cannot be switched on; wired, it must change nothing with the flag off and
fail closed at every step it cannot prove with the flag on.
"""
from __future__ import annotations

import asyncio
import math
import sys
from dataclasses import replace as dc_replace
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
    out = await w.c.park_cue(ESPN, now_c - 400.0)              # past the proven depth (E-01b) and the scrub's reach
    assert out.outcome == "refused" and "past the" in out.why
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


# ---------------------------------------------------------------------------------------------- [E-01b] the verifier's items
async def _running_catchup_show(w, tmp_path, **catchup):
    """ParkRun on the fakes with park.catchup.enabled on: a real CatchupShow, running."""
    from redzone.park_run import ParkRun
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", ESPN, "youtubetv:fs1"], [Phase.LIVE] * 3)
    eng.cfg.update({"park": {"catchup": {"enabled": True, **catchup}}})
    run_ = ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    await run_.step()
    assert run_.state == "running" and isinstance(run_.show, CatchupShow), run_.why
    said: list = []
    run_.show.say = said.append
    return run_, said


def _cu_at_leave(show, t: int, up, **more) -> dict:
    """The catch-up's state as `_cu_leave` leaves it: the show's `on` names the replay stick H, the flip is a task."""
    v = show.on
    h = 1 - v
    cu = {"phase": "clips", "up": up, "leave": t, "task": None, "game": 1, "h": h, "v": v, "key": (1, 0), "g1": show.S.sg,
          "target": 1, "join": None}
    cu.update(more)
    show.cu, show.on = cu, h
    return cu


async def _tick(run_):
    await run_.clock.sleep(1.0)
    await run_.step()


async def test_the_real_flip_pending_masks_a_flip_under_way_and_unmasks_once_it_landed(world, tmp_path):  # noqa: F811
    """The real CatchupShow.flip_pending (not a stub): while the flip to the replay is under way the loop keeps the show;
    once it is known to have landed, a controller that still shows the other stick is a real mismatch: stand down."""
    w = world
    run_, _ = await _running_catchup_show(w, tmp_path)
    show = run_.show
    assert show.flip_pending() is False
    cu = _cu_at_leave(show, int(run_.clock.now()), None)
    assert show.on != w.c.active and show.flip_pending() is True
    await _tick(run_)
    assert run_.show is show and run_.state == "running", run_.why                 # masked: the flip is under way
    cu["up"] = ("ok", "")                                                          # the flip landed...
    assert show.flip_pending() is False
    await _tick(run_)                                                              # ...but the TV still shows V
    assert run_.show is None and "input changed outside the show" in run_.why


async def test_a_failed_catchup_flip_ends_that_catchup_and_the_show_goes_on(world, tmp_path):  # noqa: F811
    """D3: the catch-up's own flip is refused ("kept": the picture that was up is still up): the catch-up is dropped,
    said and logged, the screen's stick is `on` again, and the show runs on. A real out-of-show input change still
    stands the show down."""
    w = world
    run_, said = await _running_catchup_show(w, tmp_path)
    show = run_.show
    events: list = []
    show.emit = events.append
    v = show.on
    _cu_at_leave(show, int(run_.clock.now()), ("kept", "the flip was refused"))
    assert show.flip_pending() is True                                             # not settled yet: still masked
    await _tick(run_)
    assert run_.show is show and run_.state == "running", run_.why
    assert show.cu is None and show.on == v == w.c.active and not show.stop
    assert any("did not land" in k for k in show.cu_aborts)
    assert any("CATCH-UP: the flip to the replay did not land" in line for line in said)
    assert any(e.get("ev") == "catchup" and e.get("what") == "dropped" for e in events)
    await _tick(run_)
    assert run_.show is show and run_.state == "running", run_.why                 # the show goes on
    w.c.active = 1 - w.c.active                                                    # the input changes outside the show
    await _tick(run_)
    assert run_.show is None and "input changed outside the show" in run_.why


async def test_a_catchup_flip_that_never_returns_stops_the_show_and_lifts_the_mask(world, tmp_path):  # noqa: F811
    """D4: a rig whose flip hangs. After park.catchup.step_timeout_s the flip is cancelled, the show stops (where the TV
    is is unknown) and nothing stays masked."""
    w = world
    run_, said = await _running_catchup_show(w, tmp_path, step_timeout_s=30)
    show = run_.show
    hung = asyncio.Event()

    async def hangs(k, resume, hold=None):
        await hung.wait()
        return "ok", ""
    show.rig.jump = hangs
    t = int(run_.clock.now())
    cu = _cu_at_leave(show, t, None)
    cu["task"] = asyncio.ensure_future(show.rig.jump(cu["h"], True, None))
    for _ in range(28):
        await _tick(run_)
    assert run_.show is show and show.flip_pending() is True                       # within the timeout: masked
    for _ in range(6):
        await _tick(run_)
        if run_.show is None:
            break
    assert run_.show is None and run_.state == "stopped" and "did not return in 30 s" in run_.why, run_.why
    await asyncio.gather(cu["task"], return_exceptions=True)
    assert cu["task"].cancelled() and show.flip_pending() is False


async def test_a_catchup_join_that_never_returns_stops_the_show_and_lifts_the_mask(world, tmp_path):  # noqa: F811
    w = world
    run_, said = await _running_catchup_show(w, tmp_path, step_timeout_s=30)
    show = run_.show
    hung = asyncio.Event()

    async def hangs(k, resume, hold=None):
        await hung.wait()
        return "ok", ""
    t = int(run_.clock.now())
    cu = _cu_at_leave(show, t - 60, ("ok", ""), phase="join", pkg_s=40, done=True, wait_from=t - 20, v_task=None,
                      v_tuned=t - 30, j=None, p_task=None, p_kind="", spot=None, tries=0)
    cu["join"], cu["join_at"] = asyncio.ensure_future(hangs(cu["v"], True)), t
    cu["j"] = 5
    show.on = cu["v"]
    for _ in range(10):
        await _tick(run_)
    assert run_.show is show and show.flip_pending() is True
    for _ in range(25):
        await _tick(run_)
        if run_.show is None:
            break
    assert run_.show is None and run_.state == "stopped" and "live join did not return in 30 s" in run_.why, run_.why
    assert cu["join"] is None and show.flip_pending() is False


# ---------------------------------------------------------------------------------------------- D6: the proven depth
async def test_a_cue_never_aims_past_the_proven_175_s_rewind(world, monkeypatch):  # noqa: F811
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    await _parked(w)
    lag = w.hands._live_lag()
    assert w.hands._proven_reach() == 175.0
    live = w.clk.now() - lag                                   # the live edge on YouTube's clock now
    out = await w.c.park_cue(ESPN, live - 180.0)               # the clip itself starts 180 s behind live
    assert out.outcome == "refused" and "past the proven rewind (175 s)" in out.why
    assert calls == [] and w.b.keys == []
    place = w.hands.place_of(w.b)
    back, why = w.hands._cue_back(place, live - 171.0, w.clk.now())     # 171 s deep: the 8 s lead shrinks to 4 s
    assert why == "" and abs((w.clk.now() - (live - 171.0) + 4.0) - place.behind_s(w.clk.now()) - back) < 1e-6
    now = w.clk.now()                                          # a stick at its live edge: 170 s back is past the scrub's 150
    at_live = dc_replace(place, paused=False, ref_wall=now, ref_pos=0.0, anchor=now - lag)
    back, why = w.hands._cue_back(at_live, live - 170.0, now)
    assert back == 0.0 and "past the scrub's reach (150 s)" in why


async def test_after_the_resume_the_depth_is_checked_again_and_fails_closed(world, monkeypatch):  # noqa: F811
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    await _parked(w)
    real = w.hands.resume

    async def slow_resume(stick, target):
        await w.clk.sleep(10.0)                                # the frame slips 10 s deeper before it plays again
        return await real(stick, target)
    monkeypatch.setattr(w.hands, "resume", slow_resume)
    live = w.clk.now() - w.hands._live_lag()
    out = await w.c.park_cue(ESPN, live - 170.0)               # inside the cap now, past it after the resume
    assert out.outcome == "failed" and "past the proven rewind" in out.why and "after the resume" in out.why
    assert calls == []                                         # nothing scrubbed


async def test_a_scrub_measured_past_the_proven_depth_is_never_trusted(world, monkeypatch):  # noqa: F811
    w, calls = world, []
    scrub = _scrub(calls)

    async def overshoots(stick, seconds, **kw):
        return await scrub(stick, seconds + 30.0, **kw)        # two presses too many
    monkeypatch.setattr(yttv_seek, "enter_buffer", overshoots)
    await _parked(w)
    live = w.clk.now() - w.hands._live_lag()
    out = await w.c.park_cue(ESPN, live - 160.0)
    assert out.outcome == "failed" and "past the proven rewind" in out.why
    assert w.hands.place_of(w.b) is None                       # dropped: the show tunes it again


# ---------------------------------------------------------------------------------------------- D7: the lock
async def test_the_wait_for_the_frame_holds_no_lock_and_a_finish_in_it_wins(world, monkeypatch):  # noqa: F811
    """D7: the cue holds the controller lock for the scrub and for the park, not for the wait for its frame: a
    stand-down's finish meanwhile runs at once, and the cue then sends nothing more."""
    w, calls = world, []
    monkeypatch.setattr(yttv_seek, "enter_buffer", _scrub(calls))
    await _parked(w)
    w.c.cfg.update({"park": {"steps": {"cue_lead_s": 30}}})    # land about 30 s before the frame
    want = w.b.content(w.b.pos()) - 40.0
    cue = asyncio.ensure_future(w.c.park_cue(ESPN, want))
    while not calls:
        await w.clk.sleep(0.5)
    await w.clk.sleep(3.0)                                     # the scrub is done; the frame is ~25 s away
    assert not cue.done()
    t0 = w.clk.now()
    fin = await w.c.park_finish()
    assert fin.ok and w.clk.now() - t0 < 10.0                  # it did not queue behind the cue
    w.b.keys.clear()
    out = await cue
    assert out.outcome == "failed" and "another step moved the stick" in out.why
    assert w.b.keys == [] and w.b.ps == "playing"              # no pause key after the finish


# ---------------------------------------------------------------------------------------------- flag-off parity in the app
async def test_flag_off_park_run_never_touches_the_catchup_code(world, tmp_path, monkeypatch):  # noqa: F811
    """With park.catchup.enabled off: ParkRun builds the plain Show, the catch-up module is not imported, no cue step is
    ever called, and the loop's flip check is the original one (a plain Show has no flip_pending)."""
    from redzone import park_run as pr_mod
    w = world
    monkeypatch.delitem(sys.modules, "redzone.noggin.park_catchup", raising=False)

    def no_cue(*a, **k):
        raise AssertionError("a cue with park.catchup.enabled off")
    monkeypatch.setattr(pr_mod.ControllerRig, "cu_cue", no_cue)
    monkeypatch.setattr(w.c, "park_cue", no_cue)
    eng, ids = _engine(w, tmp_path, ["youtubetv:tnt", ESPN, "youtubetv:fs1"], [Phase.LIVE] * 3)
    run_ = pr_mod.ParkRun(eng, now=w.clk.now, sleep=w.clk.sleep)
    assert await w.tv.sound_guard.check("session start") == "ok"
    for _ in range(120):
        await run_.step()
        await (run_.clock.sleep(1.0) if run_.clock is not None else w.clk.sleep(1.0))
    assert type(run_.show) is Show and not hasattr(run_.show, "flip_pending")
    assert "redzone.noggin.park_catchup" not in sys.modules
