"""EXP-022 grader: bin quantiles, the conformal shift and the trees' bin collapse are sound."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import exp022_tpp as E  # noqa: E402


def test_quantile_is_a_lower_edge_with_mass_below_at_most_alpha():
    p = np.full((1, 32), 1 / 32)
    q = E.quantile_bins(p, 0.02)
    assert q[0] == E.EDGES[0]
    p = np.zeros((1, 32))
    p[0, 10], p[0, 20] = 0.01, 0.99
    assert E.quantile_bins(p, 0.02)[0] == E.EDGES[20]          # 1% sits below bin 20


def test_conformal_shift_reaches_target_coverage_on_calibration():
    rng = np.random.default_rng(0)
    true = rng.exponential(60, 2000)
    q_raw = np.full(2000, 30.0)                           # far too loose a bound: ~40% below it
    c = E.conformal_shift(q_raw, true, 0.02)
    assert (true < E.apply_shift(q_raw, c)).mean() <= 0.02 + 1e-9


def test_breaks_are_like_for_like():
    assert set(E.MODEL_BREAKS) <= set(E.TREE_BREAKS)
    assert E.bin_of(np.array([0.0, 1e9])).tolist() == [0, 31]
