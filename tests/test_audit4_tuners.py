"""AUDIT-4: a failed read before the link is not evidence that nothing was playing.

Why: the old stream can still report 'playing' for a moment after the link; calling that the new one makes a wrong
channel a ready standby, and the next flip shows it.
"""
import pytest

from fake_adb import FakeAdb
from redzone.tuners import adb
from redzone.tuners.adb import Stick
from redzone.tuners.tuners import NewStream

OLD = {"state": "playing", "pkg": "p", "position": 5000, "updated": 100, "subtitle": "SportsCenter, ESPN, null"}


def test_an_unread_before_never_confirms_the_stream_that_was_already_playing():
    """Repro: the pre-link read failed (read_ok False, no state), so the first 'playing' report (the OLD channel, a
    moment after the link) confirmed the new target."""
    w = NewStream({"state": None, "read_ok": False}, expect=lambda r: False if r.get("subtitle") else None)
    assert w.check(OLD) is False


def test_an_unread_before_still_confirms_after_a_gap():
    w = NewStream({"state": None, "read_ok": False})
    assert w.check(OLD) is False
    assert w.check({"state": "buffering", "pkg": "p"}) is False
    assert w.check({"state": "playing", "pkg": "p"}) is True


def test_an_unread_before_still_confirms_by_name():
    w = NewStream({"state": None, "read_ok": False}, expect=lambda r: True if r.get("subtitle") else None)
    assert w.check(OLD) is True and w.by_name


def test_a_read_that_says_nothing_was_playing_still_confirms_at_once():
    """The other half: a good read of no session is real evidence, and the fast path stays."""
    assert NewStream({"state": "none", "read_ok": True}).check(OLD) is True


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeAdb(tmp_path / "dev")
    monkeypatch.setattr(adb, "ADB", str(f.adb))
    monkeypatch.setattr(adb, "SESSION_ON", True)
    return f


async def test_a_stick_with_no_media_session_is_a_read_that_found_nothing(fake):
    """Repro: `dumpsys | grep` exits 1 when nothing matches, so a stick that answered "no session" came back read_ok False
    and the first tune from nothing never confirmed (30 s, then "video never started"; test_bench)."""
    fake.set_media("")
    st = Stick("fake:5555")
    before = await st.playback()
    await st.close()
    assert before["state"] == "none" and before["no_sessions"] is True
    assert NewStream(before).check(OLD) is True


async def test_a_stick_that_did_not_answer_is_still_not_a_read(fake):
    """The AUDIT-4 half: a real failure says something on the way out, and must not take the fast path."""
    fake.mode("offline")
    st = Stick("fake:5555")
    before = await st.playback()
    await st.close()
    assert before["read_ok"] is False and "no_sessions" not in before
    assert NewStream(before).check(OLD) is False
