import importlib.util
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location("cdn", Path(__file__).resolve().parents[1] / "scripts/measure_nba_cdn_breaks.py")
cdn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cdn)


def test_a_mandatory_timeout_lasts_until_the_clock_restarts():
    rows = [(1, "2pt", "Layup", "", "07:02", "2024-11-13T00:18:36.5Z"),
            (2, "timeout", "full", "team, mandatory", "07:02", "2024-11-13T00:18:41.3Z"),
            (3, "substitution", "in", "", "07:02", "2024-11-13T00:21:18.6Z"),
            (4, "2pt", "Jump Shot", "", "06:44", "2024-11-13T00:21:54.6Z")]
    d = pd.DataFrame([{"orderNumber": o, "actionType": a, "subType": s, "qualifiers": q, "gameId": 1, "period": 1,
                       "clock": f"PT{c[:2]}M{c[3:]}.00S", "timeActual": t} for o, a, s, q, c, t in rows])
    b = cdn.breaks(d)
    assert list(b) == ["timeout_mandatory"] and abs(b["timeout_mandatory"][0] - (193.3 - 18)) < 0.01
