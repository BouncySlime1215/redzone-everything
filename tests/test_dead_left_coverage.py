"""[timing.dead_v2] The coverage arithmetic of scripts/dead_left_coverage.py, on hand-made stoppages.

Why: the held-out coverage is the only evidence that the dead-time bound can be trusted on an open stoppage; a counting slip
(a pair counted after the stoppage ended, a zero promise counted as held) would make it look safer than it is.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dead_left_coverage as C  # noqa: E402
from redzone.scoring import dead_left_lite as L  # noqa: E402


def flat(b):
    """A reader that promises `b` seconds at every landmark and level."""
    return lambda league, cls, t, a, kind=None: b


def test_a_pair_holds_when_the_stoppage_runs_at_least_the_landmark_plus_the_promise():
    rows = [("timeout", "official", 25.0), ("timeout", "official", 14.0)]
    cov = C.coverage("nfl", rows, alphas=(0.01,), marks=(0, 10, 20), reader=flat(5.0))
    # 25 s: t=0 (25 >= 5 held), t=10 (25 >= 15 held), t=20 (25 >= 25 held). 14 s: t=0 held, t=10 (14 < 15 missed); t=20 not reached
    assert cov[("timeout", "official")] == {"n": 2, 0.01: [5, 4, 25.0]}
    assert cov[("timeout", "timeout")] == cov[("timeout", "official")]      # the pooled class gets every timeout too
    assert C.rate(cov[("timeout", "official")][0.01]) == pytest.approx(0.8)


def test_a_zero_promise_is_not_counted_and_a_class_named_after_its_kind_is_counted_once():
    cov = C.coverage("nba", [("whistle", "whistle", 30.0)], alphas=(0.05,), marks=(0, 10), reader=flat(0.0))
    assert cov == {("whistle", "whistle"): {"n": 1, 0.05: [0, 0, 0.0]}}
    assert C.rate(cov[("whistle", "whistle")][0.05]) is None


def test_a_stoppage_that_ended_at_a_landmark_is_not_asked_there():
    cov = C.coverage("nba", [("timeout", "timeout", 10.0)], alphas=(0.01,), marks=(0, 10, 20), reader=flat(4.0))
    assert cov[("timeout", "timeout")][0.01] == [1, 1, 4.0]                  # only t=0: 10 s did not outlast t=10


def test_on_the_real_table_rows_that_built_a_cell_cover_it_at_its_level():
    """Survivors drawn exactly as the table's own n and bound: the lower bound is the floor(alpha (n + 1))-th smallest, so at most
    alpha of the survivors fall under it."""
    cell = L.load()["nba"]["timeout"]["timeout"]["30"]
    b = cell["left_s"]["0.01"]
    rows = [("timeout", "timeout", 30.0 + b + 1.0)] * 99 + [("timeout", "timeout", 30.0 + b / 2)]
    cov = C.coverage("nba", rows, alphas=(0.01,), marks=(30,))
    assert cov[("timeout", "timeout")][0.01][:2] == [100, 99]


def test_the_report_marks_a_low_cell():
    cov = {("timeout", "team"): {"n": 10, 0.01: [100, 98, 0.0], 0.05: [100, 96, 0.0]}}
    (line,) = C.table_lines("nfl", "valid", cov)
    assert "98.0% LOW of 100" in line and "96.0% ok of 100" in line
