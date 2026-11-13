"""Decisive-Q dataset world (A-02): causal features, rollouts that start from the decision's own state, the mask.

  - the features at a decision point equal the features rebuilt from the feed CUT at the decision's feed cutoff te
    (fence.cut_feed, the cut-data helper the feature store's own test uses), and a cut one row earlier changes them;
  - the recording planner decides exactly as the plain planner, and the core stepped one tick at a time equals one run;
  - a rollout with nothing forced, from the snapshot, earns what the base path earned over the same seconds;
  - every legal option passes the strict mask with the read states logged; every masked one carries the mask's reason.
"""
from __future__ import annotations

import numpy as np
import pytest

from redzone import featurestore as fs
from redzone.lab import fence
from redzone.lab.gym2 import qdata as Q
from redzone.lab.gym2.env import replay_module
from redzone.lab.gym2.mask import Rule1Mask
from redzone.models import League
from redzone.noggin import park_core
from redzone.noggin.park_plan import TUNE, Plan, Planner
from tests.qdata_fakes import write_archive


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = write_archive(tmp_path_factory.mktemp("qworld") / "archive")
    pool = Q.night_pool(root)
    loader = Q.QLoader(root)
    rows = []
    for i in range(16):
        d = Q.draw_slate(pool, 21, i)
        r, _ = Q.build_slate(d, loader, episode_min=8.0, points=4, master_seed=21)
        rows.extend((d, x) for x in r)
    assert len(rows) >= 6
    return root, pool, loader, rows


def test_features_at_decision_time_equal_features_from_the_feed_cut_there(world):
    _, _, loader, rows = world
    checked = 0
    for d, row in rows[:6]:
        te, lag = row["features"]["te"], row["lag"]
        for g, gid in enumerate(row["games"]):
            r, truth, _, facts = loader.game(row["leagues"][g], gid)
            wall = fs.fs_wall(r.plays)
            k = max(i for i, w in enumerate(wall) if w <= te)            # the last row stamped at or before te
            cut = fs.fs_parse("nba", fence.cut_feed(League.NBA, r.data, k))
            got = [Q.qd_round(v) for v in fs.fs_rows("value_v3", "nba", cut, [te + lag], facts, 0, lag)[0]]
            assert got == row["features"]["games"][g], f"slate {row['slate']} game {g}: a feature reads past te"
            if k >= 3:
                early = fs.fs_parse("nba", fence.cut_feed(League.NBA, r.data, k - 3))
                before = [Q.qd_round(v) for v in fs.fs_rows("value_v3", "nba", early, [te + lag], facts, 0, lag)[0]]
                assert before != got                                       # the check is not vacuous
            checked += 1
    assert checked >= 6


def _setup(world, i=0):
    root, pool, loader, rows = world
    d = Q.draw_slate(pool, 21, next(k for k in range(50) if Q.draw_slate(pool, 21, k).pad != 49))
    w, why = Q.slate_world(d, loader)
    assert w is not None, why
    return w


def test_the_recorder_decides_as_the_planner_and_ticks_equal_one_run(world):
    w = _setup(world)
    pr = replay_module()
    QRecorder, _ = Q._planner_classes()
    t0, t1 = w.a, min(w.b, w.a + 900)
    st_a, S_a = park_core.run(w.night, pr._Hindsight(), w.eng, t0=t0, t1=t1, planner=Planner(Plan(jump_bonus=3.0)))
    rec = QRecorder(Plan(jump_bonus=3.0))
    rec.qd_seen = []
    S, st = None, None
    for t in range(t0, t1):
        st, S = park_core.run(w.night, pr._Hindsight(), w.eng, t0=t, t1=t + 1, state=S, stats=st, planner=rec)
    assert st.flips == st_a.flips and st.v2_seen == st_a.v2_seen and st.hops == st_a.hops
    assert (S.sg, S.sb, S.hg, S.hb) == (S_a.sg, S_a.sb, S_a.hg, S_a.hb) and rec.qd_seen


def test_a_rollout_with_nothing_forced_earns_what_the_base_path_earned(world):
    w = _setup(world)
    pr = replay_module()
    QRecorder, _ = Q._planner_classes()
    pl = QRecorder(Plan(jump_bonus=3.0))
    pl.qd_seen = []
    t_fork = w.a + 300
    st, S = park_core.run(w.night, pr._Hindsight(), w.eng, t0=w.a, t1=t_fork, planner=pl)
    S0, st0 = Q._fork(S, st)
    import copy
    pl0 = copy.copy(pl)
    before = st.v2_seen
    st, S = park_core.run(w.night, pr._Hindsight(), w.eng, t0=t_fork, t1=t_fork + Q.QD_HORIZON, state=S, stats=st, planner=pl)
    r = Q._rollout(w, S0, st0, pl0, t_fork, None, -1, Q.QD_HORIZON, pr._Hindsight())
    assert r["q"] == Q.qd_round(st.v2_seen - before) and r["jumps"] == sum(1 for f in st.flips if f[0] >= t_fork)


def test_every_legal_option_passes_the_strict_mask_and_has_a_q(world):
    _, _, loader, rows = world
    for d, row in rows:
        w, _ = Q.slate_world(d, loader)
        m = Rule1Mask(w.reps, w.night.base, level="strict")
        assert row["mask_level"] == "strict" and row["options"][0]["route"] == "stay"
        for o in row["options"]:
            assert isinstance(o["q"], float) and isinstance(o["must"], float)
            if o["route"] == "stay":
                continue
            jr = row["reads"]["tune"] if o["route"] == "tune" else row["reads"]["hidden"]
            assert m.allows(row["features"]["state"][0], o["i"], o["g"], o["j"], row["reads"]["screen"], jr).ok
            assert o["done"] in (True, False) and o["tau"] <= Q.QD_WINDOW
        for x in row["masked"]:
            assert x["why"] and x["route"] in Q.ROUTE_NAMES
        assert row["n_legal"] == len(row["options"]) and row["n_masked"] == len(row["masked"])
        qs = [o["q"] for o in row["options"]]
        d2 = Q.decisive_of(qs)
        assert (row["gap"], row["decisive"], row["tie"]) == (d2["gap"], d2["decisive"], d2["tie"])
        assert row["features"]["set"] == fs.fs_tag("value_v3") and len(row["features"]["games"]) == len(row["games"])
    assert TUNE == Q.ROUTE_NAMES.index("tune")
