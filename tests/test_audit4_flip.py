"""AUDIT-4: what the sticks and the TV really did, against what the books say they did.

Why: Rule #1 is judged on the delay book. A book that says "live edge" for a picture still behind it lands a flip mid-play.
"""
import asyncio

import pytest

from redzone import control as ctl
from redzone.tuners.tuners import Tuner, TuneResult
from tests.test_control import FakeStick, two  # noqa: F401  (fixture)


PRIME = "prime:gti1"


class RealTuner(Tuner):
    """The real base tuner: its "already on it" path is what's under test."""
    app = "prime"
    role = "prime"

    def url(self, ref):
        return f"https://example.invalid/{ref}"


@pytest.fixture
def held(two, monkeypatch):  # noqa: F811
    c, _ = two
    tuner = RealTuner()
    monkeypatch.setitem(ctl.TUNERS, "prime", tuner)
    return c, c.hidden


async def _played_on(stick, target):
    """The stick plays `target`, uninterrupted, since the anchor a finished hold left."""
    stick.remember(target, await stick.playback(stick.packages["prime"]))


async def test_a_tune_that_sent_nothing_keeps_the_held_offset(held):
    """Repro: a stick paused 75 s behind live (a Prime pause-hold) re-anchors and plays on. A later _tune finds it still
    playing, sends no link ("already on it"), and _tune booked it at the live edge: the delay book lost 75 s."""
    c, hid = held
    await _played_on(hid, PRIME)
    c._set_held(PRIME, 75.0, how="held")
    res = await c._tune(hid, PRIME)
    assert res.ok and "already on it" in res.steps[0]
    assert c.held[PRIME] == 75.0


async def test_a_tune_that_sent_nothing_is_marked_kept(held):
    c, hid = held
    await _played_on(hid, PRIME)
    assert (await ctl.TUNERS["prime"].tune(hid, "gti1", PRIME)).kept is True


async def test_a_real_link_still_books_the_live_edge(two, monkeypatch):  # noqa: F811
    """The other half: a tune that really sent its link is the live edge, as before."""
    c, tuner = two
    c._set_held("youtubetv:espn", 40.0, how="held")
    res = await c._tune(c.hidden, "youtubetv:espn")
    assert res.ok and tuner.calls and "youtubetv:espn" not in c.held


async def test_a_preload_that_finds_the_stream_held_does_not_hold_it_again(held, monkeypatch):
    """Repro: _preload held again after a kept tune, so the picture sat 150 s behind with 75 s in the book."""
    c, hid = held
    await _played_on(hid, PRIME)
    c._set_held(PRIME, 75.0, how="held")
    monkeypatch.setattr(c, "_buffer_for", lambda *a: 75.0)
    holds = []

    async def hold(*a, **k):
        holds.append(a)
        return True
    monkeypatch.setattr(c, "_hold", hold)
    await c._preload(PRIME, None, None, 0.0)
    assert holds == [] and c.held[PRIME] == 75.0 and c.standby_ready(PRIME)
