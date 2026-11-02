"""Regression checks for recovery: a wrong channel/device must never become confirmed."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from redzone.autonomy import Autonomy, DeviceHealer, Hooks, Job, JobResult, Paths, Scheduler, quiet_plan, same_tv
from redzone.channels import ChannelMap
from redzone.discovery.stickio import Evidence, parse_media_sessions, read_evidence
from redzone.discovery.search import RecipeBlocked, run_step
from redzone.discovery.yttv import (Candidate, Entry, Table, Verdict, VerifyState, apply_verified,
                                    judge, sunday_ticket_codes, verify_codes)
from redzone.tuners.tuners import PrimeTuner, YouTubeTVTuner


def channel_table():
    return Table({
        "espn": Entry("espn", "ESPN", aliases=["ESPN"], candidates=[Candidate("AAAAAAAAAAA")]),
        "fox": Entry("fox", "FOX", aliases=["FOX"], candidates=[Candidate("BBBBBBBBBBB")]),
        "st1": Entry("st1", "Sunday Ticket 1", kind="sunday_ticket", slot=1,
                     candidates=[Candidate("CCCCCCCCCCC")]),
    }, {}, [])


def test_saved_tv_summary_is_honest_without_device_probe(tmp_path):
    path = tmp_path / "devices.json"
    path.write_text(json.dumps({"tv": {"ip": "192.0.2.10"}}))
    stick = SimpleNamespace(host="selected:5555", state="unknown")
    hooks = SimpleNamespace(controller=SimpleNamespace(sticks=[stick]))
    net = SimpleNamespace(samsung=AsyncMock())
    healer = DeviceHealer(hooks, path, net)
    assert "TV saved; connection not checked" in healer.summary()[1]
    net.samsung.assert_not_called()
    healer.tv_checked = 1
    assert "TV saved; not answering" in healer.summary()[1]
    path.write_text(json.dumps({"tv": {}}))
    assert "no TV saved yet" in healer.summary()[1]


def test_unidentified_playback_does_not_become_a_tunable_channel(tmp_path):
    table = channel_table()
    state = VerifyState(tmp_path / "verify.json")
    cm = ChannelMap(tmp_path / "channels.json")
    state.set_channel("espn", "AAAAAAAAAAA", "plays", 100, "name not shown")
    assert state.code_for("espn") is None
    assert apply_verified(cm, state, table) == []
    assert cm.codes == {} and cm.st_codes == []
    assert sunday_ticket_codes(table, state) == []


def test_verified_code_is_used_but_owner_correction_is_preserved(tmp_path):
    table = channel_table()
    state = VerifyState(tmp_path / "verify.json")
    state.set_channel("espn", "AAAAAAAAAAA", "verified", 100)
    cm = ChannelMap(tmp_path / "channels.json")
    apply_verified(cm, state, table)
    assert cm.codes["espn"] == "AAAAAAAAAAA"
    cm.codes["espn"] = "OWNERCODE12"
    assert apply_verified(cm, state, table) == []
    assert cm.codes["espn"] == "OWNERCODE12"


def test_old_title_and_ambiguous_title_never_verify_a_new_link():
    table = channel_table()
    old = Evidence(playing=True, state="playing", title="ESPN")
    assert judge(table.channels["espn"], old, table, before=old).status == "plays"
    # A longer alias must not be invented as exact game identity here.
    wrong = Evidence(playing=True, state="playing", title="FOX")
    assert judge(table.channels["espn"], wrong, table).status == "wrong_channel"
    assert judge(table.channels["espn"], Evidence(read_ok=False), table).status == "unknown"


def test_modern_android_playback_format_is_read():
    sessions = parse_media_sessions("package=com.amazon.firetv.youtube.tv\n"
                                    "state=PlaybackState {state=PLAYING(3), position=100\n"
                                    "metadata: size=3, description=ESPN, SportsCenter, Live")
    assert sessions[0].state == "playing" and sessions[0].title == "ESPN"


async def test_stationary_playing_session_cannot_verify_channel(tmp_path):
    stick = SimpleNamespace(shell=AsyncMock(return_value=SimpleNamespace(ok=True, out=(
        "package=com.amazon.firetv.youtube.tv\n"
        "state=PlaybackState {state=3, position=0, speed=0.0, updated=123}\n"
        "metadata: size=3, description=ESPN, SportsCenter, Live"))))
    evidence = await read_evidence(stick, with_screen=False)
    assert evidence.state == "playing" and not evidence.playing
    assert judge(channel_table().channels["espn"], evidence, channel_table()).status != "verified"


async def test_verification_rechecks_ownership_after_waiting_for_controller(tmp_path):
    lock = asyncio.Lock()
    await lock.acquire()
    available = True
    stick = SimpleNamespace(view=AsyncMock(), shell=AsyncMock(), key=AsyncMock())
    task = asyncio.create_task(verify_codes(stick, channel_table(), VerifyState(tmp_path / "v.json"),
                                           keys=["espn"], lock=lock, may_run=lambda: available))
    await asyncio.sleep(0)
    available = False
    lock.release()
    result = await task
    assert result.stopped == "stick needed"
    stick.view.assert_not_awaited()
    stick.shell.assert_not_awaited()
    stick.key.assert_not_awaited()


async def test_dry_run_does_not_probe_pair_scan_or_run_maintenance(cfg, tmp_path):
    c = SimpleNamespace(sticks=[], usable=[])
    a = Autonomy(Hooks(c, ChannelMap(tmp_path / "c.json"), cfg=cfg), paths=Paths.under(tmp_path))
    a._setup_if_needed = AsyncMock()
    a.healer.check = AsyncMock()
    a.scheduler.tick = AsyncMock()
    await a.step()
    for action in (a._setup_if_needed, a.healer.check, a.scheduler.tick):
        action.assert_not_awaited()
    result = await a.run_now("selftest", force=True)
    assert result.ok is None and "Dry run" in result.message


async def test_first_run_waits_for_owner_to_select_devices(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    a = Autonomy(Hooks(SimpleNamespace(sticks=[], usable=[]), ChannelMap(tmp_path / "c.json"), cfg=cfg),
                 paths=Paths.under(tmp_path))
    a._discover_all = AsyncMock()
    await a._setup_if_needed()
    a._discover_all.assert_not_awaited()
    assert "select your devices" in a.steps[0]


async def test_unresponsive_tv_is_unknown_not_powered_off(cfg, tmp_path):
    a = Autonomy(Hooks(SimpleNamespace(sticks=[], usable=[]), ChannelMap(tmp_path / "c.json"), cfg=cfg),
                 paths=Paths.under(tmp_path))
    a.healer.tv_power_now = AsyncMock(return_value=(True, None))
    assert not await a._tv_off()
    assert quiet_plan(False, None, True, True)[0] == "skip"
    assert quiet_plan(False, False, None, True)[0] == "light"
    assert quiet_plan(False, False, False, True)[0] == "light"


def test_same_model_does_not_authorize_following_another_tv():
    assert not same_tv({"ip": "192.168.1.2", "model": "Samsung"},
                       {"ip": "192.168.1.3", "model": "Samsung"})
    assert same_tv({"ip": "192.168.1.2", "wifi_mac": "aa:bb:cc:dd:ee:ff"},
                   {"ip": "192.168.1.3", "wifi_mac": "aa:bb:cc:dd:ee:ff"})


async def test_force_run_cannot_bypass_live_game_guard(tmp_path):
    work = AsyncMock(return_value=JobResult(True, "ran"))
    guard = AsyncMock(return_value="live game on screen")
    scheduler = Scheduler([Job("scan", "Scan", work, guard=guard)], tmp_path / "log.jsonl")
    result = await scheduler.run_now("scan", force=True)
    assert "live game" in result.message
    work.assert_not_awaited()


async def test_builtin_tuners_never_press_through_account_or_purchase_screen():
    for tuner in (YouTubeTVTuner(), PrimeTuner()):
        for text in ("Watch Live | Start your free trial", "Watch Live | Rent $4.99", "Sign in | Watch Live"):
            stick = SimpleNamespace(key=AsyncMock())
            assert not await tuner._press_prompt(stick, text, [], "test")
            stick.key.assert_not_awaited()


async def test_sunday_scan_can_inspect_candidates_without_publishing_unverified_channels(cfg, tmp_path):
    ch = ChannelMap(tmp_path / "c.json")
    a = Autonomy(Hooks(SimpleNamespace(sticks=[], usable=[]), ch, cfg=cfg), paths=Paths.under(tmp_path))
    assert a._st_candidates()
    assert ch.st_codes == [] and ch.st_slots == {}
    ch.st_codes = ["OWNERCODE12"]
    assert a._st_candidates() == ["OWNERCODE12"]


async def test_search_recipe_stops_before_selecting_an_unreadable_or_account_screen():
    import pytest
    for text in ("", "Start your free trial", "Watch Live | Rent $4.99", "Sign in"):
        stick = SimpleNamespace(screen_text=AsyncMock(return_value=text), key=AsyncMock())
        with pytest.raises(RecipeBlocked):
            await run_step(stick, {"keys": ["KEYCODE_DPAD_CENTER"]}, channel_table().channels["espn"],
                           "test.pkg", AsyncMock(), [])
        stick.key.assert_not_awaited()
