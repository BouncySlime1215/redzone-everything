"""Data Division A: play table ledger + self-contained Kaggle notebook (EXP-1000)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
pytest.importorskip("pyarrow")

from pbp import ledger, pack_notebook, schema  # noqa: E402


def _game(league="wnba", night="20240601", n=3):
    return [schema.row(source="t", sport="basketball", league=league, level="pro", season=2024, game_id="g1",
                       night=night, seq=i, kind="shot", license="L", tag=schema.PERSONAL) for i in range(n)]


def test_validate_blocks_locked_nights_and_unknown_columns():
    assert schema.validate(_game()) == []
    assert any("scorecard/locked" in e for e in schema.validate(_game("wcbb", "20261101")))
    with pytest.raises(KeyError):
        schema.row(bogus=1)


def test_ledger_counts_landed_shards_once_and_checks_tags(tmp_path):
    meta = {"source": "t", "league": "wnba", "season": 2024, "license": "L", "tag": schema.PERSONAL,
            "data_plan": "B"}
    side = schema.write_shard(_game(), tmp_path / "a/wnba/2024.parquet", meta)
    assert side["events"] == 3 and side["v1_tokens"] == 3 * 6 + 4
    schema.write_shard(_game(), tmp_path / "b/wnba/2024.parquet", meta)      # same shard, second run
    rows = ledger.collect([tmp_path])
    assert len(rows) == 1 and ledger.problems(rows) == []
    s = ledger.summarize(rows)
    assert s["total"]["events"] == 3 and "wnba" in ledger.render(s)
    bad = dict(meta, tag="")
    schema.write_shard(_game(), tmp_path / "c/wcbb/2024.parquet", dict(bad, league="wcbb"))
    assert any("personal-use tag" in e for e in ledger.problems(ledger.collect([tmp_path])))


def test_notebook_is_self_contained():
    nb = pack_notebook.build("baseball")
    src = "".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    assert 'CODE_B64 = "' in src and 'CODE_B64 = ""' not in src
    assert '"RZ_PBP_JOBS", "baseball"' in src
    plain = "\n".join(ln for ln in src.split("\n") if not ln.startswith("CODE_B64"))
    assert "KAGGLE_KEY" not in plain and "secret" not in plain.lower()
    json.dumps(nb)


def test_espn_plays_line_up_with_factory_order():
    from pbp import espn_plays
    plays = [{"type": {"text": "Jump Ball"}, "period": {"number": 1}, "clock": {"displayValue": "12:00"},
              "homeScore": 0, "awayScore": 0, "wallclock": "2024-01-10T00:10:00Z"},
             {"type": {"text": "Made Shot"}, "period": {"number": 1}, "clock": {"displayValue": "11:40"},
              "homeScore": 2, "awayScore": 0, "scoringPlay": True, "team": {"id": "1"}},
             {"type": {"text": "Timeout"}, "period": {"number": 1}, "clock": {"displayValue": "11:30"},
              "homeScore": 2, "awayScore": 0}]
    rows = espn_plays.convert_game("nba", "401", "20240109", plays, "BOS", "NYK", season=2024, source="hoopr",
                                   license="L", tag=schema.PERSONAL, home_id="1", away_id="2")
    assert [r["seq"] for r in rows] == [0, 1, 2] and schema.validate(rows) == []
    assert rows[1]["value"] == 2 and rows[1]["side"] == "home" and rows[1]["possession"] == "away"
    assert rows[0]["clock_s"] == 720 and rows[0]["wall_ts"] and rows[2]["wall_ts"] is None
