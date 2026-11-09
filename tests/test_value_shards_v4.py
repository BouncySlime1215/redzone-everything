"""Features v4: the drive state, the one-scale league numbers, and the proof they never read ahead.

Why: the fair test ranks ESPN feeds with a model fit on nflverse rows, and the two sources disagree after a possession ends and on rows only one has.
v4 closes that gap and adds what a tree cannot derive at depth 6. Each family gets a planted-future test, a builder that peeks must be caught by the
prefix rebuild, and the raw audit recomputes every v4 column from nflverse's columns with its own rules.
"""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V
from scripts import holdset_public as HP
from scripts import value_leak_check as LC

T0 = dt.datetime(2023, 10, 8, 17, 0, tzinfo=dt.timezone.utc)


def _iso(sec):
    return (T0 + dt.timedelta(seconds=float(sec))).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def v4():
    V.select_features("v4")
    yield
    V.select_features("v2")


def _snap(i, kind="Rush", down=1, dist=10, y0=60, gain=4, per=1, clock="10:00", text="", score=(0, 0), scoring=False):
    return (float(i), {"id": str(i), "type": {"text": kind}, "text": text, "period": {"number": per}, "clock": {"displayValue": clock}, "scoringPlay": scoring,
                       "homeScore": score[0], "awayScore": score[1], "statYardage": gain, "start": {"down": down, "distance": dist, "yardsToEndzone": y0},
                       "end": {"yardsToEndzone": y0 - gain}})


def test_v4_only_appends_and_stays_off_unless_asked_for():
    assert V.VALUE_FEATURES[:35] == G.GV_FEATURE_SETS["v3"] and V.VALUE_FEATURES[35:] == G.V4_FEATURES
    assert LC.FEATURES_BY_VERSION["v4"] == V.VALUE_FEATURES and LC.allowed_for("v4") == frozenset(V.VALUE_COLUMNS)
    assert all(f in G.GV_BOUNDS for f in V.VALUE_FEATURES)


def test_the_leak_check_restates_the_same_league_constants():
    for lg, (unit, sd, rate, total, lid) in LC.SCALE.items():
        assert G.GV_LEAGUE_SCALE[lg] == (unit, sd, rate, total) and G.GV_LEAGUE_IDS[lg] == lid
        regs, length = V.REGULATION_LEN[lg]
        assert LC.PERIODS[lg] == (regs, length) and regs * length == total


def test_next_down_is_the_down_the_viewer_will_see():
    assert G.state_now("nfl", [_snap(0, gain=4)])["next_down"] == 2.0 and G.state_now("nfl", [_snap(0, gain=4)])["next_distance"] == 6.0
    first = G.state_now("nfl", [_snap(0, down=3, dist=5, gain=7)])
    assert first["next_down"] == 1.0 and first["next_distance"] == 10.0
    fourth = G.state_now("nfl", [_snap(0, down=3, dist=5, gain=1)])
    assert fourth["next_down"] == 4.0 and fourth["next_distance"] == 4.0 and fourth["poss_flip"] == 0.0


def test_a_stop_on_fourth_down_ends_the_possession():
    got = G.state_now("nfl", [_snap(0, down=4, dist=5, gain=1)])
    assert got["poss_flip"] == 1.0 and got["next_down"] == -1.0 and got["yards_to_goal"] == -1.0


def test_a_red_zone_trip_counts_once_per_drive_and_a_new_drive_clears_it():
    plays = [_snap(0, y0=30, gain=12), _snap(1, down=1, y0=18, gain=3)]
    assert G.state_now("nfl", plays)["rz_reached"] == 1.0 and G.state_now("nfl", plays[:1])["rz_reached"] == 0.0
    after = plays + [_snap(2, "Passing Touchdown", y0=15, gain=15, scoring=True), _snap(3, "Kickoff", down=0, dist=0, y0=65, gain=20), _snap(4, y0=75, gain=2)]
    got = G.state_now("nfl", after)
    assert got["rz_reached"] == 0.0 and got["drive_plays"] == 1.0 and got["drive_start_ytg"] == 75.0 and got["drive_yards"] == 2.0


def test_drive_length_clock_and_yards_add_up():
    plays = [_snap(0, y0=75, gain=10, clock="10:00"), _snap(1, y0=65, gain=5, clock="9:20"), _snap(2, down=2, dist=5, y0=60, gain=2, clock="8:40")]
    got = G.state_now("nfl", plays)
    assert got["drive_plays"] == 3.0 and got["drive_yards"] == 17.0 and got["drive_clock_s"] == 80.0 and got["yards_to_goal"] == 58.0


