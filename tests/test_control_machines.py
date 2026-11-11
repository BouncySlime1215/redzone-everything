"""The control area's explicit state machines (redzone/machines/control.py) and the bugs they closed.

Each test named test_fix_* failed before the conversion (or its machine didn't exist)."""
import asyncio
import json
import time

import pytest

from redzone import control as ctl
from redzone import fsm
from redzone.channels import ChannelMap
from redzone.control import LeftHold, Parked
from redzone.tuners.adb import RunResult, Stick
from tests.test_control import FakeStick, FakeTuner

PRIME = "prime:amzn1.dv.gti.x"


class PausingStick(FakeStick):
    """MEDIA_PAUSE / MEDIA_PLAY really pause / play it."""

    async def key(self, *codes):
        self.log.extend(codes)
        if "KEYCODE_MEDIA_PAUSE" in codes:
            self.playback_state = "paused"
        if "KEYCODE_MEDIA_PLAY" in codes:
            self.playback_state = "playing"
        return RunResult(True, "", 0.01)


@pytest.fixture
def two(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    display = {"input": "HDMI1"}
    c.sticks = [PausingStick("a:5555", display=display, hdmi="HDMI1"),
                PausingStick("b:5555", display=display, hdmi="HDMI2")]
    c.hdmi = {"a:5555": "HDMI1", "b:5555": "HDMI2"}
    c.tv.st_device = "selected-test-display"

    async def input_status():
        return display["input"]
    monkeypatch.setattr(c.tv, "input_status", input_status)
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    return c


def buffered(c):
    c.cfg.update({"jump_mode": {"enabled": True}, "buffered_jump": {"enabled": True}})


def log_kinds(c):
    if not c.buffer_log_path.exists():
        return []
    return [json.loads(x)["kind"] for x in c.buffer_log_path.read_text().splitlines()]


# --- 1. the standby stick -------------------------------------------------------------------------------

def test_fix_a_cancelled_pause_hold_is_never_marked_live_ready(two, monkeypatch):
    c = two
    buffered(c)
    gate = asyncio.Event()

    async def hold(stick, target, behind_s, base_s=0.0):
        await gate.wait()           # cancelled mid-pause
        return True
    monkeypatch.setattr(c, "_hold", hold)

    async def go():
        c._set_hidden(PRIME, True)
        assert c.buffer_standby(PRIME) and c.slot_m.is_("hold")
        task = c._preload_task
        await asyncio.sleep(0)
        c.cancel_pending()          # Pause
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(go())
    assert not c.hidden_ready and not c.standby_m.is_("ready")
    assert PRIME not in c._scrub_backoff            # a cancel isn't a failed hold either
    assert c.slot_m.is_("idle")


def test_fix_b_a_hold_a_switch_displaced_is_not_a_failure(two):
    c = two
    buffered(c)
    hid = c.hidden

    async def go():
        c._set_hidden(PRIME, False)
        hold = asyncio.create_task(c._hold(hid, PRIME, 0.3))
        await asyncio.sleep(0.05)
        assert c.standby_m.is_("holding") and not c.hidden_ready
        c._set_hidden(PRIME, False)                 # a switch to the same game re-tunes that stick...
        c._holding = None                           # ...and _tune throws the hold away
        return await hold
    assert asyncio.run(go()) is False
    assert c._last_hold["end"] == "displaced"


def test_fix_b_hold_standby_neither_backs_off_nor_logs_a_failure_when_displaced(two, monkeypatch):
    c = two
    buffered(c)

    async def hold(stick, target, behind_s, base_s=0.0):
        c._last_hold = {"host": stick.host, "target": target, "end": "displaced"}
        return False
    monkeypatch.setattr(c, "_hold", hold)

    async def go():
        c._set_hidden(PRIME, True)
        assert c.buffer_standby(PRIME)
        await c._preload_task
    asyncio.run(go())
    assert PRIME not in c._scrub_backoff
    assert log_kinds(c) == ["hold_displaced"]


def test_a_real_hold_failure_still_backs_off(two, monkeypatch):
    c = two
    buffered(c)

    async def hold(stick, target, behind_s, base_s=0.0):
        c._last_hold = {"host": stick.host, "target": target, "end": "failed", "relinked": True}   # it reloaded at the live edge: ready again
        return False
    monkeypatch.setattr(c, "_hold", hold)

    async def go():
        c._set_hidden(PRIME, True)
        assert c.buffer_standby(PRIME)
        await c._preload_task
    asyncio.run(go())
    assert PRIME in c._scrub_backoff and log_kinds(c) == ["hold_failed"] and c.hidden_ready


def test_fix_c_a_cancelled_reseek_never_clears_the_slot_of_the_job_that_displaced_it(two):
    c = two
    p = Parked("r1", "youtubetv:fox", time.time(), False)

    async def other():
        await asyncio.sleep(0.05)

    async def go():
        c.parked = p
        first = c._slot_start("reseek", p.target, p.replay_id, c._reseek(p))
        await asyncio.sleep(0)
        first.cancel()
        second = c._slot_start("preload", "youtubetv:espn", None, other())
        await asyncio.gather(first, return_exceptions=True)
        assert c._preloading == ("youtubetv:espn", None) and c.slot_m.is_("preload")
        await second
    asyncio.run(go())
    assert c.slot_m.is_("idle") and c._preloading is None


def test_fix_d_force_live_hidden_drops_a_snap_park(two):
    c = two

    async def go():
        c._set_hidden("youtubetv:fox", True)
        c._snap_park = Parked("snap", "youtubetv:fox", time.time(), True)
        assert c.snap_parked("youtubetv:fox") is not None
        assert c.force_live_hidden("youtubetv:fox")
        assert c._snap_park is None and c.snap_parked("youtubetv:fox") is None and not c.hidden_ready
        await c._preload_task
    asyncio.run(go())


def test_fix_e_contradictory_standby_combinations_cant_be_represented(two):
    c = two
    t = "youtubetv:fox"
    c._set_hidden(t, True)
    c._holding = (c.hidden, t, object())
    assert not c.hidden_ready                                   # never ready while holding
    c._set_hidden(t, True)
    assert c._holding is None
    c._left = LeftHold(t, c.hidden.host, time.time(), 0.0)
    c.parked = Parked("r", t, time.time(), True)
    assert c._left is None and c.parked is not None             # never a left hold and a replay park
    c._set_hidden(t, True)
    c._snap_park = Parked("snap", t, time.time(), True)
    c._set_hidden(t, False)
    assert c._snap_park is None                                 # never a snap park on a stick that isn't ready
    c._set_hidden(None, True)
    assert c.standby_m.is_("empty") and not c.hidden_ready      # ready needs something to be ready with


def test_stick_job_is_the_one_busy_answer(two):
    c = two

    async def go():
        assert c.stick_job() == "" and not c._busy()
        async with c._lock:
            assert c.stick_job().startswith("lock:") and c._busy()
        c._set_hidden("youtubetv:fox", True)
        task = c._slot_start("rescrub", "youtubetv:fox", None, asyncio.sleep(0.01))
        assert c.stick_job() == "rescrub" and c._busy()
        await task
        await asyncio.sleep(0)
        assert c.stick_job() == "" and c.slot_m.is_("idle")
    asyncio.run(go())


def test_illegal_standby_move_fails_loudly_under_strict(two):
    c = two
    assert fsm.STRICT
    with pytest.raises(fsm.IllegalTransition):
        c.standby_m.to("snap_parked")                        # empty -> snap_parked isn't declared


# --- 2. the screen -----------------------------------------------------------------------------------------

def test_fix_screen_fallback_keeps_the_watchdog_off_the_stick_the_tv_isnt_showing(two):
    c = two
    c.on_target = "youtubetv:espn"
    reads = []
    other = c.sticks[1]
    real = other.playback

    async def spy(package=None):
        reads.append(package)
        return await real(package)
    other.playback = spy
    c.sticks[0].state = "offline"                    # the stick on screen dropped off adb
    assert c.main is other                           # `main` falls back to the other one...
    asyncio.run(c._watch_visible())
    assert c.screen_m.is_("fallback") and reads == []   # ...which the watchdog leaves alone
    assert asyncio.run(c.restore_visible("youtubetv:espn")) is None
    c.sticks[0].state = "connected"
    assert not c._roles_check() and c.screen_m.is_("known")


def test_fix_undo_steal_goes_through_set_hidden(two, cfg):
    c = two
    cfg.update({"hardening": {"tv": True}})
    back, now_on = c.sticks
    t = "youtubetv:espn"

    async def go():
        back.remember(t, await back.playback("yttv.pkg"))
        now_on.playback_state = "stopped"                # the stick that grabbed the TV shows its home screen
        c.active = 1
        c._set_hidden("youtubetv:fox", True)             # what we thought the hidden stick held
        await c._undo_steal(back, t, now_on)
    asyncio.run(go())
    assert c.main is back and c.on_target == t
    assert c.hidden is now_on and c.hidden_target is None and c.standby_m.is_("empty")
    assert c.screen_m.is_("known")


def test_fix_every_flip_bumps_the_flip_generation(two, monkeypatch):
    c = two
    for s in c.sticks:
        s.cec_ok = False

    async def no_tv(hdmi, methods=None):
        return False
    monkeypatch.setattr(c.tv, "set_input", no_tv)
    gen = c._flip_gen
    out = asyncio.run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert not out.ok
    assert c._flip_gen == gen + 1                    # a failed flip is a flip: reads that spanned it are stale
    assert c.screen_m.is_("unknown") and c.input_unknown and not c._flipping


def test_flipping_and_input_unknown_are_one_machine(two):
    c = two
    c._flipping = True
    assert c.screen_m.is_("flipping") and not c.input_unknown
    c.input_unknown = True
    assert c.screen_m.is_("reasserting") and c._flipping
    c._flipping = False
    assert c.screen_m.is_("unknown")
    c.input_unknown = False
    assert c.screen_m.is_("known")


# --- 3. behind-live offsets ---------------------------------------------------------------------------------

def test_fix_a_restored_offset_is_not_re_scrubbed_for_want_of_a_stick_anchor(two):
    c = two
    buffered(c)
    c.cfg.update({"timing": {"behind_live_s": {"scrub_entry": True}}})
    t = "youtubetv:fox"
    (c.buffer_log_path.parent / "held.json").write_text(json.dumps({"ts": time.time(), "held": {t: 70.0}}))
    assert c.restore_held() == {t: 70.0}
    assert c.offset_m.state(t) == "restored"
    c._set_hidden(t, True)                    # the hidden stick still plays it (no tune since the restart)
    assert c.hidden.scrubbed(t) is None        # ...but nothing anchors it
    assert not c.buffer_standby(t) and c.bj_buffer_why == "already_held"
    assert c.held[t] == 70.0


def test_offsets_follow_held_and_backoffs_are_declared(two):
    c = two
    assert "_replay_scrub_backoff" in vars(c) and "_tuned_at" in vars(c)
    c._set_held("youtubetv:fox", 30.0, how="scrubbed")
    assert c.offset_m.state("youtubetv:fox") == "scrubbed"
    c._set_held("youtubetv:fox", 32.0)                      # a stall on top keeps the kind
    assert c.offset_m.state("youtubetv:fox") == "scrubbed"
    c._set_held("youtubetv:fox", 0.0)
    assert c.offset_m.state("youtubetv:fox") == "live"


# --- 4. the visible watchdog -------------------------------------------------------------------------------

def test_fix_a_pending_recheck_never_blocks_a_restore(two):
    c = two
    c.on_target = "youtubetv:espn"
    c.main.remember("youtubetv:espn", {"state": "playing", "pkg": "yttv.pkg", "position": 1000, "updated": 1000})

    async def go():
        c._schedule_visible_check(30.0)           # a sleep-timer re-check is pending
        pending = c._resume_task
        how = await c.restore_visible("youtubetv:espn", why="supervisor")
        return how, pending
    how, pending = asyncio.run(go())
    assert how is not None and pending.cancelled()
    assert c.visible_m.is_("ok")


def test_fix_visible_how_resets_when_healthy(two):
    c = two
    c.on_target = "youtubetv:espn"
    main = c.main

    async def go():
        main.playback_state = "paused"
        await c._watch_visible()
        assert c._visible_state == "paused" and c.visible_m.is_("bad")
        main.playback_state = "playing"
        await c._watch_visible()
        c._resume_task.cancel()
    asyncio.run(go())
    assert c._visible_state == "" and c._visible_bad_since is None and c.visible_m.is_("ok")


# --- 5. the switch command ---------------------------------------------------------------------------------

def test_fix_entry_rewind_fields_end_with_their_switch(cfg, tmp_path):
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))       # dry run
    c.sticks = [FakeStick("a:5555")]
    out = asyncio.run(c.execute("youtubetv:espn", entry_rewind_s=30.0, entry_cue="hidden"))
    assert out.ok and c._entry_cue == "" and c._hidden_entry_done is False
    c.arm_entry_rewind(30)                     # a may_cut that outlived its switch
    assert c._hidden_entry == 0.0              # ...doesn't arm a hidden seek from that switch's cue


