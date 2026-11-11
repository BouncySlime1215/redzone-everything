"""Sound guard (sound_guard.*): the TV stays muted at volume 0, proven by readback. Fakes only: a fake SmartThings
transport and a fake remote socket; no TV, no stick, nothing that plays sound.

- silent = muted AND volume 0 read back; check(): silent -> ok, not silent -> incident, unreadable 3 in a row ->
  incident, TV off -> re-check at the next switch;
- an incident powers the TV off, writes values only, and blocks: no input switch, no key, until cleared by hand;
- the first switch of a session silences and must read silence back; every switch is re-checked after;
- must_be_silent: no volume-up, mute toggle, unmute or setVolume above 0 leaves SamsungTV (volume_duck included);
- flag off: no guard, no sound call, volume_duck and set_input exactly as before."""
import asyncio
import json

import httpx
import pytest

from redzone import tv as tvmod
from redzone.platform import sound_guard as sg


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(tvmod, "VERIFY_GAP_S", 0.0)
    monkeypatch.setattr(sg, "SETTLE_S", 0.0)


class FakeCloud:
    """SmartThings for one TV: power, mute, volume, input. Records every request."""

    def __init__(self, power="on", mute="muted", volume=0, input="HDMI1", obey_mute=True, obey_volume=True):
        self.power, self.mute, self.volume, self.input = power, mute, volume, input
        self.obey_mute, self.obey_volume = obey_mute, obey_volume
        self.unreadable = 0          # this many audio reads answer 500
        self.calls: list[tuple[str, str]] = []
        self.commands: list[tuple[str, str, list]] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        self.calls.append((req.method, p))
        if p == "/oauth/token":
            return httpx.Response(200, json={"access_token": "acc", "refresh_token": "ref", "expires_in": 86400,
                                             "scope": tvmod.ST_SCOPES})
        if p == "/v1/devices":
            return httpx.Response(200, json={"items": [{"deviceId": "tv-1", "components": [
                {"capabilities": [{"id": "mediaInputSource"}, {"id": "audioMute"}, {"id": "audioVolume"}]}]}]})
        if p.endswith("/status"):
            cap = p.split("/")[-2]
            if cap in ("audioMute", "audioVolume") and self.unreadable > 0:
                self.unreadable -= 1
                return httpx.Response(500)
            if cap == "switch":
                return httpx.Response(200, json={"switch": {"value": self.power}})
            if cap == "audioMute":
                return httpx.Response(200, json={"mute": {"value": self.mute}})
            if cap == "audioVolume":
                return httpx.Response(200, json={"volume": {"value": self.volume, "unit": "%"}})
            if cap == "mediaInputSource":
                return httpx.Response(200, json={"inputSource": {"value": self.input},
                                                 "supportedInputSources": {"value": ["HDMI1", "HDMI2"]}})
        if p == "/v1/devices/tv-1/commands":
            c = json.loads(req.content)["commands"][0]
            cap, cmd, args = c["capability"], c["command"], c.get("arguments") or []
            self.commands.append((cap, cmd, args))
            if cap == "switch":
                self.power = cmd
            elif cap == "audioMute" and self.obey_mute:
                self.mute = "muted" if cmd == "mute" else "unmuted" if cmd == "unmute" else self.mute
            elif cap == "audioVolume" and self.obey_volume and cmd == "setVolume":
                self.volume = args[0]
            elif cap == "mediaInputSource" and cmd == "setInputSource":
                self.input = args[0]
            return httpx.Response(200, json={"results": [{"status": "ACCEPTED"}]})
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handler)

    def posts(self):
        return [c for c in self.commands]


class FakeRemote:
    """The samsungtvws socket: records keys."""

    def __init__(self):
        self.keys: list[str] = []

    def send_key(self, k):
        self.keys.append(k)

    def open(self):
        pass

    def close(self):
        pass


def settings(**kw):
    d = {"sound_guard.enabled": True, "sound_guard.must_be_silent": True, "sound_guard.check_s": 20.0,
         "sound_guard.on_incident": "off_tv", "sound_guard.unreadable_max": 3, "sound_guard.silence_tries": 4}
    d.update({f"sound_guard.{k}": v for k, v in kw.items()})
    return lambda key, fb=None: d.get(key, fb)