@pytest.mark.parametrize("end_ytg", [23, 74])
def test_a_punt_reads_the_same_from_either_source(end_ytg):
    """ESPN's punt ends at the receiver's field position, the nflverse adapter's at the punter's: v4 must not let the model tell them apart."""
    t, punt = _snap(0, "Punt", down=4, dist=3, y0=23, gain=0)
    punt = {**punt, "end": {"yardsToEndzone": end_ytg}}
    got = G.state_now("nfl", [(t, punt)])
    assert got["poss_flip"] == 1.0 and got["yards_to_goal"] == -1.0 and got["next_down"] == -1.0


def test_rows_only_one_source_has_never_become_the_feed_head():
    plays = [_snap(0, y0=70, gain=5), _snap(1, "Official Timeout", down=0, dist=0, y0=0, gain=0), _snap(2, "Two-minute warning", down=0, dist=0, y0=0, gain=0),
             _snap(3, "End Period", down=0, dist=0, y0=0, gain=0)]
    got = G.state_now("nfl", plays)
    assert got["yards_to_goal"] == 65.0 and got["next_down"] == 2.0 and got["drive_plays"] == 1.0
    assert G.feed_row("nba", plays[1][1]) is True                      # only football has this problem


def test_a_timeout_changes_nothing_but_the_count():
    plays = [_snap(0, y0=70, gain=5), _snap(1, "Timeout", down=0, dist=0, y0=0, gain=0, text="Timeout #1 by NE")]
    got = G.state_now("nfl", plays)
    assert got["yards_to_goal"] == 65.0 and got["drive_plays"] == 1.0 and got["timeouts_half"] == 1.0 and got["next_down"] == 2.0


def test_the_half_clock_and_two_minute_flag():
    got = G.state_now("nfl", [_snap(0, per=2, clock="1:30")])
    assert got["half_left"] == 90.0 and got["two_minute"] == 1.0
    assert G.state_now("nfl", [_snap(0, per=3, clock="1:30")])["half_left"] == 990.0 and G.state_now("nfl", [_snap(0, per=3, clock="1:30")])["two_minute"] == 0.0


def test_one_margin_means_the_same_in_every_sport():
    base = {"abs_margin": np.array([7.0]), "game_left": np.array([900.0]), "since_start_s": np.array([3600.0]), "pts_5m": np.array([3.75])}
    nfl, nba = G.derived_features("nfl", base), G.derived_features("nba", {**base, "abs_margin": np.array([2.5]), "pts_5m": np.array([24.0])})
    assert nfl["margin_scores"][0] == nba["margin_scores"][0] == 1.0 and nfl["pace_rel"][0] == nba["pace_rel"][0] == 1.0
    assert nfl["league_id"][0] != nba["league_id"][0] and nfl["margin_class"][0] == 2.0
    assert nba["game_frac_left"][0] == pytest.approx(900 / 2880)


def test_a_missing_clock_stays_missing():
    got = G.derived_features("nba", {"abs_margin": np.array([3.0]), "game_left": np.array([-1.0]), "since_start_s": np.array([10.0]), "pts_5m": np.array([0.0])})
    assert got["closeness"][0] == got["crunch_eta_s"][0] == got["game_frac_left"][0] == got["margin_z"][0] == -1.0


def test_crunch_eta_reads_the_game_pace():
    base = {"abs_margin": np.array([2.0]), "game_left": np.array([1440.0]), "since_start_s": np.array([3600.0]), "pts_5m": np.array([20.0])}
    assert G.derived_features("nba", base)["crunch_eta_s"][0] == pytest.approx((1440.0 - 300.0) * 3600.0 / 1440.0)


# --- full rows: a scripted nflverse game through the real adapter ---------------------------------------------------------------------------


