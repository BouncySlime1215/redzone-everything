"""Noggin Reflex PICK v1: the rollout keeps the hard rules whatever the model says."""
from types import SimpleNamespace

import numpy as np

from redzone.noggin import pick as PK

C = {f: i for i, f in enumerate(PK.PICK_FEATURES)}


def _night(G=3, K=200, seed=0):
    rng = np.random.default_rng(seed)
    X = np.zeros((G, K, len(PK.PICK_FEATURES)), np.float32)
    X[:, :, C["leave_ok"]] = rng.random((G, K)) < .4
    X[:, :, C["land_ok"]] = rng.random((G, K)) < .5
    X[:, 0, C["land_ok"]] = 1
    X[:, :, C["game_left"]] = 2000
    reps = [SimpleNamespace(gid=f"g{g}") for g in range(G)]
    return X, reps, np.arange(K) * 5.0


def test_rollout_leaves_and_lands_only_on_dead_balls():
    for seed in range(20):
        X, reps, steps = _night(seed=seed)
        sc = np.random.default_rng(seed + 99).random(X.shape[:2])
        sw = PK.rollout(sc, X, reps, steps, 5.0, margin=0.0)
        gi = {r.gid: i for i, r in enumerate(reps)}
        for s in sw[1:]:
            k = int(s.t // 5)
            assert X[gi[s.frm], k, C["leave_ok"]] and X[gi[s.to], k, C["land_ok"]]


def test_your_team_late_game_is_forced_and_kept():
    X, reps, steps = _night()
    X[2, :, C["mine"]] = 1
    X[2, 100:, C["game_left"]] = 200          # your team's game reaches its last 5 minutes at step 100
    X[2, 100:, C["margin"]] = 3
    sc = np.zeros(X.shape[:2])
    sc[0] = 1.0                                # the model always prefers game 0
    sw = PK.rollout(sc, X, reps, steps, 5.0, margin=0.1)
    assert sw[-1].to == "g2" and sw[-1].t >= 100 * 5
    assert all(s.frm != "g2" for s in sw if s.t >= 100 * 5)
