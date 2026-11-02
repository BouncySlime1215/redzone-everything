"""Recorded navigation cannot establish live game state or race the live producer."""
import asyncio
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from redzone.engine import Engine
from redzone.config import Config
from redzone.control import Controller
from redzone.recorded import RecordedPlayback
from redzone.web.server import build_app


def setup(cfg, runner):
    stick = NS(host="selected:5555", packages={"prime": "prime.package"}, os="Fire OS", model="test-model",
               state="connected", connect_error="", connect=AsyncMock(return_value=True),
               shell=AsyncMock(return_value=NS(ok=True, out="test-model\n")))
    control = NS(sticks=[stick], usable=[stick], _lock=asyncio.Lock(), _flipping=False, _task=None,
                 _preload_task=None, cancel_pending=Mock(), _set_hidden=Mock(),
                 on_target="prime:old-live", last=object())
    dec = NS(user=NS(paused=False), abort_replay=Mock(), current="old-game", _screen_log=[(1, "old-game")])
    engine = NS(cfg=cfg, control=control, decider=dec, _pending_decision=object(),
                _house_device_task=None, mic_suggestion=object(), broadcast=Mock(),
                discovery=NS(suspend_device_work=AsyncMock(), resume_device_work=Mock()))
    engine.recorded = RecordedPlayback(engine, runner=runner, prepare_ocr=lambda: NS(status="ready"))
    body = {"execute": True, "enable_device_control": True, "provider": "prime",
            "host": stick.host, "query": "Rockets", "home": "Rockets", "away": "Warriors",
            "date": date.today().isoformat()}
    return engine, body


async def observed(stick, observe, *, proof, may_run, **kw):
    assert may_run()
    proof.status = "player_title_observed"
    proof.event_date_verified = True
    proof.last_completed_stage = "player_title_observed"


async def test_atomic_recorded_start_isolated_from_live_success_and_stop_is_safe(cfg):
    engine, body = setup(cfg, observed)
    ok, _ = await engine.recorded.start(body)
    assert ok
    assert engine.recorded.active and engine.decider.user.paused
    await engine.recorded.task
    assert not cfg.get("mode.dry_run")
    assert engine.decider.current is None and engine.control.on_target is None
    assert engine._pending_decision is None and engine.control.last is None
    status = engine.recorded.snapshot()
    assert status["identity"] == "player_title_observed"
    assert not status["video_verified"] and not status["live_delay_applicable"]
    assert engine.recorded.active  # completed navigation does not restart live production
    await engine.recorded.stop()
    assert cfg.get("mode.dry_run") and engine.decider.user.paused
    assert not engine.recorded.active
    engine.discovery.resume_device_work.assert_called_once()


@pytest.mark.parametrize("change", [
    {"execute": False}, {"execute": "true"}, {"enable_device_control": False},
    {"host": "another-device:5555"}, {"provider": "youtubetv"},
    {"date": "yesterday"}, {"home": "GSW"}, {"away": "Rockets"},
])
async def test_missing_context_or_authorization_never_starts_device_work(cfg, change):
    runner = AsyncMock()
    engine, body = setup(cfg, runner)
    ok, _ = await engine.recorded.start({**body, **change})
    assert not ok and not engine.recorded.active and cfg.get("mode.dry_run")
    runner.assert_not_called()
    engine.discovery.suspend_device_work.assert_not_called()


async def test_service_policy_is_visible_and_rejects_before_session_changes(cfg):
    engine, body = setup(cfg, AsyncMock())
    cfg.allow_live = False
    assert engine.recorded.snapshot()["device_control_allowed"] is False
    ok, reason = await engine.recorded.start(body)
    assert not ok and "enforces dry-run" in reason
    assert not engine.recorded.active and not engine.decider.user.paused
    engine.control.cancel_pending.assert_not_called()
    engine.discovery.suspend_device_work.assert_not_called()


async def test_active_live_production_or_unknown_inflight_operation_is_not_interrupted(cfg):
    engine, body = setup(cfg, AsyncMock())
    cfg.update({"mode": {"dry_run": False}})
    assert not (await engine.recorded.start(body))[0]
    engine.decider.user.paused = True
    await engine.control._lock.acquire()
    try:
        assert not (await engine.recorded.start(body))[0]
    finally:
        engine.control._lock.release()
    assert not engine.recorded.active


