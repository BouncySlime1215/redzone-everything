"""Observe the target before committing a switch; transport success is insufficient."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from redzone.control import Outcome
from redzone.engine import Engine
from redzone.scoring.engine import Personal, Scorer
from redzone.store import Store
from redzone.tv import SamsungTV
from conftest import nfl


def cloud_tv(device_id):
    return {"deviceId": device_id, "components": [{"capabilities": [{"id": "mediaInputSource"}]}]}


async def test_cloud_discovery_requires_explicit_target_even_if_only_one_tv():
    tv = SamsungTV()
    tv._st = AsyncMock(return_value={"items": [cloud_tv("xbox-tv")]})
    assert not await tv.st_discover()
    assert tv.st_device is None
    tv._st.assert_not_awaited()


async def test_cloud_discovery_reads_only_selected_tv_not_first_tv():
    tv = SamsungTV(st_device="sports-tv")
    tv._st = AsyncMock(side_effect=[{"items": [cloud_tv("xbox-tv"), cloud_tv("sports-tv")]},
                                   {"supportedInputSources": {"value": ["HDMI1", "HDMI2"]}}])
    assert await tv.st_discover()
    assert tv.st_device == "sports-tv" and tv.st_inputs == ["HDMI1", "HDMI2"]
    assert tv._st.await_args_list[1].args[1].startswith("/devices/sports-tv/")


async def test_missing_selected_tv_never_falls_back_to_other_tv():
    tv = SamsungTV(st_device="sports-tv")
    tv._st = AsyncMock(return_value={"items": [cloud_tv("xbox-tv")]})
    assert not await tv.st_discover()
    assert tv.st_device == "sports-tv" and tv.st_capability is None
    assert tv._st.await_count == 1


async def test_local_key_ack_is_not_input_confirmation_or_cloud_retry():
    tv = SamsungTV(host="192.168.1.11")
    tv.key = AsyncMock(return_value=True)
    tv.st_input = AsyncMock(return_value=True)
    assert not await tv.set_input("HDMI2")
    assert tv.last_input.accepted is True and not tv.last_input.verified
    assert tv.last_input.observed is None
    tv.st_input.assert_not_awaited()


async def test_lost_local_reply_has_unknown_delivery_not_cloud_retry():
    tv = SamsungTV(host="192.168.1.11", st_device="sports-tv")
    tv.key = AsyncMock(return_value=False)
    tv.input_status = AsyncMock(return_value=None)
    tv.st_input = AsyncMock(return_value=True)
    assert not await tv.set_input("HDMI2")
    assert tv.last_input.accepted is None and not tv.last_input.verified
    tv.st_input.assert_not_awaited()


async def test_reported_selected_tv_input_can_confirm_local_command():
    tv = SamsungTV(host="192.168.1.11", st_device="sports-tv")
    tv.key = AsyncMock(return_value=True)
    tv.input_status = AsyncMock(side_effect=["HDMI1", "HDMI2"])
    assert await tv.set_input("HDMI2")
    assert tv.last_input.verified and tv.last_input.observed == "HDMI2"


async def test_smartthings_command_ack_is_not_input_confirmation():
    tv = SamsungTV(method="smartthings", st_device="sports-tv", st_capability="mediaInputSource")
    tv.st_input = AsyncMock(return_value=True)
    tv.input_status = AsyncMock(return_value="HDMI1")
    assert not await tv.set_input("HDMI2")
    assert tv.last_input.accepted and not tv.last_input.verified


async def test_input_status_only_reads_saved_device_without_wake_or_select():
    tv = SamsungTV(st_device="sports-tv", st_capability="mediaInputSource")
    tv._st = AsyncMock(return_value={"inputSource": {"value": "HDMI 2"}})
    assert await tv.input_status() == "HDMI2"
    tv._st.assert_awaited_once_with("GET", "/devices/sports-tv/components/main/capabilities/mediaInputSource/status")


async def test_focus_does_not_become_current_before_tune_confirmation(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    engine = Engine(cfg, Store(tmp_path / "test.db"))
    old, target = nfl("old"), nfl("target")
    engine.decider.current = old.game_id
    engine.scored[target.game_id] = Scorer(cfg).score(target, Personal())
    engine.where[target.game_id] = SimpleNamespace(target="youtubetv:espn")
    callbacks = []
    engine.control.submit = lambda target, back, done, **kw: callbacks.append(done)
    ok, _ = engine.action("focus", target.game_id, "focus")
    assert ok
    assert engine.decider.current == old.game_id
    assert not engine.decider.history
    callbacks[0](Outcome(False, "youtubetv:espn", 1, "link failed", False))
    assert engine.decider.current == old.game_id
    assert not engine.decider.history
    await engine.close()


@pytest.mark.parametrize("wake", ["Awake", "Asleep"])
async def test_slow_power_read_cannot_send_wake_or_launch_after_safe_gap_closes(wake):
    from test_tuners import ScriptedStick, quick
    allowed = True
    class SlowPower(ScriptedStick):
        async def _shell(self, cmd, timeout=8):
            nonlocal allowed
            result = await super()._shell(cmd, timeout)
            if "dumpsys power" in cmd:
                allowed = False
            return result
    stick = SlowPower(wake=wake)
    result = await quick().tune(stick, "CODE", "youtubetv:espn", may_cut=lambda: allowed)
    assert not result.ok and result.screen_changed is False
    assert not stick.links() and not stick.keys()


async def test_retry_rechecks_safe_gap_after_old_stream_confirmation_times_out():
    from test_tuners import ScriptedStick, quick, yt
    allowed = True
    class ClosingGap(ScriptedStick):
        async def view(self, *args, **kwargs):
            nonlocal allowed
            result = await super().view(*args, **kwargs)
            allowed = False
            return result
    old = yt(3, 1000, 5000)
    stick = ClosingGap(now=old, after=[old])
    result = await quick(confirm_timeout=0.005).tune(stick, "CODE", "youtubetv:espn", may_cut=lambda: allowed)
    assert not result.ok and result.screen_changed is True
    assert len(stick.links()) == 1


async def test_prime_prompt_rechecks_gap_after_reading_slow_detail_page():
    from test_tuners import ScriptedStick, quick
    from redzone.tuners.tuners import PrimeTuner
    allowed = True
    class ClosingPage(ScriptedStick):
        async def screen_text(self):
            nonlocal allowed
            allowed = False
            return "Thursday Night Football | Watch Live"
    stick = ClosingPage()
    result = await quick(PrimeTuner, page_wait_s=0.001).tune(
        stick, "GTI", "prime:GTI", may_cut=lambda: allowed)
    assert not result.ok and result.screen_changed is True
    assert "KEYCODE_DPAD_CENTER" not in stick.keys()


async def test_provider_launch_rechecks_after_power_read():
    from test_providers import FakeStick, Clock, PeacockLike, tuner   # a generic AppTuner double (the real one was removed: never Peacock)
    allowed = True
    class ClosingRead(FakeStick):
        async def shell(self, cmd, timeout=8):
            nonlocal allowed
            result = await super().shell(cmd, timeout)
            if "dumpsys power" in cmd:
                allowed = False
            return result
    stick = ClosingRead(packages={"peacock": "com.peacock.peacockfiretv"})
    result = await tuner(PeacockLike, Clock()).tune(stick, "123", "peacock:123", may_cut=lambda: allowed)
    assert not result.ok and result.screen_changed is False
    assert not stick.launches and not stick.keys
