"""[time-shift buffer] The windowed-hindsight oracle (oracle_window.solve_window): exact when the window covers the night,
never above full hindsight, never uses what the buffer has not revealed, and never breaks Rule #1."""
from types import SimpleNamespace

import numpy as np

from redzone.lab import oracle as O
from redzone.lab import oracle_window as OW


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
        sw = OW.solve_window(n, feed_lag=0.0, tab=tab)
        _legal(n, tab, sw)
        assert abs(O.schedule_value(n, tab, "combined", sw) - full) < 1e-9, trial


def test_short_window_is_legal_and_never_beats_full_hindsight():
    rng = np.random.default_rng(11)
    for trial in range(30):
        n, tab = _night(rng, K=60, lag=float(rng.choice([30.0, 60.0, 90.0])))
        full = O.schedule_value(n, tab, "combined", O.solve(n, "combined", tab))
        sw = OW.solve_window(n, feed_lag=8.0, tab=tab)
        _legal(n, tab, sw)
        assert O.schedule_value(n, tab, "combined", sw) <= full + 1e-9


def test_window_never_reads_the_unrevealed_future():
    """Changing anything beyond the window at decision step k cannot change the move made at k."""
    rng = np.random.default_rng(5)
    n, tab = _night(rng, K=80, lag=60.0)
    W = OW.window_steps(60.0, 8.0, 5.0)
    first = OW.solve_window(n, 8.0, tab=tab)
    k = 5
    moves_before = [s for s in first if s.t < (k + 1) * O.STEP_S]
    tab2 = {key: v.copy() for key, v in tab.items()}
    for key in ("big", "crunch", "mine"):
        tab2[key][:, k + W + 1:] = rng.random(tab2[key][:, k + W + 1:].shape) < .5
    second = OW.solve_window(n, 8.0, tab=tab2)
    assert [s for s in second if s.t < (k + 1) * O.STEP_S] == moves_before


def test_window_size_is_buffer_minus_feed_lag_minus_tune():
    assert OW.window_steps(26 + 60, 8.0, 5.0) == int((86 - 13) // 5)
    assert OW.window_steps(10.0, 8.0, 5.0) == 0


def test_basketball_reserves_the_graders_look_ahead():
    """NBA leave/land read the next logged action up to RESUME_LEAD (26 s) ahead (#328): that is reserved."""
    from redzone.grading import RESUME_LEAD
    from redzone.models import League
    assert OW.window_steps(86.0, 8.0, 5.0, RESUME_LEAD[League.NBA]) == int((86 - 8 - 5 - 26) // 5)
    assert OW.window_steps(86.0, 70.0, 5.0, RESUME_LEAD[League.NBA]) == 0


def test_feed_slower_than_buffer_never_switches():
    """No revealed window (feed lag >= screen lag): the honest oracle starts on a dead ball and stays put."""
    rng = np.random.default_rng(9)
    for _ in range(10):
        n, tab = _night(rng, K=40, lag=86.0)
        sw = OW.solve_window(n, 85.0, tab=tab)
        assert len(sw) == 1 and sw[0].frm is None


def test_first_pick_waits_for_a_dead_ball():
    rng = np.random.default_rng(1)
    n, tab = _night(rng, K=30)
    tab["land"][:, :3] = False
    sw = OW.solve_window(n, 0.0, tab=tab)
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    k = int(round((sw[0].t - n.t0) / O.STEP_S))
    assert k >= 3 and tab["land"][gi[sw[0].to], k]
    _legal(n, tab, sw)
