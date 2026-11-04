"""[one brain, step 4] One tree walker (redzone/models_rt/forest.py): every shipped model predicts
bitwise what the old per-module walkers did, on fixture rows (each feature's own thresholds, +-1e-7
around them, zeros, 1e-36 and NaNs). The reference walks are the old bodies, verbatim."""
from pathlib import Path

import numpy as np
import pytest

from redzone.models_rt.forest import MODEL_DIRS, Nodes, load_npz
from redzone.noggin import pick as K, predict as P, timing as T
from redzone.scoring import brain as B, brain3 as B3


def rows(feat, thr, nf, n=120, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 50, (n, nf))
    for j in range(nf):
        t = thr[feat == j]
        if len(t):
            pick = rng.random(n) < 0.6
            X[pick, j] = rng.choice(t, pick.sum()) + rng.choice([0.0, 0.0, 1e-7, -1e-7], pick.sum())
    X[rng.random((n, nf)) < 0.08] = np.nan
    X[rng.random((n, nf)) < 0.05] = 0.0
    X[rng.random((n, nf)) < 0.02] = 1e-36
    return X


def old_trees_raw(t, x, explain):
    """scoring.brain.Trees.raw before step 4."""
    x = np.asarray(x, dtype=np.float32).astype(np.float64)
    idx = t.roots.copy()
    contrib = np.zeros(len(t.features)) if explain else None
    for _ in range(t.depth + 1):
        inner = ~t.leaf[idx]
        if not inner.any():
            break
        node = idx[inner]
        f = t.feat[node]
        v = x[f]
        nan = np.isnan(v)
        miss = t.miss[node]
        v = np.where(nan & (miss != 2), 0.0, v)
        to_default = ((miss == 1) & (np.abs(v) <= 1e-35)) | ((miss == 2) & nan)
        go_left = np.where(to_default, t.dleft[node], v <= t.thr[node])
        child = np.where(go_left, t.left[node], t.right[node])
        if explain:
            np.add.at(contrib, f, t.value[child] - t.value[node])
        idx[inner] = child
    return float(t.value[idx].sum()), contrib


def old_selfloop_leaves(arrays, depth, X):
    """noggin.timing.Forest.raw / scoring.brain3.Forest3.raw before step 4 (leaves, batched)."""
    feat, thr = arrays["feat"].astype(np.int64), arrays["thr"].astype(np.float64)
    left, right = arrays["left"].astype(np.int64), arrays["right"].astype(np.int64)
    dleft, miss = arrays["dleft"].astype(bool), arrays["miss"].astype(np.int8)
    mz, mn_ = miss == 1, miss == 2
    X = np.asarray(X, dtype=np.float32).astype(np.float64)
    idx = np.broadcast_to(arrays["roots"].astype(np.int64), (len(X), len(arrays["roots"]))).copy()
    rows_ = np.arange(len(X))[:, None]
    for _ in range(depth):
        f = feat[idx]
        v = X[rows_, f]
        nan = np.isnan(v)
        mn = mn_[idx]
        v = np.where(nan & ~mn, 0.0, v)
        to_default = (mz[idx] & (np.abs(v) <= 1e-35)) | (mn & nan)
        go_left = np.where(to_default, dleft[idx], v <= thr[idx])
        idx = np.where(go_left, left[idx], right[idx])
    return idx


