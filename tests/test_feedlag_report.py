"""Arrival-lag report and rebuilt replay profile (redzone/lab/feedlag_report.py, scripts/feedlag_report.py). No network."""
import json
import random

import pytest

from redzone.lab import feedlag, timing
from redzone.lab import feedlag_report as fr
from scripts import feedlag_report as cli

T0 = 1_794_000_000.0


def arr(src="espn_summary", lag=40.0, gap=10.0, kind="foul", stop="whistle", night="2026-11-12", game="g1", i=0,
        lg="nba", prev="auto", stamp=None):
    """An Arrival whose true publication was `lag` after its stamp and which we first saw `gap`/2 later on average."""
    stamp = T0 + i if stamp is None else stamp
    t = stamp + lag + gap / 2.0
    return fr.Arrival(t, t - gap if prev == "auto" else prev, src, lg, game, str(i), kind, "x", stop, stamp, night)


def test_the_two_lag_estimates_bracket_the_truth():
    a = arr(lag=30.0, gap=10.0)                       # seen 5 s after publishing, window 10 s wide
    assert a.lag_obs == pytest.approx(35.0) and a.lag_mid == pytest.approx(30.0)
    assert a.lag_mid <= a.lag_obs


def test_rows_with_no_stamp_no_window_or_a_wide_window_are_left_out_and_counted():
    no_stamp = fr.Arrival(T0 + 50, T0 + 40, "espn_summary", "nba", "g", "1", "foul", "", "", None, "n")
    rows = [arr(), no_stamp, arr(prev=None), arr(gap=100.0), arr(lag=5000.0), arr(lag=-60.0)]
    ok, why = fr.usable(rows)
    assert len(ok) == 1 and why == {"no_stamp": 1, "no_window": 1, "wide_window": 1, "implausible_lag": 2}


def test_quantiles_per_source_and_row_kind_with_the_share_inside_the_assumed_lag():
    rng = random.Random(1)
    rows = []
    for i in range(300):
        rows.append(arr("espn_summary", lag=rng.uniform(30, 50), kind="foul", i=i))          # ESPN is slow
        rows.append(arr("nba_cdn", lag=rng.uniform(4, 14), kind="shot_made", stop="none", i=i))
    rows += [arr("nba_cdn", lag=rng.uniform(40, 120), kind="block", stop="none", i=1000 + i) for i in range(40)]   # blocks have a tail
    rep = fr.report(rows)
    e, c = rep["sources"]["espn_summary"], rep["sources"]["nba_cdn"]
    assert 38 <= e["all"]["p50"] <= 42 and e["all"]["within_assumed_pct"] == 0.0
    assert 7 <= c["by_kind"]["shot_made"]["p50"] <= 11 and 0 < c["by_kind"]["shot_made"]["within_assumed_pct"] < 100
    assert c["by_kind"]["block"]["p90"] > 90 > c["by_kind"]["shot_made"]["p90"]               # the tail shows by row kind
    assert c["by_kind"]["shot_made"]["obs_p50"] > c["by_kind"]["shot_made"]["p50"]            # upper bound sits above
    assert e["dead_ball"]["n"] == 300 and c["dead_ball"] == {"n": 0}
    text = fr.format_report(rep)
    assert "espn_summary" in text and "block" in text and "not met yet" in text


def games(nights, per_night, rows_each=60, srcs=("espn_summary", "nba_cdn")):
    out = []
    for n in range(nights):
        for g in range(per_night):
            for s in srcs:
                out += [arr(s, night=f"2026-11-{10 + n:02d}", game=f"n{n}g{g}", i=k, stop="none", kind="shot_made") for k in range(rows_each)]
    return out


