"""[one brain, step 3] The play predicates live in redzone/grading.py; the lab answers with the same
functions (parity by identity) and the live path no longer imports the lab for them."""
import ast
from pathlib import Path

from redzone import grading
from redzone.lab import backtest, cutgrade, oracle, sportsgrade, timing

ROOT = Path(__file__).resolve().parent.parent


def test_the_lab_names_are_the_grading_functions():
    assert cutgrade._dead_at is grading.dead_at and cutgrade._idx is grading.idx
    assert cutgrade._action is grading.is_action and cutgrade.resume_after is grading.resume_after
    assert cutgrade._type is grading.raw_type and cutgrade._clock is grading.play_clock
    assert sportsgrade.is_big is grading.is_big and sportsgrade.is_crunch is grading.is_crunch
    assert sportsgrade._type is grading.play_type and sportsgrade._marker is grading.marker
    assert sportsgrade.PLAY_LEN is cutgrade.PLAY_LEN is grading.PLAY_LEN
    assert oracle._clean_leave is grading.clean_leave and timing.is_dead_ball is grading.is_dead_ball
    assert backtest.SwitchLog is grading.SwitchLog and backtest.lag_of is grading.lag_of
    assert backtest.Lags is grading.Lags and backtest._bisect is grading.idx


def _lab_imports(path: str) -> list[str]:
    tree = ast.parse((ROOT / path).read_text())
    out = []
    for n in ast.walk(tree):
        names = [a.name for a in n.names] if isinstance(n, ast.Import) else \
            [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        out += [x for x in names if x.startswith("redzone.lab")]
    return out


def test_pick_and_the_engine_do_not_import_the_lab():
    assert _lab_imports("redzone/noggin/pick.py") == []
    assert _lab_imports("redzone/engine.py") == []
    assert _lab_imports("redzone/grading.py") == []


def test_lags_and_lag_of():
    lag = grading.Lags(40.0, {"g1": 25.0})
    assert lag == 40.0 and grading.lag_of(lag, "g1") == 25.0 and grading.lag_of(lag, "g2") == 40.0
    assert grading.lag_of(33.0, "g1") == 33.0


def test_a_cut_cannot_land_mid_play_in_football():
    from redzone.models import League
    plays = [{"type": {"text": "Rush"}}, {"type": {"text": "Timeout"}}, {"type": {"text": "Pass Reception"}}]
    wall = [100.0, 130.0, 200.0]
    assert grading.dead_at(League.NFL, wall, plays, 196.0)[0] is False     # inside the 7 s pass
    assert grading.dead_at(League.NFL, wall, plays, 150.0)[0] is True      # after the timeout
    assert grading.clean_leave(League.NFL, wall, plays, 197.0) is False    # inside the pass