async def test_pending_housekeeping_is_awaited_before_recorded_mutation(cfg):
    canceled = asyncio.Event()
    started = asyncio.Event()

    async def house():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            canceled.set()

    async def runner(*args, **kw):
        assert canceled.is_set()
        assert engine.discovery.suspend_device_work.await_count == 1
        await observed(*args, **kw)

    engine, body = setup(cfg, runner)
    engine._house_device_task = asyncio.create_task(house())
    await started.wait()
    assert (await engine.recorded.start(body))[0]
    await engine.recorded.task
    await engine.recorded.stop()


async def test_stop_waits_for_navigation_cancellation_and_does_not_resume_live(cfg):
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def runner(*args, **kw):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    engine, body = setup(cfg, runner)
    await engine.recorded.start(body)
    await entered.wait()
    await engine.recorded.stop()
    assert cleaned.is_set() and not engine.control._lock.locked()
    assert engine.decider.current is None and engine.decider.user.paused
    assert cfg.get("mode.dry_run")


async def test_runtime_enforced_dry_run_fails_without_any_navigation(cfg, monkeypatch):
    runner = AsyncMock()
    engine, body = setup(cfg, runner)

    def reject(_):
        assert engine.recorded.active and engine.decider.user.paused
        raise ValueError("preview runtime enforces dry run")

    monkeypatch.setattr(cfg, "update", reject)
    ok, _ = await engine.recorded.start(body)
    assert ok
    await engine.recorded.task
    assert "enforces dry run" in engine.recorded.proof.reason
    assert cfg.get("mode.dry_run") and engine.decider.user.paused
    runner.assert_not_called()
    engine.control.sticks[0].connect.assert_not_called()


async def test_engine_blocks_live_actions_and_live_evidence_while_recorded(cfg):
    engine, body = setup(cfg, observed)
    await engine.recorded.start(body)
    for action in ("watch", "replay", "resume", "autopilot", "saw_it", "scan_st"):
        assert not Engine.action(engine, action)[0]
    assert Engine.decide(engine) is False
    evidence = Engine.screen_evidence(engine)
    assert evidence["mode"] == "recorded" and evidence["game_id"] is None
    assert evidence["delay_s"] is None and not evidence["safe_to_leave"]
    await engine.recorded.stop()


async def test_api_uses_existing_lan_origin_guard_and_explicit_recorded_contract(cfg):
    engine, body = setup(cfg, observed)
    app = build_app(engine)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        response = await client.post("/api/recorded/start", json=body, headers={"origin": "https://example.com"})
        assert response.status_code == 403 and not engine.recorded.active
        response = await client.post("/api/recorded/start", json=body)
        assert response.json()["ok"]
        response = await client.post("/api/recorded/stop")
        assert response.json()["ok"] and cfg.get("mode.dry_run")


async def test_process_restart_after_recorded_session_cannot_boot_live_autopilot(cfg):
    engine, body = setup(cfg, observed)
    assert (await engine.recorded.start(body))[0]
    await engine.recorded.task
    assert not cfg.get("mode.dry_run")
    reloaded = Config(user_path=cfg.user_path, history_dir=cfg.history_dir)
    new_engine, _ = setup(reloaded, observed)
    assert reloaded.get("mode.dry_run") and new_engine.decider.user.paused
    assert not new_engine.recorded.active
    assert Config(user_path=cfg.user_path, history_dir=cfg.history_dir).get("mode.dry_run")
    await engine.recorded.stop()


async def test_local_ocr_preparation_precedes_every_device_action(cfg):
    prepared = False

    def prepare():
        nonlocal prepared
        prepared = True
        return NS(status="ready")

    async def runner(*args, **kw):
        assert prepared
        await observed(*args, **kw)

    engine, body = setup(cfg, runner)
    engine.recorded.prepare_ocr = prepare
    await engine.recorded.start(body)
    await engine.recorded.task
    assert prepared and engine.recorded.proof.status == "player_title_observed"
    await engine.recorded.stop()


