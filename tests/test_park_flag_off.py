"""PARK controller, flag off (integration plan unit 8, risk R9): with `park.enabled` off the app decides a recorded
night exactly as before this unit, the PARK owner is never taken, and nothing of the show is imported or made.

Why: the keystone edits `Decider.decide`, `Engine.run` and the standby plan; each edit must be inert until the flag
is on AND the engine has started a show.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from redzone.decider.decider import Decider, Decision
from redzone.machines import decider as machines
from test_timing_truth import _night_decisions

# The recorded NBA night tests/test_timing_truth.py replays, hashed at 85ff0354 (branch park-hands, the commit this unit
# was cut from) with the same helper: 923 decisions, 9 switches. A unit that changes decisions on purpose replaces it.
NIGHT_BEFORE = "2530670bce6d23bb9055acba5fc14807c03e5de14e836b9f01fc979e854e6221"


def _hash(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def test_the_recorded_night_decides_byte_for_byte_as_before_this_unit():
    d = _night_decisions()
    assert (len(d[0]), len(d[1])) == (923, 9)
    assert _hash(d) == NIGHT_BEFORE


def test_flag_off_and_flag_on_without_a_show_decide_the_same_and_park_is_never_asked(monkeypatch):
    base = _night_decisions()
    assert len(base[0]) > 500 and base[1]
    assert _night_decisions(**{"park.enabled": False}) == base
    monkeypatch.setattr(Decider, "_park", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the PARK owner was asked")))
    assert _night_decisions() == base
    assert _night_decisions(**{"park.enabled": True}) == base          # the flag alone changes nothing: no show holds the sticks


def test_no_decider_takes_the_park_owner_until_the_engine_hands_it_over(cfg):
    dec = Decider(cfg, delays=SimpleNamespace())
    assert dec.park_show is None                                       # only Engine._park_loop sets it, and only with the flag on
    assert "park" in machines.OWNER.states and "park" in machines.STANDBY_PLAN.states


@pytest.mark.asyncio
async def test_with_the_flag_off_the_engine_loop_only_sleeps_and_imports_nothing_of_the_show(monkeypatch):
    from redzone.engine import Engine
    for name in ("redzone.park_run", "redzone.showboard", "redzone.noggin.park_show"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    naps = []

    async def nap(s):
        naps.append(s)
        if len(naps) >= 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, "sleep", nap)
    eng = SimpleNamespace(cfg={"park.enabled": False}, park=None, decider=SimpleNamespace(park_show=None))
    with pytest.raises(asyncio.CancelledError):
        await Engine._park_loop(eng)
    assert len(naps) == 3 and eng.park is None and eng.decider.park_show is None
    assert not any(n in sys.modules for n in ("redzone.park_run", "redzone.showboard", "redzone.noggin.park_show"))


def test_the_park_owner_turns_every_other_switch_into_a_stay_and_only_while_a_show_holds_the_sticks(cfg):
    dec = Decider(cfg, delays=SimpleNamespace())
    holds = {"v": False}
    dec.park_show = lambda: holds["v"]
    stay = Decision(0.0, "stay", None, "nothing to do", "idle", "open")
    assert dec._own(stay, {}, 0.0)[0] != "park"
    holds["v"] = True
    assert dec._own(stay, {}, 0.0) == ("park", "park_show")
    sw = Decision(0.0, "switch", "g2", "a better game", "idle", "open")
    out = dec._park(sw, 0.0, (dec._home, dec._home_at))
    assert out.action == "stay" and out.reason.startswith("PARK show:") and out.want == "g2"
    assert dec._park(stay, 0.0, (dec._home, dec._home_at)) is stay
    dec.user.paused = True                                             # your pause still comes first
    assert dec._own(stay, {}, 0.0)[0] == "user"
