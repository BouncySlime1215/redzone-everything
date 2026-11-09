"""GameValue shards: labels, determinism, resume, the NBA licence gate, and the leak check on planted leaks.

Why: WP-25's exit check. Every leak the check claims to catch is planted here and must fail by name.
"""
import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from redzone.moments.detect import detect
from redzone.noggin import value_shards as V
from scripts import holdset, holdset_public, kaggle_value_shards as K, value_leak_check as LC

KICK = dt.datetime(2023, 10, 8, 17, 0, tzinfo=dt.timezone.utc)
NIGHTS = ["20231008", "20231015", "20231022", "20231029", "20231105", "20231112", "20231119", "20231126"]


def _iso(sec):
    return (KICK + dt.timedelta(seconds=float(sec))).strftime("%Y-%m-%dT%H:%M:%SZ")


def nfl_summary(start_s=0.0, n=180, td_every=40):
    """A 3 h football game on ESPN's shape: a play a minute, a touchdown every td_every plays."""
    plays, home = [], 0
    for i in range(n):
        td = i % td_every == td_every - 1
        home += 7 if td else 0
        plays.append({"id": str(i), "type": {"text": "Passing Touchdown" if td else "Rush"}, "text": "",
                      "period": {"number": 1 + i * 4 // n}, "clock": {"displayValue": f"{14 - i % 15}:00"},
                      "wallclock": _iso(start_s + i * 60), "scoringPlay": td, "homeScore": home, "awayScore": 3,
                      "statYardage": 2, "start": {"down": 1 + i % 3, "distance": 10, "yardsToEndzone": 60},
                      "end": {"yardsToEndzone": 58}})
    drives = [{"plays": plays[i:i + 15]} for i in range(0, n, 15)]
    return {"drives": {"previous": drives}, "winprobability": [{"playId": str(i), "homeWinPercentage": 0.5 + (i % 2) * 0.4}
                                                               for i in range(n)]}


def _rows(gid="g1", night="20231008", start_s=0.0, **kw):
    return V.build_rows("nfl", gid, nfl_summary(start_s), night=night, season=2023, source="nflverse_pbp",
                        license_line="CC-BY", plan="A", **kw)


def _shard(out, shard, rows, feature_fn=V.features):
    summary = nfl_summary(float(rows.feed_head_t.iloc[0]) - KICK.timestamp())
    n, bad = V.prefix_check("nfl", str(rows.game_id.iloc[0]), summary, rows, feature_fn=feature_fn)
    return V.write_shard(out, "nfl", shard, rows, {"source": "nflverse_pbp", "license": "CC-BY", "plan": "A",
                                                   "prefix_checked": n, "prefix_failed": len(bad), "prefix_examples": bad[:2]})


def _weekly(i):
    night = NIGHTS[i]
    start = (dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - KICK).total_seconds()
    return _rows(gid=f"g{i}", night=night, start_s=start, game_key=f"nfl:{night}:A@B")


def _clean(out):
    for i in range(len(NIGHTS)):
        _shard(out, f"w{i}", _weekly(i))
    V.finalize(out)
    return out


@pytest.fixture
def out(tmp_path):
    return _clean(tmp_path / "shards")


def _fails(out, **kw):
    return LC.check_dir(out, **kw).fails


def _has(fails, name):
    return any(f.startswith(name + ":") for f in fails)


# --- rows ------------------------------------------------------------------------------------------------------

def test_every_row_has_source_night_id_and_labels_match_the_moments():
    r = _rows()
    assert set(r.source_night_id) == {"nfl:20231008"}
    assert r.feature_t_max.le(r.decision_t).all() and r.feed_cut_t.le(r.decision_t).all()
    moments = detect("nfl", V.strip_published_wp(nfl_summary()), "g1").moments
    for row in r.iloc[[10, 60, 200]].itertuples():
        want = sum(V.TIER_WEIGHTS[m.tier] for m in moments if row.decision_t < m.t <= row.decision_t + 180)
        t1 = any(m.tier == 1 and row.decision_t < m.t <= row.decision_t + 60 for m in moments)
        assert row.value_next_3m == pytest.approx(want) and row.p_tier1_60s == float(t1)


def test_label_is_the_future_of_the_decision_not_of_the_feed_head():
    """Repro of the review's noise game: with a head-anchored label, a feed silent for 180 s read as a label of exactly 0."""
    r = _rows()
    stale = r[(r.feed_cut_t - r.feed_head_t) > 0]
    assert len(stale) and (stale.decision_t > stale.feed_head_t).all()
    moments = detect("nfl", V.strip_published_wp(nfl_summary()), "g1").moments
    for row in r.iloc[::37].itertuples():
        first = [m.t for m in moments if row.decision_t < m.t <= row.decision_t + 180]
        if first:
            assert row.label_first_t == min(first) and row.label_first_t > row.decision_t
        else:
            assert np.isnan(row.label_first_t) and row.value_next_3m == 0


def test_a_silent_feed_is_not_a_mechanical_zero_label():
    s = nfl_summary(td_every=46)                       # play 45 opens the fourth drive and is a touchdown
    previous = s["drives"]["previous"]
    gap = 240.0
    for d in previous[3:]:
        for p in d["plays"]:
            p["wallclock"] = _iso(dt.datetime.fromisoformat(p["wallclock"].replace("Z", "+00:00")).timestamp() - KICK.timestamp() + gap)
    r = V.build_rows("nfl", "g", s, night="20231008", season=2023, source="s", license_line="l", plan="A")
    stale = r[(r.decision_t - r.feed_head_t >= 180) & (r.label_full_window == 1)]
    assert len(stale) and (stale.value_next_3m > 0).any()


def test_tier_weights_are_the_bar_the_brain_must_beat():
    assert V.TIER_WEIGHTS == holdset.WEIGHTS


def test_published_win_probability_never_reaches_a_label():
    assert "wp5" not in {m.kind for m in detect("nfl", V.strip_published_wp(nfl_summary()), "g").moments}


def test_lag_is_sampled_from_the_profile_and_stable():
    a, b = V.sample_lags("nfl", "g", 500), V.sample_lags("nfl", "g", 500)
    assert (a == b).all() and a.std() > 1 and a.max() <= V.LAG_CAP_S and a.min() >= 0
    assert (V.sample_lags("nfl", "h", 500) != a).any()


def test_rows_stop_at_a_feed_gap():
    s = nfl_summary()
    plays = s["drives"]["previous"]
    for d in plays[6:]:
        for p in d["plays"]:
            p["wallclock"] = _iso(dt.datetime.fromisoformat(p["wallclock"].replace("Z", "+00:00")).timestamp()
                                  - KICK.timestamp() + 3000)
    r = V.build_rows("nfl", "g", s, night="20231008", season=2023, source="s", license_line="l", plan="A")
    assert (r.feed_cut_t - r.feed_head_t).max() <= V.MAX_GAP_S


def test_thin_game_gives_no_rows():
    assert V.build_rows("nfl", "g", nfl_summary(n=20), night="20231008", season=2023, source="s", license_line="l", plan="A").empty


def test_columns_are_the_same_list_as_the_leak_check():
    assert set(V.VALUE_COLUMNS) == set(LC.ALLOWED) and V.VALUE_FEATURES == LC.FEATURES and V.VALUE_LABELS == LC.LABELS


# --- determinism and resume ------------------------------------------------------------------------------------

def test_shards_build_deterministically(tmp_path):
    a, b = _clean(tmp_path / "a"), _clean(tmp_path / "b")
    for name in ("nfl/w0.parquet", "nfl/w3.json", "manifest.json", "splits.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_resume_never_rebuilds_a_finished_shard(tmp_path):
    out = tmp_path / "s"
    assert not V.shard_done(out, "nfl", "2023")
    _shard(out, "2023", _rows())
    assert V.shard_done(out, "nfl", "2023")
    V.shard_paths(out, "nfl", "2023")[1].unlink()     # killed before the sidecar: looks unfinished
    assert not V.shard_done(out, "nfl", "2023")


def test_carry_over_copies_finished_shards_only(tmp_path):
    prev = tmp_path / "input" / "old" / "value_shards"
    _shard(prev, "2023", _rows())
    _shard(prev, "2022", _rows(gid="g2"))
    V.shard_paths(prev, "nfl", "2022")[0].unlink()
    new = tmp_path / "new"
    assert K.carry_over(str(tmp_path / "input"), new) == 1
    assert V.shard_done(new, "nfl", "2023") and not V.shard_done(new, "nfl", "2022")


# --- licence gate ----------------------------------------------------------------------------------------------

def test_nba_is_off_by_default():
    assert V.public_status("nba")[0] == "not_cleared"
    with pytest.raises(V.SourceNotCleared):
        V.public_source("nba")


def test_nba_flag_is_tagged_personal_use():
    source, licence, plan, _, _ = V.public_source("nba", personal_nba=True)
    assert source == "hoopr_espn_nba" and V.PERSONAL_USE in licence and plan == "B"


def test_uncleared_leagues_are_refused():
    for lg in ("cfb", "cbb", "nhl", "wcbb"):
        assert V.public_status(lg, personal_nba=True)[0] == "not_cleared"
    assert V.public_status("mlb")[0] == "no_wallclock"


def test_cli_builds_nothing_for_uncleared_leagues_and_fails_closed(tmp_path, capsys):
    rc = K.main(["--source", "public", "--leagues", "nba,cfb,mlb", "--out", str(tmp_path / "o"), "--raw", str(tmp_path / "r"),
                 "--input", str(tmp_path / "none"), "--jobs", "1"])
    text = capsys.readouterr().out
    assert rc == 1 and "not_cleared" in text and "no_wallclock" in text and not list((tmp_path / "o").glob("*/*.parquet"))


# --- the leak check: clean passes, every planted leak fails by name -------------------------------------------

def test_leak_check_passes_clean_shards(out):
    assert _fails(out) == []


def test_leak_check_fails_on_an_empty_directory(tmp_path):
    assert _has(_fails(tmp_path), "no_shards")


def _rewrite(out, mutate, shard="w2"):
    df = pd.read_parquet(V.shard_paths(out, "nfl", shard)[0])
    df = mutate(df)
    side = json.loads(V.shard_paths(out, "nfl", shard)[1].read_text())
    V.write_shard(out, "nfl", shard, df, {k: side.get(k) for k in ("source", "license", "plan", "prefix_checked", "prefix_failed")})
    V.finalize(out)


def test_planted_fresh_public_night_fails(out):
    def plant(df):
        df["night"], df["source_night_id"] = "20250906", "nfl:20250906"
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "locked_night")


def test_planted_night_relabelled_to_dodge_the_cut_fails(out):
    """A 2026 game written down as if it were an old night: its wall clock gives it away."""
    def plant(df):
        shift = (dt.datetime(2026, 1, 11, tzinfo=dt.timezone.utc) - KICK).total_seconds()
        for c in ("decision_t", "feed_cut_t", "feed_head_t", "feature_t_max"):
            df[c] += shift
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "locked_night")


