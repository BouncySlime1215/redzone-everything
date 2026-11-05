import csv
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("whb", Path(__file__).resolve().parents[1] / "scripts/context/wiki_hourly_bulk.py")
whb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(whb)


def test_parse_hits_sums_access_methods_by_hour():
    titles = {"Boston_Celtics": ("team", "BOS"), "Jayson_Tatum": ("player", "Q1")}
    lines = [b"en.wikipedia Boston_Celtics 123 desktop 15 A5X10\n",
             b"en.wikipedia Boston_Celtics 123 mobile-web 3 A3\n",
             b"en.wikipedia Jayson_Tatum 9 mobile-app 2 C2\n",
             b"de.wikipedia Boston_Celtics 1 desktop 9 A9\n",
             b"en.wikipedia Boston_Celtics_season 7 desktop 1 B1\n"]
    agg = whb.parse_hits(lines, titles)
    assert agg == {("team", "BOS", 0): 8, ("team", "BOS", 23): 10, ("player", "Q1", 2): 2}


def test_season_title_and_title_map(tmp_path):
    assert whb.season_title("nba", 2025, "Boston_Celtics") == "2025–26_Boston_Celtics_season"
    assert whb.season_title("nfl", 2025, "Washington_Commanders") == "2025_Washington_Commanders_season"
    p = tmp_path / "e.csv"
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=whb.ENT_COLS)
        w.writeheader()
        w.writerow({"league": "nba", "kind": "team", "key": "BOS", "team": "BOS", "title": "Boston_Celtics", "start": "", "end": ""})
        w.writerow({"league": "nba", "kind": "team_season", "key": "BOS", "team": "BOS", "title": "{season}:Boston_Celtics", "start": "", "end": ""})
        w.writerow({"league": "nba", "kind": "player", "key": "Q1", "team": "BOS", "title": "Old Guy", "start": "1990", "end": "1999"})
        w.writerow({"league": "nba", "kind": "player", "key": "Q2", "team": "BOS", "title": "Jayson Tatum", "start": "2017", "end": ""})
        w.writerow({"league": "nfl", "kind": "team", "key": "WAS", "team": "WAS", "title": "Washington_Commanders", "start": "", "end": ""})
    m = whb.title_map(p, "nba", 2025)
    assert m == {"Boston_Celtics": ("team", "BOS"), "2025–26_Boston_Celtics_season": ("team_season", "BOS"),
                 "Jayson_Tatum": ("player", "Q2")}


def test_game_days_cover_utc_rollover():
    days = whb.game_days("nba", 2025)
    assert len(days) > 150 and days == sorted(days)
