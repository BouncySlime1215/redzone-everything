import numpy as np
import pytest

from redzone.lab import forward_chain as FC
from redzone.lab import mistake_ledger as ML


def _setup():
    # 2 games x 10 steps; game 1 has a big play at step 5, game 0 is in a break steps 4-7
    R = np.zeros((2, 10)); R[1, 5] = 3.0
    dead = np.zeros((2, 10)); dead[0, 4:8] = 1
    return R, {"dead": dead}


def test_wrong_game_and_break():
    R, tab = _setup()
    app = np.zeros(10, int); orc = np.zeros(10, int); orc[4:8] = 1
    rows = ML.ledger("20251101", "nba", R, tab, ["A", "B"], app, orc, 0.0, 5.0)
    assert len(rows) == 1 and rows[0].value_lost == 3.0 and rows[0].cause == "break_estimate"
    assert rows[0].showed == "A" and rows[0].should == "B"


def test_bad_label_when_alt_reward_agrees_with_app():
    R, tab = _setup()
    alt = np.zeros((2, 10)); alt[0, 5] = 1.0
    app = np.zeros(10, int); orc = np.zeros(10, int); orc[4:8] = 1
    rows = ML.ledger("20251101", "nba", R, tab, ["A", "B"], app, orc, 0.0, 5.0, R_alt=alt)
    assert rows[0].cause == "bad_label"


def test_switch_cost_and_summary():
    R = np.ones((2, 10)); tab = {"dead": np.zeros((2, 10))}
    app = np.zeros(10, int); app[3] = -1; app[4:] = 1
    orc = np.zeros(10, int)
    rows = ML.ledger("20251101", "nba", R, tab, ["A", "B"], app, orc, 0.0, 5.0, app_switch_steps={2})
    assert rows[0].cause == "switch_cost"
    s = ML.summarize(rows)
    assert list(s)[0] == "switch_cost" and s["switch_cost"]["share"] == 1.0
    assert ML.sample_weights(rows, 10)[3] > 1.0


def test_locked_night_never_enters_ledger():
    R, tab = _setup()
    with pytest.raises(ValueError):
        ML.ledger("20261030", "nba", R, tab, ["A", "B"], np.zeros(10, int), np.ones(10, int), 0.0, 5.0,
                  locked={"20261030"})


def test_forward_chain_scores_before_training_and_blocks_leaks():
    trained = []
    ch = FC.Chain(locked={"20251103"})
    def score(n):
        assert n not in trained                     # the model never touched it
        return {"pct": 1.0}
    log = ch.run(["20251102", "20251101", "20251103", "20251104"], score, lambda n: None,
                 lambda seen: trained.__setitem__(slice(None), seen))
    assert [r["night"] for r in log] == ["20251101", "20251102", "20251104"]
    assert [r["trained_on_n"] for r in log] == [0, 1, 2]
    with pytest.raises(FC.Leak):
        ch.step("20251104", score, lambda n: None, lambda s: None)      # seen twice
    with pytest.raises(FC.Leak):
        ch.step("20251102", score, lambda n: None, lambda s: None)      # back in time
    with pytest.raises(FC.Leak):
        ch.step("20251103", score, lambda n: None, lambda s: None)      # locked