def test_planted_missing_source_night_id_fails(out):
    def plant(df):
        df["source_night_id"] = None
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "source_night_id")


def test_planted_wrong_source_night_id_fails(out):
    def plant(df):
        df["source_night_id"] = "nfl:19990101"
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "source_night_id")


def test_planted_feature_after_the_decision_fails(out):
    def plant(df):
        df["feature_t_max"] = df["decision_t"] + 30.0
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "future_feature")


def test_planted_future_reading_feature_builder_fails(tmp_path):
    """A builder whose stamps look fine but whose feature reads the final score."""
    def cheating(view, head, decision):
        got = V.features(view, head, decision)
        got["abs_margin"] = np.full(len(head), float(abs(view.margin[-1])))
        return got
    out = tmp_path / "s"
    for i in range(len(NIGHTS)):
        _shard(out, f"w{i}", _weekly(i) if i else V.build_rows(
            "nfl", "g0", nfl_summary(0.0), night=NIGHTS[0], season=2023, source="nflverse_pbp", license_line="CC-BY", plan="A",
            game_key="nfl:20231008:A@B", feature_fn=cheating), feature_fn=V.features)
    V.finalize(out)
    assert _has(_fails(out), "future_feature")


def test_planted_label_event_before_the_decision_fails(out):
    def plant(df):
        df["label_first_t"] = df["decision_t"] - 1.0
        df["value_next_3m"] = 1.0
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "label_window")