def make_tv(tmp_path, cloud, guard=True, **kw):
    f = tmp_path / "st.json"
    f.write_text(json.dumps({"client_id": "cid", "client_secret": "sec", "refresh_token": "ref0",
                             "access_token": "acc0", "expires_at": 10 ** 12, "refreshed_at": 10 ** 12}))
    auth = tvmod.SmartThingsAuth(f, transport=cloud.transport())
    tv = tvmod.SamsungTV(st_device="tv-1", auth=auth, transport=cloud.transport(), log_path=tmp_path / "in.jsonl",
                         sound_settings=settings(**kw))
    tv._tv = FakeRemote()
    if guard:
        tv.sound_guard = sg.SoundGuard(tv, marker=tmp_path / "incident.json", sleep=_settle_only)
    return tv


async def _no_sleep(_s):
    return None


async def _settle_only(s):
    """The silence settle passes at once; the periodic loop's check_s wait parks (cancelled at test end)."""
    if s >= 1:
        await asyncio.Event().wait()


# --- silent / not silent / unreadable --------------------------------------------------------------------

def test_silent_means_muted_and_volume_zero_both_read():
    assert tvmod.silent({"mute": "muted", "volume": 0})
    assert tvmod.silent({"mute": "muted", "volume": 0.0})
    assert not tvmod.silent({"mute": "unmuted", "volume": 0})
    assert not tvmod.silent({"mute": "muted", "volume": 1})
    assert not tvmod.silent({"mute": "muted", "volume": None})
    assert not tvmod.silent({"mute": None, "volume": 0})
    assert not tvmod.silent({"mute": "muted", "volume": False})
    assert not tvmod.silent({}) and not tvmod.silent(None)


async def test_read_returns_power_mute_volume_input(tmp_path):
    cloud = FakeCloud(volume=0, mute="muted", input="HDMI2")
    g = make_tv(tmp_path, cloud).sound_guard
    s = await g.read()
    assert s == {"power": "on", "mute": "muted", "volume": 0, "input": "HDMI2"}
    assert g.silent(s)


async def test_silent_tv_checks_ok_and_sends_nothing(tmp_path):
    cloud = FakeCloud()
    g = make_tv(tmp_path, cloud).sound_guard
    assert await g.check() == "ok"
    assert g.m.state == "ok" and cloud.commands == []


@pytest.mark.parametrize("mute,volume", [("unmuted", 0), ("muted", 7), ("unmuted", 12)])
async def test_not_silent_is_an_incident_that_powers_off_and_blocks(tmp_path, mute, volume):
    cloud = FakeCloud(mute=mute, volume=volume)
    tv = make_tv(tmp_path, cloud)
    g = tv.sound_guard
    assert await g.check() == "incident"
    assert cloud.commands == [("switch", "off", [])]          # power off, no "re-mute and carry on"
    assert cloud.power == "off"
    assert g.m.state == "blocked" and "not silent" in g.blocked_why()
    row = json.loads((tmp_path / "incident.json").read_text())
    assert set(row) == {"at", "who", "why", "power", "mute", "volume", "input", "tv_off_sent"}
    assert row["mute"] == mute and row["volume"] == volume and row["tv_off_sent"] is True
    assert "tv-1" not in (tmp_path / "incident.json").read_text()   # values only: never a device id
    assert tv.sound_blocked()


async def test_unreadable_three_times_in_a_row_is_an_incident(tmp_path):
    cloud = FakeCloud()
    cloud.unreadable = 10 ** 6
    g = make_tv(tmp_path, cloud).sound_guard
    assert await g.check() == "unreadable"
    assert await g.check() == "unreadable"
    assert cloud.commands == []
    assert await g.check() == "incident"
    assert cloud.commands == [("switch", "off", [])] and g.m.state == "blocked"
    assert "3 times" in g.blocked_why()


