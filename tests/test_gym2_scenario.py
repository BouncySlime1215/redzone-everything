"""Gym v2 slice 2 (task A-01): the scenario sampler draws from a seed only, logs every draw so an episode can be run
again exactly, and its draws match the measured numbers it cites (scenario.MEASURED).

Why: EXP1b failed on a practice world with too little variation; variation is only useful if it is the measured kind
and every episode can be reproduced.
"""
from __future__ import annotations

import json
import random
import statistics
from pathlib import Path

import pytest

from redzone.lab.gym2 import env as gym
from redzone.lab.gym2 import scenario as S

ROOT = Path(__file__).resolve().parent.parent
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


def test_a_draw_comes_from_its_seed_alone_and_round_trips_through_the_log(tmp_path):
    a, b = S.sample(11, 4), S.sample(11, 4)
    assert a == b and a != S.sample(12, 4)
    log = tmp_path / "draws.jsonl"
    row = S.log_draw(log, night="20261023", games=4, episode_seed=5, scenario=a)
    back = json.loads(log.read_text().splitlines()[0])
    assert back == json.loads(json.dumps(row)) and back["sampler"] == S.SAMPLER
    assert S.Scenario.from_dict(back["scenario"]) == a


def test_identity_is_the_default_and_changes_nothing_it_builds():
    assert S.IDENTITY.identity and S.Scenario(name="x").identity and not S.sample(1, 4).identity
    assert S.IDENTITY.build_kwargs() == {}


def test_draws_match_the_measured_numbers():
    draws = [S.sample(s, 4) for s in range(4000)]
    rungs = [d.rung for d in draws]
    for r in S.RUNGS:
        assert abs(rungs.count(r) / len(draws) - 0.25) < 0.03
    assert {d.pad_s for d in draws if d.rung == "R1"} == {10, 8} and {d.pad_s for d in draws if d.rung == "R3"} == {1, 2}
    assert {d.pad_s for d in draws if d.rung == "R0"} == {None}
    by = {"league": [], "espn": []}
    for d in draws:
        for src, lag in zip(d.feed_src, d.feed_lag_s):
            by[src].append(lag)
    for src, lags in by.items():
        assert abs(len(lags) / (4 * len(draws)) - S.FEED_W[src]) < 0.02
        assert abs(statistics.median(lags) - S.MEASURED["feed_median_s"][src][0]) < 0.15
        assert abs(statistics.pstdev(lags) - S.MEASURED["feed_game_sd_s"][src][0]) < 0.1
    sds = [d.tune_sd for d in draws]
    assert min(sds) >= 1.2 and max(sds) <= 2.0 and abs(statistics.mean(sds) - 1.6) < 0.02
    assert all(d.p_read_fail == pytest.approx(0.3) and d.p_park_fail == pytest.approx(1 / 19) for d in draws)
    assert abs(sum(d.steps == "on" for d in draws) / len(draws) - 0.5) < 0.03


def test_a_rung_pads_a_read_stream_at_exactly_its_seconds():
    from redzone.noggin.park_show import ShowTiming
    tm = ShowTiming.case("tonight", 4, delay=35)
    for p in (1, 2, 8, 10):
        out = S.rung_timing(tm, p)
        assert [out.pad(g) for g in range(4)] == [p] * 4
        assert all(out.pad(g, False) >= p for g in range(4))
    nok = ShowTiming.case("nok", 4, delay=35)
    assert [S.rung_timing(nok, 2).pad(g) for g in range(4)] == [nok.pad(g) for g in range(4)]   # no K keeps its prior


def test_late_rows_hold_back_the_rows_after_them_and_no_tail_is_the_plain_lag():
    wall = [0.0, 5.0, 10.0, 15.0]
    assert S.arrivals(wall, 8.0, 0.0, 0.0, 0.0, random.Random(0)) == [8.0, 13.0, 18.0, 23.0]
    out = S.arrivals(wall, 0.0, 1.0, 30.0, 0.0, random.Random(0))          # every row 30 s late
    assert out == pytest.approx([30.0, 35.0, 40.0, 45.0])
    assert all(b >= a for a, b in zip(out, out[1:]))


@needs_archive
def test_the_late_row_tail_has_the_measured_median_and_p90_on_recorded_stamps():
    pr = gym.replay_module()
    rs = pr.load_replay("20261023", 4)[0]
    for src in ("league", "espn"):
        lag = S.MEASURED["feed_median_s"][src][0]
        q = [S.tail_quantiles(r.wall, lag, S.TAIL_P, S.TAIL_MED[src], S.TAIL_SIG, seed=k) for k in range(5) for r in rs]
        assert abs(statistics.median(x[0] for x in q) - lag) < 1.0
        assert abs(statistics.median(x[1] for x in q) - S.MEASURED["feed_p90_s"][src][0]) < 8.0


@needs_archive
def test_an_episode_runs_again_exactly_from_its_logged_draw(tmp_path):
    log = tmp_path / "d.jsonl"
    env = gym.GymV2("20261023", 4, scenario=S.sample(4, 4), seed=3, minutes=8, draw_log=log)
    first = env.run().digest()
    row = json.loads(log.read_text().splitlines()[0])
    again = gym.GymV2.from_draw(row, minutes=8).run().digest()
    assert again == first
    assert first != gym.GymV2("20261023", 4, seed=3, minutes=8).run().digest()     # the draw did change the world


@needs_archive
def test_fake_step_times_sit_near_the_tv_measurements():
    """The fake sticks' step times (park_replay.FakeRig with STEP_COSTS off/on) against L112/L116. Gym v2 inherits the
    replay's rig unchanged; its jump runs about 0.7 s slower than the TV in both modes (named in the PR), so the bar
    here is 1 s."""
    want = S.MEASURED["steps_s"][0]
    for steps, jump in (("off", want["jump_off"]), ("on", want["jump_on"])):
        env = gym.GymV2("20261023", 4, scenario=S.Scenario(name="t", steps=steps), minutes=20)
        env.reset()
        rig, took = env.show.rig, {}
        for name in ("tune", "pause", "jump"):
            f = getattr(rig, name)

            async def w(*a, _f=f, _n=name, **k):
                t0 = rig.clock.now()
                out = await _f(*a, **k)
                took.setdefault(_n, []).append(rig.clock.now() - t0)
                return out
            setattr(rig, name, w)
        env.run()
        assert abs(statistics.mean(took["tune"]) - want["tune"]) < 1.0
        assert abs(statistics.mean(took["pause"]) - want["park"]) < 0.5
        assert abs(statistics.mean(took["jump"]) - jump) < 1.0
