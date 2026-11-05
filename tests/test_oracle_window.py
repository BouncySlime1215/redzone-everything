"""[time-shift buffer] The windowed-hindsight oracle (oracle.solve_window): exact when the window covers the night,
never above full hindsight, never uses what the buffer has not revealed, and never breaks Rule #1."""
from types import SimpleNamespace

import numpy as np

from redzone.lab import oracle as O


def _night(rng, G=3, K=40, tune=5.0, lag=0.0):
    tab = dict(big=(rng.random((G, K)) < .15) * 1.0, crunch=(rng.random((G, K)) < .2) * 1.0,
               mine=(rng.random((G, K)) < .05) * 1.0, dead=(rng.random((G, K)) < .3) * 1.0,
               leave=rng.random((G, K)) < .5, land=rng.random((G, K)) < .6)
    tab["land"][rng.integers(G), 0] = True
    n = O.Night([SimpleNamespace(gid=f"g{g}") for g in range(G)], set(), lag, tune, 0.0, K * O.STEP_S)
    return n, tab


def _legal(n, tab, sw):
    blk = int(np.ceil(n.tune_s / O.STEP_S))
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    for s in sw[1:]:
        k = int(round((s.t - n.t0) / O.STEP_S))
        assert tab["leave"][gi[s.frm], k] and tab["land"][gi[s.to], k]        # dead ball at both ends
        assert k + blk <= tab["land"].shape[1]


def test_whole_night_window_is_the_full_oracle():
    rng = np.random.default_rng(3)
    for trial in range(30):
        n, tab = _night(rng, lag=10_000.0)              # the window covers the whole night
        full = O.schedule_value(n, tab, "combined", O.solve(n, "combined", tab))
        sw = O.solve_window(n, feed_lag=0.0, tab=tab)
        _legal(n, tab, sw)
        assert abs(O.schedule_value(n, tab, "combined", sw) - full) < 1e-9, trial


def test_short_window_is_legal_and_never_beats_full_hindsight():
    rng = np.random.default_rng(11)
    for trial in range(30):
        n, tab = _night(rng, K=60, lag=float(rng.choice([30.0, 60.0, 90.0])))
        full = O.schedule_value(n, tab, "combined", O.solve(n, "combined", tab))
        sw = O.solve_window(n, feed_lag=8.0, tab=tab)
        _legal(n, tab, sw)
        assert O.schedule_value(n, tab, "combined", sw) <= full + 1e-9


def test_window_never_reads_the_unrevealed_future():
    """Changing anything beyond the window at decision step k cannot change the move made at k."""
    rng = np.random.default_rng(5)
    n, tab = _night(rng, K=80, lag=60.0)
    W = O.window_steps(60.0, 8.0, 5.0)
    first = O.solve_window(n, 8.0, tab=tab)
    k = 5
    moves_before = [s for s in first if s.t < (k + 1) * O.STEP_S]
    tab2 = {key: v.copy() for key, v in tab.items()}
    for key in ("big", "crunch", "mine"):
        tab2[key][:, k + W + 1:] = rng.random(tab2[key][:, k + W + 1:].shape) < .5
    second = O.solve_window(n, 8.0, tab=tab2)
    assert [s for s in second if s.t < (k + 1) * O.STEP_S] == moves_before


def test_window_size_is_buffer_minus_feed_lag_minus_tune():
    assert O.window_steps(26 + 60, 8.0, 5.0) == int((86 - 13) // 5)
    assert O.window_steps(10.0, 8.0, 5.0) == 0
