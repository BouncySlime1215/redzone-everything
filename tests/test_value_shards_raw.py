"""NBA value shards are built from the files fetch_sports_nba.py wrote (a Kaggle notebook input), never downloaded.

Why: github.com's robots rule stopped the release-file route; the fetch notebook's output is the only NBA source. The
personal-use tag and the raw audit must keep working on those files, and a missing input must stop the run, not fall back.
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from agent.kaggle import sports_common as sc
from scripts import kaggle_value_shards as K

TIP = dt.datetime(2024, 1, 10, 0, 30, tzinfo=dt.timezone.utc)      # night 20240109, well before the NBA eval cut


def _game(gid, n=150, season=2024, tag=sc.PERSONAL_TAG):
    """A hoopR-shaped game with a play every 80 s (about 3.3 h) and a scoring play every 5th."""
    rows, home, away = [], 0, 0
    for i in range(n):
        score = i % 5 == 4
        home += 2 if score and i % 2 else 0
        away += 3 if score and not i % 2 else 0
        rows.append({"id": f"{gid}{i}", "sequence_number": str(1000 + i), "type_text": "Made Shot" if score else "Miss",
                     "text": "x", "period_number": 1 + i * 4 // n, "clock_display_value": f"{11 - i % 12}:00",
                     "wallclock": (TIP + dt.timedelta(seconds=80 * i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "scoring_play": score, "score_value": 2 if score else 0, "home_score": home, "away_score": away,
                     "game_id": gid, "season": season, "season_type": 2, "shooting_play": True,
                     "home_team_abbrev": "BBB", "away_team_abbrev": "AAA", "night": "20240109", "tag": tag})
    return pd.DataFrame(rows)


def _attach(tmp_path, frames, notebook="redzone-fetch-nba-ncaam"):
    shards = tmp_path / "input" / notebook / "data" / "sports_hoopr_espn_nba" / "shards"
    shards.mkdir(parents=True)
    for i, f in enumerate(frames):
        f.to_parquet(shards / f"sports_hoopr_espn_nba-{i:05d}-000.parquet", index=False)
    return tmp_path / "input"


def _run(tmp_path, inp, *extra):
    return K.main(["--source", "public", "--leagues", "nba", "--personal-nba", "--seasons", "2024", "--jobs", "1",
                   "--out", str(tmp_path / "o"), "--raw", str(tmp_path / "r"), "--input", str(inp), *extra])


def test_nba_shards_build_from_attached_fetch_files_and_pass_the_raw_audit(tmp_path, capsys):
    inp = _attach(tmp_path, [pd.concat([_game("g1"), _game("g2")], ignore_index=True)])
    rc = _run(tmp_path, inp)
    text = capsys.readouterr().out
    report = json.loads((tmp_path / "o" / "run_report.json").read_text())
    side = json.loads((tmp_path / "o" / "nba" / "2024.json").read_text())
    assert rc == 0 and "raw_audit" not in text and report["leak_check_exit"] == 0
    assert side["games"] == 2 and side["rows"] > 0 and "personal-use" in side["license"]
    assert (tmp_path / "r" / "nba" / "pbp_2024.parquet").exists()
    assert "raw_rows" in text and '"raw_rows": 0' not in text        # the audit really read the staged files


def test_without_the_attached_files_it_stops_and_never_downloads(tmp_path, capsys, monkeypatch):
    import scripts.noggin_factory as NF
    monkeypatch.setattr(NF, "_fetch", lambda *a, **k: pytest.fail("NBA must not be downloaded"))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("no network"))
    rc = _run(tmp_path, tmp_path / "nothing")
    text = capsys.readouterr().out
    assert rc == 2 and "fetch_sports_nba.py" in text and "github.com" in text and "STOPPED" in text
    assert not list((tmp_path / "o").glob("*/*.parquet"))


def test_files_without_the_personal_use_tag_are_refused(tmp_path, capsys):
    rc = _run(tmp_path, _attach(tmp_path, [_game("g1", tag="")]))
    assert rc == 2 and "tagged" in capsys.readouterr().out


def test_a_future_fact_in_the_attached_files_still_fails_the_raw_audit(tmp_path):
    inp = _attach(tmp_path, [_game("g1")])
    assert _run(tmp_path, inp) == 0
    raw = tmp_path / "r" / "nba" / "pbp_2024.parquet"
    df = pd.read_parquet(raw)
    df["home_score"] = df.home_score + 9          # the raw source now disagrees with the shard
    df.to_parquet(raw, index=False)
    from scripts import value_leak_check as LC
    rep = LC.check_dir(tmp_path / "o", allow_personal_nba=True, raw=tmp_path / "r")
    assert any(f[0] == "raw_audit" if isinstance(f, tuple) else "raw_audit" in str(f) for f in rep.fails)
