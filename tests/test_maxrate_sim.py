"""[P1-MAXRATE] The rate ceiling model behind --maxrate: Rule #1 is measured, the knobs move the rate the right way, and
the editor sim without the flag is untouched."""
import json
import random
from dataclasses import replace
from datetime import datetime, timezone

import scripts.buffered_jump_sim as sim
from redzone.models import League


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def plays(gid, t0, n=240, every=40.0, breaks=()):
    """(wall, id, kind, text, type, period) rows: a play every `every` s, a two-minute warning at the given indexes."""
    out, t = [], t0
    for i in range(n):
        if i in breaks:
            out.append((t, f"{gid}b{i}", "other", "Two-Minute Warning", "Two-minute warning", 1))
            t += 180.0
        out.append((t, f"{gid}{i}", "rush", "run for 3", "Rush", 1))
        t += every
    return out


def slate(k=4, **kw):
    return [(str(9000 + i), plays(str(9000 + i), 1_700_000_000.0 + 7.0 * i, **kw)) for i in range(k)]


def run(p, games=None, seed=3):
    return sim.run_maxrate(games or slate(), p, random.Random(seed), League.NFL)


def test_skipping_the_huddle_beats_rolling_through_it():
    fast = sim.MR(games=2, seek_s=4.0, prep_s=15.0)
    roll = replace(fast, skip=False, prep_s=60.0, seek_step_s=0.0)
    assert run(fast)["jumps"] > run(roll)["jumps"]


def test_rule_one_holds_with_the_default_margin():
    r = run(sim.MR())
    assert r["jumps"] > 0 and r["joins_mid_play"] == 0 and r["leaves_mid_play"] == 0


def test_the_join_counter_can_fail():
    """Same plays, no margin and a sloppy stick: the truth check has to see the late landings."""
    r = run(sim.MR(guard_sigmas=0.0, err_sd=4.0, truth="tight", snap_known=True, snap_sd=0.0))
    assert r["joins_mid_play"] > 0


def test_a_slower_prep_never_helps_across_games():
    fast, slow = sim.MR(prep_s=4.0, sticks=3), sim.MR(prep_s=60.0, sticks=3)
    assert run(fast)["jumps"] >= run(slow)["jumps"]


def test_one_tv_cannot_beat_its_clip_length():
    p = sim.MR(sticks=4, seek_s=1.0, prep_s=1.0, seek_step_s=0.0, flip_s=1.0)
    r = run(p, slate(k=8))
    clip = r["clip_s"] / r["jumps"]
    assert r["jumps"] / r["minutes"] * 10.0 <= 600.0 / (clip + p.flip_s) + 0.5


def test_blocks_stay_on_n_games():
    two = run(sim.MR(games=2, block_s=300.0))
    every = run(sim.MR(games=0))
    assert two["supply"] < every["supply"] * 0.7 and two["games_used"] == 4


def test_no_commercial_seconds_even_with_nothing_to_cut_to():
    games = slate(k=2, breaks=(30, 60, 90))
    r = run(sim.MR(games=2, prep_s=60.0, seek_s=30.0), games)
    assert r["ad_s"] == 0.0


def test_a_hurry_up_play_is_no_job_but_stays_in_truth():
    q = [(1000.0 + 15.0 * i, f"p{i}", "rush", "run", "Rush", 1) for i in range(5)]
    items = sim.mr_items("g", q, League.NFL, sim.MR(), random.Random(1), 20.0)
    assert len(items) == 5 and not any(it.clean for it in items)


def test_replays_need_a_repeat_allowance():
    sparse = slate(k=2, every=120.0)    # the TV runs out of new plays
    base = sim.MR(sticks=3, seek_s=1.0, prep_s=1.0, seek_step_s=0.0, replay_depth_s=600.0)
    assert run(replace(base, replay_repeats=1), sparse)["repeats"] > 0 and run(base, sparse)["repeats"] == 0


def test_basketball_truth_ignores_tight():
    """Tight dwell is a snap model: a basketball landing is still judged on the live stretches."""
    q = [(1000.0 + 20.0 * i, f"p{i}", "shot", "A misses jumper", "Jump Shot", 1) for i in range(30)]
    tight = sim.MR(truth="tight", snap_known=True)
    assert sim.mr_truth_spans("g", q, League.NBA, [], tight) == sim.play_spans(q, League.NBA)


def test_flag_runs_and_writes_summary(tmp_path):
    def game(gid, t0):
        ps = [{"id": f"{gid}{i}", "wallclock": _iso(t0 + 40 * i), "type": {"text": "Rush"}, "text": "run",
               "period": {"number": 1}} for i in range(120)]
        return {"drives": {"previous": [{"plays": ps}]}}
    for gid, off in (("9001", 0), ("9002", 13), ("9003", 27)):
        (tmp_path / f"summary_{gid}.json").write_text(json.dumps(game(gid, 1_700_000_000 + off)))
    out = tmp_path / "o.json"
    assert sim.main(["--archive", str(tmp_path), "--maxrate", "--mr-games", "2", "--json", str(out)]) == 0
    s = json.loads(out.read_text())["summary"]
    assert s["joins_mid_play"] == 0 and s["jumps_per_10"] > 0
