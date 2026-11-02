"""Connection failures must distinguish transport refusal from device authorization."""
from unittest.mock import AsyncMock

from redzone.tuners import adb


async def test_refused_tcp_is_not_reported_as_waiting_for_authorization(monkeypatch):
    monkeypatch.setattr(adb, "_adb", AsyncMock(return_value=adb.RunResult(
        True, "failed to connect to '192.0.2.10:5555': Connection refused", 0.1)))
    st = adb.Stick("192.0.2.10:5555")
    st.shell = AsyncMock()
    assert not await st.connect()
    assert st.state == "offline" and "Connection refused" in st.connect_error
    st.shell.assert_not_awaited()


async def test_shell_authorization_failure_survives_successful_connect_message(monkeypatch):
    monkeypatch.setattr(adb, "_adb", AsyncMock(return_value=adb.RunResult(
        True, "connected to 192.0.2.10:5555", 0.1)))
    st = adb.Stick("192.0.2.10:5555")
    st.shell = AsyncMock(return_value=adb.RunResult(False, "error: device unauthorized", 0.1))
    assert not await st.connect()
    assert st.state == "unauthorized" and st.connect_error == "error: device unauthorized"


async def test_authenticated_connection_clears_old_error(monkeypatch):
    monkeypatch.setattr(adb, "_adb", AsyncMock(return_value=adb.RunResult(
        True, "already connected to 192.0.2.10:5555", 0.1)))
    st = adb.Stick("192.0.2.10:5555", connect_error="old refusal")
    st.shell = AsyncMock(return_value=adb.RunResult(True, "ok\n", 0.1))
    assert await st.connect()
    assert st.state == "connected" and st.connect_error == ""
