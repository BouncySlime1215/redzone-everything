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


def _play(i, w, text="jumper", typ="Jump Shot"):
    return SimpleNamespace(id=str(i), wallclock=w, clock=600.0 - i, type_text=typ, kind="play", text=text, period=1,
                           scoring=False, score=(i, i))


def test_shipped_model_is_numpy_and_round_trips(tmp_path):
    m = PK.model_for("nba")
    assert m is not None and m.meta["features"] == list(PK.PICK_FEATURES)
    X = np.random.default_rng(0).random((12, len(PK.PICK_FEATURES))).astype(np.float32)
    p = m.proba(X)
    assert p.shape == (12,) and ((p >= 0) & (p <= 1)).all()
    m.save(tmp_path / "m.npz")
    assert np.allclose(PK.PickModel.load(tmp_path / "m.npz").proba(X), p)


def test_live_game_dedupes_and_keeps_wall_order():
    g = PK.LiveGame(PK.League.NBA, "g1", "A @ B")
    g.add([_play(1, 100.0), _play(3, 130.0)])
    g.add([_play(2, 115.0), _play(3, 130.0), SimpleNamespace(**{**vars(_play(4, 0)), "wallclock": None})])
    assert g.wall == [100.0, 115.0, 130.0] and [p["id"] for p in g.plays] == ["1", "2", "3"]


def test_live_choice_never_leaves_your_late_close_game():
    games = [PK.LiveGame(PK.League.NBA, f"g{k}", m) for k, m in enumerate(("A @ WSH", "C @ D"))]
    for g in games:
        g.add([_play(i, 1000.0 + 20 * i) for i in range(30)])
    target, scores = PK.live_choice(games, "g1", 1800.0, {"g0": 116.0, "g1": 116.0}, {"WSH"}, 5.0, PK.model_for("nba"))
    assert set(scores) == {"g0", "g1"} and all(0 <= v <= 1 for v in scores.values())
    assert target in (None, "g0")


def test_pick_ships_off_and_never_overrides_crunch():
    from redzone.config import Config
    c = Config()
    assert not c.get("noggin.pick.enabled") and not c.get("noggin.pick.shadow")
    assert c.get("noggin.pick.mode") == "breaks" and "crunch" in c.get("noggin.pick.keep_kinds")
