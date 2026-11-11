"""MORE-DATA: NFL back to 1999, college football (CFB) and WNBA value shards, built from attached fetch files, and why MLB is not built.

Why: Nick wants every fetched season used. A season whose file has no wall clock must say so (an empty shard with the reason), not
look built; each new league must pass the leak check and the raw audit, and a planted future fact must fail them.
"""
import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest
from test_value_shards_raw import _game as _hoops_game, _nfl_game

from agent.kaggle import sports_common as sc
from redzone.noggin import value_shards as V
from scripts import holdset_public as HP, kaggle_value_shards as K, value_leak_check as LC

KICK = dt.datetime(2023, 9, 16, 16, 0, tzinfo=dt.timezone.utc)      # night 20230916, before the CFB eval cut (20250801)


def _cfb_game(gid="401520001", n=170, season=2023, tag=sc.PERSONAL_TAG, dotted=True):
    """An ESPN-shaped college football game (flattened column names): a play every 70 s, a touchdown every 25th play, a drive per 10."""
    rows, home, away = [], 0, 0
    for i in range(n):
        td = i % 25 == 24
        home += 7 if td and i % 50 == 24 else 0
        away += 7 if td and i % 50 == 49 else 0
        rows.append({"game_id": gid, "id": f"{gid}{i:04d}", "sequenceNumber": str(100 + i),
                     "type.text": "Rushing Touchdown" if td else ("Pass Incompletion" if i % 3 else "Rush"), "text": "x",
                     "period.number": 1 + i * 4 // n, "clock.displayValue": f"{14 - i % 15}:00",
                     "wallclock": (KICK + dt.timedelta(seconds=70 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "scoringPlay": td, "homeScore": home, "awayScore": away, "statYardage": 5.0, "drive.id": f"{gid}{i // 10}",
                     "start.down": float(1 + i % 4), "start.distance": 10.0, "start.yardsToEndzone": float(80 - i % 70),
                     "end.yardsToEndzone": float(75 - i % 70), "season_type": 2, "home_team_abbrev": "BBB", "away_team_abbrev": "AAA",
                     "junk.column": i, "season": season, "night": "20230915", "tag": tag})
    df = pd.DataFrame(rows)
    return df if dotted else df.rename(columns={"type.text": "type_text", "period.number": "period_number", "clock.displayValue": "clock_display_value",
                                                "start.down": "start_down", "start.distance": "start_distance", "drive.id": "drive_id",
                                                "start.yardsToEndzone": "start_yards_to_endzone", "end.yardsToEndzone": "end_yards_to_endzone",
                                                "scoringPlay": "scoring_play", "homeScore": "home_score", "awayScore": "away_score",
                                                "sequenceNumber": "sequence_number", "statYardage": "stat_yardage"})


def _wnba_game(gid="g1", tag=sc.PERSONAL_TAG, season=2023):
    df = _hoops_game(gid, season=season, tag=tag)
    df["clock_display_value"] = [f"{9 - i % 10}:30" for i in range(len(df))]       # four 10 min quarters
    df["wallclock"] = [(KICK + dt.timedelta(seconds=80 * i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(len(df))]
    df["night"] = "20230915"
    return df


def _attach(tmp_path, frames, source, notebook):
    shards = tmp_path / "input" / notebook / "data" / f"sports_{source}" / "shards"
    shards.mkdir(parents=True)
    for i, f in enumerate(frames):
        f.to_parquet(shards / f"sports_{source}-{i:05d}-000.parquet", index=False)
    return tmp_path / "input"


def _run(tmp_path, league, inp, season, *extra):
    return K.main(["--source", "public", "--leagues", league, "--personal-nba", "--seasons", str(season), "--jobs", "1",
                   "--out", str(tmp_path / "o"), "--raw", str(tmp_path / "r"), "--input", str(inp), *extra])


def _fails(tmp_path):
    return LC.check_dir(tmp_path / "o", allow_personal_nba=True, raw=tmp_path / "r").fails


# --- NFL 1999-2010 ---------------------------------------------------------------------------------------------

def test_nfl_seasons_reach_1999_and_a_season_without_a_snap_clock_is_an_empty_shard_that_says_why(tmp_path, capsys):
    assert V.public_source("nfl")[4][-1] == 1999 and V.public_source("nfl")[4][0] == 2024
    old = _nfl_game("1999_01_A_B", season=1999)
    old["time_of_day"] = None                                  # nflverse has no time_of_day for 1999
    inp = _attach(tmp_path, [old], "nflverse_pbp", "redzone-fetch-nfl")
    assert _run(tmp_path, "nfl", inp, 1999) == 0
    side = json.loads((tmp_path / "o" / "nfl" / "1999.json").read_text())
    rep = json.loads((tmp_path / "o" / "run_report.json").read_text())["report"]
    assert side["rows"] == 0 and side["games"] == 0 and side["no_wall_games"] == 1
    assert rep[0]["status"] == "built" and rep[0]["why_empty"] == {"no_wall_games": 1}


def test_nfl_2005_with_a_snap_clock_builds_and_passes_the_raw_audit(tmp_path, capsys):
    inp = _attach(tmp_path, [_nfl_game("2005_01_A_B", season=2005)], "nflverse_pbp", "redzone-fetch-nfl")
    assert _run(tmp_path, "nfl", inp, 2005) == 0
    assert json.loads((tmp_path / "o" / "nfl" / "2005.json").read_text())["games"] == 1
    assert '"raw_rows": 0' not in capsys.readouterr().out


# --- CFB ---------------------------------------------------------------------------------------------------------

def test_cfb_and_wnba_are_personal_use_leagues_and_off_without_the_flag():
    for lg, source in (("cfb", "espn_cfb"), ("wnba", "wehoop_espn_wnba")):
        assert V.public_status(lg)[0] == "not_cleared"
        got = V.public_source(lg, True)
        assert got[0] == source and V.PERSONAL_USE in got[1] and got[2] == "B"
        assert source in LC.PERSONAL_SOURCES and LC.PERSONAL_SOURCES[source] == lg
    assert V.REGULATION_LEN["wnba"] == (4, 600.0) and V.REGULATION_LEN["cfb"] == (4, 900.0)


@pytest.mark.parametrize("dotted", [True, False])
def test_cfb_shards_build_from_attached_files_and_pass_the_raw_audit(tmp_path, capsys, dotted):
    inp = _attach(tmp_path, [pd.concat([_cfb_game("a1", dotted=dotted), _cfb_game("a2", dotted=dotted)], ignore_index=True)],
                  "espn_cfb", "redzone-fetch-cfb")
    assert _run(tmp_path, "cfb", inp, 2023) == 0
    text = capsys.readouterr().out
    side = json.loads((tmp_path / "o" / "cfb" / "2023.json").read_text())
    assert side["games"] == 2 and side["rows"] > 0 and side["source"] == "espn_cfb" and "personal-use" in side["license"]
    assert side["nights"] == ["20230916"] and side["prefix_checked"] > 0 and side["prefix_failed"] == 0
    assert "raw_audit" not in text and '"raw_rows": 0' not in text
    staged = pd.read_parquet(tmp_path / "r" / "cfb" / "pbp_2023.parquet")
    assert "junk.column" not in staged and "start_down" in staged and "wallclock" in staged      # only what the adapter reads, canonical names
    rows = pd.read_parquet(tmp_path / "o" / "cfb" / "2023.parquet")
    assert rows.yards_to_goal.max() > 0 and (rows.down > 0).any() and rows.value_next_3m.max() > 0   # football fields reach the rows


def test_cfb_labels_come_from_the_football_detectors(tmp_path):
    summary = HP.cfb_summary(HP.cfb_frame(HP.cfb_read(_write(tmp_path, _cfb_game("a1")))[0]))
    from redzone.moments.detect import detect
    kinds = {m.kind for m in detect("cfb", summary, "a1").moments}
    assert "td" in kinds


def _write(tmp_path, df, name="x.parquet"):
    p = tmp_path / name
    df.to_parquet(p, index=False)
    return p


def test_a_cfb_file_without_the_needed_columns_is_an_empty_shard_that_names_them(tmp_path):
    df = _cfb_game("a1").drop(columns=["wallclock", "drive.id"])
    inp = _attach(tmp_path, [df], "espn_cfb", "redzone-fetch-cfb")
    assert _run(tmp_path, "cfb", inp, 2023) == 0
    side = json.loads((tmp_path / "o" / "cfb" / "2023.json").read_text())
    assert side["rows"] == 0
    rep = json.loads((tmp_path / "o" / "run_report.json").read_text())["report"][0]
    assert rep["status"] == "built" and rep["why_empty"] == {"missing_columns": "wallclock,drive_id"}


def test_a_cfb_file_with_an_unusable_wall_clock_column_names_it(tmp_path):
    df = _cfb_game("a1")
    df["wallclock"] = None                                         # the column exists, no game has a stamp
    assert _run(tmp_path, "cfb", _attach(tmp_path, [df], "espn_cfb", "redzone-fetch-cfb"), 2023) == 0
    rep = json.loads((tmp_path / "o" / "run_report.json").read_text())["report"][0]
    assert rep["rows"] == 0 and rep["why_empty"] == {"no_wall_games": 1}


def test_cfb_games_on_or_after_the_eval_cut_are_dropped_before_any_feature(tmp_path):
    late = _cfb_game("a1")
    late["wallclock"] = [(dt.datetime(2025, 9, 6, 16, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=70 * i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(len(late))]
    assert _run(tmp_path, "cfb", _attach(tmp_path, [pd.concat([late, _cfb_game("a2")], ignore_index=True)], "espn_cfb", "redzone-fetch-cfb"), 2023) == 0
    side = json.loads((tmp_path / "o" / "cfb" / "2023.json").read_text())
    assert side["games"] == 1 and side["dropped_eval_games"] == 1 and all(n < "20250801" for n in side["nights"])


def test_cfb_rows_are_refused_without_the_personal_use_tag_and_without_the_flag(tmp_path, capsys):
    assert _run(tmp_path, "cfb", _attach(tmp_path, [_cfb_game("a1", tag="")], "espn_cfb", "redzone-fetch-cfb"), 2023) == 2
    assert "tagged" in capsys.readouterr().out


def test_cfb_missing_input_stops_and_never_downloads(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))
    assert _run(tmp_path, "cfb", tmp_path / "nothing", 2023) == 2
    assert "fetch_sports_cfb.py" in capsys.readouterr().out


def test_cfb_leak_check_rejects_rows_without_the_flag(tmp_path):
    assert _run(tmp_path, "cfb", _attach(tmp_path, [_cfb_game("a1")], "espn_cfb", "redzone-fetch-cfb"), 2023) == 0
    assert LC.check_dir(tmp_path / "o", raw=tmp_path / "r").fails and not _fails(tmp_path)


@pytest.mark.parametrize("field, bump", [("home_score", 9), ("end_yards_to_endzone", 30), ("start_down", 2)])
def test_a_planted_future_fact_in_the_cfb_raw_file_fails_the_raw_audit(tmp_path, field, bump):
    assert _run(tmp_path, "cfb", _attach(tmp_path, [_cfb_game("a1")], "espn_cfb", "redzone-fetch-cfb"), 2023) == 0
    raw = tmp_path / "r" / "cfb" / "pbp_2023.parquet"
    df = pd.read_parquet(raw)
    df[field] = df[field].astype(float) + bump
    df.to_parquet(raw, index=False)
    assert any("raw_audit" in f for f in _fails(tmp_path))


def test_a_planted_cfb_adapter_field_that_reads_the_future_fails_the_raw_audit(tmp_path):
    """The adapter bug the NFL review planted, on CFB: a play's field says whether a touchdown comes in the next 180 s."""
    from redzone.moments.detect import parse_ts
    frame, _ = HP.cfb_read(_write(tmp_path, _cfb_game("a1")))
    frame = HP.cfb_frame(frame)
    summary = HP.cfb_summary(frame)
    plays = [p for d in summary["drives"]["previous"] for p in d["plays"]]
    walls = np.array([parse_ts(p["wallclock"]) for p in plays])
    tds = np.array(["Touchdown" in p["type"]["text"] for p in plays])
    for i, p in enumerate(plays):
        p["end"]["yardsToEndzone"] = 50.0 if tds[(walls > walls[i]) & (walls <= walls[i] + 180)].any() else 10.0
    rows = V.build_rows("cfb", "a1", summary, night="20230916", season=2023, source="espn_cfb",
                        license_line=f"{V.PERSONAL_USE}; x", plan="B", game_key="cfb:20230916:AAA@BBB")
    n, bad = V.prefix_check("cfb", "a1", summary, rows, n=len(rows))
    assert not bad                                        # the prefix rebuild cannot see it
    out, raw = tmp_path / "o", tmp_path / "r"
    V.write_shard(out, "cfb", "2023", rows, {"source": "espn_cfb", "license": f"{V.PERSONAL_USE}; x", "plan": "B", "prefix_checked": n, "prefix_failed": 0})
    V.finalize(out)
    (raw / "cfb").mkdir(parents=True)
    frame.to_parquet(raw / "cfb" / "pbp_2023.parquet", index=False)
    rep = LC.check_dir(out, allow_personal_nba=True, raw=raw)
    assert any("raw_audit" in f and "yards_to_goal" in f for f in rep.fails)


def test_a_cfb_feature_that_reads_the_future_fails_the_prefix_check(tmp_path):
    frame, _ = HP.cfb_read(_write(tmp_path, _cfb_game("a1")))
    summary = HP.cfb_summary(HP.cfb_frame(frame))

    def cheating(view, head, decision):
        got = V.features(view, head, decision)
        got["abs_margin"] = np.full(len(head), float(abs(view.margin[-1])))
        return got
    rows = V.build_rows("cfb", "a1", summary, night="20230916", season=2023, source="espn_cfb", license_line="x", plan="B",
                        feature_fn=cheating)
    _, bad = V.prefix_check("cfb", "a1", summary, rows, n=len(rows), feature_fn=cheating)
    assert bad


# --- WNBA --------------------------------------------------------------------------------------------------------

def test_wnba_shards_build_from_attached_files_and_pass_the_raw_audit(tmp_path, capsys):
    inp = _attach(tmp_path, [pd.concat([_wnba_game("w1"), _wnba_game("w2")], ignore_index=True)], "wehoop_espn_wnba", "redzone-fetch-wnba")
    assert _run(tmp_path, "wnba", inp, 2023) == 0
    text = capsys.readouterr().out
    side = json.loads((tmp_path / "o" / "wnba" / "2023.json").read_text())
    assert side["games"] == 2 and side["rows"] > 0 and side["source"] == "wehoop_espn_wnba" and side["prefix_failed"] == 0
    assert "raw_audit" not in text and '"raw_rows": 0' not in text
    df = pd.read_parquet(tmp_path / "o" / "wnba" / "2023.parquet")
    assert df.game_left.max() <= 4 * 600 and (df.late.sum() > 0)          # four 10 min quarters, not NBA's 12


def test_wnba_files_of_another_league_are_not_taken_and_untagged_files_are_refused(tmp_path, capsys):
    assert _run(tmp_path, "wnba", _attach(tmp_path, [_wnba_game()], "hoopr_espn_nba", "n"), 2023) == 2
    assert "fetch_sports_wnba.py" in capsys.readouterr().out
    assert _run(tmp_path / "t2", "wnba", _attach(tmp_path / "t2", [_wnba_game(tag="")], "wehoop_espn_wnba", "n"), 2023) == 2


def test_a_season_with_no_wall_clock_column_is_an_empty_wnba_shard_that_says_why(tmp_path):
    df = _wnba_game().drop(columns=["wallclock"])
    assert _run(tmp_path, "wnba", _attach(tmp_path, [df], "wehoop_espn_wnba", "n"), 2023) == 0
    rep = json.loads((tmp_path / "o" / "run_report.json").read_text())["report"][0]
    assert rep["rows"] == 0 and rep["why_empty"] == {"missing_columns": "wallclock"}


def test_a_planted_future_fact_in_the_wnba_raw_file_fails_the_raw_audit(tmp_path):
    assert _run(tmp_path, "wnba", _attach(tmp_path, [_wnba_game()], "wehoop_espn_wnba", "n"), 2023) == 0
    raw = tmp_path / "r" / "wnba" / "pbp_2023.parquet"
    df = pd.read_parquet(raw)
    df["home_score"] = df.home_score + 9
    df.to_parquet(raw, index=False)
    assert any("raw_audit" in f for f in _fails(tmp_path))


def test_wnba_and_cfb_are_accepted_as_gamevalue_leagues():
    from redzone.noggin import gamevalue as G
    assert {"cfb", "wnba"} <= set(G.GV_LEAGUES) and {"espn_cfb", "wehoop_espn_wnba"} <= set(G.GV_PERSONAL_SOURCES)
    from redzone.moments import tiers
    assert tiers.family("wnba") == "hoops" and tiers.family("cfb") == "football"


# --- MLB ---------------------------------------------------------------------------------------------------------

def test_mlb_is_not_built_and_the_reason_names_what_it_would_need(tmp_path, capsys):
    status, why = V.public_status("mlb", True)
    assert status == "no_wallclock" and "Retrosheet" in why and "startTime" in why and "Stats API" in why
    assert K.main(["--source", "public", "--leagues", "mlb", "--personal-nba", "--out", str(tmp_path / "o"), "--raw", str(tmp_path / "r"),
                   "--input", str(tmp_path / "nothing")]) in (0, 1)
    assert "no_wallclock" in capsys.readouterr().out and not list((tmp_path / "o").glob("*/*.parquet"))
