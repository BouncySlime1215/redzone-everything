"""EXP-102 green summaries: ESPN abbreviations, an End Game entry, and no night on or after the factory cut."""
import json
import sys
from pathlib import Path

import pytest  # noqa: E402

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import green_summaries as G  # noqa: E402


def _nba(tmp: Path, day: str) -> Path:
    rows = [{"id": f"{i}", "sequence_number": i, "type_text": "Jump Shot", "text": f"shot {i}", "period_number": 1,
             "clock_display_value": f"11:{59 - i:02d}", "wallclock": f"{day}T00:{i:02d}:00Z", "scoring_play": i % 3 == 0,
             "score_value": 2, "home_score": i, "away_score": 0, "game_id": "0022400001", "home_team_abbrev": "WAS",
             "away_team_abbrev": "GSW", "game_date": day} for i in range(25)]
    p = tmp / "cdn.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return p


def test_nba_summary_uses_espn_abbreviations_and_ends_the_game(tmp_path):
    (gid, s), = list(G.nba_games(_nba(tmp_path, "2024-11-13")))
    teams = {c["homeAway"]: c["team"]["abbreviation"] for c in s["header"]["competitions"][0]["competitors"]}
    assert teams == {"home": "WSH", "away": "GS"} and gid.startswith("cdn")
    assert s["plays"][-1]["type"]["text"] == "End Game"


def test_write_drops_nights_on_or_after_the_factory_cut(tmp_path):
    from redzone.noggin import factory as F
    cut = F.EVAL_FIRST_NIGHT["nba"]
    late = f"{cut[:4]}-{cut[4:6]}-{int(cut[6:]) + 2:02d}"
    rep = G.write("nba", list(G.nba_games(_nba(tmp_path, late))), tmp_path / "a")
    assert rep == {"games": 0, "nights": 0, "dropped_eval_window": 1}
    rep = G.write("nba", list(G.nba_games(_nba(tmp_path, "2024-11-13"))), tmp_path / "a")
    assert rep["games"] == 1
    sb = json.loads(next((tmp_path / "a" / "nba").glob("scoreboard_*.json")).read_text())
    assert sb["events"] == [{"id": "cdn0022400001"}]


def test_game_ids_have_no_underscore():
    # backtest.Replay.load takes the id after the last "_" of summary_<gid>.json: "2024_01_BAL_KC" would read "KC"
    import inspect
    assert 'replace("_", "")' in inspect.getsource(G.nfl_games)
    (gid, _), = list(G.nba_games(_nba(Path(__import__("tempfile").mkdtemp()), "2024-11-13")))
    assert "_" not in gid