async def test_a_good_read_resets_the_unreadable_count(tmp_path):
    cloud = FakeCloud()
    g = make_tv(tmp_path, cloud).sound_guard
    cloud.unreadable = 4                     # two checks' worth (mute and volume each fail)
    assert await g.check() == "unreadable"
    assert await g.check() == "unreadable"
    assert await g.check() == "ok" and g.misses == 0
    cloud.unreadable = 4
    assert await g.check() == "unreadable"
    assert await g.check() == "unreadable" and g.m.state == "unreadable"


async def test_no_readback_path_counts_as_unreadable(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud)
    tv.cloud_enabled = False                 # SmartThings off by policy: no readback at all
    g = tv.sound_guard
    assert [await g.check() for _ in range(3)] == ["unreadable", "unreadable", "incident"]
    assert g.blocked


async def test_tv_off_is_fine_but_the_next_switch_checks_first(tmp_path):
    cloud = FakeCloud(power="off", mute="unmuted", volume=30)
    g = make_tv(tmp_path, cloud).sound_guard
    assert await g.check() == "off"
    assert g.m.state == "unchecked" and not g.blocked


async def test_on_incident_block_only_blocks(tmp_path):
    cloud = FakeCloud(mute="unmuted")
    g = make_tv(tmp_path, cloud, on_incident="block").sound_guard
    assert await g.check() == "incident"
    assert cloud.commands == [] and cloud.power == "on" and g.blocked


async def test_silence_not_required_only_reads(tmp_path):
    cloud = FakeCloud(mute="unmuted", volume=25)
    tv = make_tv(tmp_path, cloud, must_be_silent=False)
    assert await tv.sound_guard.check() == "ok"
    assert cloud.commands == [] and not tv.sound_guard.blocked
    assert tv.sound_guard.view()["last"]["volume"] == 25


# --- blocked means no switch, no key ---------------------------------------------------------------------

async def test_blocked_means_no_input_switch_and_no_key(tmp_path):
    cloud = FakeCloud(mute="unmuted")
    tv = make_tv(tmp_path, cloud)
    assert await tv.sound_guard.check() == "incident"
    n = len(cloud.commands)
    assert await tv.set_input("HDMI2") is False
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is False
    assert "sound" in tv.last_error and tv.last_input.detail.startswith("sound guard")
    assert not any(cmd == "setInputSource" for _, cmd, _ in cloud.commands)
    assert len(cloud.commands) == n
    tv.host = "tv.test"
    for k in ("KEY_HDMI2", "KEY_HOME", "KEY_VOLDOWN", "KEY_POWER"):
        assert await tv.key(k) is False
    assert tv._tv.keys == []
    assert await tv.st_input("HDMI2") is False                   # the lower-level call is refused too
    assert await tv.st_command("switch", "on") is False          # nothing turns the TV back on
    assert cloud.power == "off"


async def test_a_marker_from_an_earlier_run_blocks_at_start(tmp_path):
    (tmp_path / "incident.json").write_text("{}")
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud)
    assert tv.sound_guard.m.state == "blocked"
    assert await tv.set_input("HDMI2") is False and cloud.commands == []


async def test_deleting_the_incident_file_by_hand_clears_and_the_next_switch_rechecks(tmp_path):
    cloud = FakeCloud(mute="unmuted")
    tv = make_tv(tmp_path, cloud)
    assert await tv.sound_guard.check() == "incident"
    cloud.power, cloud.mute = "on", "muted"
    (tmp_path / "incident.json").unlink()
    assert tv.sound_blocked() == ""
    cloud.commands.clear()
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is True
    await tv.sound_guard.settle()
    assert cloud.commands[:2] == [("audioVolume", "setVolume", [0]), ("audioMute", "mute", [])]
    assert ("mediaInputSource", "setInputSource", ["HDMI2"]) in cloud.commands


# --- before the first switch, after every switch, and periodically -----------------------------------------

async def test_first_switch_silences_then_switches(tmp_path):
    cloud = FakeCloud(mute="unmuted", volume=20)
    tv = make_tv(tmp_path, cloud)
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is True
    await tv.sound_guard.settle()
    assert cloud.commands[0] == ("audioVolume", "setVolume", [0])
    assert cloud.commands[1] == ("audioMute", "mute", [])
    assert cloud.commands[2] == ("mediaInputSource", "setInputSource", ["HDMI2"])
    assert tv.sound_guard.m.state == "ok" and cloud.input == "HDMI2"


