"""One dead-ball predictor (redzone/scoring/dead_left.py), on the real break_lengths.json rows.

Why: dead-ball length lived in five tables that disagree; the audit's --dead floor (and later anchor mode) reads this one.
"""
import pytest

from redzone.models import League, Stoppage
from redzone.scoring.dead_left import guaranteed_left, quantile


@pytest.mark.parametrize("league,kind,q10", [("nfl", "timeout", 35.0), ("nfl", "two_minute_warning", 145.0), ("nba", "timeout", 99.2)])
def test_the_default_is_the_tables_q10(league, kind, q10):
    assert guaranteed_left(league, kind, 0.0) == q10
    assert quantile(league, kind, 0.10) == q10


def test_time_already_run_comes_off_and_never_below_zero():
    assert guaranteed_left("nfl", "two_minute_warning", 45.0) == 100.0
    assert guaranteed_left(League.NFL, Stoppage.TIMEOUT, 50.0) == 0.0
    assert guaranteed_left("nba", "timeout", -5.0) == 99.2           # a negative elapsed reads as 0


def test_quantiles_interpolate_between_the_stored_ones():
    assert quantile("nfl", "timeout", 0.25) == 50.0 and quantile("nfl", "timeout", 0.90) == 161.0
    assert quantile("nfl", "timeout", 0.175) == pytest.approx(42.5)      # halfway q10 35 -> q25 50
    assert quantile("nba", "timeout", 0.375) == pytest.approx((126.4 + 183.0) / 2)
    assert guaranteed_left("nfl", "two_minute_warning", 10.0, q=0.5) == 150.0


def test_a_college_league_reads_its_pro_row_and_an_unmeasured_kind_guarantees_nothing():
    assert guaranteed_left("cfb", "timeout", 0.0) == 35.0 and guaranteed_left(League.CBB, "timeout", 0.0) == 99.2
    assert guaranteed_left("nfl", "none", 0.0) == 0.0 and guaranteed_left("mlb", "timeout", 0.0) == 0.0


@pytest.mark.parametrize("q", [0.0, 0.05, 0.95, 1.0])
def test_no_quantile_outside_the_measured_range(q):
    with pytest.raises(ValueError):
        guaranteed_left("nfl", "timeout", 0.0, q=q)
