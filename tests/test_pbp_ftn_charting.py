"""FTN charting side table: flags only, 2025 (locked NFL window) never written, ledger counts it apart."""
import json
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import ftn_charting as FT  # noqa: E402
from pbp import ledger  # noqa: E402


def _raw():
    row = {"nflverse_game_id": "2024_01_BAL_KC", "nflverse_play_id": 40, "season": 2024, "week": 1,
           "starting_hash": "L", "qb_location": "S", "read_thrown": None, "n_offense_backfield": 1,
           "n_defense_box": 7, "n_blitzers": None, "n_pass_rushers": 4, **{f: False for f in FT.FLAGS}}
    return pd.DataFrame([dict(row, is_no_huddle=True), dict(row, nflverse_play_id=57)])


def test_convert_flags_and_counts():
    t = FT.convert(_raw())
    assert list(t.play_id) == [40, 57] and list(t.is_no_huddle) == [1, 0]
    assert t.n_blitzers.tolist() == [-1, -1] and t.read_thrown.tolist() == ["0", "0"]
    assert not any(c in t for c in ("date_pulled", "ftn_game_id"))


def test_locked_season_never_pulled(tmp_path):
    assert FT.pull_season(2025, tmp_path) is None


def test_ledger_counts_charting_apart(tmp_path):
    d = tmp_path / "nfl_ftn_charting"
    d.mkdir()
    (d / "2024.parquet.json").write_text(json.dumps({
        "source": "nfl_ftn_charting", "league": "nfl", "season": 2024, "license": FT.LICENSE, "tag": FT.schema.PERSONAL,
        "data_plan": "B", "games": 2, "events": 10, "v1_tokens": 60, "bytes": 1, "rows_are": "per-play charting flags"}))
    s = ledger.summarize(ledger.collect([tmp_path]))
    assert s["charting"]["v1_tokens"] == 60 and s["total"]["v1_tokens"] == 0
    assert not ledger.problems(ledger.collect([tmp_path]))
