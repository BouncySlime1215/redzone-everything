"""AUDIT-2, adb: screencap_band's contract says what it does on a timeout.

Why: the docstring promised b'' for "anything fails", but a timeout raises asyncio.TimeoutError (like
screencap), and yttv_seek.band_png relies on that raise to count a failed read. The code is right; the docs were not.
"""
import asyncio

import pytest

from redzone.tuners.adb import Stick


async def test_screencap_band_raises_on_timeout_and_says_so():
    s = Stick("10.0.0.9:5555")

    async def hung(cmd, timeout):
        raise asyncio.TimeoutError

    s._exec_out = hung
    with pytest.raises(asyncio.TimeoutError):
        await s.screencap_band(0.7, timeout=0.01)
    assert "TimeoutError" in (Stick.screencap_band.__doc__ or "")