async def test_ocr_prepare_failure_does_not_capture_or_navigate_and_keeps_isolation(cfg):
    runner, capture = AsyncMock(), AsyncMock()
    engine, body = setup(cfg, runner)
    engine.recorded.capture = capture
    engine.recorded.prepare_ocr = lambda: NS(status="timeout", error="local OCR helper compilation timed out")
    await engine.recorded.start(body)
    await engine.recorded.task
    runner.assert_not_called()
    capture.assert_not_called()
    assert "compilation timed out" in engine.recorded.proof.reason
    assert engine.recorded.active and engine.decider.user.paused
    assert engine.decider.current is None
    await engine.recorded.stop()


async def test_cold_start_checks_only_selected_saved_stick_after_prewarm_and_permission(cfg, monkeypatch):
    engine, body = setup(cfg, observed)
    selected = engine.control.sticks[0]
    selected.state = "unknown"
    engine.control.usable = []
    other = NS(host="other:5555", connect=AsyncMock(), shell=AsyncMock())
    engine.control.sticks.append(other)
    order = []

    def prepare():
        order.append("prewarm")
        assert cfg.get("mode.dry_run")
        return NS(status="ready")

    update = cfg.update

    def permission(changes, **kwargs):
        if changes.get("mode", {}).get("dry_run") is False:
            order.append("permission")
            assert engine.recorded.active and engine.decider.user.paused
        return update(changes, **kwargs)

    async def connect():
        order.append("selected-connect")
        assert not cfg.get("mode.dry_run")
        selected.state = "connected"
        engine.control.usable = [selected]
        return True

    selected.connect = AsyncMock(side_effect=connect)
    engine.recorded.prepare_ocr = prepare
    monkeypatch.setattr(cfg, "update", permission)
    assert (await engine.recorded.start(body))[0]
    await engine.recorded.task
    assert order == ["prewarm", "permission", "selected-connect"]
    assert engine.recorded.proof.status == "player_title_observed"
    selected.shell.assert_awaited_once_with("getprop ro.product.model", timeout=5)
    other.connect.assert_not_called()
    other.shell.assert_not_called()
    await engine.recorded.stop()


@pytest.mark.parametrize("state", ["offline", "unauthorized"])
async def test_selected_reconnect_failure_cannot_try_another_device_or_navigate(cfg, state):
    runner = AsyncMock()
    engine, body = setup(cfg, runner)
    selected = engine.control.sticks[0]
    selected.state, selected.connect_error = state, "connection refused or authorization needed"
    selected.connect = AsyncMock(return_value=False)
    engine.control.usable = []
    other = NS(host="other:5555", connect=AsyncMock())
    engine.control.sticks.append(other)
    assert (await engine.recorded.start(body))[0]
    await engine.recorded.task
    assert state in engine.recorded.proof.reason
    selected.shell.assert_not_called()
    other.connect.assert_not_called()
    runner.assert_not_called()
    assert engine.recorded.active and engine.decider.user.paused
    await engine.recorded.stop()


async def test_selected_address_model_mismatch_blocks_playback(cfg):
    runner = AsyncMock()
    engine, body = setup(cfg, runner)
    engine.control.sticks[0].shell = AsyncMock(return_value=NS(ok=True, out="different-model"))
    assert (await engine.recorded.start(body))[0]
    await engine.recorded.task
    assert "saved Fire TV model" in engine.recorded.proof.reason
    runner.assert_not_called()
    await engine.recorded.stop()


async def test_saved_vega_is_not_reconnected_with_adb(cfg):
    engine, body = setup(cfg, AsyncMock())
    engine.control.sticks[0].os = "Vega OS"
    assert not (await engine.recorded.start(body))[0]
    engine.control.sticks[0].connect.assert_not_called()


@pytest.mark.parametrize("cancel_parent", [True, False])
async def test_keepalive_distinguishes_recorded_handoff_from_service_shutdown(cfg, cancel_parent):
    engine, _ = setup(cfg, observed)
    entered = asyncio.Event()

    async def keepalive():
        entered.set()
        await asyncio.Event().wait()

    engine.control.keepalive = keepalive
    task = asyncio.create_task(Engine._device_keepalive(engine))
    await entered.wait()
    engine.recorded.active = True
    if cancel_parent:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        engine._house_device_task.cancel()
        await task  # recorded mode cancels only child work; the housekeeping loop survives
    assert engine._house_device_task is None


