"""Worker B1 context landing: tiny inline fixtures, no network."""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "context"))
import land_github_ctx as L  # noqa: E402

GAMES = """game_id,season,game_type,week,gameday,weekday,gametime,away_team,home_team,old_game_id,away_rest,home_rest,spread_line,total_line,away_moneyline,home_moneyline,div_game,roof,surface,temp,wind,away_coach,home_coach,referee,stadium_id
2021_01_AAA_BBB,2021,REG,1,2021-09-12,Sunday,13:00,AAA,BBB,2021091200,7,7,-3,44,150,-170,1,outdoors,grass,70,8,Coach A,Coach B,Ref One,BBB00
2021_02_BBB_CCC,2021,REG,2,2021-09-20,Monday,20:15,BBB,CCC,2021092000,8,6,2,40,-110,-110,0,dome,fieldturf,72,0,Coach B,Coach C,Ref One,CCC00
2023_01_AAA_CCC,2023,REG,1,2023-09-10,Sunday,16:25,AAA,CCC,2023091000,7,10,1,41,-110,-110,0,outdoors,grass,80,5,Coach A,Coach C,Ref Two,CCC00
"""


def games():
    return L.nfl_games_index(pd.read_csv(io.StringIO(GAMES)))


def test_kickoff_is_eastern_converted_to_utc():
    assert L.iso(L.nfl_kickoff_utc("2021-09-12", "13:00")) == "2021-09-12T17:00:00Z"   # EDT
    assert L.iso(L.nfl_kickoff_utc("2021-12-12", "13:00")) == "2021-12-12T18:00:00Z"   # EST
    assert L.iso(L.nfl_kickoff_utc("2021-12-12", None)) == "2021-12-12T18:00:00Z"      # default 13:00 ET


def test_schedule_facts_no_lines_and_outdoor_weather_only():
    df = L.to_frame(L.nfl_schedule_facts(games()))
    assert not df["field"].str.contains("spread|total|moneyline|line").any()
    dome = df[df["game_id"] == "2021_02_BBB_CCC"]
    assert not dome["field"].isin(["temp_f", "wind_mph"]).any()
    g1 = df[df["game_id"] == "2021_01_AAA_BBB"].set_index(["field", "team"], drop=False)
    assert g1.loc[("temp_f", None), "value"].item() == 70
    assert set(df["asof_utc"][df["game_id"] == "2021_01_AAA_BBB"]) == {"2021-09-12T16:59:59Z"}
    rd = df[(df["game_id"] == "2021_02_BBB_CCC") & (df["field"] == "rest_diff")]
    assert rd["value"].item() == -2 and rd["kind"].item() == "derived"
    pt = df[(df["game_id"] == "2021_02_BBB_CCC") & (df["field"] == "primetime")]["value"].item()
    assert pt == 1.0
    ref = df[(df["field"] == "referee")]["value_str"].tolist()
    assert ref[0] == "ref_one" and (df[df["field"] == "referee"]["kind"] == "new").all()
    assert set(df[df["field"] == "coach"]["team"]) == {"AAA", "BBB", "CCC"}
    assert df["license"].str.contains("personal-use").all()


def test_injuries_drop_after_kickoff_and_count():
    inj = pd.DataFrame({
        "season": [2021] * 5, "week": [1] * 5, "team": ["AAA", "AAA", "AAA", "BBB", "BBB"],
        "gsis_id": ["p1", "p2", "p3", "p4", "p5"], "position": ["QB", "WR", "CB", "T", "G"],
        "report_status": ["Out", "Questionable", "Doubtful", "Out", "Out"],
        "date_modified": ["2021-09-10T18:00:00Z", "2021-09-10T19:00:00Z", "2021-09-11T12:00:00Z",
                          "2021-09-10T18:00:00Z", "2021-09-12T20:00:00Z"]})          # p5 is after kickoff
    facts, dropped = L.nfl_injury_facts(inj, games())
    df = L.to_frame(facts)
    assert dropped == 1
    a = df[df["team"] == "AAA"].set_index("field")
    assert a.loc["qb_out", "value"] == 1 and a.loc["players_out", "value"] == 1
    assert a.loc["players_doubtful", "value"] == 1 and a.loc["players_questionable", "value"] == 1
    assert (a["asof_utc"] == "2021-09-11T12:00:00Z").all()
    b = df[df["team"] == "BBB"].set_index("field")
    assert b.loc["players_out", "value"] == 1 and b.loc["qb_out", "value"] == 0
    assert (df["asof_utc"] < "2021-09-12T17:00:00Z").all()


