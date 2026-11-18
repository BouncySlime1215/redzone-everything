"""The PARK replay harness (integration plan unit 4, folded into unit 8): its fake sticks behave like the real ones
did on Nov 10, and the app's PARK controller replayed on recorded dev night 20261023 keeps Rule #1 at 0 / 0 / 0 with
every jump warranted.

Replayed with fake sticks is not measured on the TV.

Why: the replay is the acceptance run for the in-app controller before the flag is turned on; a fake that is kinder
than the real sticks (instant tunes, flips that never fail) would prove nothing.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402
from redzone.noggin.park_show import SHOW  # noqa: E402

needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


def rig_on(clock, **kw):
    return pr.FakeRig(clock, instant=False, delay=35, steps=2, reads=lambda g: True, **kw)


def play(clock, coro):
    return asyncio.run(clock.run(coro))


def test_a_fake_tune_takes_13_to_19_s_reports_no_place_meanwhile_and_ends_38_s_behind_live():
    for seed in range(12):
        clock = pr.FakeClock(1000.0)
        rig = rig_on(clock, seed=seed)

        async def go():
            step = asyncio.ensure_future(rig.tune(1, 0))
            await clock.sleep(pr.SESSION_ZERO)
            assert rig.game[1] is None                         # the position-0 seconds: nothing is taken as its place
            return await step
        ok, took = play(clock, go())
        assert ok and 12.7 <= took <= 21.5 and clock.now() - 1000.0 == pytest.approx(took)
        assert rig.game[1] == 0 and rig.read[1] and not rig.paused[1]
        assert rig.lag[1] + 35 == pytest.approx(38.0, abs=5.0)   # 2 LEFT steps and the read: about 38 s behind its live edge


def test_a_fake_flip_takes_about_3_s_and_a_stream_that_was_held_is_as_far_behind_as_it_was_held():
    clock = pr.FakeClock(0.0)
    rig = rig_on(clock, seed=3)
    rig.game = [0, 1]

    async def go():
        ok, frozen = await rig.pause(1, None)
        assert ok and rig.paused[1]
        lag0 = rig.lag[1]
        await clock.sleep(60.0)                                # held for a minute
        t0 = clock.now()
        out = await rig.jump(1, True, 0)
        return out, clock.now() - t0, lag0
    out, took, lag0 = play(clock, go())
    assert out == ("ok", "") and 2.7 <= rig.jump_s <= 3.7 and 3.0 <= took <= 4.4      # the flip, then the TV read back
    assert rig.input == 1 and not rig.paused[1] and rig.paused[0]                     # the stream left is paused at the swap
    assert rig.lag[1] - lag0 == pytest.approx(62.5, abs=2.0)                          # a minute held (and its read-back) further behind
    assert ("tv", "set_input", 1) in rig.sent and all("VOLUME" not in str(x) and "MUTE" not in str(x) for x in rig.sent)


def test_fake_failures_a_read_a_flip_and_the_apps_parked_only_jump():
    clock = pr.FakeClock(0.0)
    rig = rig_on(clock, hands="app")
    rig.game = [0, None]
    rig.fail.update({"read": 1, "flip": 1})

    async def go():
        assert (await rig.tune(1, 1))[0] and rig.read[1] is False                      # a label that could not be read: unread
        n = len(rig.sent)
        assert await rig.jump(1, False, 0) == ("kept", "the stick is not parked on this game") and len(rig.sent) == n
        assert (await rig.pause(1, None))[0]
        out = await rig.jump(1, True, 0)
        assert out == ("kept", "the flip did not confirm") and rig.input == 0 and not rig.paused[0]   # the picture up plays again
        await rig.finish()
        assert rig.paused == [False, False]
    play(clock, go())
    assert rig.join_playing is False and rig.deep is False


@needs_archive
def test_only_dev_nights_are_replayed():
    with pytest.raises(SystemExit, match="dev night"):
        pr.load_replay("20991231", 2)


@needs_archive
def test_the_any_shot_what_if_is_outside_rule_1_by_the_frozen_grader_and_the_show_never_uses_it():
    """Why the show has two masks: with the simulator's any-shot rule the replay's own judge says 0 / 0 / 0 while the
    frozen grader finds cuts in live play (a shot's 4 s join a stoppage or another shot's 4 s, and the padding is then
    measured to the ends of the merged run)."""
    loaded = pr.load_replay("20261023", 4)
    old = pr.result(pr.replay("20261023", 4, loaded=loaded, minutes=100, any_shot=True, grid=(0, 0)))
    assert (old["mid_entry"], old["mid_leave"], old["hl_leave"]) == (0, 0, 0)
    assert old["grader_entries_mid_play"] + old["grader_leaves_mid_play"] >= 2
    now = pr.result(pr.replay("20261023", 4, loaded=loaded, minutes=100))
    assert (now["grader_entries_mid_play"], now["grader_entries_mid_play_strict"], now["grader_leaves_mid_play"]) == (0, 0, 0)
    assert now["flips"] >= 30


@needs_archive
def test_night_20261023_two_games_whole_night_rule_1_is_zero_and_every_jump_is_warranted():
    show = pr.replay("20261023", 2)
    row = pr.result(show)
    # the bar: 0 / 0 / 0 by the FROZEN grader (cutgrade.grade), on where the pictures truly were
    assert (row["grader_entries_mid_play"], row["grader_entries_mid_play_strict"], row["grader_leaves_mid_play"]) == (0, 0, 0)
    assert row["grader_entries"] == row["grader_leaves"] == row["flips"] and row["joins_after_miss"] == 0
    assert (row["mid_entry"], row["mid_leave"], row["hl_leave"]) == (0, 0, 0)          # the replay's own judge (PARK's own mask)
    assert row["warrant_n"] == row["flips"] >= 40 and row["warranted"] == row["warrant_n"]
    assert 0.30 <= row["jumps_per_min"] <= 0.60                                        # the local runner's READY-B gave 0.50 here
    assert row["noplay_looks"] == 0 and row["break_lands"] <= 2                         # (a landing with over 10 s of a break still to come)
    assert not any("did not" in k or "not sent" in k for k in show.refused)            # no step failed (the fake was not told to fail)
    assert row["behind_max_s"] <= SHOW.late_cap and show.stop == ""
