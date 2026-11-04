"""Hindsight oracle: never breaks a hard rule, and is a ceiling (never below what the app did)."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "data/archive/nba"


@pytest.mark.skipif(not ARCH.exists(), reason="needs the pinned eval data (scripts/fetch_eval_data.py)")
def test_oracle_is_a_clean_ceiling():
    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_sports as E

    from redzone.config import Config
    from redzone.lab import oracle as O
    from redzone.lab import sportsgrade
    from redzone.lab.backtest import feed_lag_knobs, run
    from redzone.models import League
    cfg = Config(user_path=ROOT / "data/eval_config.yaml")
    day, reps, targets = next(iter(E.nights(League.NBA, 1)))
    rep = run(reps, cfg, {"WSH"}, targets=targets, **feed_lag_knobs(None, 90.0))
    app = sportsgrade.grade(reps, rep.switches, {"WSH"}, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
    n = O.Night(reps, {"WSH"}, rep.lag_true, 5.0, rep.t0, rep.t1)
    tab = O.tables(n, np.arange(n.t0, n.t1, O.STEP_S))
    for obj in O.OBJECTIVES:
        sw = O.solve(n, obj, tab)
        assert [s.t for s in sw] == sorted(s.t for s in sw)
        oc = O.grade(n, sw)
        assert oc.cuts == [] and oc.cut.summary()["entries_mid_play"] == 0       # rule 1, both ends
        assert oc.my_crunch_live == oc.my_crunch                                  # L1: your team first
    best = O.grade(n, O.solve(n, "big_live", tab))
    assert best.big_live >= app.big_live
    best = O.grade(n, O.solve(n, "crunch_live", tab))
    assert best.crunch_live >= app.crunch_live


def test_dp_matches_brute_force_on_small_nights():
    """The DP is exact for its model: on random small nights (3 games x 12 steps) it finds the same best
    value as enumerating every legal schedule. Its only approximation is time: 5 s steps, so a switch time
    is within 5 s of the continuous optimum's, and the picture is black for ceil(tune_s / 5) steps."""
    import math as _m
    from types import SimpleNamespace

    import numpy as np

    from redzone.lab import oracle as O
    rng = np.random.default_rng(7)
    for trial in range(40):
        G, K = 3, 12
        tab = dict(big=(rng.random((G, K)) < .15) * 1.0, crunch=(rng.random((G, K)) < .2) * 1.0,
                   mine=(rng.random((G, K)) < .05) * 1.0, dead=(rng.random((G, K)) < .3) * 1.0,
                   leave=rng.random((G, K)) < .5, land=rng.random((G, K)) < .6)
        tab["land"][rng.integers(G), 0] = True
        tune = float(rng.choice([5.0, 10.0]))
        n = O.Night([SimpleNamespace(gid=f"g{g}") for g in range(G)], set(), 0.0, tune, 0.0, K * O.STEP_S)
        obj = ("combined", "dead_air", "crunch_live")[trial % 3]
        wb, wc, wm, wd, ws = O.OBJECTIVES[obj]
        R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
        blk = max(1, int(_m.ceil(tune / O.STEP_S)))

        def best(k, a):          # on game a at step k, deciding now
            if k >= K:
                return 0.0
            v = R[a, k] + best(k + 1, a)
            if tab["leave"][a, k] and k + blk <= K:
                for b in range(G):
                    if b != a and tab["land"][b, k]:
                        v = max(v, -ws + best(k + blk, b))
            return v
        brute = max(best(blk, g) for g in range(G) if tab["land"][g, 0])
        sw = O.solve(n, obj, tab)
        assert abs(O.schedule_value(n, tab, obj, sw) - brute) < 1e-9, (trial, obj)


def test_possession_start_oracle_matches_brute_force():
    """[possession-start entry] solve_cue is exact for its model: on random small nights it finds the
    same best value as enumerating every schedule over (game, offset behind live); with max_cue_s = 0 it
    is solve; and cueing to the last dead ball is never worse than waiting for a live one."""
    import math as _m
    from functools import lru_cache
    from types import SimpleNamespace

    import numpy as np

    from redzone.lab import oracle as O
    rng = np.random.default_rng(11)
    for trial in range(40):
        G, K = 3, 12
        tab = dict(big=(rng.random((G, K)) < .15) * 1.0, crunch=(rng.random((G, K)) < .2) * 1.0,
                   mine=(rng.random((G, K)) < .05) * 1.0, dead=(rng.random((G, K)) < .3) * 1.0,
                   leave=rng.random((G, K)) < .5, land=rng.random((G, K)) < .35)
        tab["land"][rng.integers(G), 0] = True
        tune = float(rng.choice([5.0, 10.0]))
        cue_s = float(rng.choice([10.0, 20.0, 40.0]))
        n = O.Night([SimpleNamespace(gid=f"g{g}") for g in range(G)], set(), 0.0, tune, 0.0, K * O.STEP_S)
        obj = ("combined", "dead_air", "crunch_live")[trial % 3]
        wb, wc, wm, wd, ws = O.OBJECTIVES[obj]
        R = wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"]
        blk = max(1, int(_m.ceil(tune / O.STEP_S)))
        M = int(cue_s // O.STEP_S)

        @lru_cache(None)
        def best(k, a, o):        # on game a, o steps behind live, at step k
            if k >= K:
                return 0.0
            v = (R[a, k - o] if k - o >= 0 else 0.0) + best(k + 1, a, o)
            if k - o >= 0 and tab["leave"][a, k - o] and k + blk <= K:
                for b in range(G):
                    ob = next((x for x in range(M + 1) if k - x >= 0 and tab["land"][b, k - x]), None)
                    if b != a and ob is not None:
                        v = max(v, -ws + best(k + blk, b, ob))
            return v
        brute = max(best(blk, g, 0) for g in range(G) if tab["land"][g, 0])
        sw = O.solve_cue(n, obj, tab, max_cue_s=cue_s)
        assert abs(O.schedule_value(n, tab, obj, sw) - brute) < 1e-9, (trial, obj)
        live = O.schedule_value(n, tab, obj, O.solve(n, obj, tab))
        assert O.schedule_value(n, tab, obj, sw) >= live - 1e-9
        assert abs(O.schedule_value(n, tab, obj, O.solve_cue(n, obj, tab, max_cue_s=0.0)) - live) < 1e-9
