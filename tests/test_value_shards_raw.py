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
from redzone.moments.detect import clock_s, detect
from redzone.noggin import value_shards as V
from scripts import holdset, kaggle_value_shards as K, value_leak_check as LC
from scripts.holdset_public import hoops_summary

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


# --- WP-26C: the two failure kinds the Kaggle NBA leak check found, rebuilt from synthetic plays --------------------


def _inverted(gid="g1"):
    """A home run the feed interrupts with an away basket whose wall clock is stamped after the next two home baskets.
    In feed order the away basket resets the run, so no run8. Cut the feed before that stamp and the basket is missing:
    the next home basket looks like a 5-point play that extends the run, and run8 fires. Same shape as the Kaggle
    failure (t2_5m 0.0 from the full game, 1.0 from the feed)."""
    df = _game(gid)
    scores = {8: (3, 0), 9: (3, 0), 10: (0, 2), 11: (3, 0), 12: (3, 0)}
    home = away = 0
    for i in range(len(df)):
        h, a = scores.get(i, (0, 0))
        home, away = home + h, away + a
        df.loc[i, ["home_score", "away_score"]] = home, away
        df.loc[i, "scoring_play"] = i in scores
        df.loc[i, "score_value"] = h + a
    df.loc[10, "wallclock"] = (TIP + dt.timedelta(seconds=80 * 10 + 200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return df


def _tenths(gid="g1"):
    """Under a minute hoopR's clock can read M:SS.d, and clock_s keeps whole seconds of that form."""
    df = _game(gid)
    df["clock_display_value"] = [f"0:{59 - i % 50}.{i % 9 + 1}" for i in range(len(df))]
    return df


def _raw_fails(tmp_path, capsys):
    rep = LC.check_dir(tmp_path / "o", allow_personal_nba=True, raw=tmp_path / "r")
    capsys.readouterr()
    return rep


def test_a_basket_stamped_out_of_order_does_not_change_features_when_the_feed_is_cut(tmp_path):
    summary = hoops_summary(_inverted())
    # feed order, as a caller without the adapter's sort would pass it
    summary["plays"] = sorted(summary["plays"], key=lambda p: int(p["sequenceNumber"]))
    rows = V.build_rows("nba", "g1", summary, night="20240109", season=2024, source="hoopr_espn_nba", license_line="x", plan="B")
    checked, bad = V.prefix_check("nba", "g1", summary, rows, n=len(rows))
    assert checked == len(rows) and bad == []


def test_out_of_order_stamps_pass_the_shard_and_raw_checks_end_to_end(tmp_path, capsys):
    rc = _run(tmp_path, _attach(tmp_path, [_inverted()]))
    text = capsys.readouterr().out
    assert rc == 0 and "future_feature" not in text and "raw_audit" not in text


def test_the_raw_audit_detector_reads_plays_in_the_same_order_as_the_shards():
    """The audit's labels come from holdset's detector through hoops_summary. In any other order than the builder's, a
    run the builder counted would be missing from the audit, on exactly the games with out-of-order stamps."""
    summary = hoops_summary(_inverted())
    audit = [e for e in holdset.game_row("nba", "g1", summary)[4] if e[1] == 2]
    shards = sorted((m.t, m.tier) for m in detect("nba", V.wall_ordered("nba", summary), "g1").moments if m.tier == 2)
    assert audit == shards and audit


def test_feeds_that_differ_only_in_tied_stamps_are_not_the_cause():
    """Plays sharing a wall clock are kept or dropped together by the cut, so a tie alone cannot change a feature."""
    df = _inverted()
    df.loc[[10, 11, 12], "wallclock"] = df.loc[12, "wallclock"]      # a real tie: no later play carries an earlier stamp
    summary = hoops_summary(df)
    rows = V.build_rows("nba", "g1", summary, night="20240109", season=2024, source="hoopr_espn_nba", license_line="x", plan="B")
    assert V.prefix_check("nba", "g1", summary, rows, n=len(rows))[1] == []


def test_tenths_on_the_clock_pass_the_exact_raw_comparison(tmp_path, capsys):
    rc = _run(tmp_path, _attach(tmp_path, [_tenths()]))
    text = capsys.readouterr().out
    assert rc == 0 and "raw_audit" not in text


CLOCK_FORMS = ["3.9", "29.2", "0.1", "59.1", "0:03.9", "0:29.2", "0:00.1", "00:03.9", "3", "0:03", "1:05", "12:00", "", "bad"]


@pytest.mark.parametrize("form", CLOCK_FORMS)
def test_the_audit_parses_every_clock_form_as_the_feature_does(form):
    c = clock_s({"clock": {"displayValue": form}})
    assert LC._raw_clock(form) == (-1.0 if c is None else float(c))


def test_a_game_mixing_bare_seconds_and_tenths_passes_the_exact_raw_comparison(tmp_path, capsys):
    df = _game("g1")
    df["clock_display_value"] = [CLOCK_FORMS[i % 12] for i in range(len(df))]
    rc = _run(tmp_path, _attach(tmp_path, [df]))
    text = capsys.readouterr().out
    assert rc == 0 and "raw_audit" not in text


@pytest.mark.parametrize("pattern", ["0:{s}.{d}", "{s}.{d}"], ids=["minute_form", "bare_seconds"])
def test_a_clock_one_second_ahead_still_fails_the_raw_audit(tmp_path, capsys, pattern):
    df = _game("g1")
    df["clock_display_value"] = [pattern.format(s=59 - i % 50, d=i % 9 + 1) for i in range(len(df))]
    assert _run(tmp_path, _attach(tmp_path, [df])) == 0
    raw = tmp_path / "r" / "nba" / "pbp_2024.parquet"
    df = pd.read_parquet(raw)
    # every play's clock one second further on (nearer the end of the period) than the shard recorded
    df["clock_display_value"] = [pattern.format(s=58 - i % 50, d=i % 9 + 1) for i in range(len(df))]
    df.to_parquet(raw, index=False)
    fails = [f for f in _raw_fails(tmp_path, capsys).fails if "raw_audit" in f and "clock_left" in f]
    assert fails and "raw clock text" in fails[0]
