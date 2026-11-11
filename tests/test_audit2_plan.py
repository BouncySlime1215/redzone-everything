"""AUDIT-2, plan: a check that keeps crashing stops being retried.

Why: _check left a crashed target "pending", so step() re-tuned the hidden stick for it every 20 s, all night.
"""
import asyncio
from types import SimpleNamespace

from redzone import plan as P

NOW = 1_800_000_000.0


def _plan():
    e = {"game_id": "g1", "league": "nfl", "matchup": "A @ B", "start": NOW + 600, "start_et": "8:00 PM",
         "mine": False, "status": "ready", "why": "", "phase": "pre",
         "source": {"target": "youtubetv:espn", "label": "ESPN", "tunable": True, "app": "youtubetv"},
         "fallbacks": [], "gaps": [], "check": {"state": "pending", "target": None, "at": None, "detail": ""}}
    return {"day": "2027-01-15", "games": [e], "windows": [], "gaps": [], "note": "", "cant_reach": []}


def _tonight(tmp_path):
    async def boom():
        raise RuntimeError("adb exploded")

    ctl = SimpleNamespace(verify_hidden=lambda target: asyncio.ensure_future(boom()))
    eng = SimpleNamespace(cfg=SimpleNamespace(user_path=None), control=ctl, toast=lambda *a, **k: None)
    return P.Tonight(eng, data_dir=tmp_path, clock=lambda: NOW)


async def test_a_check_that_keeps_crashing_is_given_up_on(tmp_path):
    t = _tonight(tmp_path)
    plan = _plan()
    for _ in range(P.CHECK_CRASH_LIMIT):
        assert P.due(plan, NOW, 3600)            # still worth another try
        await t._check(plan, "youtubetv:espn")
    assert plan["games"][0]["check"]["state"] == "not checked"
    assert P.due(plan, NOW, 3600) == []
    assert "crashed" in plan["games"][0]["check"]["detail"]
