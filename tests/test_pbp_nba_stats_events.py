"""NBA.com stats pbp for the SportVU games (real trimmed fixture: 0021500001, DET 106 at ATL 94)."""
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import nba_stats_events as N, schema  # noqa: E402

FX = Path(__file__).parent / "fixtures/pbp/nba_stats_events_min.csv"


def test_convert_real_fixture():
    rows = N.convert_game(pd.read_csv(FX), "0021500001", "20151027", "ATL", "DET")
    assert len(rows) == 65 and schema.validate(rows) == []
    assert rows[0]["kind"] == "start_period" and rows[1]["kind"] == "jump_ball"
    assert (rows[-1]["home_score"], rows[-1]["away_score"]) == (94, 106) and rows[-1]["kind"] == "end_period"
    assert {r["side"] for r in rows} <= {"home", "away", ""} and rows[0]["clock_s"] == 720
    made = [r for r in rows if r["kind"] == "made_shot"]
    assert made and all(r["value"] in (2.0, 3.0) for r in made)
    assert all(r["tag"] == schema.PERSONAL and r["wall_ts"] is None for r in rows)
