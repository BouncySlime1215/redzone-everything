"""AUDIT-7: a stick that answered "no media session" is a good read, so control's watchdogs look at it."""
from redzone.tuners import adb
from redzone.tuners.adb import Stick
from tests.test_audit4_tuners import fake  # noqa: F401  (fixture)


async def test_no_session_read_is_read_ok(fake):
    fake.set_media("")
    st = Stick("fake:5555")
    pb = await st.playback()
    await st.close()
    assert pb["state"] == "none" and pb["no_sessions"] is True and pb["read_ok"] is True
