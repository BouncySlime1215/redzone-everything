"""[seek model] YouTube TV's rewind as the live lab measured it (Nov 4 2026, Nick 12:10 PM): 15 s a press,
the first press of a batch swallowed, a 2-4 s stall per seek. Rule #1: the planner, control.py and the
sims must all agree on where a rewind really lands."""
import random
from pathlib import Path

import pytest

from redzone.config import Config
from redzone.contracts.switching import YTTV_SEEK, SeekModel, seek_model
from redzone.decider.cuts import plan_entry
from redzone.lab.backtest import SwitchLog, seek_cue
from tests.test_cut_timing import nfl_tracker

pytestmark = pytest.mark.rule1


def _cfg() -> Config:
    return Config(user_path=Path("/nonexistent/redzone.yaml"))


class _D:
    def __init__(self, r, cue=""):
        self.rewind_s, self.cue = r, cue


def test_the_old_assumption_reproduces_the_lab_misses():
    """A "60 s" rewind assumed 10 s steps: 6 presses landed 75-80 s back; a "20 s" one 15-19 s back."""
    old = SeekModel(step=10.0)
    for asked, lo, hi in ((60, 75, 80), (20, 15, 19)):
        n = old.sent(asked)
        assert lo <= YTTV_SEEK.landed(n, YTTV_SEEK.stall_lo) and YTTV_SEEK.landed(n, YTTV_SEEK.stall_hi) <= hi


def test_the_calibrated_model_lands_where_it_plans():
    cal = seek_model(_cfg())
    assert (cal.step, cal.swallowed, cal.stall_lo, cal.stall_hi) == (15.0, 1, 2.0, 4.0)
    for k in (15, 30, 45, 60):
        n = cal.sent(k + cal.stall_mid)
        assert n == k // 15 + 1                       # one extra for the swallowed press
        assert YTTV_SEEK.landed(n, 2.0) == k + 2 and YTTV_SEEK.landed(n, 4.0) == k + 4


def test_the_plan_holds_the_whole_stall_on_a_dead_ball():
    dt = nfl_tracker()
    # lands at real 1035 (mid-snap): whole 15 s steps, and every stall 2..4 s must still be dead
    p = plan_entry(dt, 40.0, 3.0, 1070.0, 5.0, can_rewind=True, step=15.0, stall=(2.0, 4.0))
    assert p.ok and p.rewind_s == 15.0 + 3.0
    for stall in (2.0, 3.0, 4.0):
        x = 1035 - 15 - stall
        assert dt.landing(x - 3, x + 3).state == "dead"


def test_the_sim_lands_the_seek_the_stick_really_does():
    cfg = _cfg()
    old = _cfg()
    old.data.setdefault("replay", {}).update(seek_step_s=10, seek_swallowed_presses=0, seek_stall_min_s=0, seek_stall_max_s=0)
    rng = random.Random(1)
    sw = SwitchLog(0.0, "a", "b", "live", "", offset=20.0)
    seek_cue(sw, _D(20.0), old, rng)                 # today's live code: 2 presses -> 15 s + stall
    assert 17.0 <= sw.offset <= 19.0 and sw.flash > 0
    sw = SwitchLog(0.0, "a", "b", "live", "", offset=18.0)
    seek_cue(sw, _D(18.0), cfg, rng)                 # calibrated: 2 presses (1 swallowed) -> 15 + 2..4
    assert 17.0 <= sw.offset <= 19.0


def test_a_pause_cue_lands_on_its_plan_and_flips_later():
    cfg = _cfg()
    sw = SwitchLog(0.0, "a", "b", "live", "", offset=22.5, jitter=4.0, arrive_dt=0.3)
    seek_cue(sw, _D(22.5, cue="pause"), cfg, random.Random(2))
    cue_t = sw.leave_dt
    assert cue_t > 0 and sw.jitter == 0.0 and sw.flash == 0.0
    assert abs(sw.arrive_dt - (0.3 + cue_t)) < 1e-9
    # the content on screen (arrive - offset) is the planned one within the pause's error
    assert abs((sw.arrive_dt - sw.offset) - (0.0 - 22.5)) <= 0.5 + 1e-9