def test_command_lifecycle_and_lock_owner_are_visible(two):
    c = two
    seen = {}

    async def go():
        async def holder():
            async with c._lock:
                seen["lock"] = c.machines_view()["command"]["lock"]
        await asyncio.create_task(holder(), name="stuck-holder")
        c.submit("youtubetv:espn", may_cut=lambda: True)
        seen["queued"] = c.command_m.state
        await c._task
    asyncio.run(go())
    assert seen["lock"]["locked"] and seen["lock"]["owner"] == "stuck-holder"
    assert seen["queued"] == "queued" and c.command_m.is_("idle")
    assert [h[2] for h in c.command_m.history][:2] == ["queued", "running"]


def test_a_command_cancelled_before_it_ran_ends_idle(two):
    c = two

    async def go():
        c.submit("youtubetv:espn", may_cut=lambda: True)
        c.cancel_pending()                       # Pause before the switch task ever started
        await asyncio.gather(c._task, return_exceptions=True)
    asyncio.run(go())
    assert c.command_m.is_("idle")


# --- 6/7. restart adopt, two-stick degrade -----------------------------------------------------------------

def test_adopt_once_then_never_after_a_replay(two):
    c = two
    assert c._adopt_once and c.adopt_m.is_("fresh")
    c._adopt_once = False
    c._replayed = True
    assert c.adopt_m.is_("replayed") and not c._adopt_once
    with pytest.raises(fsm.IllegalTransition):
        c.adopt_m.to("fresh")


