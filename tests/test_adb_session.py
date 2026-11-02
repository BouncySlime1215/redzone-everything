"""The always-open adb shell, against a fake adb whose "stick" is a real local /bin/sh:
replies match one adb process per command byte-for-byte, hung or dead shells are replaced,
nothing with side effects is ever sent twice, and the old way still works when the shell won't."""
import asyncio
import time

import pytest

from fake_adb import FakeAdb, alive, media
from redzone.tuners import adb
from redzone.tuners.adb import Stick, _adb_problem, _changes_screen, _repeatable, parse_playback, same_stream


@pytest.fixture(scope="module")
def fake_device(tmp_path_factory):
    return FakeAdb(tmp_path_factory.mktemp("adb") / "dev")


@pytest.fixture
def fake(fake_device, monkeypatch):
    monkeypatch.setattr(adb, "ADB", str(fake_device.adb))
    monkeypatch.setattr(adb, "SESSION_ON", True)
    return fake_device.reset()


def new_stick() -> Stick:
    s = Stick("fake:5555", name="fake", os="Fire OS 8")
    s.state = "connected"
    return s


async def test_one_shell_serves_every_command(fake):
    st = new_stick()
    outs = [(await st.shell(f"echo {i}")).out for i in range(5)]
    await st.close()
    assert outs == [f"{i}\n" for i in range(5)]
    assert fake.calls() == ["session"]          # one adb started, no per-command processes
    assert st.last_ok > 0


@pytest.mark.parametrize("cmd", [
    "echo hi", "printf abc", "printf ''", "printf 'a\\nb\\n\\n'", "sh -c 'exit 3'", "false",
    "echo 'error: nope'", "printf '\\377\\376x\\000y'", "echo __RZdeadbeef00000000__ 0",
    "printf '\\n__RZ0123456789abcdef__ 1\\n'", "echo out; echo err >&2", "printf 'a\\r\\nb'",
])
async def test_replies_match_one_adb_process_per_command(fake, cmd):
    st = new_stick()
    via_shell = await st.shell(cmd)
    one_shot = await st.run("shell", cmd)
    await st.close()
    assert (via_shell.ok, via_shell.out) == (one_shot.ok, one_shot.out)


async def test_big_and_trickling_replies_are_read_whole(fake):
    st = new_stick()
    big = await st.shell("head -c 2000000 /dev/zero | tr '\\000' x")
    slow = await st.shell("printf ab; sleep 0.2; printf cd")
    await st.close()
    assert big.out == "x" * 2_000_000
    assert slow.out == "abcd"


async def test_commands_cant_leak_into_each_other(fake):
    st = new_stick()
    await st.shell("cd /tmp; X=1")
    after = await st.shell("pwd; echo \"[$X]\"")
    broken = await st.shell("echo it's")          # unbalanced quote: only this command fails
    fine = await st.shell("echo still here")
    await st.close()
    assert "/tmp" not in after.out and "[]" in after.out
    assert not broken.ok and fine.out == "still here\n"
    assert fake.sessions() == 1


async def test_hung_command_times_out_and_the_shell_is_replaced(fake):
    st = new_stick()
    await st.shell("echo warm")
    t0 = time.monotonic()
    r = await st.shell("sleep 3", timeout=0.3)
    took = time.monotonic() - t0
    nxt = await st.shell("echo next")
    await st.close()
    assert not r.ok and "timeout" in r.out and took < 1.5
    assert nxt.out == "next\n"
    assert fake.sessions() == 2                    # killed and reopened
    assert not any(c.startswith("oneshot sleep") for c in fake.calls())   # a stall isn't doubled


async def test_cancelled_command_leaves_no_leftovers(fake):
    st = new_stick()
    await st.shell("echo warm")
    task = asyncio.create_task(st.shell("sleep 0.5; echo late"))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    r = await st.shell("echo after")
    await asyncio.sleep(0.5)
    r2 = await st.shell("echo again")
    await st.close()
    assert r.out == "after\n" and r2.out == "again\n"     # "late" never shows up in a later reply
    assert fake.sessions() == 2


async def test_shell_that_wont_start_falls_back_and_backs_off(fake):
    fake.mode("nosession")
    st = new_stick()
    a = await st.shell("echo hi")
    b = await st.shell("echo again")
    await st.close()
    assert (a.ok, a.out, b.out) == (True, "hi\n", "again\n")
    assert fake.calls() == ["session", "oneshot echo hi", "oneshot echo again"]   # not retried every call
    assert "not working" in st.session_status() or "not opened" in st.session_status()


