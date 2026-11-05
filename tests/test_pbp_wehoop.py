"""scripts/pbp/wehoop.py: ESPN wehoop pbp -> play table, on a hand-made fixture (no network)."""
import json

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from scripts.pbp import schema, wehoop as W  # noqa: E402

S = -214748340
COLS = ["game_id", "sequence_number", "period_number", "clock_display_value", "wallclock", "home_score",
        "away_score", "type_text", "type_id", "scoring_play", "shooting_play", "score_value", "team_id",
        "home_team_id", "away_team_id", "home_team_abbrev", "away_team_abbrev", "athlete_id_1", "athlete_id_2",
        "coordinate_x", "coordinate_y", "game_date", "season"]


def _g1():  # WNBA game with wallclock, out of order on disk; home 5 = LVA, away 7 = NYL
    w = "2025-06-01T23:{:02d}:00Z"
    p = [  # seq, per, clock, wall, hs, as, type, scoring, shooting, sv, team, a1, a2, x, y
        (101, 1, "10:00", w.format(0), 0, 0, "Jumpball", False, False, 0, 5, 11, 21, S, S),
        (102, 1, "9:41", w.format(1), 2, 0, "Jump Shot", True, True, 2, 5, 11, None, 25, 15),
        (104, 1, "9:10", w.format(2), 2, 0, "Layup Shot", False, True, 0, 7, 21, None, 24, 3),
        (105, 1, "9:08", w.format(3), 2, 0, "Defensive Rebound", False, False, 0, 5, 12, None, S, S),
        (103, 1, "9:20", w.format(2), 2, 0, "Full Timeout", False, False, 0, 7, None, None, S, S),
        (106, 1, "8:50", w.format(4), 2, 0, "Lost Ball Turnover", False, False, 0, 5, 12, 22, S, S),
        (107, 1, "8:31", w.format(5), 2, 3, "Pullup Jump Shot", True, True, 3, 7, 22, None, 49, 26),
        (108, 1, "8:00", w.format(6), 2, 3, "Official TV Timeout", False, False, 0, None, None, None, S, S),
        (109, 1, "0.0", w.format(30), 2, 3, "End Period", False, False, 0, None, None, None, S, S),
    ]
    return [dict(game_id=401, sequence_number=s, period_number=pe, clock_display_value=c, wallclock=wc,
                 home_score=hs, away_score=as_, type_text=t, type_id=0, scoring_play=sc, shooting_play=sh,
                 score_value=sv, team_id=tm, home_team_id=5, away_team_id=7, home_team_abbrev="LVA",
                 away_team_abbrev="NYL", athlete_id_1=a1, athlete_id_2=a2, coordinate_x=x, coordinate_y=y,
                 game_date="2025-06-01", season=2025)
            for s, pe, c, wc, hs, as_, t, sc, sh, sv, tm, a1, a2, x, y in p]


def _g2(gid=402, gdate="2010-07-04", season=2010):  # old season: no wallclock
    p = [(1, 1, "10:00", 0, 0, "Jumpball", False, 0, 8), (2, 1, "9:30", 0, 1, "Free Throw - 1 of 1", True, 1, 9),
         (3, 2, "5:00", 0, 1, "Instant Replay", False, 0, None), (4, 2, "3:00", 2, 1, "Dunk Shot", True, 2, 8),
         (5, 4, "0.0", 2, 1, "End Game", False, 0, None), (6, 4, "0.0", 2, 1, "Substitution", False, 0, 8)]
    return [dict(game_id=gid, sequence_number=s, period_number=pe, clock_display_value=c, wallclock=None,
                 home_score=hs, away_score=as_, type_text=t, type_id=0, scoring_play=sc, shooting_play=None,
                 score_value=sv, team_id=tm, home_team_id=8, away_team_id=9, home_team_abbrev="SEA",
                 away_team_abbrev="PHO", athlete_id_1=None, athlete_id_2=None, coordinate_x=None,
                 coordinate_y=None, game_date=gdate, season=season)
            for s, pe, c, hs, as_, t, sc, sv, tm in p]


def _frame(*games):
    return pd.DataFrame([r for g in games for r in g], columns=COLS)


