"""Men's college basketball value shards: built from the files fetch_sports_ncaam.py wrote, never downloaded.

Why: CBB has two 20 min halves, so its late/overtime/game_left features must come from the league table (REGULATION_LEN),
not NBA's four quarters. Same rules as NBA: personal-use tag, night ids, raw audit, no github.com fallback.
"""
import datetime as dt
import json

import pandas as pd
import pytest

from agent.kaggle import sports_common as sc
from redzone.noggin import value_shards as V
from scripts import kaggle_value_shards as K, value_leak_check as LC

TIP = dt.datetime(2024, 1, 10, 0, 30, tzinfo=dt.timezone.utc)      # night 20240109


def _game(gid, n=150, season=2024, tag=sc.PERSONAL_TAG):
    """A hoopR-shaped CBB game: halves 1 and 2 (period_number 1..2, 20:00 each), a play every 80 s, a score every 5th."""
    rows, home, away = [], 0, 0
    for i in range(n):
        score = i % 5 == 4
        home += 2 if score and i % 2 else 0
        away += 3 if score and not i % 2 else 0
        half = 1 + i * 2 // n
        left = 1200 - (i % (n // 2)) * 1200 // (n // 2)
        rows.append({"id": f"{gid}{i}", "sequence_number": str(1000 + i), "type_text": "Made Shot" if score else "Miss",
                     "text": "x", "period_number": half, "clock_display_value": f"{left // 60}:{left % 60:02d}",
                     "wallclock": (TIP + dt.timedelta(seconds=80 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "scoring_play": score, "score_value": 2 if score else 0, "home_score": home, "away_score": away,
                     "game_id": gid, "season": season, "season_type": 2, "shooting_play": True,
                     "home_team_abbrev": "BBB", "away_team_abbrev": "AAA", "night": "20240109", "tag": tag})
    return pd.DataFrame(rows)


def _attach(tmp_path, frames, source="hoopr_espn_cbb"):
    shards = tmp_path / "input" / "redzone-fetch-nba-ncaam" / "data" / f"sports_{source}" / "shards"
    shards.mkdir(parents=True)
    for i, f in enumerate(frames):
        f.to_parquet(shards / f"sports_{source}-{i:05d}-000.parquet", index=False)
    return tmp_path / "input"


def _run(tmp_path, inp, flag="--personal-hoopr"):
    return K.main(["--source", "public", "--leagues", "cbb", flag, "--seasons", "2024", "--jobs", "1",
                   "--out", str(tmp_path / "o"), "--raw", str(tmp_path / "r"), "--input", str(inp)])


def test_cbb_is_off_without_the_flag_and_tagged_personal_with_it():
    assert V.public_status("cbb")[0] == "not_cleared"
    with pytest.raises(V.SourceNotCleared):
        V.public_source("cbb")
    source, licence, plan, _, seasons = V.public_source("cbb", True)
    assert source == "hoopr_espn_cbb" and V.PERSONAL_USE in licence and plan == "B" and 2024 in seasons


def test_cbb_has_two_halves_in_the_league_table_and_features_follow_it():
    assert V.REGULATION_LEN["cbb"] == (2, 1200.0)
    df = V.build_rows("cbb", "g1", {"plays": _plays(_game("g1"))}, night="20240109", season=2024, source="hoopr_espn_cbb",
                      license_line="x", plan="B")
    assert not df.empty
    assert set(df.period.unique()) <= {1.0, 2.0}
    first = df[df.period == 1.0]
    assert first.game_left.max() <= 2 * 1200 and (first.game_left >= 1200).all()     # a whole second half still to play
    assert (df[(df.period == 2.0) & (df.clock_left >= 0)].game_left <= 1200).all()
    late = df[(df.period == 2.0) & (df.clock_left <= 300) & (df.clock_left >= 0)]
    assert len(late) and (late.late == 1.0).all() and (first.late == 0.0).all()       # crunch = last 5 min of half 2


def _plays(g):
    from scripts.holdset_public import hoops_summary
    return hoops_summary(g)["plays"]


def test_cbb_shards_build_from_attached_fetch_files_and_pass_the_raw_audit(tmp_path, capsys):
    rc = _run(tmp_path, _attach(tmp_path, [pd.concat([_game("g1"), _game("g2")], ignore_index=True)]))
    text = capsys.readouterr().out
    side = json.loads((tmp_path / "o" / "cbb" / "2024.json").read_text())
    assert rc == 0 and "raw_audit" not in text
    assert side["games"] == 2 and side["rows"] > 0 and "personal-use" in side["license"] and side["source"] == "hoopr_espn_cbb"
    assert side["nights"] == ["20240109"]
    assert (tmp_path / "r" / "cbb" / "pbp_2024.parquet").exists()
    assert '"raw_rows": 0' not in text                    # the audit really read the staged file
    assert side["prefix_checked"] > 0 and side["prefix_failed"] == 0


def test_the_old_flag_name_still_works(tmp_path):
    assert _run(tmp_path, _attach(tmp_path, [_game("g1")]), flag="--personal-nba") == 0


def test_missing_input_stops_and_never_downloads(tmp_path, capsys, monkeypatch):
    import scripts.noggin_factory as NF
    monkeypatch.setattr(NF, "_fetch", lambda *a, **k: pytest.fail("CBB must not be downloaded"))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))
    rc = _run(tmp_path, tmp_path / "nothing")
    text = capsys.readouterr().out
    assert rc == 2 and "fetch_sports_ncaam.py" in text and "github.com" in text and "STOPPED" in text
    assert not list((tmp_path / "o").glob("*/*.parquet"))


def test_nba_files_are_not_taken_for_cbb(tmp_path, capsys):
    rc = _run(tmp_path, _attach(tmp_path, [_game("g1")], source="hoopr_espn_nba"))
    assert rc == 2 and "STOPPED" in capsys.readouterr().out


def test_files_without_the_personal_use_tag_are_refused(tmp_path, capsys):
    assert _run(tmp_path, _attach(tmp_path, [_game("g1", tag="")])) == 2
    assert "tagged" in capsys.readouterr().out


def test_leak_check_rejects_cbb_rows_without_the_flag_and_a_wrong_night(tmp_path, capsys):
    assert _run(tmp_path, _attach(tmp_path, [_game("g1")])) == 0
    capsys.readouterr()
    assert LC.check_dir(tmp_path / "o", raw=tmp_path / "r").fails                    # personal source, flag not given
    assert not LC.check_dir(tmp_path / "o", allow_personal_nba=True, raw=tmp_path / "r").fails


def test_a_future_fact_in_the_attached_cbb_files_fails_the_raw_audit(tmp_path, capsys):
    assert _run(tmp_path, _attach(tmp_path, [_game("g1")])) == 0
    raw = tmp_path / "r" / "cbb" / "pbp_2024.parquet"
    df = pd.read_parquet(raw)
    df["home_score"] = df.home_score + 9
    df.to_parquet(raw, index=False)
    capsys.readouterr()
    rep = LC.check_dir(tmp_path / "o", allow_personal_nba=True, raw=tmp_path / "r")
    assert any("raw_audit" in str(f) for f in rep.fails)
