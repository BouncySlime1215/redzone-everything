"""EXP-1723 wrapper: WV beyond value swapped into the frozen windowed oracle without editing it."""
import numpy as np

from redzone.lab import oracle_window as OW
from redzone.lab import wv_beyond as WB


def test_patch_restores_frozen_beyond_value():
    orig = OW.beyond_value
    calls = []

    def fake(R, k, horizon, memory=120):
        calls.append(k)
        return np.zeros(R.shape[0])
    with WB.patched_beyond(fake):
        assert OW.beyond_value is fake
        OW.beyond_value(np.zeros((2, 5)), 3, 4)
    assert OW.beyond_value is orig and calls == [3]
    with WB.patched_beyond(None):
        assert OW.beyond_value is orig


def test_patch_restores_on_error():
    orig = OW.beyond_value
    try:
        with WB.patched_beyond(lambda *a, **k: 1 / 0):
            raise RuntimeError
    except RuntimeError:
        pass
    assert OW.beyond_value is orig


def test_wv_grid_uses_only_delivered_plays():
    steps = np.arange(0.0, 50.0, 5.0)
    plays = {"a": (np.array([10.0, 20.0, 30.0]), np.array([1.0, 2.0, 3.0]))}
    g = WB.wv_grid(["a", "b"], plays, steps, 10.0)        # screen runs 10 s behind
    # screen 20 shows real 10 -> play 1; screen 25 -> real 15 -> still play 1; screen 40 -> real 30 -> play 3
    assert g[0, 4] == 1.0 and g[0, 5] == 1.0 and g[0, 8] == 3.0
    assert np.isnan(g[0, 0]) and np.isnan(g[0, 9])        # before the first play / after the last
    assert np.isnan(g[1]).all()                            # no plays known: no value


def test_future_mean_excludes_now():
    R = np.array([[100.0, 1.0, 2.0, 3.0]])
    F = WB.future_mean(R, 2)
    assert F[0, 0] == 1.5 and F[0, 1] == 2.5 and F[0, 2] == 3.0 and F[0, 3] == 0.0


def test_isomap_monotone_and_fallback():
    m = WB.IsoMap()
    m.refit()
    assert m.f is None and (m(np.array([5.0])) == 0).all()
    x = np.linspace(0, 1, 200)
    m.add(x, 2 * x + np.random.default_rng(0).normal(0, 0.1, 200))
    m.refit()
    y = m(np.array([0.1, 0.5, 0.9, np.nan]))
    assert y[0] <= y[1] <= y[2] and y[3] == 0.0


def test_beyond_reads_window_end_not_future():
    WV = np.array([[0.0, 1.0, 2.0, 3.0, 4.0, 5.0]])
    m = WB.IsoMap()
    m.add(np.arange(6.0).repeat(10), np.arange(6.0).repeat(10))
    m.refit()
    f = WB.beyond_from_wv(WV, m, W=2)
    assert np.isclose(f(np.zeros((1, 6)), 1, 10)[0], 2.0 * 10)    # window k=1..2 -> WV at step 2
    assert np.isclose(f(np.zeros((1, 6)), 5, 10)[0], 5.0 * 10)    # clipped at the night's end


def test_pooled_pct_and_delta():
    full = np.array([10.0, 10.0])
    p = WB.pooled_pct(np.array([5.0, 5.0]), full)
    assert p["pct"] == 50.0 and p["nights"] == 2
    d = WB.delta_ci(np.array([6.0, 6.0]), np.array([5.0, 5.0]), full)
    assert d["delta"] == 10.0 and d["ci"] == [10.0, 10.0]


def test_step_weights_maps_screen_time():
    w = WB.step_weights(np.array([1.0, 3.0, 5.0]), 100.0, 5.0, np.array([100.0, 105.0, 111.0, 500.0]))
    assert list(w) == [1.0, 3.0, 5.0, 1.0]
