"""autonomy.Scheduler: each job is one explicit machine (autonomy.job: idle/waiting/running/retrying)."""
import asyncio
import json
from datetime import datetime

from redzone.autonomy import ET, At, Job, JobResult, Scheduler

T4 = datetime(2026, 11, 3, 4, 0, tzinfo=ET).timestamp()   # a 4:00 AM slot


class Clock:
    def __init__(self, t=T4 + 60):
        self.t = t

    def __call__(self):
        return self.t


def sched(tmp_path, work, *, guard=None, attempts=1, clock=None, group=None, extra=()):
    clock = clock or Clock()
    jobs = [Job("chk", "Nightly check", work, when=(At(4, 0),), retry_s=300, guard=guard, attempts=attempts,
                group=group), *extra]
    return Scheduler(jobs, tmp_path / "autonomy.jsonl", clock=clock), clock


def log(tmp_path):
    return [json.loads(x) for x in (tmp_path / "autonomy.jsonl").read_text().splitlines()]


async def test_due_job_runs_once_and_goes_back_to_idle(tmp_path):
    gate = asyncio.Event()
    seen = []

    async def work():
        seen.append(s.state["chk"].m.state)
        await gate.wait()
        return JobResult(True, "fine")
    s, clock = sched(tmp_path, work)
    tasks = await s.tick()
    await asyncio.sleep(0)
    assert seen == ["running"] and s.state["chk"].running and s.status()[0]["state"] == "running"
    assert await s.tick() == []                          # single-flight
    gate.set()
    await asyncio.gather(*tasks)
    st = s.state["chk"]
    assert st.m.state == "idle" and st.done_slot == T4 and not st.running
    assert await s.tick() == []                          # this slot is done
    assert [h[1:3] for h in st.m.history] == [("idle", "running"), ("running", "idle")]


async def test_unsafe_job_waits_then_window_closes_as_missed(tmp_path):
    async def guard():
        return "a game is on screen"
    s, clock = sched(tmp_path, lambda: None, guard=guard)
    assert await s.tick() == []
    st = s.state["chk"]
    assert st.m.state == "waiting" and st.m.why == "a game is on screen"
    clock.t = T4 + 3700                                  # past the window
    await s.tick()
    assert st.m.state == "idle" and st.done_slot == T4
    assert [r["outcome"] for r in log(tmp_path)] == ["waiting", "missed"]


async def test_failed_try_retries_inside_its_window(tmp_path):
    results = [JobResult(False, "flaky"), JobResult(True, "ok")]

    async def work():
        return results.pop(0)
    s, clock = sched(tmp_path, work, attempts=2)
    await asyncio.gather(*await s.tick())
    st = s.state["chk"]
    assert st.m.state == "retrying" and st.done_slot is None
    assert await s.tick() == []                          # not before retry_s
    clock.t += 301
    await asyncio.gather(*await s.tick())
    assert st.m.state == "idle" and st.done_slot == T4 and st.ok is True


async def test_run_now_while_running_is_refused_and_returns_to_the_window_state(tmp_path):
    gate = asyncio.Event()

    async def work():
        await gate.wait()
        return JobResult(True, "fine")
    s, clock = sched(tmp_path, work)
    tasks = await s.tick()
    await asyncio.sleep(0)
    assert (await s.run_now("chk")).message.endswith("already running")
    gate.set()
    await asyncio.gather(*tasks)
    res = await s.run_now("chk")                         # by hand, outside the slot logic
    assert res.ok and s.state["chk"].m.state == "idle"


async def test_run_now_during_the_safety_check_wins_and_tick_does_not_mark_it_waiting(tmp_path):
    """Race: tick() awaits the guard; a phone "run now" starts the job meanwhile. tick must not log a
    'waiting' for (or start a second copy of) a job that is running."""
    checking, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def guard():
        nonlocal calls
        calls += 1
        if calls == 1:
            checking.set()
            await release.wait()
            return "busy for a moment"
        return None

    gate = asyncio.Event()

    async def work():
        await gate.wait()
        return JobResult(True, "ran")
    s, clock = sched(tmp_path, work, guard=guard)
    tick = asyncio.create_task(s.tick())
    await checking.wait()
    now = asyncio.create_task(s.run_now("chk", force=True))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert s.state["chk"].running
    release.set()
    assert await tick == []
    assert s.state["chk"].m.state == "running" and "Waiting" not in s.state["chk"].message
    gate.set()
    assert (await now).ok
    assert all(r["outcome"] != "waiting" for r in log(tmp_path))


async def test_group_mates_never_overlap(tmp_path):
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return JobResult(True, "ok")

    async def quick():
        return JobResult(True, "ok")
    other = Job("other", "Other job", quick, when=(At(4, 0),), group="sticks")
    s, clock = sched(tmp_path, slow, group="sticks", extra=(other,))
    tasks = await s.tick()
    assert len(tasks) == 1 and s.state["other"].m.state == "waiting"
    gate.set()
    await asyncio.gather(*tasks)
    clock.t += 301
    await asyncio.gather(*await s.tick())
    assert s.state["other"].m.state == "idle" and s.state["other"].ok


async def test_a_cancelled_run_goes_back_to_idle(tmp_path):
    async def work():
        await asyncio.Event().wait()
    s, clock = sched(tmp_path, work)
    tasks = await s.tick()
    await asyncio.sleep(0)
    tasks[0].cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert s.state["chk"].m.state == "idle" and not s.state["chk"].running
