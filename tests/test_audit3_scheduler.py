"""AUDIT-3: Scheduler._restore must not treat a failed try with retries left as the end of its slot."""
import asyncio
import json

from redzone.autonomy import At, Job, JobResult, Scheduler
from tests.test_autonomy_scheduler import T4, Clock


def _make(tmp_path, recs, attempts, runs):
    path = tmp_path / "autonomy.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))

    async def work():
        runs.append(1)
        return JobResult(True, "fine")
    return Scheduler([Job("chk", "Nightly check", work, when=(At(4, 0),), retry_s=300, attempts=attempts)],
                     path, clock=Clock(T4 + 600))


async def test_restart_retries_a_failed_slot_with_tries_left(tmp_path):
    runs = []
    rec = {"t": T4 + 60, "job": "chk", "outcome": "failed", "ok": False, "message": "x", "slot": T4}
    s = _make(tmp_path, [rec], 3, runs)
    assert s.state["chk"].done_slot is None
    await asyncio.gather(*await s.tick())
    assert runs == [1]


async def test_restart_keeps_a_slot_whose_tries_are_used_up(tmp_path):
    runs = []
    rec = {"t": T4 + 60, "job": "chk", "outcome": "failed", "ok": False, "message": "x", "slot": T4}
    s = _make(tmp_path, [rec, rec], 2, runs)
    assert s.state["chk"].done_slot == T4
    assert await s.tick() == [] and runs == []