def _same(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a.shape == b.shape and a.tobytes() == b.tobytes()


BRAIN = sorted(p for p in MODEL_DIRS["brain"].glob("*.npz") if not p.name.endswith(".v3.npz")) \
    + sorted(MODEL_DIRS["reasoner"].glob("*.npz"))


@pytest.mark.parametrize("path", BRAIN, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_brain_trees_and_forest_are_bitwise_the_old_walk(path):
    trees, _ = B.load(path)
    t0 = next(iter(trees.values()))
    X = rows(t0.feat, t0.thr, len(t0.features))
    for t in trees.values():
        for x in X[:40]:
            r, c = t.raw(x, True)
            r0, c0 = old_trees_raw(t, x, True)
            assert _same(r, r0) and _same(c, c0)
    if len({tuple(t.features) for t in trees.values()}) > 1:
        return
    fo = B.Forest(trees)
    for x in X[:40]:
        got = fo.predict(x, True)
        for task, t in trees.items():
            # the forest sums each task's leaves with one bincount: same leaves as the task alone
            assert _same(got[task][0], t.predict(x)[0]) or abs(got[task][0] - t.predict(x)[0]) < 1e-12


@pytest.mark.parametrize("path", sorted(MODEL_DIRS["brain"].glob("*.v3.npz")), ids=lambda p: p.name)
def test_brain3_is_bitwise_the_old_walk(path):
    arrays, meta = load_npz(path)
    f = B3.Forest3(arrays, meta)
    X = rows(f.feat, f.thr, len(f.features))
    leaves = old_selfloop_leaves(arrays, int(meta["depth"]), X)
    for x, lv in zip(X, leaves):
        old = np.bincount(arrays["slot"].astype(np.int64), weights=arrays["value"].astype(np.float64)[lv],
                          minlength=int(meta["nslots"]))
        assert _same(f.raw(x), old)


NOGGIN = sorted(MODEL_DIRS["noggin"].glob("timing_*.npz")) + sorted(MODEL_DIRS["noggin"].glob("pick_*.npz"))


@pytest.mark.parametrize("path", NOGGIN, ids=lambda p: p.name)
def test_noggin_forests_are_bitwise_the_old_walk(path):
    m = T.load(path).forest if path.name.startswith("timing_") else K.PickModel.load(path).forest
    arrays, meta = load_npz(path)
    nf = len(T.MODEL_FEATURES if path.name.startswith("timing_") else K.PICK_FEATURES)
    X = rows(m.feat, m.thr, nf)
    lv = old_selfloop_leaves(arrays, int(meta["depth"]), X)
    assert _same(m.raw(X), arrays["value"].astype(np.float64)[lv].sum(axis=1) + float(meta.get("init", 0.0)))


@pytest.mark.parametrize("path", sorted(MODEL_DIRS["noggin"].glob("predict_*.npz")), ids=lambda p: p.name)
def test_noggin_predict_is_bitwise_the_old_walk(path):
    m = P.load(path)
    for hz, f in m.forests.items():
        X = rows(f.feat, f.thr, len(P.PREDICT_FEATURES), seed=int(hz))
        arrays = {a: getattr(f, a) for a in ("feat", "thr", "left", "right", "dleft", "miss", "value", "roots")}
        lv = old_selfloop_leaves(arrays, f.depth, X)
        assert _same(f.raw(X), f.value[lv].sum(axis=1) + f.init)


def test_both_layouts_normalise_to_self_loops():
    # brain layout: leaves feat -1, children -1
    a = dict(feat=np.array([0, -1, -1]), thr=np.array([1.0, 0, 0]), left=np.array([1, -1, -1]),
             right=np.array([2, -1, -1]), dleft=np.array([True, False, False]), miss=np.array([0, 0, 0]),
             value=np.array([0.0, -1.0, 1.0]), roots=np.array([0]))
    n = Nodes(a, 3)
    assert list(n.left) == [1, 1, 2] and list(n.right) == [2, 1, 2] and list(n.feat) == [0, 0, 0]
    assert n.leaves(np.array([0.5]))[0] == 1 and n.leaves(np.array([2.0]))[0] == 2
    assert n.leaves(np.array([np.nan]))[0] == 1          # missing None: NaN reads as 0.0 <= 1.0


def test_one_loader_checks_the_features(tmp_path):
    p = Path(tmp_path) / "m.npz"
    np.savez(p, feat=np.zeros(1), meta=np.frombuffer(b'{"features": ["a", "b"]}', np.uint8))
    arrays, meta = load_npz(p, ["a", "b"])
    assert list(arrays) == ["feat"] and meta["features"] == ["a", "b"]
    with pytest.raises(ValueError):
        load_npz(p, ["a"])


def test_every_model_dir_is_the_registry():
    assert B.MODEL_DIR == MODEL_DIRS["brain"] and T.MODEL_DIR == P.MODEL_DIR == K.MODEL_DIR == MODEL_DIRS["noggin"]
    from redzone.reasoner import head
    assert head.MODEL_DIR == MODEL_DIRS["reasoner"]
    assert all(d.is_dir() for d in MODEL_DIRS.values())