async def test_shell_that_never_answers_is_given_up_on_quickly(fake, monkeypatch):
    monkeypatch.setattr(adb, "HANDSHAKE_S", 0.3)
    fake.mode("silent")    # takes our commands, never replies
    st = new_stick()
    t0 = time.monotonic()
    r = await st.shell("echo hi")
    took = time.monotonic() - t0
    await st.close()
    assert r.out == "hi\n" and took < 1.5
    assert fake.calls() == ["session", "oneshot echo hi"]


async def test_terminal_style_shell_is_not_trusted(fake):
    fake.mode("pty")       # echoes our line back and ends lines with \r\n, like a real terminal
    st = new_stick()
    r = await st.shell("echo hi")
    await st.close()
    assert r.out == "hi\n"
    assert fake.calls() == ["session", "oneshot echo hi"]


async def test_garbled_reply_reopens_and_retries_only_lookups(fake):
    fake.mode("garble")    # the handshake works, then replies come back out of step
    st = new_stick()
    r = await st.shell("echo hi")
    await st.close()
    assert r.ok and r.out == "hi\n"
    assert fake.calls() == ["session", "oneshot echo hi"]


async def test_lookup_is_retried_when_the_shell_dies_mid_command(fake):
    fake.set_media(media("yttv.pkg", 3, 1000, 5000))
    st = new_stick()
    await st.shell("echo warm")
    fake.die_once()
    pb = await st.playback("yttv.pkg")
    await st.close()
    assert pb["state"] == "playing"
    assert any(c.startswith("oneshot dumpsys media_session") for c in fake.calls())


async def test_key_press_is_never_sent_twice(fake):
    st = new_stick()
    await st.shell("echo warm")
    fake.die_once()        # the shell dies while the key press runs: did it land? we can't know
    await st.key("KEYCODE_DPAD_CENTER")
    nxt = await st.shell("echo next")
    await st.close()
    assert fake.device_log().count("input keyevent KEYCODE_DPAD_CENTER") == 1
    assert nxt.out == "next\n" and fake.sessions() == 2


async def test_concurrent_callers_each_get_their_own_reply(fake, monkeypatch):
    monkeypatch.setattr(adb, "LOCK_WAIT_S", 10.0)
    st = new_stick()
    outs = await asyncio.gather(*(st.shell(f"echo {i}; sleep 0.01; echo {i}") for i in range(20)))
    await st.close()
    assert [r.out for r in outs] == [f"{i}\n{i}\n" for i in range(20)]
    assert fake.calls() == ["session"]


async def test_busy_shell_doesnt_hold_up_a_quick_command(fake, monkeypatch):
    monkeypatch.setattr(adb, "LOCK_WAIT_S", 0.05)
    st = new_stick()
    await st.shell("echo warm")
    slow = asyncio.create_task(st.shell("sleep 1; echo slow"))
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    quick = await st.shell("echo quick")
    quick_took = time.monotonic() - t0
    assert quick.out == "quick\n" and quick_took < 0.8 and not slow.done()
    assert (await slow).out == "slow\n"
    await st.close()
    assert "oneshot echo quick" in fake.calls()


# The first loop ends with its shell still open (that's the point), so asyncio can't tidy its pipes.
@pytest.mark.filterwarnings("ignore::ResourceWarning")
def test_a_new_event_loop_gets_a_new_shell(fake):
    st = new_stick()
    assert asyncio.run(st.shell("echo a")).out == "a\n"
    assert asyncio.run(st.shell("echo b")).out == "b\n"
    asyncio.run(st.close())
    assert fake.sessions() == 2


async def test_switch_off_uses_one_process_per_command(fake, monkeypatch):
    monkeypatch.setattr(adb, "SESSION_ON", False)
    st = new_stick()
    await st.shell("echo a")
    await st.shell("echo b")
    assert fake.calls() == ["oneshot echo a", "oneshot echo b"]
    assert "off" in st.session_status()


async def test_offline_stick_is_reported(fake):
    fake.mode("offline")
    st = new_stick()
    r = await st.shell("echo hi")
    await st.close()
    assert not r.ok and st.state == "offline"