def test_two_stick_degrade_is_a_named_state(two, monkeypatch):
    c = two
    for s in c.sticks:
        s.cec_ok = False

    async def no_tv(hdmi, methods=None):
        return False
    monkeypatch.setattr(c.tv, "set_input", no_tv)
    asyncio.run(c.execute("youtubetv:espn", may_cut=lambda: True))
    assert c.two_stick_m.is_("degraded") and c.two_stick_why() == "degraded" and c.hidden is None
    assert "machines" in c.switch_times() and c.switch_times()["machines"]["two_stick"]["why_off"] == "degraded"


def test_cec_book_drives_the_cec_machines(two):
    c = two
    c._cec_on = {"a:5555": False}
    assert c.cec_m.state("a:5555") == "off" and c.cec_off(c.sticks[0])
    c._cec_on["a:5555"] = True
    assert c.cec_m.state("a:5555") == "on"


# --- 8. the TV ------------------------------------------------------------------------------------------------

async def test_fix_auth_check_is_pure_and_the_rearm_is_explicit(tmp_path):
    from tests.test_st_hardening import make
    from redzone import tv as tvmod
    tv, cloud, clock = make(tmp_path)
    tv._auth_down("refused")
    assert tv.st_m.is_("auth") and tv.cloud_blocked()
    clock.t += tvmod.AUTH_RETRY_S + 1
    tv.auth._d = {"cached": True}
    assert tv.cloud_blocked() == "" and tv.cloud_blocked() == ""
    assert tv.st_m.is_("auth") and tv.auth._d == {"cached": True}     # a check never changes state
    tv._auth_rearm()
    assert tv.st_m.is_("auth_retry") and tv.st_fault == "auth" and tv.auth._d is None