def test_planted_event_with_a_zero_label_fails(out):
    def plant(df):
        df["label_first_t"] = df["decision_t"] + 1.0
        df["value_next_3m"] = 0.0
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "label_window")


def test_a_shard_from_before_the_label_fix_is_not_finished_and_fails(out):
    side = V.shard_paths(out, "nfl", "w2")[1]
    info = json.loads(side.read_text())
    assert V.shard_done(out, "nfl", "w2")
    side.write_text(json.dumps({**info, "features_version": "v1"}))
    assert not V.shard_done(out, "nfl", "w2")
    assert _has(_fails(out), "sidecar")


def test_leak_check_knows_the_shards_version():
    assert LC.FEATURES_VERSION == V.FEATURES_VERSION and LC.HORIZON_S == V.HORIZON_S and LC.TIER1_S == V.TIER1_S


# --- the raw-source audit: a future fact written by the adapter ------------------------------------------------

def _raw_game(gid="2023_05_AAA_BBB"):
    """An nflverse-shaped game with sparse touchdowns, so a future-TD flag is a real signal."""
    rows, home = [], 0
    for i in range(180):
        td = i % 25 == 24
        home += 7 if td else 0
        rows.append({"game_id": gid, "play_id": i + 1, "time_of_day": _iso(i * 60), "time": f"{14 - i % 15}:00", "qtr": 1 + i * 4 // 180,
                     "play_type": "run", "desc": "x", "timeout": 0, "touchdown": float(td), "field_goal_result": None,
                     "extra_point_result": None, "interception": 0, "fumble_lost": 0, "sack": 0, "complete_pass": 0, "safety": 0,
                     "sp": float(td), "total_home_score": float(home), "total_away_score": 3.0, "season_type": "REG",
                     "two_point_attempt": 0, "drive": i // 12, "down": 1.0, "ydstogo": 10.0, "yardline_100": 60.0, "yards_gained": 4.0})
    return pd.DataFrame(rows)


def _raw_shard(tmp_path, summary, frame, gid="2023_05_AAA_BBB"):
    out, raw = tmp_path / "s", tmp_path / "raw"
    rows = V.build_rows("nfl", gid, summary, night="20231008", season=2023, source="nflverse_pbp", license_line="CC-BY", plan="A",
                        game_key="nfl:20231008:AAA@BBB")
    n, bad = V.prefix_check("nfl", gid, summary, rows, n=len(rows))
    V.write_shard(out, "nfl", "2023", rows, {"source": "nflverse_pbp", "license": "CC-BY", "plan": "A", "prefix_checked": n,
                                             "prefix_failed": len(bad), "prefix_examples": bad[:2]})
    V.finalize(out)
    (raw / "nfl").mkdir(parents=True, exist_ok=True)
    frame.to_parquet(raw / "nfl" / "pbp_2023.parquet", index=False)
    return out, raw


def test_clean_adapter_passes_the_raw_audit(tmp_path):
    frame = _raw_game()
    out, raw = _raw_shard(tmp_path, holdset_public.nfl_summary(frame), frame)
    rep = LC.check_dir(out, raw=raw)
    assert rep.fails == [] and rep.checked["raw_rows"] == LC.RAW_ROWS


def _plant_future_td(summary):
    """The adapter bug the review planted: a play's field says whether a touchdown comes in the next 180 s."""
    plays = [p for d in summary["drives"]["previous"] for p in d["plays"]]
    walls = np.array([V.parse_ts(p["wallclock"]) for p in plays])
    tds = np.array(["Touchdown" in p["type"]["text"] for p in plays])
    for i, p in enumerate(plays):
        p["end"]["yardsToEndzone"] = 50.0 if tds[(walls > walls[i]) & (walls <= walls[i] + 180)].any() else 10.0
    return summary


def test_planted_future_touchdown_in_an_adapter_field_fails(tmp_path):
    frame = _raw_game()
    out, raw = _raw_shard(tmp_path, _plant_future_td(holdset_public.nfl_summary(frame)), frame)
    assert LC.check_dir(out).fails == []                    # stamps and the prefix rebuild cannot see it
    rep = LC.check_dir(out, raw=raw)
    assert _has(rep.fails, "raw_audit")
    assert any("yards_to_goal" in f for f in rep.fails)


def test_planted_head_anchored_label_fails_the_raw_audit(tmp_path):
    frame = _raw_game()
    out, raw = _raw_shard(tmp_path, holdset_public.nfl_summary(frame), frame)
    df = pd.read_parquet(V.shard_paths(out, "nfl", "2023")[0])
    moments = detect("nfl", holdset_public.nfl_summary(frame), "x").moments
    times = np.array([m.t for m in moments])
    df["value_next_3m"] = [float(sum(V.TIER_WEIGHTS[m.tier] for m in moments if h < m.t <= h + 180)) for h in df.feed_head_t]
    df["label_first_t"] = [float(times[(times > h)][0]) if ((times > h) & (times <= h + 180)).any() else float("nan") for h in df.feed_head_t]
    side = json.loads(V.shard_paths(out, "nfl", "2023")[1].read_text())
    V.write_shard(out, "nfl", "2023", df, {k: side.get(k) for k in ("source", "license", "plan", "prefix_checked", "prefix_failed")})
    assert any(f.startswith("raw_audit") and "label" in f for f in LC.check_dir(out, raw=raw).fails)


def test_raw_audit_fails_closed_on_a_missing_raw_file_or_game(tmp_path):
    frame = _raw_game()
    out, raw = _raw_shard(tmp_path, holdset_public.nfl_summary(frame), frame)
    frame.assign(game_id="other").to_parquet(raw / "nfl" / "pbp_2023.parquet", index=False)
    assert _has(LC.check_dir(out, raw=raw).fails, "raw_audit")
    (raw / "nfl" / "pbp_2023.parquet").unlink()
    assert _has(LC.check_dir(out, raw=raw).fails, "raw_audit")


# --- the drive in progress -------------------------------------------------------------------------------------

def test_the_drive_in_progress_counts_in_plays_truncate_and_moments():
    s = nfl_summary()
    whole = len(V.flat_plays("nfl", s))
    live = {**s, "drives": {"previous": s["drives"]["previous"][:-1], "current": s["drives"]["previous"][-1]}}
    assert len(V.flat_plays("nfl", live)) == whole
    assert len(detect("nfl", live, "g").moments) == len(detect("nfl", s, "g").moments)
    end = V.flat_plays("nfl", s)[-1][0] + 1
    assert len(V.flat_plays("nfl", V.truncate("nfl", live, end))) == whole


def test_a_play_listed_in_both_drives_counts_once():
    s = nfl_summary()
    prev = s["drives"]["previous"]
    both = {**s, "drives": {"previous": prev, "current": {"plays": prev[-1]["plays"][-2:]}}}
    assert len(V.flat_plays("nfl", both)) == len(V.flat_plays("nfl", s))


def test_prefix_check_is_clean_on_the_real_features():
    r = _rows()
    assert V.prefix_check("nfl", "g1", nfl_summary(), r) == (V.PREFIX_ROWS, [])


def test_planted_published_wp_column_fails(out):
    def plant(df):
        df["home_wp_post"] = 0.5
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "columns")


def test_planted_label_copied_into_a_feature_fails(out):
    def plant(df):
        df["pts_5m"] = df["value_next_3m"]
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "label_copy")