async def test_first_switch_refused_and_tv_off_when_silence_cannot_be_set(tmp_path):
    cloud = FakeCloud(mute="unmuted", obey_mute=False)
    tv = make_tv(tmp_path, cloud)
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is False
    assert not any(cmd == "setInputSource" for _, cmd, _ in cloud.commands)
    assert sum(cmd == "mute" for _, cmd, _ in cloud.commands) == 4          # silence_tries
    assert cloud.power == "off" and tv.sound_guard.blocked


async def test_first_switch_refused_without_readback_but_no_incident_yet(tmp_path):
    cloud = FakeCloud()
    cloud.unreadable = 10                  # four silence tries and the check read nothing (mute + volume each)
    tv = make_tv(tmp_path, cloud)
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is False
    assert not any(cmd == "setInputSource" for _, cmd, _ in cloud.commands)
    assert tv.sound_guard.m.state == "unreadable" and cloud.power == "on"


async def test_every_switch_is_checked_after(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud)
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is True
    await tv.sound_guard.settle()
    n = tv.sound_guard.checks
    cloud.mute = "unmuted"                 # something unmuted the TV (a remote, a CEC device)
    assert await tv.set_input("HDMI1", methods=["smartthings"]) is True
    await tv.sound_guard.settle()
    assert tv.sound_guard.checks == n + 1
    assert tv.sound_guard.blocked and cloud.power == "off"
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is False


async def test_the_periodic_check_runs_every_check_s_on_an_asyncio_task(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, check_s=7.0)
    waits = []

    async def sleep(s):
        waits.append(s)
        if len(waits) == 3:
            cloud.volume = 5
    tv.sound_guard.sleep = sleep
    tv.sound_guard.m.reset("ok", why="test: past the first switch")
    tv.sound_guard.ensure_running()
    await asyncio.wait_for(tv.sound_guard._task, 2)             # the loop ends itself at the incident
    assert waits == [7.0, 7.0, 7.0]
    assert tv.sound_guard.blocked and cloud.power == "off"
    tv.sound_guard.ensure_running()                              # blocked: no new loop
    assert tv.sound_guard._task.done()


async def test_the_periodic_check_never_spins_and_stops_on_request(tmp_path):
    """The first test run hung: under a sleep that returns at once, a blocked guard's loop never yielded, so it
    spun a core and could not be cancelled. It must always yield, end when blocked, and end on stop()."""
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud)
    g = tv.sound_guard
    g.sleep = _no_sleep                                         # every wait returns at once
    g.m.reset("ok", why="test: past the first switch")
    g.ensure_running()
    for _ in range(50):
        await asyncio.sleep(0)
    assert g.checks > 0 and not g._task.done()                  # it runs, and the test loop still gets turns
    g.stop()
    await asyncio.wait_for(asyncio.gather(g._task, return_exceptions=True), 2)
    assert g._task.done()
    (tmp_path / "b").mkdir()
    blocked = make_tv(tmp_path / "b", FakeCloud(), guard=False)
    (tmp_path / "b" / "incident.json").write_text("{}")
    bg = sg.SoundGuard(blocked, marker=tmp_path / "b" / "incident.json", sleep=_no_sleep)
    bg.ensure_running()
    assert bg._task is None                                     # blocked at start: no loop at all


# --- must_be_silent: nothing loud leaves the TV class ------------------------------------------------------

LOUD = [("audioVolume", "volumeUp", []), ("audioVolume", "setVolume", [1]), ("audioVolume", "setVolume", [100]),
        ("audioVolume", "setVolume", []), ("audioVolume", "setVolume", ["0"]), ("audioMute", "unmute", []),
        ("audioMute", "setMute", ["unmuted"]), ("samsungvd.audioVolume", "volumeUp", []),
        ("audioMute", "toggle", []), ("audioVolume", "someNewCommand", [])]


@pytest.mark.parametrize("guard", [True, False])
@pytest.mark.parametrize("cap,cmd,args", LOUD)
async def test_no_loud_smartthings_command_leaves_the_tv_class(tmp_path, guard, cap, cmd, args):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, guard=guard, enabled=guard)
    assert await tv.st_command(cap, cmd, args) is False
    assert not any(m == "POST" for m, _ in cloud.calls)
    assert "must_be_silent" in tv.last_error and tv._definite