def _raw(gid="2023_05_A_B", repeat=9):
    """Scripted nflverse rows: kickoff, two snaps, punt; kickoff, six snaps into the red zone, touchdown. Timeouts, penalties, one official timeout."""
    rows, sec, home = [], 0.0, 0.0
    def add(ptype, desc, down, togo, yl, gain, **kw):
        nonlocal sec, home
        clock = max(900 - int(sec) % 900 - 1, 1)
        row = {"game_id": gid, "play_id": len(rows) + 1, "time_of_day": _iso(sec), "time": f"{clock // 60:02d}:{clock % 60:02d}", "qtr": 1 + min(int(sec // 3000), 3), "play_type": ptype,
               "desc": desc, "timeout": 0, "touchdown": 0.0, "field_goal_result": None, "extra_point_result": None, "interception": 0, "fumble_lost": 0, "sack": 0,
               "complete_pass": 1, "safety": 0, "sp": 0.0, "total_home_score": home, "total_away_score": 3.0, "season_type": "REG", "two_point_attempt": 0, "drive": 1,
               "down": down, "ydstogo": togo, "yardline_100": yl, "yards_gained": gain}
        row.update(kw)
        rows.append(row)
        sec += 45.0
    for _ in range(repeat):
        add("kickoff", "kick", np.nan, 0, 35, 0)
        add("run", "run", 1, 10, 75, 3); add("pass", "pass", 2, 7, 72, 2)
        add("no_play", "Timeout #1 by NE at 09:12.", np.nan, 0, 70, 0, timeout=1)
        add("no_play", "OFFICIAL Timeout at 09:00.", np.nan, 0, 70, 0, timeout=1)
        add("pass", "pass", 3, 5, 70, 1); add("punt", "punt", 4, 4, 69, 0)
        add("kickoff", "kick", np.nan, 0, 35, 0)
        for down, togo, yl, gain in ((1, 10, 60, 12), (1, 10, 48, 13), (1, 10, 35, 15), (1, 10, 20, 3), (2, 7, 17, 6), (3, 1, 11, 2)):
            add("run", "run", down, togo, yl, gain)
        home += 7.0
        add("pass", "pass for touchdown", 1, 9, 9, 9, touchdown=1.0, sp=1.0, total_home_score=home)
    return pd.DataFrame(rows)


def _rows(frame):
    return V.build_rows("nfl", frame.game_id.iloc[0], HP.nfl_summary(frame), night="20231008", season=2023, source="nflverse_pbp", license_line="x", plan="A")


def _audit(frame, rows):
    rep = LC.Report()
    LC._audit_game("nfl", frame.game_id.iloc[0], frame, rows, "x", rep, "v4")
    return rep.fails


def test_a_scripted_game_rebuilds_from_a_cut_feed_and_passes_the_raw_audit():
    frame = _raw()
    rows = _rows(frame)
    assert rows.poss_flip.sum() > 5 and (rows.rz_reached == 1).sum() > 5
    assert V.prefix_check("nfl", frame.game_id.iloc[0], HP.nfl_summary(frame), rows, n=60)[1] == []
    assert _audit(frame, rows) == []


def test_the_official_timeout_row_is_never_a_head():
    frame = _raw()
    rows = _rows(frame)
    official = {holdset_t for holdset_t in (dt.datetime.fromisoformat(w.replace("Z", "+00:00")).timestamp() for w in frame[frame.desc.str.startswith("OFFICIAL")].time_of_day)}
    assert not official & set(rows.feed_head_t)


@pytest.mark.parametrize("column,change", [("yardline_100", 7.0), ("yards_gained", 3.0), ("down", 1.0), ("ydstogo", 2.0)])
def test_a_planted_future_in_the_raw_source_fails_the_audit(column, change):
    frame = _raw()
    rows = _rows(frame)
    tampered = frame.copy()
    tampered.loc[tampered.play_type == "run", column] += change
    assert any("v4 feature" in f or "feature differs" in f for f in _audit(tampered, rows))


def _plant(frame, first):
    out = frame.copy()
    later = out.index >= first
    out.loc[later & (out.play_type == "run"), "yards_gained"] += 40.0
    out.loc[later, "total_home_score"] += 30.0
    return out


@pytest.mark.parametrize("family", ["drive", "derived"])
def test_a_future_event_never_moves_an_earlier_row(family):
    frame = _raw()
    first = 80
    a, b = _rows(frame), _rows(_plant(frame, first))
    cut = dt.datetime.fromisoformat(frame.time_of_day.iloc[first].replace("Z", "+00:00")).timestamp()
    a, b = a[a.feed_head_t < cut], b[b.feed_head_t < cut]
    owned = G.DRIVE_FEATURES if family == "drive" else G.DERIVED_FEATURES + G.RELATIVE_FEATURES
    assert len(a) == len(b) and len(a) > 50
    for f in owned:
        assert np.array_equal(a[f].to_numpy(), b[f].to_numpy()), f


@pytest.mark.parametrize("family", [G.DRIVE_FEATURES, G.DERIVED_FEATURES + G.RELATIVE_FEATURES])
def test_a_builder_that_peeks_is_caught_by_the_cut_feed_rebuild(family):
    frame = _raw()
    summary = HP.nfl_summary(frame)
    def cheating(view, head, decision):
        got = V.features(view, head, decision)
        last = np.array([len(view.walls) - 1])
        whole = {**G.state_features(view.state, last), **G.derived_features("nfl", V.features(view, last, last * 0.0 + float(view.walls[-1])))}
        for f in family:
            got[f] = np.full(len(head), float(whole[f][0]) + 1.0)
        return got
    rows = V.build_rows("nfl", "g", summary, night="20231008", season=2023, source="nflverse_pbp", license_line="x", plan="A", feature_fn=cheating)
    _, bad = V.prefix_check("nfl", "g", summary, rows, n=40, feature_fn=cheating)
    assert bad and any(any(f in line for f in family) for line in bad)
