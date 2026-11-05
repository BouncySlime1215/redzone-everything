"""The suite never reaches a real Fire TV stick or SmartThings (tests/conftest.py _no_real_devices).

On the Mac, scripts/deploy.py runs this suite beside the live sticks; a test that shelled out to the real
adb could press keys on the TV. These prove the guard blocks the real thing and still lets fakes run."""
import asyncio
import os
import subprocess

import httpx
import pytest

from conftest import _is_real_adb
from redzone.tuners import adb


def test_real_adb_paths_are_blocked_and_fakes_are_not(tmp_path):
    assert _is_real_adb("/opt/homebrew/bin/adb")
    assert _is_real_adb("/usr/local/bin/adb")
    fake = tmp_path / "adb"
    fake.write_text("#!/bin/sh\necho fake\n")
    assert not _is_real_adb(fake)
    assert not _is_real_adb("/bin/sh")


def test_async_exec_of_the_real_adb_fails_like_a_missing_adb():
    async def go():
        await asyncio.create_subprocess_exec("/opt/homebrew/bin/adb", "devices")
    with pytest.raises(FileNotFoundError, match="real adb"):
        asyncio.run(go())


def test_popen_of_the_real_adb_is_blocked():
    with pytest.raises(FileNotFoundError, match="real adb"):
        subprocess.run(["/opt/homebrew/bin/adb", "devices"])


def test_a_fake_adb_script_still_runs(tmp_path):
    fake = tmp_path / "adb"
    fake.write_text("#!/bin/sh\necho fake-adb \"$@\"\n")
    os.chmod(fake, 0o755)
    assert subprocess.run([str(fake), "devices"], capture_output=True, text=True).stdout.strip() == "fake-adb devices"

    async def go():
        p = await asyncio.create_subprocess_exec(str(fake), "x", stdout=asyncio.subprocess.PIPE)
        return (await p.communicate())[0].decode().strip()
    assert asyncio.run(go()) == "fake-adb x"


def test_the_stick_client_sees_no_adb_on_the_real_path(monkeypatch):
    """A Stick pointed at the real binary gets the same result as Linux CI with no adb installed."""
    monkeypatch.setattr(adb, "ADB", "/opt/homebrew/bin/adb")
    r = asyncio.run(adb.Stick("192.0.2.1:5555").run("shell", "echo", "hi", timeout=2.0))
    assert not r.ok


def test_real_smartthings_calls_fail_and_fake_transports_work():
    with pytest.raises(httpx.ConnectError, match="SmartThings"):
        httpx.get("https://api.smartthings.com/v1/devices")

    async def go():
        with pytest.raises(httpx.ConnectError, match="SmartThings"):
            async with httpx.AsyncClient() as c:
                await c.get("https://api.smartthings.com/v1/devices")
        fake = httpx.MockTransport(lambda req: httpx.Response(200, json={"items": []}))
        async with httpx.AsyncClient(transport=fake) as c:
            return (await c.get("https://api.smartthings.com/v1/devices")).json()
    assert asyncio.run(go()) == {"items": []}
