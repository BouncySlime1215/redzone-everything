"""[time-shift buffer, T3] The oracle teacher: backoff solver exact on a whole-night window, never above full
hindsight, never reads the unrevealed future, never lands mid-play; the learned value hook is backward compatible."""
import numpy as np

from redzone.lab import oracle as O
from redzone.lab import oracle_teacher as T
from redzone.lab import oracle_window as OW
from tests.test_oracle_window import _night


def _val(n, tab, sw):
    return O.schedule_value(n, tab, "combined", sw)


def _legal_cue(n, tab, sw, backoff_s):
    blk = int(np.ceil(n.tune_s / O.STEP_S))
    cue = O.cue_steps(tab, backoff_s)
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    prev_off = 0
    for s in sw[1:]:
        k = int(round((s.t - n.t0) / O.STEP_S))
        o = int(round(s.offset / O.STEP_S))
        assert tab["leave"][gi[s.frm], k - prev_off]                       # clean leave of the picture shown
        assert cue[gi[s.to], k] == o and tab["land"][gi[s.to], k - o]      # lands on a dead ball (Rule #1)
        assert k + blk <= tab["land"].shape[1]
        prev_off = o


def test_backoff_zero_is_solve_window():
    rng = np.random.default_rng(7)
    for _ in range(15):
        n, tab = _night(rng, K=50, lag=60.0)
        a = OW.solve_window(n, 8.0, tab=tab)
        b = T.solve_window_cue(n, 8.0, tab=tab, backoff_s=0.0)
        assert abs(_val(n, tab, a) - _val(n, tab, b)) < 1e-9


def test_whole_night_window_with_backoff_is_the_cue_oracle():
    rng = np.random.default_rng(13)
    for trial in range(20):
        n, tab = _night(rng, K=40, lag=10_000.0)
        full = _val(n, tab, O.solve_cue(n, "combined", tab, max_cue_s=15.0))
        sw = T.solve_window_cue(n, 0.0, tab=tab, backoff_s=15.0)
        _legal_cue(n, tab, sw, 15.0)
        assert abs(_val(n, tab, sw) - full) < 1e-9, trial


def test_short_window_backoff_legal_and_below_ceiling():
    rng = np.random.default_rng(17)
    for _ in range(20):
        n, tab = _night(rng, K=60, lag=float(rng.choice([30.0, 60.0, 90.0])))
        ceil = _val(n, tab, O.solve_cue(n, "combined", tab, max_cue_s=15.0))
        sw = T.solve_window_cue(n, 8.0, tab=tab, backoff_s=15.0)
        _legal_cue(n, tab, sw, 15.0)
        assert _val(n, tab, sw) <= ceil + 1e-9


def test_backoff_never_reads_unrevealed_future():
    rng = np.random.default_rng(5)
    n, tab = _night(rng, K=80, lag=60.0)
    W = OW.window_steps(60.0, 8.0, 5.0)
    first = T.solve_window_cue(n, 8.0, tab=tab)
    k = 5
    tab2 = {key: v.copy() for key, v in tab.items()}
    for key in ("big", "crunch", "mine"):
        tab2[key][:, k + W + 1:] = rng.random(tab2[key][:, k + W + 1:].shape) < .5
    second = T.solve_window_cue(n, 8.0, tab=tab2)
    cut = (k + 1) * O.STEP_S
    assert [s for s in second if s.t < cut] == [s for s in first if s.t < cut]


def test_value_fn_hook_default_matches_and_zero_past_end():
    rng = np.random.default_rng(3)
    n, tab = _night(rng, K=50, lag=60.0)
    R = 3 * tab["big"] + tab["crunch"] + 100 * tab["mine"] - .02 * tab["dead"]
    hz = int(120 // O.STEP_S)
    a = OW.solve_window(n, 8.0, tab=tab)
    b = OW.solve_window(n, 8.0, tab=tab, value_fn=lambda k, e: OW.beyond_value(R, k, hz))
    assert a == b
    seen = []
    OW.solve_window(n, 8.0, tab=tab, value_fn=lambda k, e: seen.append(e) or np.zeros(3))
    assert seen and max(seen) < 50                       # never asked past the night's end (0 there)


def test_ridge_recovers_a_linear_target_and_roundtrips(tmp_path):
    rng = np.random.default_rng(0)
    X = np.c_[np.ones(500), rng.random((500, len(T.FEATURES) - 1))]
    Wt = rng.random((len(T.FEATURES), 4))
    m = T.fit({"nba": ([X], [X @ Wt])}, horizon=24, alpha=1e-6)
    assert np.allclose(np.asarray(m.weights["nba"]), Wt, atol=1e-3)
    m.save(tmp_path / "m.json")
    m2 = T.ValueModel.load(tmp_path / "m.json")
    assert m2.horizon == 24 and np.allclose(m2.weights["nba"], m.weights["nba"])