def test_the_c2_bar_needs_five_nights_ten_games_and_both_sources():
    ok = fr.report(games(5, 2))["coverage"]                                  # 5 nights x 2 games = 10 per source
    assert ok["bar_met"] and ok["nba_games_by_source"] == {"espn_summary": 10, "nba_cdn": 10} and ok["nba_games_with_both"] == 10
    assert not fr.report(games(4, 3))["coverage"]["bar"]["nights>=5"]
    assert not fr.report(games(5, 1))["coverage"]["bar"]["nba_games>=10"]
    assert not fr.report(games(5, 2, srcs=("espn_summary",)))["coverage"]["bar"]["both_sources"]
    assert not fr.report(games(5, 2, rows_each=10))["coverage"]["bar_met"]    # games with too few usable rows do not count


def test_load_arrivals_reads_row_events_only_and_skips_garbage(tmp_path):
    p = tmp_path / "2026-11-12.jsonl"
    good = {"ev": "row", "t": T0 + 50, "prev": T0 + 40, "src": "nba_cdn", "lg": "nba", "game": "g", "row": "1", "kind": "foul",
            "typ": "foul/personal", "stop": "whistle", "stamp": T0}
    p.write_text("\n".join([json.dumps(good), json.dumps({"ev": "start"}), "not json", json.dumps({"ev": "row", "t": "x"}), ""]))
    rows = fr.load_arrivals([p, tmp_path / "missing.jsonl"])
    assert len(rows) == 1 and rows[0].night == "2026-11-12" and rows[0].kind == "foul" and rows[0].dead
    assert fr.day_files(tmp_path) == [p] and fr.day_files(tmp_path, ["2026-11-13"]) == []


def base_profile():
    q = [float(i) for i in range(21)]
    return {"source": "espn_summary", "days": ["2026-11-02"], "quantiles": list(timing.QS),
            "leagues": {"nba": {"n": 1952, "basis": "dead_ball", "q": q}, "nfl": {"n": 150, "basis": "all_plays", "q": q},
                        "cbb": {"n": 846, "basis": "dead_ball", "q": q}},
            "sources": {"nhl_wsc": {"nhl": {"n": 0, "basis": "derived", "q": q}}, "nba_cdn": {"nba": {"n": 1359, "basis": "dead_ball", "q": q}}}}


def test_the_rebuilt_profile_keeps_the_schema_replaces_only_covered_entries_and_the_sampler_reads_it():
    rng = random.Random(2)
    rows = [arr("espn_summary", lag=rng.uniform(30, 50), i=i, night="2026-11-12") for i in range(300)]
    rows += [arr("nba_cdn", lag=rng.uniform(5, 15), i=i, night="2026-11-12", kind="shot_made", stop="none") for i in range(120)]
    base = base_profile()
    prof = fr.build_replay_profile(rows, base=base)
    assert base == base_profile()                                             # the input is not touched
    assert prof["quantiles"] == list(timing.QS) and set(prof["leagues"]) == {"nba", "nfl", "cbb"}
    nba = prof["leagues"]["nba"]
    assert nba["from"] == "feedlag" and nba["n"] == 300 and nba["basis"] == "dead_ball" and len(nba["q"]) == 21
    assert nba["q"] == sorted(nba["q"]) and 30 <= nba["q"][10] <= 50
    assert prof["leagues"]["nfl"] == base["leagues"]["nfl"] and prof["leagues"]["cbb"] == base["leagues"]["cbb"]   # no rows: kept
    assert prof["sources"]["nhl_wsc"] == base["sources"]["nhl_wsc"]
    cdn = prof["sources"]["nba_cdn"]["nba"]
    assert cdn["basis"] == "all_plays" and cdn["n"] == 120 and 5 <= cdn["q"][10] <= 15 and cdn["days"] == ["2026-11-12"]
    assert prof["feedlag"]["replaced"] == ["espn_summary/nba", "nba_cdn/nba"]
    sampler = timing.LagSampler(prof, use={"nba": "nba_cdn"})
    assert sampler.has("nba")
    draws = [sampler.draw("nba", random.Random(i)) for i in range(50)]
    assert all(0 <= d <= 16 for d in draws)                                   # the NBA feed's profile, not ESPN's
    assert 25 <= timing.LagSampler(prof).draw("nba", random.Random(3)) <= 60  # ESPN's, rebuilt