def test_crew_rate_uses_prior_games_only():
    rows, stw = [], []
    for i in range(40):
        d = pd.Timestamp("2020-09-13") + pd.Timedelta(days=7 * i)
        gid = f"g{i:02d}"
        rows.append({"game_id": gid, "season": 2020, "week": i, "gameday": d.strftime("%Y-%m-%d"),
                     "gametime": "13:00", "old_game_id": str(i), "referee": "Ref A" if i % 2 else "Ref B"})
        stw += [{"game_id": gid, "team": "X", "penalties": 10 if i % 2 else 5},
                {"game_id": gid, "team": "Y", "penalties": 5 if i % 2 else 3}]
    g = L.nfl_games_index(pd.DataFrame(rows))
    off = pd.DataFrame({"game_id": ["39", "39"], "position": ["Referee", "Umpire"], "official_id": [25, 26]})
    df = L.to_frame(L.nfl_official_facts(off, pd.DataFrame(stw), g))
    cc = df[df["field"] == "crew_chief_id"]
    assert cc["value_str"].tolist() == ["off25"] and cc["game_id"].item() == "g39"
    z = df[df["field"] == "ref_flag_rate_z"].set_index("game_id")["value"]
    assert z.loc["g39"] > 0 and z.loc["g38"] < 0
    pg = df[df["field"] == "ref_prior_games"].set_index("game_id")["value"]
    assert pg.loc["g39"] == 19                       # 19 earlier Ref A games, its own game excluded


GL = ('"20230330","0","Thu","MIL","NL",1,"CHN","NL",1,0,4,51,"N","","","","CHI11",36054,141'
      + ',""' * 58 + ',"kulpr901","Ron Kulpa"' + ',""' * 82 + "\n")


def test_mlb_gamelog_and_event_info():
    rows = list(csv.reader(io.StringIO(GL)))
    assert rows[0][77] == "kulpr901"
    ev = L.parse_event_info("id,CHN202303300\nversion,2\ninfo,starttime,7:05PM\ninfo,temp,45\n"
                            "info,windspeed,12\ninfo,winddir,ltor\ninfo,sky,cloudy\ninfo,precip,unknown\n"
                            "start,a,b,0,1,2\n")
    df = L.to_frame(L.mlb_facts(rows, ev))
    f = df.set_index("field")
    assert f.loc["hp_umpire", "value_str"] == "kulpr901" and f.loc["park", "value_str"] == "chi11"
    assert f.loc["day_night", "value_str"] == "night"
    assert f.loc["temp_f", "asof_utc"] == "2023-03-31T00:04:59Z"          # 7:05 PM CDT minus 1s
    assert f.loc["attendance_postgame", "value"] == 36054
    assert f.loc["attendance_postgame", "asof_utc"] == "2023-03-31T10:00:00Z"
    assert "precip" not in f.index and "duration" not in " ".join(df["field"])
    assert set(df["game_id"]) == {"CHN202303300"}


def test_buckets_fit_on_train_only(tmp_path):
    rows = [L.Fact(f"g{i}", None, "2020-01-01T00:00:00Z", "x", float(i), "new", "s", "l") for i in range(20)]
    df = L.to_frame(rows)
    dates = pd.Series({f"g{i}": pd.Timestamp("2020-01-01" if i < 10 else "2024-01-01") for i in range(20)})
    out, edges = L.bucketize(df, dates, pd.Timestamp("2022-01-01"))
    assert max(edges["x"]) <= 9                      # test rows (10-19) never shaped the edges
    assert out["bucket"].iloc[-1] == len(edges["x"])
    res = L.write_league(out, pd.Series({k: v.year for k, v in dates.items()}), "zz", tmp_path, edges)
    assert set(res["seasons"]) == {"2020", "2024"}
    assert json.loads((tmp_path / "shards" / "zz" / "edges.json").read_text()) == edges
    back = pd.read_parquet(tmp_path / "shards" / "zz" / "2024.parquet")
    assert list(back.columns) == L.to_frame([]).columns.tolist()


def test_injuries_without_timestamp_fall_back_to_kickoff():
    inj = pd.DataFrame({"season": [2021], "week": [1], "team": ["AAA"], "gsis_id": ["p1"], "position": ["QB"],
                        "report_status": ["Out"]})                  # 2025+ schema: no date_modified column
    df = L.to_frame(L.nfl_injury_facts(inj, games())[0])
    assert (df["asof_utc"] == "2021-09-12T16:59:59Z").all() and len(df) == 4
