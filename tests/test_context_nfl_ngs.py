import importlib.util
from pathlib import Path

import pandas as pd

_spec = importlib.util.spec_from_file_location("nfl_ngs", Path(__file__).parents[1] / "scripts/context/nfl_ngs.py")
N = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(N)


def _ngs(rows):
    base = {"season": 2024, "team_abbr": "LAR"}
    p = pd.DataFrame([{**base, **r} for r in rows])
    for c in ("completion_percentage_above_expectation", "avg_time_to_throw", "aggressiveness",
              "avg_air_yards_to_sticks"):
        p[c] = p["cpoe"]
    return {"passing": p, "rushing": pd.DataFrame(columns=["season", "week", "team_abbr"]),
            "receiving": pd.DataFrame(columns=["season", "week", "team_abbr"])}


def test_form_uses_only_earlier_weeks_and_maps_abbr():
    ngs = _ngs([{"week": 1, "attempts": 10, "cpoe": 1.0}, {"week": 2, "attempts": 30, "cpoe": 3.0},
                {"week": 3, "attempts": 50, "cpoe": 99.0}])
    v = N.team_form(ngs, 2024, 3, "LA")          # nflverse "LA" -> NGS "LAR"; week 3 itself is excluded
    assert v["ngs_qb_cpoe"] == 2.5
    assert N.team_form(ngs, 2024, 1, "LA") == {}  # nothing before week 1