@pytest.mark.parametrize("guard", [True, False])
async def test_no_volume_up_or_mute_toggle_key_and_volume_duck_is_locked(tmp_path, guard):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, guard=guard, enabled=guard)
    tv.host = "tv.test"
    for k in ("KEY_VOLUP", "KEY_MUTE", "key_volup"):
        assert await tv.key(k, 3) is False
    await tv.volume_duck(steps=4, hold_s=0.0)
    assert tv._tv.keys == []
    assert "volume_duck" in tv.last_error
    assert await tv.key("KEY_VOLDOWN") is True and tv._tv.keys == ["KEY_VOLDOWN"]   # quieter is still allowed


async def test_the_calming_commands_still_go_out(tmp_path):
    cloud = FakeCloud(mute="unmuted", volume=9)
    tv = make_tv(tmp_path, cloud, guard=False)
    assert await tv.st_command("audioVolume", "setVolume", [0]) is True
    assert await tv.st_command("audioMute", "mute") is True
    assert await tv.st_command("audioMute", "setMute", ["muted"]) is True
    assert await tv.st_command("audioVolume", "volumeDown") is True
    assert cloud.mute == "muted" and cloud.volume == 0


def test_loud_command_classifier():
    for cap, cmd, args in LOUD:
        assert tvmod.loud_command(cap, cmd, args), (cap, cmd, args)
    assert tvmod.loud_command("audioVolume", "setVolume", [0]) == ""
    assert tvmod.loud_command("audioMute", "mute", []) == ""
    assert tvmod.loud_command("mediaInputSource", "setInputSource", ["HDMI1"]) == ""
    assert tvmod.loud_command("switch", "off", []) == ""


async def test_an_unreadable_must_be_silent_setting_counts_as_on(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, guard=False)

    def broken(key, fb=None):
        raise RuntimeError("settings unreadable")
    tv.sound_settings = broken
    assert await tv.st_command("audioMute", "unmute") is False and cloud.commands == []


# --- flag off: identity ------------------------------------------------------------------------------------

async def test_flag_off_from_devices_builds_no_guard_and_makes_no_sound_call(tmp_path, monkeypatch):
    (tmp_path / "devices.json").write_text(json.dumps({"tv": {"smartthings_device_id": "tv-1"}}))
    tv = tvmod.SamsungTV.from_devices(tmp_path / "devices.json")
    assert tv.sound_guard is None and tv.must_be_silent() is False
    assert "sound" not in tv.view()
    assert tvmod._config.default("sound_guard.enabled") is False
    assert tvmod._config.default("sound_guard.must_be_silent") is False
    monkeypatch.setattr(tvmod, "_saved_settings", lambda: settings())
    on = tvmod.SamsungTV.from_devices(tmp_path / "devices.json")
    assert isinstance(on.sound_guard, sg.SoundGuard) and "sound" in on.view()


async def test_flag_off_set_input_makes_exactly_the_old_calls(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, guard=False, enabled=False, must_be_silent=False)
    assert await tv.set_input("HDMI2", methods=["smartthings"]) is True
    assert cloud.commands == [("mediaInputSource", "setInputSource", ["HDMI2"])]
    assert not any(("audio" in p or "/switch/" in p) for _, p in cloud.calls)
    assert asyncio.all_tasks() == {asyncio.current_task()}     # no watch loop, no background check


async def test_flag_off_volume_duck_and_keys_are_unchanged(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, guard=False, enabled=False, must_be_silent=False)
    tv.host = "tv.test"
    await tv.volume_duck(steps=2, hold_s=0.0)
    assert tv._tv.keys == ["KEY_VOLDOWN", "KEY_VOLDOWN", "KEY_VOLUP", "KEY_VOLUP"]
    assert cloud.calls == []


async def test_guard_with_enabled_off_never_starts_a_task(tmp_path):
    cloud = FakeCloud()
    tv = make_tv(tmp_path, cloud, enabled=False)
    tv.sound_guard.ensure_running()
    assert tv.sound_guard._task is None