async def test_turning_device_control_off_cancels_pending_recorded_start(cfg):
    engine, body = setup(cfg, observed)
    assert (await engine.recorded.start(body))[0]
    # The task has been queued but has not prepared OCR or enabled controls yet.
    engine.recorded.cancel_navigation()
    await asyncio.gather(engine.recorded.task, return_exceptions=True)
    assert cfg.get("mode.dry_run")
    engine.control.sticks[0].connect.assert_not_called()
    await engine.recorded.stop()


async def test_queued_manual_channel_scan_is_canceled_before_recorded_runner(cfg):
    engine, body = setup(cfg, observed)
    cfg.update({"mode": {"dry_run": False}})
    engine.decider.user.paused = True
    engine._st_scan_task = None
    engine.channels = NS(st_codes=["channel-code"], st_slots={}, save=Mock())
    engine.merger = NS(games={})
    engine.toast = Mock()
    engine.control.live_control, engine.control.two_stick = True, False
    engine.control.scan_sunday_ticket = AsyncMock(return_value={})
    assert Engine._start_st_scan(engine)[0]
    queued_scan = engine._st_scan_task
    # No event-loop yield between scheduling a scan and entering recorded mode.
    assert (await engine.recorded.start(body))[0]
    assert queued_scan.done() and queued_scan.cancelled()
    await engine.recorded.task
    engine.control.scan_sunday_ticket.assert_not_called()
    assert not Engine._start_st_scan(engine)[0]
    await engine.recorded.stop()


async def test_channel_scan_rechecks_permission_after_waiting_for_controller_lock(cfg, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    stick = NS(key=AsyncMock(), screen_text=AsyncMock())
    controller = NS(hidden=None, main=stick, cfg=cfg, _lock=asyncio.Lock(), on_target="old", input_unknown=False)
    tune = AsyncMock(return_value=NS(ok=True))
    monkeypatch.setitem(__import__("redzone.control", fromlist=["TUNERS"]).TUNERS,
                        "youtubetv", NS(tune=tune))
    allowed = True
    await controller._lock.acquire()
    task = asyncio.create_task(Controller.scan_sunday_ticket(controller, ["code"], [], may_run=lambda: allowed))
    await asyncio.sleep(0)
    allowed = False  # recorded mode took ownership while scan waited for the lock
    controller._lock.release()
    assert await task == {}
    tune.assert_not_called()
    stick.key.assert_not_called()


async def test_channel_scan_cannot_press_overlay_key_after_authorization_changes(cfg, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    stick = NS(key=AsyncMock(), screen_text=AsyncMock())
    controller = NS(hidden=None, main=stick, cfg=cfg, _lock=asyncio.Lock(), on_target="old", input_unknown=False)
    allowed = True

    async def tune(*args, may_cut):
        nonlocal allowed
        assert may_cut()
        allowed = False
        return NS(ok=True)

    monkeypatch.setitem(__import__("redzone.control", fromlist=["TUNERS"]).TUNERS,
                        "youtubetv", NS(tune=tune))
    assert await Controller.scan_sunday_ticket(controller, ["code"], [], may_run=lambda: allowed) == {}
    stick.key.assert_not_called()
    stick.screen_text.assert_not_called()
    assert controller.on_target is None


async def test_engine_shutdown_drains_manual_scan_before_closing_controller(cfg, tmp_path):
    from redzone.store import Store
    engine = Engine(cfg, Store(tmp_path / "scan-shutdown.db"))
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def scan():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    async def close_control():
        assert cleaned.is_set()

    engine.control.close = close_control
    engine._st_scan_task = asyncio.create_task(scan())
    await entered.wait()
    await engine.close()
    assert engine._st_scan_task.cancelled()


async def test_selected_stick_receives_configured_profile_before_recorded_runner(cfg):
    cfg.update({"providers": {"profiles": {"prime": "Taylor", "youtubetv": "Morgan"}}})

    async def runner(stick, *args, **kwargs):
        assert stick.preferred_profiles["prime"] == "Taylor"
        assert stick.preferred_profiles["youtubetv"] == "Morgan"
        await observed(stick, *args, **kwargs)

    engine, body = setup(cfg, runner)
    assert (await engine.recorded.start(body))[0]
    await engine.recorded.task
    assert engine.recorded.proof.status == "player_title_observed"
    await engine.recorded.stop()
