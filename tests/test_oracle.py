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