async def test_rate_limit_is_a_machine_state(tmp_path):
    from tests.test_st_hardening import make
    tv, cloud, clock = make(tmp_path)
    cloud.status, cloud.retry_after = 429, "60"
    await tv.input_status()
    assert tv.st_m.is_("rate_limited") and tv.st_breaker.retry_in() == pytest.approx(60)


# --- 9. tuners --------------------------------------------------------------------------------------------------

def test_stick_link_machine():
    s = Stick("h:5555")
    assert s.state == "unknown" and s.link_m.is_("unknown")
    s.state = "connected"
    assert s.link_m.is_("connected") and s.controllable
    with pytest.raises(ValueError):
        s.state = "sideways"


async def test_adb_shell_machine():
    from redzone.tuners.adb import AdbShell
    sh = AdbShell("h:5555")
    assert sh.m.is_("closed")
    sh._broken("no adb")
    assert sh.m.is_("broken") and sh.view()["failures"] == 1


def test_prime_tuner_per_host_state_is_declared():
    from redzone.tuners.tuners import PrimeMultiviewTuner, PrimeTuner
    for t in (PrimeTuner(), PrimeMultiviewTuner()):
        assert t._before_player == {} and t._link_at == {} and not t._pressed("h") and not t._saw_page("h")
        t.tune_m["h"].to("linked", why="test")
        t.tune_m["h"].to("page", why="test")
        assert t._saw_page("h") and not t._pressed("h")


def test_yttv_search_stages_only_move_forward():
    from redzone.tuners.yttv_search import YTTVSearchProof
    p = YTTVSearchProof()
    p.last_completed_stage = "search_opened"
    p.last_completed_stage = "replay_start_sent"
    assert p.stage_m.is_("replay_start_sent")
    with pytest.raises(fsm.IllegalTransition):
        p.last_completed_stage = "search_opened"
