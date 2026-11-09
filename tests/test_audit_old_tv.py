"""AUDIT-OLD, TV: a key that only waited for the socket was never sent, whatever else is pending.

Why: the "busy" verdict read a flag that an older timed-out warm() leaves set, turning "not sent" into "may have been".
"""
import asyncio

from redzone.tv import SamsungTV


async def test_a_key_that_never_got_the_socket_is_a_definite_non_delivery(monkeypatch):
    """Repro: a warm() that timed out left _drop_pending set while its thread ran on. A key then timed out waiting for
    the lock, but `busy` also required not _drop_pending, so the key was reported as maybe delivered."""
    monkeypatch.setattr("redzone.tv.LOCAL_LOCK_WAIT_S", 0.01)
    tv = SamsungTV(host="10.0.0.5")
    await tv._local_lock.acquire()      # the old worker thread is still on the socket
    tv._drop_pending = True
    assert await tv.key("KEY_HDMI1") is False
    assert tv._definite is True
    assert "busy" in tv.last_error


async def test_a_key_that_ran_out_of_time_on_the_socket_stays_ambiguous(monkeypatch):
    tv = SamsungTV(host="10.0.0.5")

    async def fake_run(fn, timeout, wait_s):
        tv._drop_pending = True
        raise asyncio.TimeoutError
    monkeypatch.setattr(tv, "_run_local", fake_run)
    assert await tv.key("KEY_HDMI1") is False
    assert tv._definite is False