def test_game_with_wallclock():
    rows = W.convert_game(_g1(), "wnba", 2025)
    assert len(rows) == 9 and [r["seq"] for r in rows] == list(range(9))
    assert [r["kind"] for r in rows][:5] == ["jumpball", "jump shot", "full timeout", "layup shot",
                                             "defensive rebound"]
    assert schema.validate(rows) == []
    by = {r["kind"]: r for r in rows}
    # 23:00 UTC = 19:00 EDT, minus 5 h -> same day
    assert all(r["night"] == "20250601" for r in rows)
    assert rows[0]["wall_ts"] == pytest.approx(1748818800.0)
    assert by["jump shot"]["side"] == "home" and by["full timeout"]["side"] == "away"
    assert by["official tv timeout"]["side"] == ""
    assert by["jump shot"]["clock_s"] == 581 and by["end period"]["clock_s"] == 0
    assert (by["jump shot"]["sub_kind"], by["jump shot"]["value"]) == ("made", 2)
    assert (by["layup shot"]["sub_kind"], by["layup shot"]["value"]) == ("missed", 0)
    assert by["pullup jump shot"]["value"] == 3 and by["pullup jump shot"]["away_score"] == 3
    assert by["jumpball"]["sub_kind"] == ""
    assert [r["stop"] for r in rows] == ["none", "none", "timeout", "none", "none", "none", "none", "timeout",
                                         "end_period"]
    assert by["jumpball"]["x"] is None and by["jumpball"]["y"] is None
    assert by["jump shot"]["x"] == pytest.approx(0.5) and by["jump shot"]["y"] == pytest.approx(15 / 94)
    assert (by["lost ball turnover"]["actor_id"], by["lost ball turnover"]["actor2_id"]) == ("12", "22")
    assert (rows[0]["home"], rows[0]["away"], rows[0]["license"], rows[0]["tag"]) == ("LVA", "NYL", W.LICENSE,
                                                                                      schema.PERSONAL)
    # possession: made home 2 -> away; missed away layup carries; home def reb -> home; home TO -> away
    assert [r["possession"] for r in rows] == ["", "away", "away", "away", "home", "away", "home", "home",
                                               "home"]


def test_game_without_wallclock_falls_back_to_date():
    rows = W.convert_game(_g2(), "wnba", 2010)
    assert schema.validate(rows) == []
    assert all(r["night"] == "20100704" and r["wall_ts"] is None for r in rows)
    assert [r["stop"] for r in rows] == ["none", "none", "review", "none", "end_period", "none"]
    ft, dunk = rows[1], rows[3]
    assert (ft["side"], ft["sub_kind"], ft["value"], ft["possession"]) == ("away", "made", 1, "home")
    assert (dunk["sub_kind"], dunk["value"], dunk["possession"]) == ("made", 2, "away")  # shot-word fallback
    assert rows[2]["side"] == "" and rows[0]["x"] is None


def test_night_et_rule_after_midnight_utc():
    assert W.night_of(W.wall_ts("2025-06-02T03:30:00Z")) == "20250601"  # 23:30 EDT
    assert W.night_of(W.wall_ts("2025-06-02T05:30:00Z")) == "20250601"  # 01:30 EDT -> minus 5 h
    assert W.coord(S, 50) is None and W.coord(-3, 50) is None and W.coord(60, 50) is None


def test_wcbb_locked_night_rejected():
    rows = W.convert_game(_g2(gid=9, gdate="2026-11-01", season=2027), "wcbb", 2027)
    assert rows[0]["level"] == "college"
    assert any("locked window" in e for e in schema.validate(rows))
    ok, counts = W.convert_frame(_frame(_g2(gid=9, gdate="2026-11-01", season=2027),
                                        _g2(gid=10, gdate="2026-03-01", season=2026)), "wcbb", 2027)
    assert counts == {"games": 1, "dropped_games": 0, "locked_games": 1} and {r["game_id"] for r in ok} == {"10"}


def test_frame_drops_bad_game_and_shard_roundtrip(tmp_path):
    bad = _g2(gid=403, gdate=None)  # no wallclock, no date -> no night -> dropped
    rows, counts = W.convert_frame(_frame(_g1(), _g2(), bad), "wnba", 2025)
    assert counts == {"games": 2, "dropped_games": 1, "locked_games": 0} and len(rows) == 15
    side = schema.write_shard(rows, tmp_path / "s.parquet", {"source": "wehoop", **counts})
    back = pd.read_parquet(tmp_path / "s.parquet")
    assert len(back) == 15 and side["games"] == 2 and list(back.columns) == list(schema.COLUMNS)
    assert back.x.isna().sum() > 0 and back.wall_ts.isna().sum() == 6


def test_pull_season_with_fake_downloader(tmp_path):
    fixture = tmp_path / "fixture.parquet"
    _frame(_g1(), _g2()).to_parquet(fixture)
    urls = []

    def fake(url, dest):
        urls.append(url)
        if "play_by_play_" in url:
            return False  # first template 404s -> fallback spelling
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(fixture.read_bytes())
        return True

    out = tmp_path / "out"
    side = W.pull_season("wnba", 2025, out, downloader=fake)
    assert len(urls) == 2 and urls[0] == W.URL_TEMPLATES["wnba"][0].format(season=2025)
    assert side["events"] == 15 and side["dropped_games"] == 0 and side["data_plan"] == "B"
    meta = json.loads((out / "wnba" / "2025.parquet.json").read_text())
    assert (meta["license"], meta["tag"], meta["source"]) == (W.LICENSE, schema.PERSONAL, "wehoop")
    assert not (out / "raw" / "wnba" / "play_by_play_2025.parquet").exists()  # keep_raw=False
    assert W.pull_season("wnba", 2025, out, downloader=fake)["skipped"]  # resumable: shard already there
    assert W.pull_season("wnba", 2024, out, downloader=lambda u, d: False) is None
    assert W.SOURCE["id"] == "wehoop" and set(W.SOURCE["leagues"]) == {"wnba", "wcbb"}
    assert W._seasons("2025-2023") == [2025, 2024, 2023]