def test_planted_night_in_two_splits_fails(out):
    s = json.loads((out / "splits.json").read_text())
    s["nfl"]["test"].append(s["nfl"]["train"][0])
    (out / "splits.json").write_text(json.dumps(s))
    assert _has(_fails(out), "splits")


def test_missing_splits_file_fails_closed(out):
    (out / "splits.json").unlink()
    assert _has(_fails(out), "splits")


def test_planted_game_on_two_nights_fails(out):
    def plant(df):
        df["game_id"] = "g0"
        return df
    _rewrite(out, plant, shard="w3")
    assert _has(_fails(out), "game_two_nights")


def test_planted_same_game_from_two_sources_fails(out):
    df = pd.read_parquet(V.shard_paths(out, "nfl", "w0")[0]).copy()
    df["game_id"], df["source"] = "espn9", "nflverse_pbp"
    df["game_key"] = "nfl:20231008:A@B"
    side = json.loads(V.shard_paths(out, "nfl", "w0")[1].read_text())
    df2 = df.copy()
    df2["source"] = "other"
    V.write_shard(out, "nfl", "x", df2, {**{k: side.get(k) for k in ("license", "plan", "prefix_checked", "prefix_failed")}, "source": "other"})
    V.finalize(out)
    fails = _fails(out)
    assert _has(fails, "game_two_sources") and _has(fails, "licence")


