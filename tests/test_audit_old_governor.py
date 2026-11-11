"""AUDIT-OLD, governor and poll cadence: a game past its listed start that has not kicked off yet.

Why: a listed start is an estimate; both the Mac's keep-awake and the fast pre-game poll stopped at that moment.
"""
import asyncio
from types import SimpleNamespace as NS

import pytest

from redzone import engine as engine_mod
from redzone import governor as gv
from redzone.engine import Engine
from redzone.models import Phase


NOW = 1_000_000.0


def test_a_game_listed_as_started_but_still_pre_keeps_the_mac_awake():
    """Repro: `0 < t - now` went False at the listed time, and an NFL 1:00 game kicks off about 1:03."""
    assert gv.keep_awake_wanted([NS(phase="pre", start_time=NOW - 120)], NOW)


def test_a_long_stale_pre_game_does_not_hold_the_mac_awake_forever():
    assert not gv.keep_awake_wanted([NS(phase="pre", start_time=NOW - 3601)], NOW)


def _poll_self(games, cfg):
    calls = NS(sleeps=[])

    async def poll():
        return None
    return calls, NS(poll=poll, decider=NS(note_error=lambda: None), _reel_tick=lambda: None,
                     merger=NS(games=games), _keep_awake=lambda live: None, _planned_soon=lambda: False,
                     _burst=set(), _burst_s=lambda: 1.0, cfg=NS(get=lambda k, d=None: cfg.get(k, d)),
                     governor=NS(scale=lambda kind: 1.0))


def test_the_poll_stays_fast_after_the_listed_start_until_the_game_goes_live(monkeypatch):
    """Repro: past the listed start the loop fell back to polling.idle_s (300 s), so a kickoff was first seen minutes late."""
    games = {"g": NS(phase=Phase.PRE, start_time=NOW - 120)}
    calls, me = _poll_self(games, {"polling.idle_s": 300, "polling.live_s": 10, "polling.hot_s": 3})

    async def fake_sleep(s):
        calls.sleeps.append(s)
        raise asyncio.CancelledError
    monkeypatch.setattr(engine_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(engine_mod.time, "time", lambda: NOW)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Engine._poll_loop(me))
    assert calls.sleeps == [10]