def test_the_upper_bound_basis_gives_later_lags_than_the_midpoint():
    rows = [arr("espn_summary", lag=40.0, gap=10.0, i=i) for i in range(100)]
    mid = fr.build_replay_profile(rows, base=base_profile(), basis="mid")["leagues"]["nba"]["q"][10]
    obs = fr.build_replay_profile(rows, base=base_profile(), basis="obs")["leagues"]["nba"]["q"][10]
    assert obs - mid == pytest.approx(5.0, abs=0.1)
    with pytest.raises(ValueError):
        fr.build_replay_profile(rows, base=base_profile(), basis="nope")


def test_too_few_rows_replace_nothing():
    prof = fr.build_replay_profile([arr(i=i) for i in range(10)], base=base_profile())
    assert prof["leagues"] == base_profile()["leagues"] and prof["feedlag"]["replaced"] == []


def test_the_committed_profile_is_never_overwritten(tmp_path):
    before = timing.PROFILE_PATH.read_text()
    with pytest.raises(ValueError):
        fr.write_profile({"x": 1}, timing.PROFILE_PATH)
    assert timing.PROFILE_PATH.read_text() == before
    out = fr.write_profile({"x": 1}, tmp_path / "sub" / "p.json")
    assert json.loads(out.read_text()) == {"x": 1}


def test_cli_prints_the_report_and_writes_a_profile_copy(tmp_path, capsys):
    assert cli.main(["--dir", str(tmp_path)]) == 2                            # nothing logged yet
    lines = []
    for i in range(300):
        for s, lag, kind in (("espn_summary", 40.0, "foul"), ("nba_cdn", 10.0, "shot_made")):
            lines.append(json.dumps({"ev": "row", "t": T0 + i + lag + 5, "prev": T0 + i + lag - 5, "src": s, "lg": "nba", "game": "g1",
                                     "row": str(i), "kind": kind, "typ": "x", "stop": "whistle" if kind == "foul" else "none",
                                     "stamp": T0 + i}))
    (tmp_path / "2026-11-12.jsonl").write_text("\n".join(lines) + "\n")
    out = tmp_path / "replay_profile.json"
    assert cli.main(["--dir", str(tmp_path), "--json", str(tmp_path / "rep.json"), "--write-profile", str(out)]) == 0
    text = capsys.readouterr().out
    assert "espn_summary" in text and "nba_cdn" in text and "replaced espn_summary/nba, nba_cdn/nba" in text
    assert json.loads(out.read_text())["feedlag"]["replaced"] == ["espn_summary/nba", "nba_cdn/nba"]
    assert json.loads((tmp_path / "rep.json").read_text())["sources"]["espn_summary"]["all"]["p50"] == pytest.approx(40.0, abs=0.2)
    assert cli.main(["--dir", str(tmp_path), "--write-profile", str(timing.PROFILE_PATH)]) == 1       # refuses the committed file


def test_the_logger_and_the_report_agree_on_the_row_format(tmp_path):
    """What the logger writes is exactly what the report reads (one end-to-end hop, no feed)."""
    log = feedlag.DayLog(tmp_path)
    tr = feedlag.ArrivalTracker(feedlag.SRC_CDN, "nba", "g1", gid="0022600230", primed=True)
    row = feedlag.FeedRow("5", T0, "timeout", "timeout/full", "timeout", 2, 300.0, T0 + 6)
    tr.empty_poll(T0 + 4)
    log.append("2026-11-12", tr.observe([row], T0 + 14, T0 + 14.3))
    (a,) = fr.load_arrivals(fr.day_files(tmp_path))
    assert (a.src, a.kind, a.dead, a.lg, a.game) == ("nba_cdn", "timeout", True, "nba", "g1")
    assert a.lag_obs == pytest.approx(14.3) and a.lag_mid == pytest.approx(9.15)