async def test_command_output_saying_not_found_isnt_an_offline_stick(fake):
    st = new_stick()
    a = await st.shell("echo 'sh: uiautomator: not found'")
    b = await st.run("shell", "echo 'You are offline'")
    await st.close()
    assert st.state == "connected" and a.out and b.ok


async def test_reconnect_gives_the_shell_another_go(fake):
    fake.mode("nosession")
    st = new_stick()
    await st.shell("echo hi")                       # falls back; the shell is on a backoff now
    fake.mode("")
    assert await st.connect()                       # "already connected" -> backoff reset, shell warmed
    r = await st.shell("echo again")
    await st.close()
    assert r.out == "again\n"
    assert fake.sessions() == 2 and "oneshot echo again" not in fake.calls()


async def test_close_stops_the_shell(fake):
    st = new_stick()
    await st.shell("echo hi")
    pid = fake.session_pid()
    assert alive(pid)
    await st.close()
    assert not alive(pid)


# --- pure helpers ----------------------------------------------------------------------

def test_only_lookups_are_repeatable():
    assert _repeatable("dumpsys media_session | grep -E 'package=|state=PlaybackState'")
    assert _repeatable("getprop ro.product.model; getprop ro.build.version.release; settings get global x")
    assert _repeatable("echo ok")
    assert not _repeatable("input keyevent KEYCODE_DPAD_CENTER")
    assert not _repeatable("am start -a android.intent.action.VIEW -d 'https://x' pkg")
    assert not _repeatable("cmd hdmi_control onetouchplay")
    assert not _repeatable("echo hi; input keyevent KEYCODE_MEDIA_REWIND")


def test_what_changes_the_screen():
    assert _changes_screen("input keyevent KEYCODE_DPAD_CENTER")
    assert _changes_screen("am start -a android.intent.action.VIEW -d 'u' pkg")
    assert _changes_screen("input keyevent KEYCODE_MEDIA_REWIND; input keyevent KEYCODE_MEDIA_REWIND")
    assert not _changes_screen("input keyevent KEYCODE_WAKEUP")
    assert not _changes_screen("dumpsys media_session | grep -E 'package=|state=PlaybackState'")
    assert not _changes_screen("cmd hdmi_control onetouchplay")


def test_adb_problems_come_from_adb_not_from_commands():
    assert _adb_problem("error: device unauthorized.\nThis adb server's $ADB_VENDOR_KEYS is not set") == "unauthorized"
    assert _adb_problem("error: device offline") == "offline"
    assert _adb_problem("adb: device '1.2.3.4:5555' not found") == "offline"
    assert _adb_problem("sh: uiautomator: not found") is None
    assert _adb_problem('<node text="You are offline" />') is None


def test_parse_playback_formats():
    old_style = media("other.pkg", 2, 5, 10) + media("yttv.pkg", 3, 1000, 5000)
    assert parse_playback(old_style, "yttv.pkg") == {"state": "playing", "pkg": "yttv.pkg", "position": 1000,
                                                     "updated": 5000}
    assert parse_playback(old_style, "other.pkg")["state"] == "paused"
    assert parse_playback(old_style)["pkg"] == "yttv.pkg"           # a playing session wins
    new_style = old_style.replace("state=3,", "state=PLAYING(3),")
    assert parse_playback(new_style, "yttv.pkg")["state"] == "playing"
    assert parse_playback("", "yttv.pkg") == {"state": "none", "pkg": None, "position": None, "updated": None}
    grepped = "\n".join(x for x in old_style.splitlines() if "package=" in x or "state=PlaybackState" in x)
    assert parse_playback(grepped, "yttv.pkg") == parse_playback(old_style, "yttv.pkg")


def test_same_stream_uses_the_apps_own_clock():
    a = {"position": 10_000, "updated": 50_000}
    assert same_stream(a, dict(a)) is True                                     # nothing changed since
    assert same_stream(a, {"position": 14_000, "updated": 54_000}) is True     # played on for 4s
    assert same_stream(a, {"position": 900_000, "updated": 54_000}) is False   # a different stream / seek
    assert same_stream(a, {"position": 10_000, "updated": 60_000}) is False    # paused for 10s
    assert same_stream(a, {"position": 0, "updated": 54_000}) is None          # live stream, no positions
    assert same_stream(a, {"position": None, "updated": None}) is None
