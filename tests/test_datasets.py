"""Training-data labels and loader (redzone.lab.sportsdata / redzone.lab.datasets)."""
import shutil
from pathlib import Path

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from redzone.lab import datasets, sportsdata as sd  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def _game(rows, league="nba", gid="g1"):
    """rows: (period, clock_s, home, away, stoppage, big, clutch)"""
    e = pd.DataFrame(rows, columns=["period", "clock_s", "score_home", "score_away", "stoppage", "big", "clutch"])
    e.insert(0, "league", league)
    e["season"] = 2026
    e["game_id"] = gid
    e["seq"] = range(len(e))
    e["etype"] = "x"
    e["t_game"] = sd.game_elapsed(league, e.period.values, e.clock_s.values.astype(float))
    e["wall_s"] = e.t_game * 2.0
    e["wp_home"] = np.nan
    e["_exp_margin"] = 0.0
    return sd._blank(e)


def _build(e, league="nba"):
    e = sd.fill_wp(league, e)
    e = sd.derive(league, e)
    return sd.finalize(league, e)


def test_index_helpers():
    m = np.array([False, True, False, False, True])
    assert sd._next_after(m).tolist() == [1, 4, 4, 4, 5]
    assert sd._last_upto(m).tolist() == [-1, 1, 1, 1, 4]


def test_phi_matches_known_values():
    assert sd.phi(np.array([0.0]))[0] == pytest.approx(0.5)
    assert sd.phi(np.array([1.96]))[0] == pytest.approx(0.975, abs=1e-4)
    assert sd.phi(np.array([-1.0]))[0] == pytest.approx(0.1587, abs=1e-4)


def test_parse_clock():
    s = sd.parse_clock(pd.Series(["11:35", "0:48.2", "12.5", None]))
    assert s.tolist()[:3] == [695.0, 48.2, 12.5]
    assert np.isnan(s.iloc[3])


def test_break_soon_uses_game_clock_and_ignores_final_buzzer():
    e = _game([
        (4, 200, 90, 88, "none", False, False),
        (4, 150, 90, 88, "none", False, False),     # timeout 50 s later -> break_soon
        (4, 100, 92, 88, "timeout", False, False),
        (4, 30, 92, 90, "none", False, False),
        (4, 0, 92, 90, "end_period", False, False),  # end of regulation = end of game, not a break
        (4, 0, 92, 90, "game_end", False, False),
    ])
    df, _ = _build(e)
    assert df.break_soon.tolist() == [0, 1, 0, 0, 0, 0]
    assert df.next_break_s.iloc[1] == pytest.approx(50)
    assert df.stoppage.iloc[4] == datasets.STOPPAGE_CODE["game_end"]
    assert df.in_break.tolist() == [0, 0, 1, 0, 0, 0]
    # wall clock runs at 2x here, so the same timeout is 100 wall-seconds away
    assert df.break_soon_wall.iloc[1] == 0


def test_lead_changes_runs_and_key_moments():
    e = _game([
        (1, 720, 0, 0, "none", False, False),
        (1, 700, 2, 0, "none", False, False),
        (1, 680, 2, 3, "none", False, False),   # lead change (early: not key)
        (4, 100, 80, 82, "none", False, False),
        (4, 60, 83, 82, "none", False, True),   # late lead change via a 3 in crunch -> key
        (4, 50, 85, 82, "none", True, False),   # big play -> key
        (4, 0, 85, 82, "game_end", False, False),
    ])
    df, _ = _build(e)
    assert df.lead_changes.tolist() == [0, 0, 1, 1, 2, 2, 2]
    assert df.run_home.iloc[5] == 5 and df.run_away.iloc[5] == 0
    assert df.key_now.iloc[4] == 1 and df.key_now.iloc[5] == 1
    assert df.key_now.iloc[2] == 0
    assert df.key_soon.iloc[3] == 1  # key moment 40 s later, inside the 120 s window
    assert df.wp_home.iloc[-1] == 1.0
    assert 0 <= df.hotness.min() and df.hotness.max() <= 100


def test_wp_models_are_sane():
    assert 0.5 < sd.wp_hockey(np.array([0.0]), np.array([3600.0]))[0] < 0.56  # home ice
    lead = sd.wp_hockey(np.array([1.0, 1.0]), np.array([1200.0, 60.0]))
    assert 0.6 < lead[0] < lead[1] < 1.0
    b = sd.wp_basketball("nba", np.array([0.0, 5.0, 5.0]), np.array([2880.0, 2880.0, 30.0]), np.zeros(3))
    assert b[0] == pytest.approx(0.5) and 0.5 < b[1] < b[2]
    walk_off = sd.wp_baseball(np.array([1.0]), np.array([9.0]), np.array([1.0]), np.array([1.0]), np.array([0]))
    assert walk_off[0] == 1.0
    start = sd.wp_baseball(np.array([0.0]), np.array([1.0]), np.array([0.0]), np.array([0.0]), np.array([0]))
    assert 0.4 < start[0] < 0.6


def test_espn_archive_rows_use_live_stoppage_rules(tmp_path):
    arch = tmp_path / "nba"
    arch.mkdir()
    shutil.copy(FIX / "espn_nba_summary_401909906.json", arch / "summary_401909906.json")
    e = sd.norm_espn_archive("nba", arch)
    assert len(e) > 100 and e.game_id.nunique() == 1
    assert {"timeout", "none"} <= set(e.stoppage)
    assert e.t_game.is_monotonic_increasing
    df, knots = _build(e, "espn_nba")
    assert len(knots) == 101
    assert set(df.columns) == set(datasets.META + datasets.FEATURES + datasets.LABELS)


def test_loader_roundtrip_and_split(tmp_path):
    e = _game([(1, 720 - 10 * i, i, i // 2, "timeout" if i % 9 == 8 else "none", False, False)
               for i in range(40)] + [(4, 0, 40, 20, "game_end", False, False)])
    df, _ = _build(e)
    df.to_parquet(datasets.path_for("nba", tmp_path), index=False)
    back = datasets.load("nba", root=tmp_path)
    assert len(back) == len(df)
    assert datasets.available(tmp_path) == ["nba"]
    s = datasets.summary("nba", root=tmp_path)
    assert s["rows"] == len(df) and 0 < s["break_soon_rate"] < 1
    sp = datasets.splits(back, "break_soon")
    assert sum(len(x) for x, _ in sp.values()) == len(df)
    assert datasets.split_of("401909906") == datasets.split_of("401909906")


def test_load_missing_league_says_how_to_build(tmp_path):
    with pytest.raises(FileNotFoundError, match="build_training"):
        datasets.load("nfl", root=tmp_path)
