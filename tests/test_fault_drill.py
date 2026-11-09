"""Fault drill scenarios added by WP-10 / Q14: visible-stick prompt, crash, update; queue tune, scrub, route.

Why: a real fault is when a mid-play cut would happen, so every new scenario must show 0 mid-play cuts, and
the queue ones must show the typed move (gamequeue.on_failure) and no flip except at a dead ball.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fault_drill", ROOT / "scripts" / "fault_drill.py")
fd = importlib.util.module_from_spec(spec)
sys.modules["fault_drill"] = fd
spec.loader.exec_module(fd)

from redzone.noggin import gamequeue as gq  # noqa: E402
from redzone.platform.faults import QueueTuner, World, FaultStick, drive_queue, failure_of  # noqa: E402
from redzone.tuners.tuners import TuneResult  # noqa: E402

VISIBLE = ["visible_prompt", "visible_app_crash", "visible_app_update"]
QUEUE = ["queue_never_started", "queue_link_refused", "queue_walled", "queue_blackout", "queue_scrub_failed",
         "queue_scrub_banner", "queue_stick_hung", "queue_no_route"]


@pytest.fixture(scope="module")
def results():
    return asyncio.run(fd.run(VISIBLE + QUEUE))


@pytest.mark.parametrize("name", VISIBLE + QUEUE)
def test_new_scenarios_pass_with_zero_mid_play_cuts(results, name):
    r = results[name]
    assert r["mid_play_cuts"] == 0 and r["ok"] and not r["stuck"], r


def test_visible_faults_restore_the_game_without_touching_the_other_stick(results):
    for name in VISIBLE:
        on = results[name]["on"]
        assert on["restored_after_s"] is not None and not on["other_stick_touched"] and on["tv_input_changes"] == 0
    assert results["visible_app_update"]["on"]["relinks"] >= 2     # the first links fail while it updates


def test_queue_moves_are_the_typed_ones(results):
    steps = lambda n: results[n]["detail"].split("moves ")[1].split("]")[0] + "]"  # noqa: E731
    assert steps("queue_never_started") == "['retry', 'next_route', 'ready']"
    assert steps("queue_link_refused") == "['next_route', 'ready']"
    assert steps("queue_walled") == "['stop']"
    assert steps("queue_scrub_failed") == "['stay_live']"
    assert steps("queue_stick_hung") == "['wait_stick']"
    assert "flipped False" in results["queue_walled"]["detail"] and "level R0" in results["queue_walled"]["detail"]


def test_scrub_failure_stays_at_the_live_edge_and_is_not_flipped_to(results):
    d = results["queue_scrub_failed"]["detail"]
    assert "level R3" in d and "flipped False" in d      # R3 is not flippable by default (queue.r3_flippable off)


def test_flip_waits_for_the_dead_ball():
    async def go():
        world = World(safe=lambda t: not (0.3 <= t < 0.6))
        world.t0 -= 0.31                                    # mid-play right now
        b = FaultStick("b:5555", world)
        entry = gq.Entry("g", routes=(gq.Route("youtubetv", "youtubetv:espn"),), level=gq.Level.R1)
        run = await drive_queue(entry, QueueTuner(), b, world)
        return run, world
    run, world = asyncio.run(go())
    assert run.flipped == "b:5555" and world.mid_play_cuts == 0 and world.cuts[0].at >= 0.29


def test_flip_is_refused_when_no_dead_ball_comes_in_time():
    async def go():
        world = World(safe=lambda t: False)
        b = FaultStick("b:5555", world)
        entry = gq.Entry("g", routes=(gq.Route("youtubetv", "youtubetv:espn"),), level=gq.Level.R1)
        return await drive_queue(entry, QueueTuner(), b, world, wait_safe_s=0.2), world
    run, world = asyncio.run(go())
    assert run.flipped is None and world.cuts == []


def test_failure_classifier_maps_text_to_typed_failures():
    f = lambda e: failure_of(TuneResult(False, "t", 0.1, [], e))  # noqa: E731
    assert f("purchase or sign-in screen") == gq.Failure.WALLED
    assert f("stick not responding") == gq.Failure.STICK_HUNG
    assert f("video never started") == gq.Failure.NEVER_STARTED
    assert f("") == gq.Failure.UNKNOWN


def test_a_walled_route_is_tuned_once_and_never_pressed():
    async def go():
        world = World()
        b = FaultStick("b:5555", world)
        tuner = QueueTuner({"prime:x": "walled"})
        entry = gq.Entry("g", routes=(gq.Route("prime", "prime:x"),), level=gq.Level.R1)
        return await drive_queue(entry, tuner, b, world), tuner, b
    run, tuner, b = asyncio.run(go())
    assert tuner.calls == [("b:5555", "prime:x")] and run.keys_after_wall == 0 and run.flipped is None
    assert not any("keyevent" in c for c in b.log)