def test_planted_uncleared_source_fails(out):
    def plant(df):
        df["source"] = "statcast_bulk"
        return df
    _rewrite(out, plant)
    assert _has(_fails(out), "licence")


def test_nba_rows_fail_without_the_flag_and_pass_with_it(tmp_path):
    t0 = (dt.datetime(2023, 12, 1, 23, 0, tzinfo=dt.timezone.utc) - KICK).total_seconds()
    plays = [{"id": str(i), "type": {"text": "Jump Shot"}, "text": "", "period": {"number": 1 + i * 4 // 300},
              "clock": {"displayValue": "9:00"}, "wallclock": _iso(t0 + i * 30), "scoringPlay": False,
              "homeScore": 0, "awayScore": 0} for i in range(300)]
    out = tmp_path / "s"
    r = V.build_rows("nba", "401", {"plays": plays}, night="20231201", season=2024, source="hoopr_espn_nba",
                     license_line=V.public_source("nba", True)[1], plan="B", game_key="nba:20231201:A@B")
    n, bad = V.prefix_check("nba", "401", {"plays": plays}, r)
    V.write_shard(out, "nba", "2024", r, {"source": "hoopr_espn_nba", "license": V.public_source("nba", True)[1], "plan": "B",
                                          "prefix_checked": n, "prefix_failed": len(bad)})
    V.finalize(out)
    assert _has(_fails(out), "licence")
    assert _fails(out, allow_personal_nba=True) == []


def test_tampered_shard_fails(out):
    pq = V.shard_paths(out, "nfl", "w1")[0]
    df = pd.read_parquet(pq)
    df.loc[0, "value_next_3m"] = 99.0
    df.to_parquet(pq, index=False)
    assert _has(_fails(out), "sidecar")


# --- archive rows ----------------------------------------------------------------------------------------------

@pytest.fixture
def archive(tmp_path):
    """Eight fixture nights: the newest two are the locked block (splits.TEST_FRAC)."""
    root = tmp_path / "archive"
    (root / "nfl").mkdir(parents=True)
    nights = [f"202509{d:02d}" for d in (7, 8, 14, 15, 21, 22, 28, 29)]
    for i, n in enumerate(nights):
        (root / "nfl" / f"scoreboard_{n}.json").write_text(json.dumps({"events": [{"id": f"a{i}"}]}))
    return root, nights


def _archive_shard(out, gid, night):
    start = (dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - KICK).total_seconds()
    rows = V.build_rows("nfl", gid, nfl_summary(start), night=night, season=2025, source="archive", license_line="dev", plan="B")
    n, bad = V.prefix_check("nfl", gid, nfl_summary(start), rows)
    V.write_shard(out, "nfl", gid, rows, {"source": "archive", "license": "dev", "plan": "B", "prefix_checked": n, "prefix_failed": len(bad)})
    V.finalize(out)


def test_archive_dev_night_passes(tmp_path, archive):
    root, nights = archive
    _archive_shard(tmp_path / "s", "a0", nights[0])
    assert _fails(tmp_path / "s", archive=root) == []


def test_planted_locked_archive_night_fails(tmp_path, archive):
    root, nights = archive
    _archive_shard(tmp_path / "s", "a7", nights[7])
    assert _has(_fails(tmp_path / "s", archive=root), "locked_night")


def test_planted_fresh_archive_game_fails(tmp_path, archive):
    """A game the pinned scoreboards never listed is fresh: refused even though its night looks old."""
    root, nights = archive
    _archive_shard(tmp_path / "s", "fresh1", nights[0])
    assert _has(_fails(tmp_path / "s", archive=root), "locked_night")


def test_archive_rows_with_no_archive_fail_closed(tmp_path, archive):
    _, nights = archive
    _archive_shard(tmp_path / "s", "a0", nights[0])
    assert _has(_fails(tmp_path / "s"), "locked_night")


def test_archive_cli_refuses_locked_splits():
    with pytest.raises(SystemExit):
        K.main(["--source", "archive", "--split", "test", "--archive", "x", "--out", "y"])


def test_archive_build_opens_only_dev_summaries(tmp_path, archive):
    root, nights = archive
    for i, n in enumerate(nights[:6]):
        start = (dt.datetime.strptime(n, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc) - KICK).total_seconds()
        (root / "nfl" / f"summary_a{i}.json").write_text(json.dumps(nfl_summary(start)))
    (root / "nfl" / "summary_a7.json").write_text("{ a locked file that must never be parsed")
    rc = K.main(["--source", "archive", "--split", "fit", "--archive", str(root), "--leagues", "nfl", "--out", str(tmp_path / "o")])
    assert rc == 0
    got = pd.read_parquet(tmp_path / "o" / "nfl" / "archive_fit.parquet")
    assert set(got.night) <= set(nights[:6]) and len(got)


# --- public pipeline on a tiny nflverse-shaped parquet ----------------------------------------------------------

def _nflverse(path, nights):
    rows = []
    for g, night in enumerate(nights):
        base = dt.datetime.strptime(night, "%Y%m%d").replace(hour=17, tzinfo=dt.timezone.utc)
        for i in range(180):
            rows.append({"game_id": f"2023_{g:02d}_AAA_BBB", "play_id": i + 1, "time_of_day": (base + dt.timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "time": "10:00", "qtr": 1 + i * 4 // 180, "play_type": "pass", "desc": "pass", "timeout": 0, "touchdown": float(i % 40 == 39),
                         "field_goal_result": None, "extra_point_result": None, "interception": 0, "fumble_lost": 0, "sack": 0, "complete_pass": 1, "safety": 0,
                         "sp": float(i % 40 == 39), "total_home_score": float(7 * (i // 40)), "total_away_score": 3.0, "season_type": "REG", "two_point_attempt": 0,
                         "drive": i // 12, "down": 1.0, "ydstogo": 10.0, "yardline_100": 60.0, "yards_gained": 4.0, "home_team": "BBB", "away_team": "AAA"})
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_public_season_builds_drops_eval_nights_and_passes_the_leak_check(tmp_path, monkeypatch):
    src = tmp_path / "pbp.parquet"
    _nflverse(src, ["20231008", "20231015", "20250907"])     # the third is inside the eval window
    import shutil
    import scripts.noggin_factory as NF

    def fake_fetch(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        return True
    monkeypatch.setattr(NF, "_fetch", fake_fetch)
    out = tmp_path / "o"
    rc = K.main(["--source", "public", "--leagues", "nfl", "--seasons", "2023", "--out", str(out), "--raw", str(tmp_path / "raw"),
                 "--input", str(tmp_path / "none"), "--jobs", "1"])
    assert rc == 0
    side = json.loads(V.shard_paths(out, "nfl", "2023")[1].read_text())
    got = pd.read_parquet(V.shard_paths(out, "nfl", "2023")[0])
    assert side["dropped_eval_games"] == 1 and set(got.night) == {"20231008", "20231015"}
    assert side["prefix_checked"] and not side["prefix_failed"] and set(got.source_night_id) == {"nfl:20231008", "nfl:20231015"}
    assert set(got.game_key) == {"nfl:20231008:AAA@BBB", "nfl:20231015:AAA@BBB"}
    before = V.shard_paths(out, "nfl", "2023")[0].stat().st_mtime_ns
    assert K.main(["--source", "public", "--leagues", "nfl", "--seasons", "2023", "--out", str(out), "--raw", str(tmp_path / "raw"),
                   "--input", str(tmp_path / "none"), "--jobs", "1"]) == 0
    assert V.shard_paths(out, "nfl", "2023")[0].stat().st_mtime_ns == before       # resumed, not rebuilt
