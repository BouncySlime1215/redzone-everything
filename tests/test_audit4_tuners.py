"""AUDIT-4: a failed read before the link is not evidence that nothing was playing.

Why: the old stream can still report 'playing' for a moment after the link; calling that the new one makes a wrong
channel a ready standby, and the next flip shows it.
"""
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
