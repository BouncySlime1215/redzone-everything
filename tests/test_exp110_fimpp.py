"""EXP-110 FIM-PP harness: break sequences from v1 tokens, the survival integral, the median baseline."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import exp110_fimpp as E  # noqa: E402
import noggin_tokens as T  # noqa: E402


def test_expected_gap_of_a_constant_intensity_is_its_mean():
    grid = np.linspace(0, 3000, 6001)[1:]
    assert abs(E.expected_gap(np.full(len(grid), 1 / 40), grid) - 40) < 0.5


def test_breaks_of_reads_stoppages_and_their_times():
    ev = []
    for i, stop in enumerate(["none", "timeout", "none", "end_period"]):
        ev += [T.KIND0, T.DT0 + 4, T.CLOCK0, T.MARGIN0, T.PTS0, T.STOP0 + T.STOPS.index(stop)]
    t, m = E.breaks_of(np.array([T.BOS0, T.TEAM0, T.TEAM0] + ev + [T.EOS]))
    assert list(m) == [T.STOPS.index("timeout"), T.STOPS.index("end_period")] and t[1] > t[0]


def test_median_baseline_per_mark():
    b = E.median_baseline([(np.array([0.0, 10, 40]), np.array([1, 1, 2]))])
    assert b == {1: 20.0}
